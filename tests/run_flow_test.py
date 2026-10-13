"""`aero run` — the API helper and what the CLI makes of the server's answer.

The server answers 200 whether it submitted a run or declined to, so the whole
value of this command is in reading `submitted`/`reason` and saying which
happened. A test that only checks the request went out would miss that.
"""

import importlib
import json
import sys

import pytest


FLOW_ID = "f9aaedb0-2f23-42e3-bb69-0bc4dce4d7b2"
DATA_ID = "6f76e7b5-0f1d-4b4d-9769-e8b85dca5266"


@pytest.fixture(name="api")
def api_fixture(monkeypatch):
    """Import aero_client.api with auth stubbed; it mints a token at import."""
    from aero_client import utils

    monkeypatch.setattr(utils, "_client_auth", lambda: "test-token")
    sys.modules.pop("aero_client.api", None)
    module = importlib.import_module("aero_client.api")
    yield module
    sys.modules.pop("aero_client.api", None)


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"error"

    def json(self):
        return self._payload


def _submitted(flow_id=FLOW_ID):
    return {
        "submitted": True,
        "reason": None,
        "flow": {"id": flow_id, "last_executed": "2026-10-13T12:00:00Z"},
    }


def _skipped(reason="no input newer than last_executed=2026-10-13 12:00:00+00:00"):
    return {
        "submitted": False,
        "reason": reason,
        "flow": {"id": FLOW_ID, "last_executed": "2026-10-13T12:00:00Z"},
    }


# --------------------------------------------------------------------------
# the api helper
# --------------------------------------------------------------------------


def test_run_flow_posts_to_the_run_route(api, monkeypatch):
    seen = {}

    def _post(url, headers=None, data=None, **kw):
        seen["url"] = url
        seen["body"] = json.loads(data)
        return _Resp(_submitted())

    monkeypatch.setattr(api.requests, "post", _post)

    out = api.run_flow(FLOW_ID, force=True)

    assert seen["url"].endswith(f"/flow/{FLOW_ID}/run")
    assert seen["body"] == {"force": True}
    assert out["submitted"] is True


def test_the_per_run_url_fields_are_omitted_when_unset(api, monkeypatch):
    """An explicit null is not the same as absent for a presigned url.

    Sending `signed_url: null` would be harmless today, but the body should say
    only what the caller asked for.
    """
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(body=json.loads(data))
        or _Resp(_submitted()),
    )

    api.run_flow(FLOW_ID)

    assert seen["body"] == {"force": False}


def test_per_run_url_fields_are_passed_through(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(body=json.loads(data))
        or _Resp(_submitted()),
    )

    api.run_flow(
        FLOW_ID,
        trigger_url="http://minio/traffic/a.xml.gz",
        signed_url="http://minio/traffic/a.xml.gz?X-Amz-Signature=abc",
        source_data_id=DATA_ID,
    )

    assert seen["body"]["trigger_url"] == "http://minio/traffic/a.xml.gz"
    assert seen["body"]["source_data_id"] == DATA_ID
    assert "X-Amz-Signature" in seen["body"]["signed_url"]


def test_run_flow_raises_on_an_error_response(api, monkeypatch):
    """A policy the server won't run by hand comes back 400, not a quiet no-op."""
    monkeypatch.setattr(api.requests, "post", lambda *a, **k: _Resp(None, 400))

    with pytest.raises(api.ClientError):
        api.run_flow(FLOW_ID)


# --------------------------------------------------------------------------
# resolving which no-copy input a url belongs to
# --------------------------------------------------------------------------


OTHER_DATA_ID = "2b1f3c44-9a7e-4a1b-8a0f-7c5d2e9b4a31"
PLAIN_URL = "http://minio.internal:9000/traffic/a.xml.gz"
PRESIGNED = PLAIN_URL + "?X-Amz-Signature=abc&X-Amz-Expires=600"


def _stub_flow(api, monkeypatch, inputs, types=()):
    monkeypatch.setattr(
        api, "get_flow", lambda flow_id, inputs_only=True: {"derived_from": inputs}
    )
    monkeypatch.setattr(api, "list_source_types", lambda: list(types))


def test_a_presigned_url_resolves_to_its_registered_form(api, monkeypatch):
    """The stable url names the worker's temp file; signing params must not reach it."""
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}])

    data_id, trigger_url = api.resolve_no_copy_input(FLOW_ID, PRESIGNED)

    assert data_id == DATA_ID
    assert trigger_url == PLAIN_URL


