"""Report export, PDF, AI trace and deterministic fingerprints."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from oj_sentinel.ai.agent import MockAgent
from oj_sentinel.ai.trace import TraceRecorder
from oj_sentinel.analysis.normalize import kgram_hashes
from oj_sentinel.analysis.codebert import ExternalCodeBertEmbedder
from oj_sentinel.report.export import build_export


def _analysis(tmp_path: Path) -> Path:
    data = {
        "schema_version": 1,
        "domain": "DEMO",
        "homework_id": "H1",
        "title": "Demo homework",
        "generated_at": "2026-01-01T00:00:00+0800",
        "stats": {"submissions": 2, "users": 2, "profiles": 2, "pairs": 1, "clusters": 0, "anomalies": 1},
        "pairs": [{
            "problem": "P1", "rid_a": "a", "rid_b": "b", "username_a": "A", "username_b": "B",
            "score": 0.9, "containment": 0.9, "jaccard": 0.8, "ast_cosine": 0.7,
            "longest_fragment": 20, "token_coverage": 0.5, "reasons": ["shared structure"],
            "evidence": [{"a_start": 1, "a_end": 3, "b_start": 1, "b_end": 3, "lines": 3,
                          "snippet": "print(1)\nprint(2)"}],
        }],
        "anomalies": [{"uid": 1, "username": "A", "score": 0.8, "reasons": ["burst"]}],
        "final_scores": [{"uid": 1, "username": "A", "total": 100, "problems": {}}],
    }
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_kgram_hashes_are_stable():
    tokens = ["def", "V", "(", "V", ")", ":", "return", "V"]
    assert kgram_hashes(tokens) == kgram_hashes(tokens)
    assert kgram_hashes(tokens) != kgram_hashes(list(reversed(tokens)))


def test_export_zip_and_pdf(tmp_path):
    analysis = _analysis(tmp_path)
    result = build_export(tmp_path, analysis, tmp_path / "exports", include_pdf=True,
                          include_ai=False, top_pairs=5)
    assert Path(result["path"]).is_file()
    assert "report.pdf" in result["files"]
    with zipfile.ZipFile(result["path"]) as zf:
        names = set(zf.namelist())
        assert {"analysis.json", "README.md", "manifest.json", "report.pdf"} <= names
        assert zf.read("report.pdf").startswith(b"%PDF")


def test_ai_trace_records_prompt_and_output():
    recorder = TraceRecorder(task="unit", meta={"x": 1})
    agent = recorder.wrap(MockAgent())
    result = agent.complete("hello", system="sys", json_schema={"type": "object"})
    assert result.ok
    trace = recorder.to_dict()
    assert trace["task"] == "unit" and len(trace["calls"]) == 1
    call = trace["calls"][0]
    assert call["prompt"] == "hello" and call["system"] == "sys"
    assert call["provider"] == "mock"
    assert call["raw_text"]


def test_external_codebert_status_rejects_missing(tmp_path):
    embedder = ExternalCodeBertEmbedder(tmp_path / "missing-python", tmp_path / "missing-model")
    status = embedder.status()
    assert status.available is False
    assert status.reason


class _StreamingAgent:
    config = None

    def complete(self, prompt, *, system=None, json_schema=None, on_stream=None):
        from oj_sentinel.ai.agent import AgentResult
        if on_stream:
            on_stream({"type": "text", "text": "hello "})
            on_stream({"type": "text", "text": "world"})
            on_stream({"type": "final", "text": "hello world"})
        return AgentResult(text="hello world", data=None, provider="fake", model="fake")


def test_trace_stream_text_is_live_capable():
    recorder = TraceRecorder(task="stream")
    agent = recorder.wrap(_StreamingAgent())
    result = agent.complete("x")
    assert result.text == "hello world"
    call = recorder.to_dict()["calls"][0]
    assert call["stream_text"] == "hello world"
    assert len(call["stream_events"]) == 3
