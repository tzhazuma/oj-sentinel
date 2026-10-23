"""Optional CodeBERT semantic-similarity signal.

This module is deliberately lazy and optional: importing it does not import
``torch``/``transformers`` and the default analysis path never downloads a
model.  Enable it with ``ojs analyze --codebert`` (or the GUI checkbox) after
installing the optional extra::

    pip install -e ".[codebert]"

The model is a *secondary* signal for structurally different rewrites (renamed
variables, reordered helpers, different idioms).  It is slower and uses much
more memory than winnowing/AST, so only the strongest candidate pairs are
embedded and the default weight is small.  Local-only: code never leaves the
machine, but the first run downloads model weights from Hugging Face.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_CODEBERT_MODEL = "microsoft/codebert-base"
DEFAULT_CODEBERT_WEIGHT = 0.15
DEFAULT_MAX_PAIRS = 300
MODEL_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt")
WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class CodeBertStatus:
    available: bool
    model: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "model": self.model, "reason": self.reason}


def model_dir_for(data_dir: str | Path | None, model: str) -> Path:
    """Per-model cache directory under the user data dir (never in the bundle)."""
    base = Path(data_dir) if data_dir else Path(os.environ.get("OJS_MODEL_DIR", "data/models"))
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(model).rstrip("/").split("/")[-1])[:80] or "codebert"
    return Path(base) / "codebert" / slug


def _model_files(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(str(item.relative_to(path)) for item in path.rglob("*") if item.is_file())


def check_model(model: str = DEFAULT_CODEBERT_MODEL, *, cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Report whether a local CodeBERT model and its runtime dependencies exist."""
    local = Path(model).expanduser()
    target = local if local.is_dir() else model_dir_for(cache_dir, model)
    files = _model_files(target)
    has_config = any(Path(name).name == "config.json" for name in files)
    has_weights = any(Path(name).name in WEIGHT_FILES for name in files)
    has_tokenizer = any(Path(name).name in ("tokenizer.json", "tokenizer_config.json") for name in files)
    deps = {}
    for name in ("torch", "transformers", "huggingface_hub"):
        try:
            __import__(name)
            deps[name] = True
        except Exception:  # noqa: BLE001
            deps[name] = False
    total = 0
    for name in files:
        try:
            total += (target / name).stat().st_size
        except OSError:
            pass
    installed = bool(has_config and has_weights and has_tokenizer)
    return {
        "model": model,
        "path": str(target),
        "installed": installed,
        "dependencies": deps,
        "dependencies_ok": all(deps.values()),
        "files": len(files),
        "size_mb": round(total / (1024 * 1024), 1),
        "can_download": True,
        "bundled": False,
        "hint": (
            "模型未随安装包分发；点击“下载模型”后保存在本机数据目录。"
            "需要可选依赖：pip install -e '.[codebert]'"
        ),
    }


def download_model(
    model: str = DEFAULT_CODEBERT_MODEL,
    *,
    cache_dir: str | Path | None = None,
    on_progress: Any | None = None,
    on_bytes: Any | None = None,
) -> dict[str, Any]:
    """Download model files into the user data dir (never into the app bundle)."""
    local = Path(model).expanduser()
    if local.is_dir():
        return check_model(str(local), cache_dir=cache_dir)
    target = model_dir_for(cache_dir, model)
    target.mkdir(parents=True, exist_ok=True)
    if on_progress:
        on_progress(f"开始下载 {model} 到 {target}")
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=model,
            local_dir=str(target),
            local_dir_use_symlinks=False,
            ignore_patterns=["*.h5", "*.msgpack", "*.onnx", "*.ot", "*.tflite"],
        )
    except Exception as hub_exc:  # noqa: BLE001 - fall back to direct HTTP
        # A packaged app has no huggingface_hub; still let the user download the
        # model files so they can later point a CodeBERT-capable Python at them.
        names = ("config.json", "tokenizer.json", "tokenizer_config.json",
                 "vocab.json", "merges.txt", "pytorch_model.bin", "model.safetensors")
        base = f"https://huggingface.co/{model}/resolve/main/"
        downloaded = 0
        for name in names:
            dest = target / name
            if dest.exists() and dest.stat().st_size > 0:
                continue
            try:
                request = urllib.request.Request(
                    base + name, headers={"User-Agent": "OJ-Sentinel/0.1"}
                )
                with urllib.request.urlopen(request, timeout=120) as response:
                    total = int(response.headers.get("Content-Length") or 0)
                    part = dest.with_suffix(dest.suffix + ".part")
                    done = 0
                    with part.open("wb") as handle:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            done += len(chunk)
                            if on_bytes:
                                on_bytes(done, total, name)
                    part.replace(dest)
                downloaded += 1
                if on_progress:
                    on_progress(f"已下载 {name}（{done / 1024 / 1024:.1f} MB）")
            except Exception:
                continue
        if downloaded == 0:
            return {
                "ok": False,
                "error": (
                    f"模型下载失败：{type(hub_exc).__name__}；直连 Hugging Face 也没有可用文件。"
                    "请检查网络/模型名，或手动把模型放到 " + str(target)
                ),
                **check_model(model, cache_dir=cache_dir),
            }
    status = check_model(model, cache_dir=cache_dir)
    status["ok"] = bool(status.get("installed"))
    if on_progress:
        on_progress(f"下载完成：{status.get('files', 0)} 个文件，{status.get('size_mb', 0)} MB")
    return status