def test_a_typed_source_matches_on_any_of_its_urls(api, monkeypatch):
    """Data.url holds only the first url of a type; the rest live on the type."""
    second = "http://minio.internal:9000/traffic/b.xml.gz"
    _stub_flow(
        api,
        monkeypatch,
        [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}],
        types=[
            {
                "name": "traffic",
                "data_id": DATA_ID,
                "urls": [
                    {"url": PLAIN_URL, "object_key": "traffic/a.xml.gz"},
                    {"url": second, "object_key": "traffic/b.xml.gz"},
                ],
            }
        ],
    )

    data_id, trigger_url = api.resolve_no_copy_input(
        FLOW_ID, second + "?X-Amz-Signature=xyz"
    )

    assert (data_id, trigger_url) == (DATA_ID, second)


def test_a_rewritten_host_still_matches(api, monkeypatch):
    """Matching is on bucket/key, so a tunnel or a container name resolves the same."""
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}])

    data_id, trigger_url = api.resolve_no_copy_input(
        FLOW_ID, "http://localhost:9000/traffic/a.xml.gz?X-Amz-Signature=abc"
    )

    assert (data_id, trigger_url) == (DATA_ID, PLAIN_URL)


def test_copy_inputs_are_not_candidates(api, monkeypatch):
    _stub_flow(
        api,
        monkeypatch,
        [{"id": DATA_ID, "no_copy": False, "url": PLAIN_URL}],
    )

    with pytest.raises(ValueError, match="no no-copy inputs"):
        api.resolve_no_copy_input(FLOW_ID, PRESIGNED)


def test_an_unmatched_url_names_the_candidates(api, monkeypatch):
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}])

    with pytest.raises(ValueError) as e:
        api.resolve_no_copy_input(FLOW_ID, "http://minio.internal:9000/other/z.gz")

    assert DATA_ID in str(e.value)
    assert "traffic/a.xml.gz" in str(e.value)
    assert "--source-data-id" in str(e.value)


def test_two_inputs_on_the_same_object_are_ambiguous(api, monkeypatch):
    """Two no-copy inputs can register the same object; the url cannot pick one."""
    _stub_flow(
        api,
        monkeypatch,
        [
            {"id": DATA_ID, "no_copy": True, "url": PLAIN_URL},
            {"id": OTHER_DATA_ID, "no_copy": True, "url": PLAIN_URL},
        ],
    )

    with pytest.raises(ValueError, match="matches 2 no-copy inputs"):
        api.resolve_no_copy_input(FLOW_ID, PRESIGNED)


def test_an_explicit_source_data_id_settles_it(api, monkeypatch):
    _stub_flow(
        api,
        monkeypatch,
        [
            {"id": DATA_ID, "no_copy": True, "url": PLAIN_URL},
            {"id": OTHER_DATA_ID, "no_copy": True, "url": PLAIN_URL},
        ],
    )

    data_id, trigger_url = api.resolve_no_copy_input(
        FLOW_ID, PRESIGNED, source_data_id=OTHER_DATA_ID
    )

    assert (data_id, trigger_url) == (OTHER_DATA_ID, PLAIN_URL)


def test_an_unlisted_object_under_an_explicit_input_is_taken_at_face_value(
    api, monkeypatch
):
    """Naming the input is the caller asserting the url belongs to it.

    A type registered by glob lists no concrete url to match, so this is the only
    way to reach one of its objects.
    """
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": None}])

    data_id, trigger_url = api.resolve_no_copy_input(
        FLOW_ID, PRESIGNED, source_data_id=DATA_ID
    )

    assert data_id == DATA_ID
    assert trigger_url == PLAIN_URL  # query stripped


def test_an_input_outside_the_flow_is_refused(api, monkeypatch):
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}])

    with pytest.raises(ValueError, match="is not a no-copy input"):
        api.resolve_no_copy_input(FLOW_ID, PRESIGNED, source_data_id=OTHER_DATA_ID)


# --------------------------------------------------------------------------
# the cli
# --------------------------------------------------------------------------


@pytest.fixture(name="cli")
def cli_fixture(api, monkeypatch):
    """Run the CLI against a stubbed api.run_flow, recording the call."""
    from aero_client import cli as cli_module

    calls = []

    def run(argv, result=None):
        monkeypatch.setattr(
            api,
            "run_flow",
            lambda flow_id, **kw: calls.append((flow_id, kw))
            or (result if result is not None else _submitted()),
        )
        monkeypatch.setattr(sys, "argv", ["aero", *argv])
        cli_module.main()
        return calls

    return run


