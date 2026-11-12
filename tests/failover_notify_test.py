"""Handing a failed run to a failover endpoint, from inside the run.

`request_failover` is called by an analysis that has watched its HPC job fail
on an endpoint that is still healthy. What matters: it sends back exactly what
the run was given (so the chain skips every endpoint already tried and re-runs
the same input versions), and it always raises -- a run that returns normally
would go on to commit provenance for output it never produced.
"""

import importlib
import json
import sys

import pytest
import requests


FLOW_ID = "f9aaedb0-2f23-42e3-bb69-0bc4dce4d7b2"
PRIMARY = "7591d0fb-3d12-4ff8-b8fb-7a51146c4871"
BACKUP = "0ed1ddbc-ae82-428c-8431-989a295eaa6a"
OTHER = "3aa0677e-f729-45ba-895c-adc9937fb276"
DATA_ID = "6f76e7b5-0f1d-4b4d-9769-e8b85dca5266"
VERSION_ID = "4af0e417-0527-43c8-99e7-22cfd88ef951"


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"error"

    def json(self):
        return self._payload


def _aero_run(**overrides):
    run = {
        "flow_id": FLOW_ID,
        "endpoint": BACKUP,
        "tried_endpoints": [PRIMARY],
        "input_versions": {DATA_ID: VERSION_ID},
    }
    run.update(overrides)
    return run


# --------------------------------------------------------------------------
# request_failover, on the worker
# --------------------------------------------------------------------------


@pytest.fixture(name="post")
def post_fixture(monkeypatch):
    """Stub the token file and record the one POST; set `reply` to answer it."""
    from aero_client import utils

    monkeypatch.setattr(
        utils,
        "load_tokens",
        lambda: {utils.CONF.portal_client_id: {"refresh_token": "rt"}},
    )

    seen = {"reply": _Resp({"submitted": True, "endpoint": OTHER, "failover": True})}

    def fake(url, headers=None, data=None, **kw):
        seen.update(url=url, headers=headers, body=json.loads(data))
        reply = seen["reply"]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(requests, "post", fake)
    return seen


def test_sends_back_what_the_run_was_given(post):
    from aero_client.failover import FailoverRequested
    from aero_client.failover import request_failover

    with pytest.raises(FailoverRequested):
        request_failover(_aero_run(), reason="PBS job 1234 exited 271")

    assert post["url"].endswith(f"/flow/{FLOW_ID}/failover/run")
    assert post["body"] == {
        "failed_endpoint": BACKUP,
        "tried_endpoints": [PRIMARY],
        "reason": "PBS job 1234 exited 271",
        "input_versions": {DATA_ID: VERSION_ID},
    }
    assert post["headers"]["Authorization"] == "Bearer rt"


def test_raises_even_when_the_rerun_was_submitted(post):
    """The failed run must stop here, or its commit step records an empty run."""
    from aero_client.failover import FailoverRequested
    from aero_client.failover import request_failover

    with pytest.raises(FailoverRequested) as e:
        request_failover(_aero_run(), reason="qsub rejected the job")

    assert e.value.submitted is True
    assert e.value.endpoint == OTHER
    assert f"handed to {OTHER}" in str(e.value)


def test_raises_with_the_reason_when_nothing_is_left(post):
    from aero_client.failover import FailoverRequested
    from aero_client.failover import request_failover

    post["reply"] = _Resp(
        {"submitted": False, "reason": "no failover endpoint left to try; tried: x"}
    )

    with pytest.raises(FailoverRequested) as e:
        request_failover(_aero_run(), reason="job failed")

    assert e.value.submitted is False
    assert "no failover endpoint left" in str(e.value)


def test_raises_when_aero_refuses(post):
    from aero_client.failover import FailoverRequested
    from aero_client.failover import request_failover

    post["reply"] = _Resp(None, status_code=400)

    with pytest.raises(FailoverRequested) as e:
        request_failover(_aero_run(), reason="job failed")

    assert "400" in str(e.value)


def test_raises_when_aero_cannot_be_reached(post):
    from aero_client.failover import FailoverRequested
    from aero_client.failover import request_failover

    post["reply"] = requests.ConnectionError("no route to host")

    with pytest.raises(FailoverRequested) as e:
        request_failover(_aero_run(), reason="job failed")

    assert isinstance(e.value.__cause__, requests.ConnectionError)


def test_an_older_server_still_gets_a_request(post):
    """No `run` context: the route defaults the failed endpoint to the primary."""
    from aero_client.failover import FailoverRequested
    from aero_client.failover import request_failover

    with pytest.raises(FailoverRequested):
        request_failover(
            _aero_run(endpoint=None, tried_endpoints=[]), reason="job failed"
        )

    assert post["body"]["failed_endpoint"] is None
    assert post["body"]["tried_endpoints"] == []


def test_a_missing_flow_id_is_a_usage_error(post):
    from aero_client.failover import request_failover

    with pytest.raises(ValueError, match="flow_id"):
        request_failover({}, reason="job failed")

    assert "url" not in post


