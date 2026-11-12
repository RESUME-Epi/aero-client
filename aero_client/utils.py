"""AERO client util module"""

import codecs
import dill
import hashlib
import json
import logging
import mimetypes
import os
import requests
import urllib
import uuid

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path

from globus_compute_sdk import Client as ComputeClient
from globus_sdk import AccessTokenAuthorizer
from globus_sdk import NativeAppAuthClient
from globus_sdk import RefreshTokenAuthorizer
from globus_sdk import TransferClient


from aero_client.config import _conf_symlink_path
from aero_client.config import _conf_fn
from aero_client.config import load_conf
from aero_client.error import ClientError


logger = logging.getLogger(__name__)

_CONF_ENV_VAR = "AERO_CONFIG_FILE"  # set to a config.toml path to override the default
_PROFILE_ENV_VAR = "AERO_PROFILE"  # set to select a profile within config.toml


def _load_conf_from_env_or_default():
    """Load CONF at import time.

    If ``AERO_CONFIG_FILE`` is set, load that config file without touching the
    ``~/.aero`` symlink; otherwise load the standard ``~/.aero/config.toml``.
    ``AERO_PROFILE`` selects a named profile within the file (default
    ``"default"``). Both env vars are resolved once, at import. Raises if the
    selected config or profile cannot be loaded.
    """
    profile = os.environ.get(_PROFILE_ENV_VAR, "default")
    override = os.environ.get(_CONF_ENV_VAR)
    if override:
        return load_conf(
            str(Path(override).expanduser()), symlink=False, profile=profile
        )
    return load_conf(_conf_symlink_path / _conf_fn, profile=profile)


try:
    CONF = _load_conf_from_env_or_default()
except Exception as e:
    logger.error(f"{e}")
    raise e

_REDIRECT_URI = "https://auth.globus.org/v2/web/auth-code"
_TOKEN_PATH = Path(CONF.aero_dir, CONF.token_file)

logger = logging.getLogger(__name__)


def build_url(*parts: str, trailing_slash: bool = False) -> str:
    """Join ``CONF.server_url`` with path ``parts``, preserving the server's
    path prefix (e.g. ``/fhwa``).

    Unlike :func:`urllib.parse.urljoin`, this never silently drops the base
    path when it lacks a trailing slash (``.../fhwa`` + ``data`` stays
    ``.../fhwa/data`` instead of collapsing to ``.../data``). Leading and
    trailing slashes on ``parts`` are normalized so callers don't have to be
    careful, and empty parts are ignored.
    """
    base = CONF.server_url.rstrip("/")
    cleaned = [str(p).strip("/") for p in parts if str(p).strip("/")]
    url = "/".join([base, *cleaned])
    return url + "/" if trailing_slash else url


class PolicyEnum(IntEnum):
    """
    Enum for the types of policies supported by AERO.
    """

    NONE = -1
    """No policy."""

    INGESTION = 0
    """Data ingestion policy."""

    TIMER = 1
    """Timer-based policy."""

    ANY = 2
    """..."""

    ALL = 3
    """..."""

    INGESTION_EVENT = 4
    """Ingestion flow run on external notification (POST /data/{id}/notify), no timer."""


@dataclass
class AeroOutput:
    name: str
    path: str


def serialize(obj) -> str:
    try:
        return codecs.encode(dill.dumps(obj), "base64").decode()
    except Exception:
        raise ClientError(400, "Cannot serialize the function")


def authenticate(client: NativeAppAuthClient, scope: str):
    """Perform Globus Authentication."""

    client.oauth2_start_flow(
        redirect_uri=_REDIRECT_URI, refresh_tokens=True, requested_scopes=scope
    )

    url = client.oauth2_get_authorize_url()
    print("Please visit the following url to authenticate:")
    print(url)

    auth_code = input("Enter the auth code:")
    auth_code = auth_code.strip()
    return client.oauth2_exchange_code_for_tokens(auth_code)


