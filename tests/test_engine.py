import json
from pathlib import Path

from oj_sentinel.analysis.engine import compute_final_scores, run_analysis
from oj_sentinel.ingest import load_snapshot


def _snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snap"
    (root / "problems").mkdir(parents=True)
    (root / "submissions").mkdir()
    (root / "homework.json").write_text(json.dumps({
        "id": "hid", "title": "HW", "domain": "dom",
        "problems": [{"key": "P1", "title": "One", "points": 25},
                     {"key": "P2", "title": "Two", "points": 25}],
    }), encoding="utf-8")
    problems = {
        "P1": {"key": "P1", "title": "One", "points": 25, "numeric_id": 1},
        "P2": {"key": "P2", "title": "Two", "points": 25, "numeric_id": 2},
    }
    for k, v in problems.items():
        (root / "problems" / f"{k}.json").write_text(json.dumps(v), encoding="utf-8")
    records = [
        {"rid": "r1", "problem_key": "P1", "uid": 1, "username": "alice", "display_name": "Alice",
         "status_text": "Accepted", "score": 25, "language": "Python 3", "submit_ts": 100,
         "submit_at": "t1", "time_ms": 1, "memory": "1M"},
        {"rid": "r2", "problem_key": "P1", "uid": 1, "username": "alice", "display_name": "Alice",
         "status_text": "Wrong Answer", "score": 0, "language": "Python 3", "submit_ts": 200,
         "submit_at": "t2", "time_ms": 1, "memory": "1M"},
        {"rid": "r3", "problem_key": "P2", "uid": 1, "username": "alice", "display_name": "Alice",
         "status_text": "Accepted", "score": 25, "language": "Python 3", "submit_ts": 150,
         "submit_at": "t3", "time_ms": 1, "memory": "1M"},
    ]
    (root / "records.json").write_text(json.dumps(records), encoding="utf-8")
    for rid, code in {
        "r1": "def f(x):\n    return x + 1\n",
        "r2": "def f(x):\n    return x + 2\n",
        "r3": "def g(y):\n    return y * 2\n",
    }.items():
        (root / "submissions" / f"{rid}.json").write_text(json.dumps({
            "summary": next(r for r in records if r["rid"] == rid),
            "detail": {"rid": rid, "code": code, "language": "python", "status": "pass",
                       "status_text": "Accepted", "score": 25, "tests": []},
        }), encoding="utf-8")
    (root / "scoreboard.json").write_text(json.dumps({"rows": [
        {"uid": 1, "username": "alice", "display_name": "Alice"},
        {"uid": 2, "username": "bob", "display_name": "Bob"},
    ]}), encoding="utf-8")
    return root


def test_final_score_uses_last_submission(tmp_path):
    snap = load_snapshot(_snapshot(tmp_path))
    rows = compute_final_scores(snap)
    alice = next(r for r in rows if r["uid"] == 1)
    # last P1 submission is the WA one -> final score 0, not the earlier 25
    assert alice["problems"]["P1"]["score"] == 0
    assert alice["problems"]["P1"]["rid"] == "r2"
    assert alice["problems"]["P2"]["score"] == 25
    assert alice["total"] == 25
    bob = next(r for r in rows if r["uid"] == 2)
    assert bob["problems"]["P1"]["status"] == "未提交"
    assert bob["total"] == 0


def test_run_analysis_writes_schema(tmp_path):
    root = _snapshot(tmp_path)
    payload = run_analysis(root, out_dir=tmp_path / "out", threshold=0.3)
    assert payload["schema_version"] == 1
    assert len(payload["final_scores"]) == 2
    assert (tmp_path / "out" / "analysis.json").exists()
    data = json.loads((tmp_path / "out" / "analysis.json").read_text())
    assert data["stats"]["submissions"] == 3
    assert "pairs" in data and "final_scores" in data
