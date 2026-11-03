"""`aero failover` — managing the endpoints a flow falls back to.

The command exists so that an analysis whose Globus Compute endpoint is down
runs somewhere else instead of submitting into the void. The part worth testing
is what the CLI makes of the server's answer: a status of "unknown" means the
check cannot answer, which looks like a working configuration and silently never
fires.
"""

import importlib
import json
import sys

import pytest


FLOW_ID = "f9aaedb0-2f23-42e3-bb69-0bc4dce4d7b2"
PRIMARY = "7591d0fb-3d12-4ff8-b8fb-7a51146c4871"
BACKUP = "0ed1ddbc-ae82-428c-8431-989a295eaa6a"
ROW_ID = "3aa0677e-f729-45ba-895c-adc9937fb276"


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


def _row(priority=0, enabled=True, **extra):
    return {
        "id": ROW_ID,
        "flow_id": FLOW_ID,
        "priority": priority,
        "endpoint": BACKUP,
        "function_id": None,
        "pull_function_id": None,
        "commit_function_id": None,
        "enabled": enabled,
        **extra,
    }


# --------------------------------------------------------------------------
# the api helpers
# --------------------------------------------------------------------------


def test_add_failover_sends_only_what_was_given(api, monkeypatch):
    """Unset function uuids must stay absent so the server inherits the flow's."""
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(
            url=url, body=json.loads(data)
        )
        or _Resp(_row()),
    )

    api.add_failover(FLOW_ID, BACKUP, priority=2)

    assert seen["url"].endswith(f"/flow/{FLOW_ID}/failover")
    assert seen["body"] == {"endpoint": BACKUP, "priority": 2, "enabled": True}


def test_add_failover_passes_explicit_functions(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(body=json.loads(data))
        or _Resp(_row()),
    )

    api.add_failover(
        FLOW_ID,
        BACKUP,
        function_uuid="fn",
        pull_function_uuid="pull",
        commit_function_uuid="commit",
        enabled=False,
    )

    assert seen["body"]["function_uuid"] == "fn"
    assert seen["body"]["pull_function_uuid"] == "pull"
    assert seen["body"]["commit_function_uuid"] == "commit"
    assert seen["body"]["enabled"] is False


def test_list_delete_and_status_hit_the_right_routes(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "get",
        lambda url, headers=None, **kw: seen.setdefault("get", []).append(url)
        or _Resp([]),
    )
    monkeypatch.setattr(
        api.requests,
        "delete",
        lambda url, headers=None, **kw: seen.update(delete=url) or _Resp(_row()),
    )

    api.list_failover(FLOW_ID)
    api.endpoint_status(FLOW_ID)
    api.delete_failover(FLOW_ID, ROW_ID)

    assert seen["get"][0].endswith(f"/flow/{FLOW_ID}/failover")
    assert seen["get"][1].endswith(f"/flow/{FLOW_ID}/endpoint-status")
    assert seen["delete"].endswith(f"/flow/{FLOW_ID}/failover/{ROW_ID}")


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.list_failover(FLOW_ID),
        lambda api: api.add_failover(FLOW_ID, BACKUP),
        lambda api: api.delete_failover(FLOW_ID, ROW_ID),
        lambda api: api.endpoint_status(FLOW_ID),
    ],
)
def test_errors_raise_rather_than_returning_a_body(api, monkeypatch, call):
    """A duplicate priority comes back 409; it must not read as success."""
    for verb in ("get", "post", "delete"):
        monkeypatch.setattr(api.requests, verb, lambda *a, **k: _Resp(None, 409))

    with pytest.raises(api.ClientError):
        call(api)


def test_register_flow_omits_failover_when_there_is_none(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(body=json.loads(data))
        or _Resp({"id": FLOW_ID}),
    )

    api.register_flow(PRIMARY, "fn", input_data={"a": {"id": "x"}}, policy=None)

    assert "failover" not in seen["body"]