def _client_auth() -> str:
    """Authorizes the client to communicate with AERO

    Returns:
        str: Access token of the authorizer
    """
    client = NativeAppAuthClient(client_id=CONF.client_uuid)
    auth_token = None
    if _TOKEN_PATH.is_file():
        tokens = load_tokens()
        if CONF.portal_client_id in tokens:
            # Use the stored refresh token to mint a fresh *access* token; never
            # send the refresh token itself as the API bearer.
            rta = RefreshTokenAuthorizer(
                refresh_token=tokens[CONF.portal_client_id]["refresh_token"],
                auth_client=client,
            )
            auth_token = rta.get_authorization_header().split(" ")[-1]

    if auth_token is None:
        scopes = [
            f"https://auth.globus.org/scopes/{CONF.portal_client_id}/action_all",
            "openid",
            "profile",
            "email",
            TransferClient.scopes.all,
        ]
        token_response = authenticate(client=client, scope=scopes)

        CONF.aero_dir.mkdir(exist_ok=True, parents=True)
        with open(_TOKEN_PATH, "w+") as f:
            json.dump(token_response.by_resource_server, f)

        auth_token = token_response.by_resource_server[CONF.portal_client_id][
            "access_token"
        ]
        _ = AccessTokenAuthorizer(access_token=auth_token)

    return auth_token


def get_transfer_token(collection_uuid: str) -> str:
    """Get the transfer token belonging to the Globus Guest Collection.

    This function first verifies whether the token already exists. If
    it does not, it generates the tokens and updates the token file.

    Args:
        collection_uuid (str): The UUID of the Globus Guest Collection.

    Returns:
        str: The transfer token for the guest collection
    """
    tokens = load_tokens()

    client = NativeAppAuthClient(client_id=CONF.client_uuid)

    if collection_uuid in tokens:
        ref_transfer_token = tokens[collection_uuid]["refresh_token"]
        rta = RefreshTokenAuthorizer(
            refresh_token=ref_transfer_token, auth_client=client
        )
        transfer_token = rta.get_authorization_header().split(" ")[-1]
        return transfer_token

    else:
        scopes = [
            f"https://auth.globus.org/scopes/{collection_uuid}/https",
            TransferClient.scopes.all,
        ]

        token_response = authenticate(client=client, scope=scopes)

        tokens = tokens | token_response.by_resource_server
        with open(_TOKEN_PATH, "w+") as f:
            json.dump(tokens, f)

        transfer_token = token_response.by_resource_server[collection_uuid][
            "access_token"
        ]
        _ = AccessTokenAuthorizer(access_token=transfer_token)

        return transfer_token


def load_tokens():
    logger.debug("Token file exists. Instantiating tokens from authorizer.")
    with open(_TOKEN_PATH, "r") as f:
        tokens = json.load(f)

    return tokens


def get_collection_metadata(domain: str) -> None:
    tokens = load_tokens()
    transfer_token = tokens["transfer.api.globus.org"]["refresh_token"]
    client = NativeAppAuthClient(client_id=CONF.client_uuid)
    authorizer = RefreshTokenAuthorizer(
        refresh_token=transfer_token, auth_client=client
    )
    return TransferClient(authorizer=authorizer).endpoint_search(
        domain.replace(".data.globus.org", "")
    )


def download(*args, **kwargs):
    """Download data from user-specified repository.

    Returns:
        tuple[str, str]: Path to the data and its
            associated extension.
    """
    import hashlib
    import pathlib
    import requests
    import uuid
    from mimetypes import guess_extension
    from pathlib import Path

    from aero_client.config import CONF
    from aero_client.utils import load_tokens

    if "temp_dir" in kwargs:
        TEMP_DIR = kwargs["temp_dir"]
    else:
        TEMP_DIR = pathlib.Path.home() / "aero"
        kwargs["temp_dir"] = str(TEMP_DIR)

    tokens = load_tokens()
    auth_token = tokens[CONF.portal_client_id]["refresh_token"]

    headers = {"Authorization": f"Bearer {auth_token}"}

    response = requests.get(
        f'{CONF.server_url}/source/{kwargs["source_id"]}', headers=headers, verify=False
    )
    source = response.json()

    response = requests.get(source["url"])
    content_type = response.headers["content-type"]
    ext = guess_extension(content_type.split(";")[0])

    bn = str(uuid.uuid4())
    fn = Path(TEMP_DIR, bn)

    TEMP_DIR.mkdir(exist_ok=True, parents=True)

    with open(fn, "w+") as f:
        f.write(response.content.decode("utf-8"))

    kwargs["file"] = str(fn)
    kwargs["file_bn"] = bn
    kwargs["file_format"] = ext
    kwargs["checksum"] = hashlib.md5(response.content).hexdigest()
    kwargs["size"] = fn.stat().st_size
    kwargs["download"] = True

    return args, kwargs


