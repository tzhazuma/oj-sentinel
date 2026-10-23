"""Best-effort discovery of existing local AI/judge runtimes.

Used by the settings screen: the packaged app has no bundled torch/transformers,
but the user may already have a suitable Python, a cached CodeBERT model, or a
dsh installation.  This module only inspects local paths; it never downloads or
uploads anything.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_PROBE = (
    "import json, sys\n"
    "out={'major': sys.version_info[0], 'version': sys.version.split()[0]}\n"
    "for name in ('torch','transformers','huggingface_hub'):\n"
    "    try:\n"
    "        mod=__import__(name); out[name]=getattr(mod,'__version__','?')\n"
    "    except Exception:\n"
    "        out[name]=None\n"
    "print(json.dumps(out))\n"
)


def _python_candidates() -> list[str]:
    candidates: list[str] = []
    for env_name in ("OJS_PYTHON", "PYTHON", "VIRTUAL_ENV"):
        value = os.environ.get(env_name, "").strip()
        if not value:
            continue
        candidates.append(value if env_name != "VIRTUAL_ENV" else str(Path(value) / "bin" / "python"))
    if not getattr(sys, "frozen", False):
        candidates.append(sys.executable)
    for name in ("python3", "python", "python3.12", "python3.11"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates += ["/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"]
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        candidates.append(str(Path(conda) / "bin" / "python"))
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates += [
                str(Path(local) / "Programs" / "Python" / "Python312" / "python.exe"),
                str(Path(local) / "Programs" / "Python" / "Python311" / "python.exe"),
            ]
        candidates += ["python.exe", "python3.exe"]
    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        path = candidate if os.path.isabs(candidate) else (shutil.which(candidate) or "")
        if not path:
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def detect_python_interpreters() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in _python_candidates():
        item: dict[str, Any] = {"path": path, "ok": False, "error": ""}
        try:
            proc = subprocess.run(
                [path, "-c", _PROBE],
                capture_output=True, text=True, timeout=8,
                env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            )
            if proc.returncode != 0:
                item["error"] = (proc.stderr or "").strip()[-200:]
            else:
                info = json.loads((proc.stdout or "").strip().splitlines()[-1])
                item.update(info)
                item["ok"] = True
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
    return results


def _model_candidates(data_dir: str | Path | None) -> list[Path]:
    roots: list[Path] = []
    if data_dir:
        roots += [Path(data_dir) / "codebert", Path(data_dir) / "models" / "codebert"]
    env_root = os.environ.get("OJS_MODEL_DIR")
    if env_root:
        roots.append(Path(env_root) / "codebert")
    hf_home = os.environ.get("HF_HOME") or os.environ.get("TRANSFORMERS_CACHE")
    if hf_home:
        roots.append(Path(hf_home) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("*", "models--*"):
            for item in root.glob(pattern):
                targets = [item]
                if item.is_dir() and (item / "snapshots").is_dir():
                    targets = [p for p in (item / "snapshots").iterdir() if p.is_dir()]
                for target in targets:
                    if target not in out and (target / "config.json").exists():
                        out.append(target)
    return out


def detect_codebert_models(data_dir: str | Path | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in _model_candidates(data_dir):
        files = [p for p in path.rglob("*") if p.is_file()]
        size = 0
        for item in files:
            try:
                size += item.stat().st_size
            except OSError:
                pass
        results.append({
            "path": str(path),
            "files": len(files),
            "size_mb": round(size / (1024 * 1024), 1),
            "has_config": any(p.name == "config.json" for p in files),
            "has_weights": any(p.name in {"pytorch_model.bin", "model.safetensors"} for p in files),
            "has_tokenizer": any(p.name in {"tokenizer.json", "tokenizer_config.json", "vocab.json"} for p in files),
        })
    return results


def detect_environment(data_dir: str | Path | None = None) -> dict[str, Any]:
    from .ai.dsh_discovery import discover_dsh

    dsh = discover_dsh()
    return {
        "python": detect_python_interpreters(),
        "codebert_models": detect_codebert_models(data_dir),
        "dsh": dsh.to_dict(),
        "data_dir": str(Path(data_dir) if data_dir else ""),
        "platform": sys.platform,
    }


__all__ = ["detect_codebert_models", "detect_environment", "detect_python_interpreters"]