def test_a_submitted_run_says_so(cli, capsys):
    calls = cli(["run", FLOW_ID, "--force"])

    assert calls == [
        (
            FLOW_ID,
            {
                "force": True,
                "trigger_url": None,
                "signed_url": None,
                "source_data_id": None,
            },
        )
    ]
    out = capsys.readouterr().out
    assert "Submitted a run" in out
    # submitted is not finished -- the run is with Globus from here
    assert "Globus" in out


def test_a_skipped_run_reports_the_reason_not_success(cli, capsys):
    """The server answers 200 either way, so a bare exit code says nothing."""
    cli(["run", FLOW_ID], result=_skipped())

    out = capsys.readouterr().out
    assert "Nothing was submitted" in out
    assert "no input newer than" in out
    assert "--force" in out


def test_force_is_not_suggested_when_it_would_not_help(cli, capsys):
    """force overrides staleness only, never a missing input version."""
    cli(
        ["run", FLOW_ID],
        result=_skipped(reason=f"inputs with no version yet: {DATA_ID}"),
    )

    out = capsys.readouterr().out
    assert "Nothing was submitted" in out
    assert "no version yet" in out
    assert "--force" not in out


def test_force_is_not_re_suggested_after_forcing(cli, capsys):
    cli(["run", FLOW_ID, "-f"], result=_skipped())

    assert "Re-run with --force" not in capsys.readouterr().out


def test_json_emits_the_server_answer_verbatim(cli, capsys):
    cli(["run", FLOW_ID, "--json"], result=_skipped())

    assert json.loads(capsys.readouterr().out) == _skipped()


def test_a_signed_url_without_its_input_is_refused(cli, capsys):
    """The server drops signed_url unless trigger_url and source_data_id are both set.

    Accepting it here would look like the private object was re-read when it was
    fetched unsigned, so the run fails 403 for no visible reason.
    """
    with pytest.raises(SystemExit):
        cli(["run", FLOW_ID, "--signed-url", "http://minio/a?sig=1"])

    assert "must be given together" in capsys.readouterr().err


def test_a_trigger_url_without_its_input_is_refused(cli, capsys):
    with pytest.raises(SystemExit):
        cli(["run", FLOW_ID, "--trigger-url", "http://minio/a"])

    assert "must be given together" in capsys.readouterr().err


def test_url_resolves_the_input_and_splits_stable_from_signed(cli, api, monkeypatch):
    """`--url` is the whole point: one flag, and the client works out the rest."""
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}])

    calls = cli(["run", FLOW_ID, "--force", "--url", PRESIGNED])

    _, kw = calls[0]
    assert kw["source_data_id"] == DATA_ID
    assert kw["trigger_url"] == PLAIN_URL  # stable: names the worker's temp file
    assert kw["signed_url"] == PRESIGNED  # transient: what it fetches with


def test_url_reports_which_input_it_read(cli, api, monkeypatch, capsys):
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}])

    cli(["run", FLOW_ID, "--force", "--url", PRESIGNED])

    out = capsys.readouterr().out
    assert f"input {DATA_ID} read from {PLAIN_URL}" in out


def test_an_unresolvable_url_exits_without_running(cli, api, monkeypatch, capsys):
    _stub_flow(api, monkeypatch, [{"id": DATA_ID, "no_copy": True, "url": PLAIN_URL}])

    with pytest.raises(SystemExit):
        cli(["run", FLOW_ID, "--url", "http://minio.internal:9000/other/z.gz"])

    err = capsys.readouterr().err
    assert "matches no registered url" in err
    assert "--source-data-id" in err


def test_url_and_the_manual_flags_are_mutually_exclusive(cli, capsys):
    """Both set signed_url, so accepting both would make one of them a no-op."""
    with pytest.raises(SystemExit):
        cli(["run", FLOW_ID, "--url", PRESIGNED, "--signed-url", PRESIGNED])

    assert "supplies both" in capsys.readouterr().err


def test_the_per_run_url_trio_is_accepted(cli):
    calls = cli(
        [
            "run",
            FLOW_ID,
            "--trigger-url",
            "http://minio/a",
            "--signed-url",
            "http://minio/a?sig=1",
            "--source-data-id",
            DATA_ID,
        ]
    )

    assert calls[0][1]["trigger_url"] == "http://minio/a"
    assert calls[0][1]["source_data_id"] == DATA_ID
