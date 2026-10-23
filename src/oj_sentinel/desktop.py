"""OJ Sentinel desktop shell.

Starts the local FastAPI server on a free loopback port in a daemon thread,
waits until ``/api/health`` answers, then opens a native :mod:`pywebview`
window pointing at it.  ``--no-window`` runs the exact same server headless;
that mode is used by the packaging smoke tests and by CI.

Source checkout::

    PYTHONPATH=src python -m oj_sentinel.desktop
    PYTHONPATH=src python -m oj_sentinel.desktop --no-window --port 0

Frozen builds (PyInstaller) use the same entry point, see ``packaging/``.

Data directory (``OJS_DATA_DIR`` always wins):

======================  ===========================================
macOS                   ``~/Library/Application Support/OJ Sentinel``
Windows                 ``%APPDATA%/OJ Sentinel``
Linux                   ``$XDG_DATA_HOME/oj-sentinel`` or
                        ``~/.local/share/oj-sentinel``
======================  ===========================================
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import base64
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "OJ Sentinel"
APP_SLUG = "oj-sentinel"
DEFAULT_HOST = "127.0.0.1"
HEALTH_PATH = "/api/health"
_FALLBACK_VERSION = "0.1.3"

LOG = logging.getLogger("oj_sentinel.desktop")

#: Bypass HTTP(S)_PROXY for loopback health probes.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _emit(message: str) -> None:
    """print() that is safe in PyInstaller windowed mode (sys.stdout is None)."""
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except Exception:  # pragma: no cover - closed pipe on shutdown
        pass


def _emit_error(message: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except Exception:  # pragma: no cover - closed pipe on shutdown
        pass


class ServerUnavailableError(RuntimeError):
    """Raised when the web server package cannot be imported or started."""


# ---------------------------------------------------------------------------
# version / data directory / port helpers
# ---------------------------------------------------------------------------


def package_version() -> str:
    """Best-effort version for ``--version`` and the readiness line."""
    try:
        from . import __version__ as version  # type: ignore[attr-defined]

        if version:
            return str(version)
    except Exception:  # pragma: no cover - optional metadata
        pass
    try:
        from importlib.metadata import version as _dist_version

        return _dist_version("oj-sentinel")
    except Exception:  # pragma: no cover - not installed (source checkout)
        return _FALLBACK_VERSION


def default_data_dir() -> Path:
    """Per-OS application data directory, honouring ``OJS_DATA_DIR``."""
    override = os.environ.get("OJS_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root / APP_SLUG


def resolve_data_dir(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve ``--data-dir`` / ``OJS_DATA_DIR`` / per-OS default, creating it."""
    path = Path(value).expanduser() if value else default_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def allocate_free_port(host: str = DEFAULT_HOST) -> int:
    """Bind ``host:0`` once to obtain a free TCP port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# server lifecycle
# ---------------------------------------------------------------------------


def render_index_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


def health_url(base_url: str) -> str:
    return base_url.rstrip("/") + HEALTH_PATH


def wait_for_health(
    base_url: str,
    timeout: float = 45.0,
    interval: float = 0.1,
    thread: threading.Thread | None = None,
) -> bool:
    """Poll ``GET /api/health`` until it returns HTTP 200.

    Raises :class:`RuntimeError` when the deadline expires or ``thread`` —
    the thread running uvicorn — died before the server became healthy.
    """
    deadline = time.monotonic() + timeout
    last_error = "no response"
    url = health_url(base_url)
    while time.monotonic() < deadline:
        if thread is not None and not thread.is_alive():
            raise RuntimeError(
                "OJ Sentinel server thread exited before becoming healthy; "
                "see the log above for the underlying error"
            )
        try:
            with _OPENER.open(url, timeout=2.0) as response:
                if 200 <= response.status < 300:
                    return True
                last_error = f"HTTP {response.status}"
        except Exception as exc:  # URLError, ConnectionError, timeout, ...
            last_error = str(exc) or exc.__class__.__name__
        time.sleep(interval)
    raise RuntimeError(f"OJ Sentinel did not become healthy at {url} within {timeout:.0f}s ({last_error})")


def load_app(data_dir: Path) -> Any:
    """Import the documented ``oj_sentinel.server.app:create_app`` factory."""
    try:
        from .server.app import create_app
    except Exception as exc:  # ModuleNotFoundError or a broken import
        raise ServerUnavailableError(
            "the OJ Sentinel web server (oj_sentinel.server.app.create_app) is not "
            f"available: {exc}. Run from the repository with PYTHONPATH=src, or "
            "reinstall the application."
        ) from exc
    return create_app(data_dir=Path(data_dir))


@dataclass
class ServerHandle:
    """A uvicorn server running in a daemon thread."""

    host: str
    port: int
    data_dir: Path
    server: Any = field(repr=False)
    thread: threading.Thread = field(repr=False)
    _stopped: bool = field(default=False, repr=False, init=False)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def app_url(self) -> str:
        return render_index_url(self.url)

    @property
    def health_url(self) -> str:
        return health_url(self.url)

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()

    def stop(self, timeout: float = 10.0) -> None:
        """Ask uvicorn to exit, then force it if the graceful stop hangs."""
        if self._stopped:
            return
        self._stopped = True
        LOG.info("stopping OJ Sentinel server at %s", self.url)
        try:
            self.server.should_exit = True
        except Exception:  # pragma: no cover - defensive
            pass
        self.thread.join(timeout)
        if self.thread.is_alive():
            LOG.warning("server did not stop within %.0fs, forcing exit", timeout)
            try:
                self.server.force_exit = True
            except Exception:  # pragma: no cover - defensive
                pass
            self.thread.join(2.0)


def start_server(
    data_dir: str | os.PathLike[str] | None = None,
    host: str = DEFAULT_HOST,
    port: int = 0,
    log_level: str = "warning",
    ready_timeout: float = 45.0,
    app: Any | None = None,
) -> ServerHandle:
    """Start the FastAPI app in a daemon thread and wait for ``/api/health``.

    ``port=0`` (the default) selects a free loopback port.  Returns a
    :class:`ServerHandle` whose :meth:`ServerHandle.stop` shuts the server down.
    """
    import uvicorn  # local import: ``--help`` must work without server deps

    resolved = resolve_data_dir(data_dir)
    if app is None:
        app = load_app(resolved)

    if not port:
        port = allocate_free_port(host)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        access_log=False,
        lifespan="auto",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="ojs-uvicorn", daemon=True)
    thread.start()

    handle = ServerHandle(host=host, port=port, data_dir=resolved, server=server, thread=thread)
    LOG.info("OJ Sentinel server starting at %s (data dir %s)", handle.url, resolved)
    try:
        wait_for_health(handle.url, timeout=ready_timeout, thread=thread)
    except BaseException:
        handle.stop()
        raise
    LOG.info("OJ Sentinel server ready at %s", handle.url)
    return handle


# ---------------------------------------------------------------------------
# logging / window / CLI
# ---------------------------------------------------------------------------


def setup_logging(data_dir: Path, level: str = "info") -> None:
    """Log to ``<data_dir>/desktop.log`` (and stderr when one exists)."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if root.handlers:  # already configured by an embedding process
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        file_handler = logging.FileHandler(Path(data_dir) / "desktop.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:  # read-only data dir: stderr-only logging is still useful
        pass
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)


