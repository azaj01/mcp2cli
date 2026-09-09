"""The session daemon must spawn where /dev/null cannot be opened.

`--session-start` used to hand `subprocess.DEVNULL` to Popen for the daemon's
stdout and stdin. Popen resolves DEVNULL by opening the device in the *parent*
with `os.open` (not `builtins.open`), so sandboxes that deny /dev/null failed
the spawn with PermissionError before the daemon existed:

    PermissionError: [Errno 13] Permission denied: '/dev/null'

These tests deny exactly that `os.open` and then start a real daemon against
the local stdio MCP test server, so they fail on the old spawn and pass on the
current one.
"""

import json
import os
from pathlib import Path

import pytest

import mcp2cli

_PROC_FD = Path("/proc/self/fd")
needs_proc = pytest.mark.skipif(
    not _PROC_FD.exists(), reason="descriptor introspection needs /proc"
)


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


def _fd_targets(pid: int | str = "self") -> dict[str, str]:
    targets = {}
    for entry in Path(f"/proc/{pid}/fd").iterdir():
        try:
            targets[entry.name] = os.readlink(entry)
        except OSError:  # descriptor closed while we walked the directory
            continue
    return targets


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

    # Two consecutive calls: an EOF stdin must not knock the daemon over
    # between requests.
    result = mcp2cli._session_request(
        "sandboxed",
        "call_tool",
        {"name": "echo", "arguments": {"message": "via session"}},
    )
    assert "via session" in json.dumps(result)
    result = mcp2cli._session_request(
        "sandboxed",
        "call_tool",
        {"name": "add_numbers", "arguments": {"a": 2, "b": 3}},
    )
    assert "5" in json.dumps(result)


@needs_proc
def test_daemon_streams_land_in_the_session_log(
    session_home, started, mcp_test_server_cmd, capsys
):
    """The live daemon's own descriptors: log for output, EOF pipe for input."""
    started.append("logged")

    mcp2cli.session_start("logged", mcp_test_server_cmd, True, [], {})
    capsys.readouterr()

    log_path = mcp2cli._session_log_path("logged")
    assert log_path.exists()

    pid = json.loads(mcp2cli._session_meta_path("logged").read_text())["pid"]
    fds = _fd_targets(pid)
    assert fds["1"] == str(log_path)
    assert fds["2"] == str(log_path)
    assert fds["0"].startswith("pipe:")

    # The parent kept neither the log handle nor the stdin pipe.
    assert str(log_path) not in _fd_targets().values()


@needs_proc
def test_failed_spawn_leaves_no_log_descriptor_behind(session_home, capsys):
    """The daemon dies immediately; the parent must not leak its log handle."""
    # No such binary: the daemon raises on spawn and exits at once.
    dead_server = "mcp2cli-nonexistent-test-server"

    with pytest.raises(SystemExit) as exc:
        mcp2cli.session_start("broken", dead_server, True, [], {})
    assert exc.value.code == 1
    capsys.readouterr()

    log_path = mcp2cli._session_log_path("broken")
    assert log_path.exists()
    assert str(log_path) not in _fd_targets().values()