def register_function(fn: callable):
    """Registers function with Globus Compute by registering the function with the wrapper"""
    gcc = ComputeClient()
    func_uuid = gcc.register_function(aero_format(fn))
    return func_uuid


def gcs_save(path: str, collection_url: str, collection_uuid: str) -> dict:
    # collection_domain = urllib.parse.urlparse(collection_url).netloc
    TRANSFER_TOKEN = get_transfer_token(collection_uuid)
    headers = {"Authorization": f"Bearer {TRANSFER_TOKEN}"}

    filename = str(uuid.uuid4())
    url = urllib.parse.urljoin(collection_url, filename)

    mtype = mimetypes.guess_type(path)

    if "text" not in mtype:
        with open(path, "rb") as f:
            data = f.read()
            checksum = hashlib.md5(data).hexdigest()
    else:
        with open(path, "r") as f:
            data = f.read()
            checksum = hashlib.md5(data.encode("utf-8")).hexdigest()

    # store in GCS
    resp = requests.put(url, headers=headers, data=data)

    Path(path).unlink(missing_ok=True)  # remove tmp output

    assert resp.status_code == 200, resp.content

    return {
        "created_at": datetime.now().ctime(),
        "checksum": checksum,
        "size": len(data),
        "file_bn": filename,
        "file_format": mtype,
    }