class DesktopApi:
    """Small JS bridge used by the Web GUI for native save/folder dialogs."""

    def choose_directory(self) -> str:
        """Native folder picker for the default report export location."""
        try:
            import webview
        except ImportError:
            return ""
        if not webview.windows:
            return ""
        dialog = getattr(webview, "FOLDER_DIALOG", None)
        if dialog is None:
            try:
                dialog = webview.FileDialog.FOLDER
            except Exception:  # noqa: BLE001
                return ""
        try:
            selected = webview.windows[0].create_file_dialog(
                dialog,
                directory=str(Path.home()),
                allow_multiple=False,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("folder dialog failed: %s", exc)
            return ""
        if not selected:
            return ""
        return str(selected[0] if not isinstance(selected, str) else selected)

    def save_bytes(self, filename: str, data_base64: str) -> str:
        """Ask the native window for a save path and write the ZIP there."""
        try:
            import webview
        except ImportError:
            return ""
        if not webview.windows:
            return ""
        safe_name = Path(str(filename or "OJ-Sentinel-report.zip")).name
        if not safe_name.lower().endswith(".zip"):
            safe_name += ".zip"
        dialog = getattr(webview, "SAVE_DIALOG", None)
        if dialog is None:
            return ""
        try:
            chosen = webview.windows[0].create_file_dialog(
                dialog,
                save_filename=safe_name,
                file_types=("ZIP archive (*.zip)",),
            )
        except Exception as exc:  # noqa: BLE001 - native dialog failure
            LOG.warning("save dialog failed: %s", exc)
            return ""
        if not chosen:
            return ""
        path = Path(chosen if isinstance(chosen, str) else chosen[0])
        try:
            path.write_bytes(base64.b64decode(data_base64))
        except (OSError, ValueError) as exc:
            LOG.warning("could not write exported report: %s", exc)
            return ""
        return str(path)


def open_window(url: str, title: str = APP_NAME, debug: bool = False) -> Any:
    """Open the pywebview window (blocking until the last window closes)."""
    try:
        import webview
    except ImportError as exc:
        raise ServerUnavailableError(
            "pywebview is not installed, so the native window cannot be opened. "
            "Install the desktop extra (`pip install -e '.[desktop]'`) or run "
            "`ojs desktop --no-window` and point a browser at the printed URL."
        ) from exc

    window = webview.create_window(
        title,
        url,
        width=1280,
        height=860,
        min_size=(980, 640),
        text_select=True,
        js_api=DesktopApi(),
    )
    LOG.info("opening desktop window for %s", url)
    webview.start(debug=debug)
    return window


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ojs desktop",
        description=(
            "OJ Sentinel desktop shell: run the local Web GUI and open a native window. "
            "Use --no-window for a headless server (smoke tests / remote access)."
        ),
        epilog=(
            "Data directory defaults: macOS ~/Library/Application Support/OJ Sentinel, "
            "Windows %APPDATA%/OJ Sentinel, Linux ~/.local/share/oj-sentinel. "
            "The OJS_DATA_DIR environment variable overrides all of them."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host (default: 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="TCP port; 0 (default) picks a free loopback port",
    )
    parser.add_argument("--data-dir", help="override the per-OS application data directory")
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="run the server headless (prints a JSON readiness line) instead of opening pywebview",
    )
    parser.add_argument("--debug", action="store_true", help="enable pywebview debug/devtools")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="log level (default: info)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    return parser


def run_headless(args: argparse.Namespace) -> int:
    """Run the server until SIGINT/SIGTERM; used by smoke tests and CI."""
    # Install the signal handlers *before* the server becomes reachable: a
    # supervisor can hit /api/health and send SIGTERM while start_server() is
    # still returning, and the default SIGTERM handler would kill the process
    # instead of triggering the graceful shutdown path.
    stop = threading.Event()
    previous: dict[int, Any] = {}

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ANN001 - signal API
        LOG.info("received signal %s, shutting down", signum)
        stop.set()

    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if signum is None:
            continue
        try:
            previous[signum] = signal.signal(signum, _on_signal)
        except (ValueError, OSError):  # not the main thread / unsupported signal
            pass

    data_dir = resolve_data_dir(args.data_dir)
    setup_logging(data_dir, args.log_level)
    handle = start_server(
        data_dir=data_dir,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )

    ready = {
        "event": "ready",
        "url": handle.app_url,
        "health": handle.health_url,
        "port": handle.port,
        "host": handle.host,
        "data_dir": str(handle.data_dir),
        "version": package_version(),
        "pid": os.getpid(),
    }
    _emit(json.dumps(ready, ensure_ascii=False))
    _emit(f"OJ Sentinel listening on {handle.app_url}")

    try:
        while not stop.wait(0.25):
            if not handle.alive:
                LOG.error("server thread exited unexpectedly")
                return 1
    finally:
        handle.stop()
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):  # pragma: no cover - defensive
                pass
    return 0


def run_desktop(args: argparse.Namespace) -> int:
    """Start the server, then open the native window until it is closed."""
    data_dir = resolve_data_dir(args.data_dir)
    setup_logging(data_dir, args.log_level)
    handle = start_server(
        data_dir=data_dir,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    _emit(f"OJ Sentinel listening on {handle.app_url}")
    try:
        open_window(handle.app_url, APP_NAME, debug=args.debug)
    finally:
        handle.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.no_window:
            return run_headless(args)
        return run_desktop(args)
    except KeyboardInterrupt:
        return 130
    except ServerUnavailableError as exc:
        _emit_error(f"oj-sentinel desktop: {exc}")
        return 2
    except RuntimeError as exc:
        _emit_error(f"oj-sentinel desktop: {exc}")
        return 2
    except OSError as exc:
        _emit_error(f"oj-sentinel desktop: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
