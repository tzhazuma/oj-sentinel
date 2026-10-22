"""Smoke tests for the desktop shell (no window is opened).

The full path is exercised once the web server package has landed:
allocate a free port, start uvicorn in its daemon thread, probe
``/api/health`` over real TCP, then verify a graceful shutdown.

Run with::

    PYTHONPATH=src .venv/bin/python -m pytest tests/test_desktop_smoke.py -q
"""
from __future__ import annotations

import importlib
import importlib.util
import socket
import urllib.request

import pytest

from oj_sentinel import desktop

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("webview") is None,
    reason="pywebview is not installed (desktop extra); install `.[desktop]` to run",
)


def _server_available() -> bool:
    try:
        importlib.import_module("oj_sentinel.server.app")
    except Exception:
        return False
    return True


def test_allocate_free_port_is_usable() -> None:
    port = desktop.allocate_free_port()
    assert 1024 < port < 65536
    # The helper releases the port, so a real server can bind it immediately.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_ojs_data_dir_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OJS_DATA_DIR", str(tmp_path / "custom-data"))
    resolved = desktop.resolve_data_dir()
    assert resolved == tmp_path / "custom-data"
    assert resolved.is_dir()
    assert desktop.default_data_dir() == tmp_path / "custom-data"


def test_explicit_data_dir_wins(tmp_path) -> None:
    explicit = tmp_path / "explicit"
    assert desktop.resolve_data_dir(explicit) == explicit
    assert explicit.is_dir()


def test_health_url_join() -> None:
    assert desktop.health_url("http://127.0.0.1:1234/") == "http://127.0.0.1:1234/api/health"
    assert desktop.render_index_url("http://127.0.0.1:1234/") == "http://127.0.0.1:1234/"


def test_cli_help_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        desktop.build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    assert "OJ Sentinel desktop shell" in capsys.readouterr().out


@pytest.mark.skipif(not _server_available(), reason="oj_sentinel.server.app not available yet")
def test_start_server_health_and_graceful_shutdown(tmp_path) -> None:
    port = desktop.allocate_free_port()
    handle = desktop.start_server(
        data_dir=tmp_path,
        port=port,
        log_level="error",
        ready_timeout=40.0,
    )
    try:
        assert handle.alive
        assert handle.port == port
        assert handle.app_url == f"http://127.0.0.1:{port}/"

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(handle.health_url, timeout=5.0) as response:
            assert response.status == 200
            payload = response.read()
        assert payload, "health endpoint returned an empty body"
    finally:
        handle.stop(timeout=10.0)

    assert not handle.thread.is_alive(), "server thread survived graceful shutdown"
    with pytest.raises(Exception):
        urllib.request.urlopen(handle.health_url, timeout=1.0)
    # stopping twice is a no-op
    handle.stop(timeout=1.0)