def aero_format(fn: callable):
    """AERO decorator that wraps user analysis function to capture provenance information."""
    import inspect
    import os
    import requests
    import urllib

    from pathlib import Path

    def wrapper(*args, **kwargs):
        fn_in = {}
        extra_in = {}
        direct_in = {}
        tmp_dirs = []

        assert "aero" in kwargs.keys()

        if "output_data" in kwargs["aero"]:
            for name, val in kwargs["aero"]["output_data"].items():
                if "file" in val:
                    fn_in[name] = val["file"]
        if "input_data" in kwargs["aero"]:
            for name, val in kwargs["aero"]["input_data"].items():
                if "tmp_dir" not in val:
                    val["tmp_dir"] = "/tmp"

                # Which revision this input is, whatever the copy mode -- staged
                # before the branches below so a plain collection input gets it
                # too. Opt-in by parameter name, like the urls.
                extra_in[f"{name}_version"] = val.get("version")
                extra_in[f"{name}_version_id"] = val.get("version_id")

                trigger_url = val.get("trigger_url")

                if val.get("fetch") is False:
                    # The function retrieves this input itself, with credentials
                    # AERO does not have. Only the input whose notify triggered
                    # the run gets a signed url, so on a private bucket every
                    # other one would 403 here.
                    url = trigger_url
                    if not url and val.get("collection_url") and val.get("file_bn"):
                        url = urllib.parse.urljoin(
                            f"{val['collection_url']}/", f"{val['file_bn']}"
                        )
                    if not url:
                        raise ValueError(
                            f"input '{name}' is marked fetch: false but has no url "
                            "to hand over. A no-copy source resolves one from its "
                            "latest version; check that it has one."
                        )

                    # The url replaces the path this input would have arrived as,
                    # so it is passed unconditionally: a mistyped parameter has to
                    # fail rather than silently drop the input. The signature
                    # stays opt-in like the extras below -- it is supplementary,
                    # and is None for every input that did not trigger the run.
                    direct_in[f"{name}_url"] = url
                    extra_in[f"{name}_signed_url"] = val.get("signed_url")
                    continue

                if trigger_url:
                    # No-copy source: AERO stores no bytes, so fetch the object
                    # itself. The signed url is the relay's presigned GET when it
                    # made one; without it (public bucket, or presigning off) the
                    # plain trigger url has to serve.
                    fetch_url = val.get("signed_url") or trigger_url
                    resp = requests.get(fetch_url)
                    # Unlike the collection fetch below, don't let an error page
                    # get written to disk and passed off to the user function as
                    # data — a 403 here usually means an unsigned or expired url.
                    resp.raise_for_status()

                    # Name the temp file after the *trigger* url, so it keeps the
                    # object's own name and extension without any signing params.
                    basename = os.path.basename(
                        urllib.parse.urlparse(trigger_url).path
                    )
                    tmp_dir = Path(val["tmp_dir"]) / str(uuid.uuid4())
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    tmp_dirs.append(tmp_dir)
                    tmp_path = tmp_dir / (basename or str(uuid.uuid4()))

                    extra_in[f"{name}_url"] = trigger_url
                    extra_in[f"{name}_signed_url"] = val.get("signed_url")
                else:
                    TRANSFER_TOKEN = get_transfer_token(val["collection_uuid"])
                    headers = {"Authorization": f"Bearer {TRANSFER_TOKEN}"}

                    resp = requests.get(
                        urllib.parse.urljoin(
                            f"{val['collection_url']}/", f"{val['file_bn']}"
                        ),
                        headers=headers,
                    )

                    tmp_path = Path(val["tmp_dir"]) / str(uuid.uuid4())

                with open(tmp_path, "wb+") as f:
                    f.write(resp.content)
                fn_in[name] = str(tmp_path)

        aero_args = kwargs.pop("aero")
        fn_in.update(**kwargs)

        # Where this run is and what it consumed, for a function that may need
        # to hand the run on to a failover endpoint (see
        # aero_client.failover.request_failover). The server puts the endpoint
        # and the tried list in `run`; an older server sends neither.
        run = aero_args.get("run") or {}
        extra_in["aero_run"] = {
            "flow_id": aero_args.get("flow_id"),
            "endpoint": run.get("endpoint"),
            "tried_endpoints": list(run.get("tried_endpoints") or []),
            "input_versions": {
                val["id"]: val["version_id"]
                for val in (aero_args.get("input_data") or {}).values()
                if val.get("id") and val.get("version_id")
            },
        }

        # Inputs the function fetches itself: the url replaces the path it would
        # otherwise have received, so it is not optional.
        fn_in.update(direct_in)

        # Opt-in: hand the url to functions that ask for it by parameter name, so
        # existing analysis functions are untouched.
        if extra_in:
            try:
                accepted = set(inspect.signature(fn).parameters)
            except (TypeError, ValueError):  # builtins/C functions
                accepted = set()
            fn_in.update(
                {k: v for k, v in extra_in.items() if k in accepted}
            )

        outputs = fn(**fn_in)

        kwargs["aero"] = aero_args

        # A flow may declare no output_data at all: the analysis writes its result
        # wherever it likes (back to the object store it read from, say) and AERO
        # records only that the run happened. Such a function returns None.
        declared = kwargs["aero"].get("output_data") or {}
        produced = (
            outputs if isinstance(outputs, list) else ([] if outputs is None else [outputs])
        )

        if not produced:
            if declared:
                raise ValueError(
                    f"function returned no outputs, but the flow declares "
                    f"{sorted(declared)}. Return an AeroOutput for each, or drop "
                    "output_data from the flow if the function stores its own results."
                )
        elif not declared:
            raise ValueError(
                "function returned an output, but the flow declares no output_data. "
                "Add an output_data entry for it, or return None if the function "
                "stores its own results."
            )
        elif isinstance(outputs, list):
            for ao in outputs:
                name = ao.name

                metadata = gcs_save(
                    path=ao.path,
                    collection_url=kwargs["aero"]["output_data"][name][
                        "collection_url"
                    ],
                    collection_uuid=kwargs["aero"]["output_data"][name][
                        "collection_uuid"
                    ],
                )
                kwargs["aero"]["output_data"][name].update(**metadata)
        else:
            assert isinstance(
                outputs, AeroOutput
            ), "ERROR: function output is not an AeroOutput"
            name = outputs.name

            metadata = gcs_save(
                path=outputs.path,
                collection_url=kwargs["aero"]["output_data"][name]["collection_url"],
                collection_uuid=kwargs["aero"]["output_data"][name]["collection_uuid"],
            )

            if "url" in kwargs["aero"]["output_data"][name].keys():
                metadata.pop("checksum", None)
            kwargs["aero"]["output_data"][name].update(**metadata)

        # remove tmp data
        for k, v in fn_in.items():
            if (
                ("input_data" in kwargs["aero"] and k in kwargs["aero"]["input_data"])
                and isinstance(v, str)
                and Path(v).exists()
            ):
                Path(v).unlink(missing_ok=True)

        # The url branch gives each input its own dir so the file can keep the
        # object's real name; take those with it.
        for d in tmp_dirs:
            try:
                d.rmdir()
            except OSError:  # not empty / already gone
                pass

        return kwargs

    return wrapper
