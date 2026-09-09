"""The session daemon must spawn where /dev/null cannot be opened.

`--session-start` used to hand `subprocess.DEVNULL` to Popen for the daemon's
stdout and stdin. Popen resolves DEVNULL by opening the device in the *parent*
with `os.open` (not `builtins.open`), so sandboxes that deny /dev/null failed
the spawn with PermissionError before the daemon existed:

    PermissionError: [Errno 13] Permission denied: '/dev/null'

Both tests deny exactly that `os.open` and start a real daemon, so they fail
on the old spawn and pass on the current one.
"""

import os
from pathlib import Path

import pytest

import mcp2cli

_PROC_FD = Path("/proc/self/fd")


@pytest.fixture
def session_home(tmp_path, monkeypatch):
    """Point this process *and* the spawned daemon at a throwaway cache."""
    cache = tmp_path / "session-cache"
    sessions = cache / "sessions"
    # The daemon is a fresh interpreter: only the environment reaches it.
    monkeypatch.setenv("MCP2CLI_CACHE_DIR", str(cache))
    monkeypatch.setattr(mcp2cli, "CACHE_DIR", cache)
    monkeypatch.setattr(mcp2cli, "SESSIONS_DIR", sessions)
    return sessions


@pytest.fixture
def started(session_home):
    """Names to tear down; set up after `session_home`, so torn down first."""
    names: list[str] = []
    yield names
    for name in names:
        mcp2cli.session_stop(name)


def _deny_dev_null(monkeypatch):
    """Refuse every /dev/null open, the way a hardened sandbox does."""
    real_open = os.open

    def guarded(path, *args, **kwargs):
        if str(path) == os.devnull:
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded)


def _text_content(result: dict) -> list[str]:
    return [
        item["text"] for item in result["content"] if item.get("type") == "text"
    ]


def _parent_holds(log_path: Path) -> bool:
    """Whether this process still has the session log open."""
    if not _PROC_FD.exists():
        pytest.skip("descriptor introspection needs /proc")
    for entry in _PROC_FD.iterdir():
        try:
            if os.readlink(entry) == str(log_path):
                return True
        except OSError:  # descriptor closed while we walked the directory
            continue
    return False


def test_session_serves_calls_where_dev_null_is_denied(
    session_home, started, monkeypatch, mcp_test_server_cmd, capsys
):
    """A sandbox that denies /dev/null must not break `--session-start`."""
    _deny_dev_null(monkeypatch)
    started.append("sandboxed")

    mcp2cli.session_start("sandboxed", mcp_test_server_cmd, True, [], {})
    assert "sandboxed" in capsys.readouterr().out
    assert mcp2cli._session_sock_path("sandboxed").exists()

    names = [
        tool["name"] for tool in mcp2cli._session_request("sandboxed", "list_tools", {})
    ]
    assert "echo" in names

    echoed = mcp2cli._session_request(
        "sandboxed",
        "call_tool",
        {"name": "echo", "arguments": {"message": "via session"}},
    )
    assert _text_content(echoed) == ["via session"]
    assert echoed["isError"] is False

    # A second call: the daemon's EOF stdin must not knock it over between
    # requests.
    summed = mcp2cli._session_request(
        "sandboxed",
        "call_tool",
        {"name": "add_numbers", "arguments": {"a": 2, "b": 3}},
    )
    assert _text_content(summed) == ["5"]
    assert summed["isError"] is False

    # The spawn handed its log descriptor to the daemon and kept none.
    assert not _parent_holds(mcp2cli._session_log_path("sandboxed"))


def test_failed_daemon_reports_through_the_session_log(
    session_home, monkeypatch, capsys
):
    """A daemon that dies must leave its diagnostics in the log, not nowhere."""
    _deny_dev_null(monkeypatch)
    # No such binary: the daemon raises on spawn and exits with a traceback.
    dead_server = "mcp2cli-nonexistent-test-server"

    with pytest.raises(SystemExit) as exc:
        mcp2cli.session_start("broken", dead_server, True, [], {})
    assert exc.value.code == 1
    assert "session daemon" in capsys.readouterr().err

    log_path = mcp2cli._session_log_path("broken")
    # The daemon's uncaught traceback reached the log instead of /dev/null.
    assert "Traceback" in log_path.read_text()
    assert not _parent_holds(log_path)
    assert not mcp2cli._session_sock_path("broken").exists()
