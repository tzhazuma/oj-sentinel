"""Fail if a release bundle contains CodeBERT weights or heavy AI runtimes."""
from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_NAMES = (
    "pytorch_model.bin",
    "model.safetensors",
    "tf_model.h5",
    "flax_model.msgpack",
)
FORBIDDEN_DIRS = ("torch", "transformers", "tokenizers", "huggingface_hub", "sentence_transformers")


def main(paths: list[str]) -> int:
    bad: list[str] = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            continue
        for item in root.rglob("*"):
            name = item.name.lower()
            if any(token in name for token in FORBIDDEN_NAMES):
                bad.append(str(item))
            if item.is_dir() and item.name.lower() in FORBIDDEN_DIRS:
                bad.append(str(item))
    if bad:
        print("CodeBERT/model runtime found in bundle (must be downloaded by the user):", file=sys.stderr)
        for path in bad[:40]:
            print(f"  {path}", file=sys.stderr)
        return 1
    print(f"bundle check OK: no bundled CodeBERT weights/runtimes ({len(paths)} path(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