def test_the_worker_helper_does_not_import_the_api_module():
    """aero_client.api mints a token at import, which can stop at input()."""
    sys.modules.pop("aero_client.api", None)
    sys.modules.pop("aero_client.failover", None)

    importlib.import_module("aero_client.failover")

    assert "aero_client.api" not in sys.modules


# --------------------------------------------------------------------------
# aero_format hands the run context to a function that asks for it
# --------------------------------------------------------------------------


def _wrap_and_call(fn, aero):
    from aero_client.utils import aero_format

    return aero_format(fn)(aero=aero)


def _aero_block(**extra):
    block = {
        "input_data": {
            "track": {
                "id": DATA_ID,
                "version": 3,
                "version_id": VERSION_ID,
                "fetch": False,
                "trigger_url": "https://minio/tracks/a.csv",
            }
        },
        "output_data": {},
        "flow_id": FLOW_ID,
        "run": {"endpoint": BACKUP, "tried_endpoints": [PRIMARY]},
    }
    block.update(extra)
    return block


def test_aero_run_reaches_a_function_that_declares_it():
    seen = {}

    def run(track_url, aero_run):
        seen.update(aero_run)
        return None

    _wrap_and_call(run, _aero_block())

    assert seen == _aero_run()


def test_aero_run_is_opt_in():
    def run(track_url):  # would TypeError if passed unconditionally
        return None

    _wrap_and_call(run, _aero_block())


def test_aero_run_from_an_older_server():
    """No `run` block: no endpoint and nothing tried, but still a flow id."""
    seen = {}

    def run(track_url, aero_run):
        seen.update(aero_run)
        return None

    block = _aero_block()
    del block["run"]
    _wrap_and_call(run, block)

    assert seen["flow_id"] == FLOW_ID
    assert seen["endpoint"] is None
    assert seen["tried_endpoints"] == []


def test_the_aero_block_survives_for_the_commit():
    def run(track_url, aero_run):
        return None

    out = _wrap_and_call(run, _aero_block())

    assert out["aero"]["flow_id"] == FLOW_ID
    assert "aero_run" not in out


# --------------------------------------------------------------------------
# aero failover run, by hand
# --------------------------------------------------------------------------


@pytest.fixture(name="api")
def api_fixture(monkeypatch):
    """Import aero_client.api with auth stubbed; it mints a token at import."""
    from aero_client import utils

    monkeypatch.setattr(utils, "_client_auth", lambda: "test-token")
    sys.modules.pop("aero_client.api", None)
    module = importlib.import_module("aero_client.api")
    yield module
    sys.modules.pop("aero_client.api", None)


def test_run_failover_sends_the_exclusions(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(
            url=url, body=json.loads(data)
        )
        or _Resp({"submitted": True}),
    )

    api.run_failover(FLOW_ID, exclude_endpoints=[BACKUP], reason="maintenance")

    assert seen["url"].endswith(f"/flow/{FLOW_ID}/failover/run")
    assert seen["body"] == {"tried_endpoints": [BACKUP], "reason": "maintenance"}


def test_run_failover_sends_nothing_unasked(api, monkeypatch):
    """No failed_endpoint: the server takes it to be the primary."""
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(body=json.loads(data))
        or _Resp({"submitted": True}),
    )

    api.run_failover(FLOW_ID)

    assert seen["body"] == {"tried_endpoints": []}


def test_run_failover_raises_on_an_error(api, monkeypatch):
    monkeypatch.setattr(
        api.requests, "post", lambda *a, **kw: _Resp(None, status_code=404)
    )

    with pytest.raises(api.ClientError):
        api.run_failover(FLOW_ID)


@pytest.fixture(name="cli")
def cli_fixture(api, monkeypatch):
    from aero_client import cli as cli_module

    def run(argv, result):
        calls = []
        monkeypatch.setattr(
            api, "run_failover", lambda *a, **kw: calls.append((a, kw)) or result
        )
        monkeypatch.setattr(sys, "argv", ["aero", *argv])
        cli_module.main()
        return calls

    return run


def test_cli_names_where_the_run_went(cli, capsys):
    calls = cli(
        ["failover", "run", FLOW_ID, "-x", BACKUP, "--reason", "maintenance"],
        {
            "submitted": True,
            "endpoint": OTHER,
            "failover": True,
            "flow": {"last_executed": "2026-11-12T12:00:00Z"},
        },
    )

    assert calls[0] == ((FLOW_ID,), {"exclude_endpoints": [BACKUP], "reason": "maintenance"})
    assert f"endpoint: {OTHER} (failover)" in capsys.readouterr().out


def test_cli_says_why_nothing_ran(cli, capsys):
    cli(
        ["failover", "run", FLOW_ID],
        {"submitted": False, "reason": "no failover endpoint left to try; tried: x"},
    )

    out = capsys.readouterr().out
    assert "Nothing was submitted" in out
    assert "no failover endpoint left" in out