_EXTERNAL_WORKER = r"""
import json, sys
model_path = sys.argv[1]
texts = json.load(sys.stdin)
import torch
from transformers import AutoTokenizer, AutoModel
tok = AutoTokenizer.from_pretrained(model_path)
mod = AutoModel.from_pretrained(model_path)
mod.eval()
out = []
with torch.no_grad():
    for start in range(0, len(texts), 8):
        batch = [str(x or "") for x in texts[start:start + 8]]
        enc = tok(batch, padding=True, truncation=True, max_length=512, return_tensors="pt")
        hidden = mod(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-6)
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        out.extend(pooled.cpu().tolist())
print(json.dumps(out))
"""


class ExternalCodeBertEmbedder:
    """Run CodeBERT in an existing Python that already has torch/transformers."""

    def __init__(self, python: str, model_path: str | Path, *, timeout: float = 600.0) -> None:
        self.python = str(Path(python).expanduser())
        self.model_path = str(Path(model_path).expanduser())
        self.timeout = timeout

    def status(self) -> CodeBertStatus:
        if not Path(self.python).exists():
            return CodeBertStatus(False, self.model_path, f"Python 不存在：{self.python}")
        if not Path(self.model_path).is_dir():
            return CodeBertStatus(False, self.model_path, f"模型目录不存在：{self.model_path}")
        try:
            proc = subprocess.run(
                [self.python, "-c", "import torch, transformers; print('ok')"],
                capture_output=True, text=True, timeout=20,
            )
        except Exception as exc:  # noqa: BLE001
            return CodeBertStatus(False, self.model_path, f"运行时不可用：{type(exc).__name__}")
        if proc.returncode != 0:
            return CodeBertStatus(False, self.model_path, (proc.stderr or "缺少 torch/transformers").strip()[-300:])
        return CodeBertStatus(True, self.model_path)

    def embed(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
        status = self.status()
        if not status.available:
            raise RuntimeError(status.reason)
        proc = subprocess.run(
            [self.python, "-c", _EXTERNAL_WORKER, self.model_path],
            input=json.dumps([str(x or "") for x in texts], ensure_ascii=False),
            capture_output=True, text=True, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "外部 CodeBERT 运行失败").strip()[-800:])
        try:
            return json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法解析外部 CodeBERT 输出：{exc}") from exc


class CodeBertEmbedder:
    """Lazy CodeBERT mean-pooled embeddings (CPU by default)."""

    def __init__(self, model: str = DEFAULT_CODEBERT_MODEL, *, device: str | None = None,
                 cache_dir: str | Path | None = None) -> None:
        self.model_name = model
        self.cache_dir = cache_dir
        self.device = device or os.environ.get("OJS_CODEBERT_DEVICE", "cpu")
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._load_error = ""

    def status(self) -> CodeBertStatus:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except Exception as exc:  # noqa: BLE001 - optional dependency
            return CodeBertStatus(
                False,
                self.model_name,
                f"未安装可选依赖（pip install -e '.[codebert]'）：{type(exc).__name__}",
            )
        return CodeBertStatus(True, self.model_name)

    def _load(self) -> None:
        if self._model is not None:
            return
        status = self.status()
        if not status.available:
            raise RuntimeError(status.reason)
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        local = Path(self.model_name).expanduser()
        if not local.is_dir() and self.cache_dir:
            cached = model_dir_for(self.cache_dir, self.model_name)
            if cached.is_dir():
                local = cached
        source = str(local) if local.is_dir() else self.model_name
        kwargs = {"cache_dir": str(self.cache_dir)} if self.cache_dir and not local.is_dir() else {}
        self._tokenizer = AutoTokenizer.from_pretrained(source, **kwargs)
        self._model = AutoModel.from_pretrained(source, **kwargs)
        self._model.eval()
        self._model.to(self.device)

    def embed(self, texts: Iterable[str], *, batch_size: int = 8, max_length: int = 512) -> list[list[float]]:
        self._load()
        assert self._torch is not None and self._tokenizer is not None and self._model is not None
        torch = self._torch
        out: list[list[float]] = []
        items = [str(text or "") for text in texts]
        for start in range(0, len(items), max(1, batch_size)):
            batch = items[start : start + max(1, batch_size)]
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            with torch.no_grad():
                hidden = self._model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-6)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            out.extend(pooled.cpu().tolist())
        return out


