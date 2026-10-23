"""Locate and validate a DeepSeek Harness (``dsh``) executable.

The packaged desktop app is launched by Finder/Explorer, so its ``PATH`` is
often much smaller than the user's shell ``PATH``.  Relying on
``shutil.which("dsh")`` alone therefore fails on a normal macOS install where
``dsh`` is a symlink in ``/usr/local/bin``.  This module probes the usual
locations, validates each candidate with ``dsh --version``, and lets the GUI
offer both "auto discover" and "custom path" modes.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.\-]+)?")
PROBE_TIMEOUT = 12.0


@dataclass
class DshCandidate:
    path: str
    source: str
    exists: bool = False
    executable: bool = False
    ok: bool = False
    version: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DshDiscovery:
    found: bool
    selected: str = ""
    version: str = ""
    source: str = ""
    candidates: list[DshCandidate] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    error: str = ""
    mode: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "selected": self.selected,
            "version": self.version,
            "source": self.source,
            "mode": self.mode,
            "candidates": [c.to_dict() for c in self.candidates],
            "searched": self.searched,
            "error": self.error,
        }


def _app_candidates() -> list[tuple[str, str]]:
    """Bundled dsh locations for the DeepSeek Harness desktop app."""
    out: list[tuple[str, str]] = []
    if sys.platform == "darwin":
        bases = [
            Path("/Applications/DeepSeek Harness.app"),
            Path.home() / "Applications" / "DeepSeek Harness.app",
        ]
        rels = [
            "Contents/Resources/runtime/cli/bin/dsh",
            "Contents/Resources/runtime/bin/dsh",
            "Contents/MacOS/dsh",
        ]
        for base in bases:
            for rel in rels:
                out.append((str(base / rel), "deepseek-harness-app"))
    elif sys.platform.startswith("win"):
        roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DeepSeek Harness"]
        for root in roots:
            out.append((str(root / "dsh.exe"), "deepseek-harness-app"))
            out.append((str(root / "resources" / "runtime" / "cli" / "dsh.cmd"), "deepseek-harness-app"))
    return out


def _candidate_paths(custom_path: str | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if custom_path:
        out.append((str(Path(custom_path).expanduser()), "custom"))
    for env_name in ("OJS_DSH_COMMAND", "DSH_COMMAND", "DSH_BIN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            out.append((value, f"env:{env_name}"))
    for name in ("dsh", "dsh.exe", "dsh.cmd"):
        found = shutil.which(name)
        if found:
            out.append((found, "PATH"))
    home = Path.home()
    common = [
        "/usr/local/bin/dsh",
        "/opt/homebrew/bin/dsh",
        "/usr/bin/dsh",
        "/snap/bin/dsh",
        str(home / ".local" / "bin" / "dsh"),
        str(home / "bin" / "dsh"),
        str(home / ".npm-global" / "bin" / "dsh"),
        str(home / ".bun" / "bin" / "dsh"),
    ]
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "")
        localapp = os.environ.get("LOCALAPPDATA", "")
        common += [
            str(Path(appdata) / "npm" / "dsh.cmd"),
            str(Path(appdata) / "npm" / "dsh.exe"),
            str(Path(localapp) / "Programs" / "dsh" / "dsh.exe"),
        ]
    out.extend((p, "common") for p in common)
    out.extend(_app_candidates())
    # Preserve order but remove duplicates.
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for path, source in out:
        key = os.path.normcase(os.path.abspath(os.path.expanduser(path)))
        if key in seen:
            continue
        seen.add(key)
        unique.append((path, source))
    return unique


def probe_dsh(path: str, *, timeout: float = PROBE_TIMEOUT) -> DshCandidate:
    """Run ``dsh --version`` and classify the result."""
    expanded = os.path.expanduser(path)
    cand = DshCandidate(path=expanded, source="")
    p = Path(expanded)
    cand.exists = p.exists() or (shutil.which(expanded) is not None)
    cand.executable = os.access(expanded, os.X_OK) if p.exists() else bool(shutil.which(expanded))
    if not cand.exists:
        cand.error = "文件不存在"
        return cand
    if not cand.executable:
        cand.error = "文件不可执行"
        return cand
    # ``dsh`` is a launcher that may spawn the Electron app; if we capture
    # pipes, a grandchild can keep them open and make ``communicate()`` hang
    # even after the direct child exits.  Redirect to temp files and kill the
    # whole process group on timeout instead.
    out_f = tempfile.TemporaryFile()
    err_f = tempfile.TemporaryFile()
    try:
        try:
            proc = subprocess.Popen(
                [expanded, "--version"],
                stdout=out_f,
                stderr=err_f,
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            cand.error = f"无法执行：{type(exc).__name__}: {exc}"
            return cand
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            cand.error = f"--version 超时（>{timeout:.0f}s）"
            return cand
        out_f.seek(0)
        err_f.seek(0)
        stdout = out_f.read().decode("utf-8", "replace")
        stderr = err_f.read().decode("utf-8", "replace")
    finally:
        out_f.close()
        err_f.close()
    text = (stdout or "") + "\n" + (stderr or "")
    match = VERSION_RE.search(text)
    cand.version = match.group(0) if match else text.strip().splitlines()[0][:80] if text.strip() else ""
    if rc == 0 and (match or "dsh" in text.lower()):
        cand.ok = True
    else:
        cand.error = f"退出码 {rc}: {text.strip()[:160] or '无输出'}"
    return cand


def discover_dsh(custom_path: str | None = None, *, timeout: float = PROBE_TIMEOUT) -> DshDiscovery:
    """Probe env/PATH/common app locations and return the best usable dsh."""
    candidates: list[DshCandidate] = []
    searched: list[str] = []
    for path, source in _candidate_paths(custom_path):
        searched.append(path)
        cand = probe_dsh(path, timeout=timeout)
        cand.source = source
        candidates.append(cand)
        # A valid custom path wins immediately; auto discovery takes the first
        # working candidate (custom > env > PATH > common > bundled app).
        if cand.ok:
            return DshDiscovery(
                found=True,
                selected=cand.path,
                version=cand.version,
                source=cand.source,
                candidates=candidates,
                searched=searched,
                mode="custom" if custom_path else "auto",
            )
        if custom_path and source == "custom":
            # Do not silently fall back to another dsh when the user explicitly
            # chose a custom path: report the failure so the UI can show it.
            return DshDiscovery(
                found=False,
                candidates=candidates,
                searched=searched,
                error=f"自定义路径不可用：{cand.error}",
                mode="custom",
            )
    return DshDiscovery(
        found=False,
        candidates=candidates,
        searched=searched,
        error="未找到可用的 dsh",
        mode="auto",
    )


def resolve_dsh(command: str | None = None) -> str | None:
    """Resolve an AgentConfig ``dsh_command`` value to an executable path.

    ``None``/``""``/``"dsh"`` mean auto-discover.  An explicit absolute or
    relative path is validated first; if it fails we fall back to discovery.
    """
    value = (command or "").strip()
    if value and value not in {"dsh", "dsh.exe", "dsh.cmd"}:
        expanded = str(Path(value).expanduser())
        return expanded if probe_dsh(expanded).ok else None
    found = discover_dsh()
    return found.selected if found.found else None


__all__ = [
    "DshCandidate",
    "DshDiscovery",
    "discover_dsh",
    "probe_dsh",
    "resolve_dsh",
]
