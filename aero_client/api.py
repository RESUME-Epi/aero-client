"""AERO client API module"""

import json
import logging
import requests

from pathlib import Path
from typing import Generator
from typing import Literal
from typing import Callable, TypeAlias

from globus_compute_sdk import Client

from aero_client.error import ClientError
from aero_client.jobs import commit_analysis
from aero_client.jobs import download
from aero_client.jobs import database_commit
from aero_client.jobs import get_versions
from aero_client.jobs import stage
from aero_client.utils import _client_auth
from aero_client.utils import build_url
from aero_client.utils import register_function as register_aero_function
from aero_client.utils import CONF
from aero_client.utils import PolicyEnum

logger = logging.getLogger(__name__)

AUTH_ACCESS_TOKEN = _client_auth()
JSON: TypeAlias = dict[str, "JSON"] | list["JSON"] | str | int | float | bool | None

# tmp fix
session = requests.Session()


def register_function(func: Callable):
    """
    Register function to a Globus Compute Client.
    """
    gcc = Client()
    return gcc.register_function(func)


def list_versions(data_id: str) -> JSON:
    headers = {"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"}
    url = build_url("data", data_id, "versions")
    req = session.get(
        url=url,
        headers=headers,
        verify=False,
    )

    assert req.status_code == 200, str(req.content, encoding="utf-8")
    try:
        return req.json()
    except requests.exceptions.JSONDecodeError:
        return {
            "status_code": req.status_code,
            "message": str(req.content, encoding="utf-8"),
        }


def list_metadata(
    metadata_type: Literal["data", "prov", "flow"],
) -> Generator[JSON, JSON, JSON]:
    """Get the metadata records.

    Args:
        metadata_type (Literal["data", "prov", "flow"]): List metadata of a certain type.

    Returns:
        Generation[JSON]: a generator returning up to 15 metadata records at a time.
    """
    logger.debug("Retrieving all sources from server")
    headers = {"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"}

    url = build_url(metadata_type, trailing_slash=True)
    req = session.get(
        url=url,
        headers=headers,
        verify=False,
    )

    try:
        assert req.status_code == 200, str(req.content, encoding="utf-8")
        yield req.json()

        page = 1

        while req.status_code == 200:
            page += 1
            req = session.get(url, headers=headers, params={"page": page}, verify=False)
            yield req.json()
    except requests.exceptions.JSONDecodeError:
        return {
            "status_code": req.status_code,
            "message": str(req.content, encoding="utf-8"),
        }


def search_sources(query: str) -> list[dict[str, str | int]]:
    """Get the sources that match the query

    Args:
        query (str): a Globus Search query string

    Returns:
        list[dict[str, str | int]]: list of sources matching the query
    """

    logger.debug(f"Querying the sources with {query}")
    params = {"query": query}
    headers = {"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"}
    req = requests.get(
        build_url("data", "search"), params=params, headers=headers, verify=False
    )

    assert req.status_code == 200, str(req.content, encoding="utf-8")
    try:
        resp = req.json()
    except requests.exceptions.JSONDecodeError:
        resp = {
            "status_code": req.status_code,
            "message": str(req.content, encoding="utf-8"),
        }
    return resp