def enrich_pairs(
    pairs: list[Any],
    profiles: list[Any],
    *,
    model: str = DEFAULT_CODEBERT_MODEL,
    weight: float = DEFAULT_CODEBERT_WEIGHT,
    max_pairs: int = DEFAULT_MAX_PAIRS,
    cache_dir: str | Path | None = None,
    python: str | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Add ``codebert_cosine`` to the strongest pairs, in place.

    Returns a status dict suitable for ``analysis.json``.  Any failure is
    reported and the ordinary algorithm result is kept (never abort analysis).
    """
    chosen = sorted(pairs, key=lambda p: (-float(getattr(p, "score", 0.0)), -int(getattr(p, "shared", 0))))[: max(0, int(max_pairs))]
    if not chosen:
        return {"enabled": True, "available": True, "model": model, "pairs": 0, "reason": "no pairs"}
    if embedder is not None:
        engine = embedder
    else:
        engine = CodeBertEmbedder(model, cache_dir=cache_dir)
        status = engine.status()
        local = Path(model).expanduser()
        if not local.is_dir():
            local = model_dir_for(cache_dir, model)
        if not status.available and not python:
            try:
                from ..system_env import detect_python_interpreters

                for item in detect_python_interpreters():
                    if item.get("ok") and item.get("torch") and item.get("transformers"):
                        python = str(item.get("path") or "") or None
                        if python:
                            break
            except Exception:  # noqa: BLE001
                pass
        if not status.available and python:
            if local.is_dir():
                external = ExternalCodeBertEmbedder(python, local)
                if external.status().available:
                    engine = external
    status = engine.status() if hasattr(engine, "status") else CodeBertStatus(True, model)
    if not status.available:
        return {"enabled": True, "available": False, "model": model, "pairs": 0, "reason": status.reason}
    by_rid = {str(getattr(p, "rid", "")): p for p in profiles}
    rids: list[str] = []
    for pair in chosen:
        for rid in (str(pair.rid_a), str(pair.rid_b)):
            if rid not in rids and rid in by_rid:
                rids.append(rid)
    try:
        vectors = engine.embed([by_rid[rid].code for rid in rids])
    except Exception as exc:  # noqa: BLE001 - optional signal
        return {"enabled": True, "available": False, "model": model, "pairs": 0,
                "reason": f"CodeBERT 运行失败：{type(exc).__name__}: {exc}"}
    vec = {rid: vector for rid, vector in zip(rids, vectors)}
    updated = 0
    w = max(0.0, min(1.0, float(weight)))
    for pair in chosen:
        a, b = vec.get(str(pair.rid_a)), vec.get(str(pair.rid_b))
        if a is None or b is None:
            continue
        cosine = max(0.0, min(1.0, _cosine(a, b)))
        pair.codebert_cosine = round(cosine, 4)
        pair.score = round((1.0 - w) * float(pair.score) + w * cosine, 6)
        if "codebert" not in str(pair.method):
            pair.method = f"{pair.method}+codebert"
        updated += 1
    return {"enabled": True, "available": True, "model": model, "pairs": updated,
            "weight": w, "max_pairs": int(max_pairs)}


__all__ = [
    "CodeBertEmbedder",
    "CodeBertStatus",
    "DEFAULT_CODEBERT_MODEL",
    "DEFAULT_CODEBERT_WEIGHT",
    "DEFAULT_MAX_PAIRS",
    "check_model",
    "download_model",
    "ExternalCodeBertEmbedder",
    "enrich_pairs",
    "model_dir_for",
]
