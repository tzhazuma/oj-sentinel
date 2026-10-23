"""Optional CodeBERT signal tests; no model download or torch required."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from oj_sentinel.analysis.codebert import CodeBertStatus, enrich_pairs
from oj_sentinel.analysis.normalize import build_profile
from oj_sentinel.analysis.similarity import compare

SRC_A = "def add(a, b):\n    return a + b\n"
SRC_B = "def sum_two(x, y):\n    return x + y\n"


class FakeEmbedder:
    def __init__(self, available=True):
        self.available = available

    def status(self):
        return CodeBertStatus(self.available, "fake-codebert", "" if self.available else "no torch")

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] if "def add" in text else [0.8, 0.2, 0.0] for text in texts]


def test_enrich_pairs_adds_semantic_signal():
    a = build_profile("r1", 1, "P", SRC_A)
    b = build_profile("r2", 2, "P", SRC_B)
    pair = compare(a, b)
    before = pair.score
    status = enrich_pairs([pair], [a, b], embedder=FakeEmbedder(), weight=0.15)
    assert status["available"] and status["pairs"] == 1
    assert pair.codebert_cosine is not None
    assert "codebert" in pair.method
    assert pair.score != before
    assert pair.to_dict()["codebert_cosine"] == pair.codebert_cosine


def test_enrich_pairs_unavailable_is_non_fatal():
    a = build_profile("r1", 1, "P", SRC_A)
    b = build_profile("r2", 2, "P", SRC_B)
    pair = compare(a, b)
    before = pair.score
    status = enrich_pairs([pair], [a, b], embedder=FakeEmbedder(available=False))
    assert status["available"] is False
    assert "no torch" in status["reason"]
    assert pair.codebert_cosine is None
    assert pair.score == before


def test_default_import_does_not_require_torch():
    import oj_sentinel.analysis.codebert as module

    assert "torch" not in sys.modules or module.DEFAULT_CODEBERT_MODEL


def test_check_model_local_files_are_user_data_not_bundle(tmp_path):
    from oj_sentinel.analysis.codebert import check_model

    status = check_model("microsoft/codebert-base", cache_dir=tmp_path)
    assert status["installed"] is False
    assert status["bundled"] is False
    target = tmp_path / "codebert" / "codebert-base"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / "tokenizer.json").write_text("{}", encoding="utf-8")
    (target / "model.safetensors").write_bytes(b"x" * (1024 * 1024))
    status = check_model("microsoft/codebert-base", cache_dir=tmp_path)
    assert status["installed"] is True
    assert status["files"] == 3
    assert status["size_mb"] > 0
    assert status["bundled"] is False