def register_flow(
    endpoint_uuid: str,
    function_uuid: str,
    input_data: dict[str | dict[str | int | None]] = {},
    output_data: dict[str | dict[str, str]] = {},
    kwargs: JSON = {},
    config: str | None = None,
    description: str | None = None,
    policy: PolicyEnum = PolicyEnum.NONE,
    timer_delay: int | None = None,
    pull_function_uuid: str | None = None,
    commit_function_uuid: str | None = None,
) -> None:
    """Register user function to run as a Globus Flow on remote server periodically.

    Args:
        endpoint_uuid (str): Globus Compute endpoint uuid
        function_uuid (str): Globus Compute registered function UUID
        input_data (dict[str | dict[str, uuid | int]],  optional): The input data,
            presented in the format {"name": {"id": <aero_id>, "version": <version no. or None>}}.
            Default is None.
        output_data (dict[str | dict[str, str]], optional): The output data that will be created,
            presented in the format {"name": {"url": <url to fetch the data>}}. Default is None.
            Omit it entirely for an analysis that stores its own results: the function then returns
            None, AERO records the run and the input versions it consumed, and no output version is
            tracked -- so no other flow can be registered downstream of it.
        kwargs (JSON, optional): Keyword arguments to pass to function. Default is None
        config (str, optional): Path to config file. Default is None.
        description (str | None, optional): A description of the Flow. Default is None.
        policy (PolicyEnum, optional): Which policy to use to rerun the flow. Default is never rerun.
        timer_delay (int | None, optional): The timer delay in seconds if PolicyEnum.TIMER is applied. Default is None.
        pull_function_uuid (str | none, optional): the uuid returned when registering either `aero_client.jobs.download`
            or `aero_client.jobs.get_versions` with Globus Compute. The function will register with GC if not provided,
            but issues may arise if local python version does not match endpoint python version. default is none.
        commit_function_uuid (str | none, optional): the uuid returned when registering either `aero_client.jobs.database_commit`
            or `aero_client.jobs.commit_analysis` with Globus Compute. The function will register with GC if not provided,
            but issues may arise if local python version does not match endpoint python version. default is none.

    Raises:
        ClientError: if function was not able to be registered as a flow, this error is raised

    Returns:
        str: the timer job uuid.
    """

    tasks = []
    if len(kwargs.keys()) == 0 and config is not None:
        with open(config) as f:
            tasks = json.load(f)

        if len(tasks) > 0:
            kwargs = tasks[0]

    # hack to ensure trailing slashes in URLs
    # TODO: fix
    for k, v in output_data.items():
        if v["collection_url"][-1] != "/":
            v["collection_url"] += "/"

    if policy is not None:
        if policy in (PolicyEnum.INGESTION, PolicyEnum.INGESTION_EVENT):
            if pull_function_uuid is None:
                pull_function_uuid = register_function(download)
            if commit_function_uuid is None:
                commit_function_uuid = register_function(database_commit)
        elif policy != PolicyEnum.NONE:
            if pull_function_uuid is None:
                pull_function_uuid = register_function(get_versions)
            if commit_function_uuid is None:
                commit_function_uuid = register_function(commit_analysis)

    data = {}
    data["input_data"] = input_data
    data["output_data"] = output_data
    data["description"] = description
    data["gc_endpoint"] = endpoint_uuid
    data["function_uuid"] = function_uuid
    data["pull_function_uuid"] = pull_function_uuid
    data["commit_function_uuid"] = commit_function_uuid
    data["flow_kwargs"] = kwargs
    data["rule"] = policy
    # Only send timer when set; event-driven/analysis flows have none, and the
    # server rejects an explicit null (its default applies only when omitted).
    if timer_delay is not None:
        data["timer"] = timer_delay

    if len(tasks) > 1:
        data["tasks"] = tasks

    headers = {
        "Authorization": f"Bearer {AUTH_ACCESS_TOKEN}",
        "Content-type": "application/json",
    }
    response = requests.post(
        build_url("flow", "register"),
        headers=headers,
        data=json.dumps(data),
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    raise ClientError(response.status_code, response.content)


def get_data_flows(data_id: str) -> list[dict]:
    """The flows attached to a Data: what produces it and what consumes it.

    Each entry is tagged with the role it plays -- "ingestion", "analysis", or
    "both".
    """
    response = requests.get(
        build_url("data", data_id, "flows"),
        headers={"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"},
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    raise ClientError(response.status_code, response.content)


def delete_data_flows(data_id: str) -> list[dict]:
    """Delete every flow attached to a Data, and their provenance records.

    The Data, its versions and any source type survive, so flows can be
    re-registered against the same UUID. Irreversible: it discards which input
    versions those runs consumed, and cancels any Globus timer driving them.
    """
    response = requests.delete(
        build_url("data", data_id, "flows"),
        headers={"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"},
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    raise ClientError(response.status_code, response.content)


def list_source_types() -> list[dict]:
    """Every notification type with its Data UUID and registered urls."""
    response = requests.get(
        build_url("data", "types"),
        headers={"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"},
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    raise ClientError(response.status_code, response.content)


def get_source_type(name: str) -> dict | None:
    """One type, or None if it doesn't exist yet."""
    response = requests.get(
        build_url("data", "types", name),
        headers={"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"},
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    if response.status_code == 404:
        return None
    raise ClientError(response.status_code, response.content)


def add_type_url(name: str, url: str) -> dict:
    """Associate another url with an existing type.

    The type keeps its Data UUID, so analysis flows already registered against it
    also fire when this url changes.
    """
    response = requests.post(
        build_url("data", "types", name, "urls"),
        headers={
            "Authorization": f"Bearer {AUTH_ACCESS_TOKEN}",
            "Content-type": "application/json",
        },
        data=json.dumps({"url": url}),
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    raise ClientError(response.status_code, response.content)


def delete_type_url(name: str, url: str) -> dict:
    """Remove one url from a type and return the type as it now stands.

    The type, its Data UUID and every flow registered against it stay in place.
    A type left with no urls cannot be reached by notify at all, so for a no-copy
    source nothing more is ingested until a url is added back.
    """
    response = requests.delete(
        build_url("data", "types", name, "urls"),
        headers={"Authorization": f"Bearer {AUTH_ACCESS_TOKEN}"},
        params={"url": url},
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    raise ClientError(response.status_code, response.content)


def create_data_source(
    name: str,
    url: str,
    type_name: str | None = None,
    no_copy: bool = False,
    collection_uuid: str | None = None,
    collection_url: str | None = None,
    description: str | None = None,
) -> dict:
    """Create a source Data record directly, with no flow attached."""
    body = {
        "name": name,
        "url": url,
        "type": type_name,
        "no_copy": no_copy,
        "collection_uuid": collection_uuid,
        "collection_url": collection_url,
        "description": description,
    }
    response = requests.post(
        build_url("data", "source"),
        headers={
            "Authorization": f"Bearer {AUTH_ACCESS_TOKEN}",
            "Content-type": "application/json",
        },
        data=json.dumps(body),
        verify=False,
    )
    if response.status_code == 200:
        return response.json()
    raise ClientError(response.status_code, response.content)


def create_source(
    name: str,
    url: str,
    collection_uuid: str | None = None,
    collection_url: str | None = None,
    endpoint_uuid: str | None = None,
    function_uuid: str | None = None,
    description: str | None = None,
    kwargs: JSON = {},
    type_name: str | None = None,
    no_copy: bool = False,
    policy: PolicyEnum = PolicyEnum.INGESTION_EVENT,
    timer_delay: int | None = None,
) -> dict:
    """Register a data source.

    By default the source is pulled when the server receives
    ``POST /data/notify`` for it. Pass ``policy=PolicyEnum.INGESTION`` with a
    ``timer_delay`` to pull it on a schedule instead.

    Three shapes, depending on ``type_name`` and ``no_copy``:

    * **Existing type** — the url is added to it and the *same* Data UUID comes
      back. No flow is registered, so analysis flows already pointed at that UUID
      now also run when this object changes.
    * **``no_copy``** — a Data record with no flow at all. Notify records a version
      straight from the event metadata; nothing is pulled and no bytes are stored,
      and the analysis fetches the object by url.
    * **Otherwise** — an ingestion flow that pulls the object and stages it into
      the given Globus guest collection for downstream analyses to read. Driven
      by the notify webhook, or by a timer when ``policy`` says so.

    Args:
        name (str): Name for the source (becomes the Data record). On the copy
            path with a ``function_uuid``, this is **also the keyword argument
            the pulled file arrives as**, and must match both that function's
            parameter name and the ``AeroOutput(name=...)`` it returns. Without a
            verifier, or for a no-copy source, it is free-form.
        url (str): HTTPS URL of the object.
        collection_uuid (str | None): Guest collection UUID to store the file in.
            Required on the copy path only.
        collection_url (str | None): Guest collection HTTPS domain. Copy path only.
        endpoint_uuid (str | None): Globus Compute endpoint to pull on. Copy path
            only — nothing executes for a no-copy source.
        function_uuid (str | None, optional): Ingestion verify/modify wrapper. If
            omitted a raw-passthrough ``stage`` function is registered so the file
            is stored unchanged. Copy path only.
        description (str | None, optional): Description of the source.
        kwargs (JSON, optional): Extra keyword arguments for the verifier
            function, beyond the pulled file it always receives. Copy path only.
        type_name (str | None, optional): Group this url under a named type.
        no_copy (bool, optional): Track changes without copying any data.
        policy (PolicyEnum, optional): ``INGESTION_EVENT`` (the default) pulls on
            the notify webhook; ``INGESTION`` pulls on a timer. Anything else is
            rejected — those are the only two ways a source gets produced.
        timer_delay (int | None, optional): Seconds between pulls, for
            ``INGESTION``. Left unset the server's default of 86400 applies.
            Meaningless for the other paths, which run no flow of their own.

    Raises:
        ValueError: for a policy that does not produce a source, or a timer on a
            path that has no timer to set.

    Returns:
        dict: The created source. ``id`` (or ``data_id`` when adding to an
        existing type) is the UUID to reference as ``input_data`` when
        registering analysis flows.
    """
    if policy not in (PolicyEnum.INGESTION, PolicyEnum.INGESTION_EVENT):
        raise ValueError(
            f"policy {PolicyEnum(policy).name} does not produce a source. Use "
            "INGESTION (timer-driven) or INGESTION_EVENT (notify-driven); "
            "register analysis flows with `aero register` instead."
        )

    if timer_delay is not None and policy is not PolicyEnum.INGESTION:
        raise ValueError(
            "timer_delay only applies to a timer-driven source. Set "
            "policy=INGESTION, or drop the timer to be driven by notify."
        )

    if no_copy and (timer_delay is not None or policy is PolicyEnum.INGESTION):
        raise ValueError(
            "a no-copy source registers no flow at all, so there is nothing to "
            "run on a timer. Drop no_copy to have AERO pull the object, or drop "
            "the timer and let notify record the change."
        )

    if no_copy and kwargs:
        raise ValueError(
            "a no-copy source runs no function, so there is nothing to pass "
            f"{sorted(kwargs)} to. Drop no_copy, or drop the kwargs."
        )

    if type_name is not None:
        existing = get_source_type(type_name)
        if existing is not None:
            # The type already owns a Data, and with it whatever flow was set up
            # when it was created; adding a url cannot change that.
            if timer_delay is not None or policy is PolicyEnum.INGESTION or kwargs:
                logger.warning(
                    "type '%s' already exists; its existing flow is unchanged and "
                    "the policy/timer/kwargs given here are not applied",
                    type_name,
                )
            return add_type_url(type_name, url)

    if no_copy:
        return create_data_source(
            name=name,
            url=url,
            type_name=type_name,
            no_copy=True,
            collection_uuid=collection_uuid,
            collection_url=collection_url,
            description=description,
        )

    if function_uuid is None:
        # Raw passthrough: register the stager aero_format-wrapped so its output
        # (the unchanged pulled file) is uploaded to the collection by gcs_save.
        function_uuid = register_aero_function(stage)

    output_data = {
        name: {
            "url": url,
            "collection_uuid": collection_uuid,
            "collection_url": collection_url,
        }
    }
    if type_name is not None:
        output_data[name]["type"] = type_name

    return register_flow(
        endpoint_uuid=endpoint_uuid,
        function_uuid=function_uuid,
        output_data=output_data,
        kwargs=kwargs,
        description=description,
        policy=policy,
        timer_delay=timer_delay,
    )


def source_id(result: dict) -> str | None:
    """Pull the source Data UUID out of whatever ``create_source`` returned.

    The three paths return different shapes: a FlowOut with contributed_to, a
    bare Data, or the type the url was added to.
    """
    if not isinstance(result, dict):
        return None
    try:
        return result["contributed_to"][0]["id"]
    except (KeyError, IndexError, TypeError):
        pass
    return result.get("data_id") or result.get("id")


def get_flow(flow_id: str, inputs_only: bool = True) -> dict:
    """Get metadata on the flow provided a flow ID.

    Args:
        flow_id (str): The flow UUID
        inputs_only (bool): Whether to return flow input data exclusively.
            Defaults to True.

    Returns:
        dict: Flow metadata in dictionary representation.
    """

    headers = {
        "Authorization": f"Bearer {AUTH_ACCESS_TOKEN}",
        "Content-type": "application/json",
    }

    response = requests.get(
        build_url("flow", flow_id),
        headers=headers,
        verify=False,
    )

    assert response.status_code == 200, str(response.content, encoding="utf-8")

    if inputs_only:
        return response.json()["function_args"]["kwargs"]
    else:
        return response.json()


# TODO: Fix bug where it'll request to login if tokens are not present
def globus_logout():
    """Remove the Globus Auth token file to invoke login on next API access."""
    logger.debug("Removing Globus auth tokens.")
    Path(CONF.aero_dir, CONF.token_file).unlink()