def test_register_flow_passes_failover_through(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        api.requests,
        "post",
        lambda url, headers=None, data=None, **kw: seen.update(body=json.loads(data))
        or _Resp({"id": FLOW_ID}),
    )

    api.register_flow(
        PRIMARY,
        "fn",
        input_data={"a": {"id": "x"}},
        policy=None,
        failover=[{"endpoint": BACKUP, "priority": 1}],
    )

    assert seen["body"]["failover"] == [{"endpoint": BACKUP, "priority": 1}]


# --------------------------------------------------------------------------
# the cli
# --------------------------------------------------------------------------


@pytest.fixture(name="cli")
def cli_fixture(api, monkeypatch):
    """Run the CLI with one api helper stubbed, recording the call."""
    from aero_client import cli as cli_module

    def run(argv, name, result):
        calls = []
        monkeypatch.setattr(
            api,
            name,
            lambda *a, **kw: calls.append((a, kw)) or result,
        )
        monkeypatch.setattr(sys, "argv", ["aero", *argv])
        cli_module.main()
        return calls

    return run


def test_add_passes_the_flags_through(cli, capsys):
    calls = cli(
        ["failover", "add", FLOW_ID, "-e", BACKUP, "-p", "3", "--disabled"],
        "add_failover",
        _row(priority=3, enabled=False),
    )

    assert calls[0][0] == (FLOW_ID, BACKUP)
    assert calls[0][1]["priority"] == 3
    assert calls[0][1]["enabled"] is False
    assert "priority 3" in capsys.readouterr().out


def test_add_points_at_the_status_check(cli, capsys):
    """Configuring a failover that AERO cannot see is the quiet failure mode."""
    cli(["failover", "add", FLOW_ID, "-e", BACKUP], "add_failover", _row())

    assert "aero failover status" in capsys.readouterr().out


def test_list_says_so_when_there_are_none(cli, capsys):
    cli(["failover", "list", FLOW_ID], "list_failover", [])

    out = capsys.readouterr().out
    assert "never status-checked" in out


def test_list_shows_priority_order_and_overrides(cli, capsys):
    cli(
        ["failover", "list", FLOW_ID],
        "list_failover",
        [_row(priority=1, function_id="alt-fn"), _row(priority=2, enabled=False)],
    )

    out = capsys.readouterr().out
    assert "function: alt-fn" in out
    assert "[disabled]" in out


def test_status_warns_when_everything_is_unknown(cli, capsys):
    cli(
        ["failover", "status", FLOW_ID],
        "endpoint_status",
        [
            {"endpoint": PRIMARY, "role": "primary", "status": "unknown"},
            {"endpoint": BACKUP, "role": "failover", "status": "unknown"},
        ],
    )

    out = capsys.readouterr().out
    assert "failover will never fire" in out


def test_status_is_quiet_when_it_can_answer(cli, capsys):
    cli(
        ["failover", "status", FLOW_ID],
        "endpoint_status",
        [
            {"endpoint": PRIMARY, "role": "primary", "status": "offline"},
            {"endpoint": BACKUP, "role": "failover", "status": "online"},
        ],
    )

    out = capsys.readouterr().out
    assert "offline" in out and "online" in out
    assert "never fire" not in out


def test_rm_names_what_it_removed(cli, capsys):
    cli(["failover", "rm", FLOW_ID, ROW_ID], "delete_failover", _row())

    assert BACKUP in capsys.readouterr().out


def test_json_output(cli, capsys):
    cli(["failover", "list", FLOW_ID, "--json"], "list_failover", [_row()])

    assert json.loads(capsys.readouterr().out) == [_row()]


def test_bare_failover_prints_help(api, monkeypatch, capsys):
    from aero_client import cli as cli_module

    monkeypatch.setattr(sys, "argv", ["aero", "failover"])
    cli_module.main()

    assert "Failover actions" in capsys.readouterr().out
