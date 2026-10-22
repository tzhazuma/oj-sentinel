#!/usr/bin/env python3
"""Smoke-test a packaged OJ Sentinel desktop binary in ``--no-window`` mode.

Starts the executable on a free loopback port, waits for ``/api/health`` to
return HTTP 200, then asks it to shut down.  Used by the GitHub Actions desktop
matrix (and by hand after ``packaging/build_macos.sh``)::

    python packaging/smoke_desktop.py "dist/OJ Sentinel.app/Contents/MacOS/OJ Sentinel"
    python packaging/smoke_desktop.py "dist/OJ-Sentinel/OJ Sentinel"
    python packaging/smoke_desktop.py "dist/OJ Sentinel.exe"

Exit codes: 0 healthy, 1 failure (process died/timeout/HTTP error/shutdown).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(url: str, timeout: float, proc: subprocess.Popen[str]) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"process exited early with code {proc.returncode}")
        try:
            with OPENER.open(url, timeout=2.0) as response:
                body = response.read(4096).decode("utf-8", "replace")
                if response.status == 200:
                    return response.status, body
                last_error = f"HTTP {response.status}"
        except urllib.error.URLError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - report whatever the socket did
            last_error = f"{exc.__class__.__name__}: {exc}"
        time.sleep(0.25)
    raise SystemExit(f"timed out after {timeout:.0f}s waiting for {url} ({last_error})")


def stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # Kill the PyInstaller onefile parent *and* its extracted child.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", help="packaged OJ Sentinel executable")
    parser.add_argument("--timeout", type=float, default=90.0, help="health timeout seconds")
    parser.add_argument("--data-dir", help="optional OJS_DATA_DIR for the smoke run")
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="do not require GET / to serve the bundled Web GUI",
    )
    args = parser.parse_args(argv)

    exe = Path(args.executable).expanduser()
    if not exe.is_file():
        print(f"error: executable not found: {exe}", file=sys.stderr)
        return 1

    port = free_port()
    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="ojs-smoke-"))
    env = dict(os.environ)
    env["OJS_DATA_DIR"] = str(data_dir)
    log_path = data_dir / "smoke.log"
    print(f"==> {exe} --no-window --port {port} (data dir {data_dir})", flush=True)

    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [str(exe), "--no-window", "--port", str(port), "--data-dir", str(data_dir)],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    try:
        status, body = wait_for_health(f"http://127.0.0.1:{port}/api/health", args.timeout, proc)
        print(f"==> health HTTP {status}: {body[:300]}", flush=True)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            print(f"==> health payload keys: {', '.join(sorted(payload))}", flush=True)

        if not args.skip_index:
            # The packaged app must ship the Web GUI static assets, not only the API.
            with OPENER.open(f"http://127.0.0.1:{port}/", timeout=10) as response:
                index_body = response.read(2048).decode("utf-8", "replace")
                if response.status != 200 or "<html" not in index_body.lower():
                    raise SystemExit(
                        f"GET / did not serve the bundled Web GUI (HTTP {response.status})"
                    )
                print(f"==> Web GUI index HTTP {response.status} ({len(index_body)} bytes read)", flush=True)
        return 0
    except SystemExit as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        stop(proc)
        if proc.returncode not in (0, 130, None) and os.name != "nt":
            print(f"warning: process exited with {proc.returncode}", file=sys.stderr)
        if log_path.exists():
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            print("==> server log (tail):")
            for line in tail:
                print(f"    {line}")
        print(f"==> shutdown complete (exit code {proc.returncode})", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
