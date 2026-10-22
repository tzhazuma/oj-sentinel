"""Offline tests for evaluation, generation, diagnosis and cross-validation.

Everything uses a synthetic snapshot in ``tmp_path``; no student data and no OJ
access.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from oj_sentinel.ai import MockAgent  # noqa: E402
from oj_sentinel.judge import (  # noqa: E402
    ACCEPTED,
    crossvalidate,
    diagnose_rid,
    evaluate_and_crossvalidate,
    evaluate_rid,
    evaluate_snapshot,
    evaluate_submission,
    find_record,
    generate_and_validate,
    validate_tests,
    write_crossvalidation,
    write_generated_tests,
)
from oj_sentinel.judge.runner import load_cases  # noqa: E402

GOOD_CODE = "a, b = map(int, input().split())\nprint(a + b)\n"
BAD_CODE = "print(0)\n"
PROBLEM_DESCRIPTION = "Read two integers on one line and print their sum."


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _record(rid: str, problem: str, status: str, score: float, uid: int, username: str) -> dict:
    return {
        "rid": rid,
        "status": "ok" if status == "Accepted" else "fail",
        "status_text": status,
        "score": score,
        "problem_key": problem,
        "problem_title": problem,
        "uid": uid,
        "display_name": f"Student {uid}",
        "username": username,
        "time_ms": 10,
        "memory": "1 MiB",
        "language": "Python 3",
        "submit_at": "2026-10-20 20:00:00",
        "submit_ts": 1792500000 + uid,
    }


def _submission(record: dict, code: str) -> dict:
    return {
        "summary": record,
        "detail": {
            "rid": record["rid"],
            "code": code,
            "language": "Python 3",
            "status": record["status"],
            "status_text": record["status_text"],
            "score": record["score"],
            "tests": [],
            "compile_error": "",
            "judge_text": "",
        },
    }


def make_snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snap"
    _write(
        root / "homework.json",
        json.dumps({"id": "hw1", "domain": "TEST_DOMAIN", "title": "Test Homework"}),
    )
    _write(
        root / "problems" / "HW1.json",
        json.dumps(
            {
                "key": "HW1",
                "title": "Add Two Numbers",
                "numeric_id": 11,
                "time_limit": "1s",
                "memory_limit": "64MB",
                "description_text": PROBLEM_DESCRIPTION,
            }
        ),
    )
    _write(
        root / "problems" / "HW2.json",
        json.dumps({"key": "HW2", "title": "Second", "numeric_id": 12}),
    )
    testdata = root / "testdata" / "11"
    _write(
        testdata / "config.yaml",
        "type: default\n"
        "subtasks:\n"
        "  - score: 100\n"
        "    time: 1000ms\n"
        "    memory: 64MB\n"
        "    cases:\n"
        "      - input: a1.in\n"
        "        output: a1.out\n"
        "      - input: a2.in\n"
        "        output: a2.out\n",
    )
    _write(testdata / "a1.in", "1 2\n")
    _write(testdata / "a1.out", "3\n")
    _write(testdata / "a2.in", "5 7\n")
    _write(testdata / "a2.out", "12\n")

    r1 = _record("r1", "HW1", "Accepted", 100, 1, "u1")
    r2 = _record("r2", "HW1", "Wrong Answer", 0, 2, "u2")
    r3 = _record("r3", "HW1", "Accepted", 100, 3, "u3")
    r4 = _record("r4", "HW2", "Accepted", 100, 4, "u4")
    r6 = _record("r6", "HW1", "Accepted", 100, 6, "u6")
    records = [r1, r2, r3, r4, r6]
    _write(root / "records.json", json.dumps(records))
    _write(root / "submissions" / "r1.json", json.dumps(_submission(r1, GOOD_CODE)))
    _write(root / "submissions" / "r2.json", json.dumps(_submission(r2, BAD_CODE)))
    _write(root / "submissions" / "r3.json", json.dumps(_submission(r3, GOOD_CODE)))
    _write(root / "submissions" / "r4.json", json.dumps(_submission(r4, "print(1)\n")))
    _write(root / "submissions" / "r6.json", json.dumps(_submission(r6, "")))
    return root


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------
def test_load_cases_on_synthetic_snapshot(tmp_path):
    root = make_snapshot(tmp_path)
    cases, meta = load_cases(root, "HW1")
    assert len(cases) == 2
    assert meta["numeric_id"] == 11
    assert meta["time_limit_ms"] == 1000
    assert meta["memory_limit_mb"] == 64


def test_evaluate_rid_matches_oj_verdicts(tmp_path):
    root = make_snapshot(tmp_path)
    accepted = evaluate_rid(root, "r1")
    assert accepted["status"] == ACCEPTED
    assert accepted["score"] == 100.0
    assert accepted["passed"] == 2 and accepted["total"] == 2
    assert accepted["oj_status"] == "Accepted"
    assert accepted["problem"] == "HW1"
    assert accepted["language"] == "python"
    assert accepted["cases"][0]["status"] == "AC"

    wrong = evaluate_rid(root, "r2")
    assert wrong["status"] == "Wrong Answer"
    assert wrong["oj_status"] == "Wrong Answer"
    assert wrong["passed"] == 0
    assert "first mismatch" in wrong["cases"][0]["message"]


def test_evaluate_rid_error_paths(tmp_path):
    root = make_snapshot(tmp_path)
    with pytest.raises(FileNotFoundError):
        evaluate_rid(root, "missing")
    with pytest.raises(ValueError):
        evaluate_rid(root, "r6")
    assert find_record(root, "r1")["problem_key"] == "HW1"
    assert find_record(root, "missing") is None


def test_evaluate_submission_line_compare(tmp_path):
    root = make_snapshot(tmp_path)
    from oj_sentinel.judge import RunResult

    result = evaluate_submission(root, "HW1", GOOD_CODE)
    assert isinstance(result, RunResult)
    assert result.status == ACCEPTED


def test_evaluate_snapshot_filters_and_writes_json(tmp_path):
    root = make_snapshot(tmp_path)
    out = tmp_path / "reports" / "local.json"
    payload = evaluate_snapshot(root, problem_key="HW1", out=out)
    assert payload["summary"]["evaluated"] == 4  # r1 r2 r3 r6
    assert payload["summary"]["local_accepted"] == 2
    assert payload["summary"]["errors"] == 1
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1

    limited = evaluate_snapshot(root, problem_key="HW1", limit=2)
    assert len(limited["results"]) == 2

    accepted_only = evaluate_snapshot(root, problem_key="HW1", accepted_only=True)
    assert {row["rid"] for row in accepted_only["results"]} == {"r1", "r3", "r6"}

    all_rows = evaluate_snapshot(root)
    assert all_rows["summary"]["evaluated"] == 5


# ---------------------------------------------------------------------------
# crossvalidate
# ---------------------------------------------------------------------------
def test_crossvalidate_matrix_and_disagreements(tmp_path):
    root = make_snapshot(tmp_path)
    local = {
        "r1": "Accepted",
        "r2": "Accepted",
        "r3": ACCEPTED,
        "r4": "Wrong Answer",
        "r9": "Accepted",  # not present in records
    }
    result = crossvalidate(root, local, rids=["r1", "r2", "r3", "r4", "r5"])
    assert result["compared"] == 4
    assert result["agreement"] == 2
    assert result["disagreement"] == 2
    assert result["agreement_rate"] == 0.5
    assert result["matrix"]["Accepted"] == {"Accepted": 2, "Wrong Answer": 1}
    assert result["matrix"]["Wrong Answer"] == {"Accepted": 1}
    assert {row["rid"] for row in result["disagreements"]} == {"r2", "r4"}
    assert result["missing_local"] == ["r5"]
    assert result["unmatched_local"] == ["r9"]
    assert result["by_problem"]["HW1"]["compared"] == 3
    assert result["by_problem"]["HW1"]["agreement"] == 2
    assert result["by_problem"]["HW2"]["agreement"] == 0

    out = tmp_path / "reports" / "crossvalidation.json"
    write_crossvalidation(result, out)
    assert json.loads(out.read_text(encoding="utf-8"))["compared"] == 4


def test_crossvalidate_accepts_run_results_and_oj_overrides(tmp_path):
    root = make_snapshot(tmp_path)
    local = {
        "r1": {"rid": "r1", "status": "Accepted", "cases": []},
        "r2": {"status": "Wrong Answer"},
    }
    result = crossvalidate(root, local, oj_status={"r2": "Wrong Answer"})
    assert result["compared"] == 2
    assert result["agreement"] == 2
    assert result["disagreements"] == []


def test_crossvalidate_status_aliases(tmp_path):
    root = make_snapshot(tmp_path)
    result = crossvalidate(
        root,
        {"r1": "AC", "r2": "wa", "r3": "TLE"},
        rids=["r1", "r2", "r3"],
    )
    assert result["matrix"]["Accepted"]["Accepted"] == 1
    assert result["matrix"]["Wrong Answer"]["Wrong Answer"] == 1
    assert result["matrix"]["Time Exceeded"]["Accepted"] == 1


def test_evaluate_and_crossvalidate_combines(tmp_path):
    root = make_snapshot(tmp_path)
    out = tmp_path / "combined.json"
    payload = evaluate_and_crossvalidate(root, rids=["r1", "r2"], out=out)
    assert len(payload["results"]) == 2
    assert payload["crossvalidation"]["compared"] == 2
    assert payload["crossvalidation"]["agreement"] == 2
    assert payload["crossvalidation"]["disagreement"] == 0
    assert out.exists()


# ---------------------------------------------------------------------------
# generate tests
# ---------------------------------------------------------------------------
def test_generate_and_validate_uses_accepted_references(tmp_path):
    root = make_snapshot(tmp_path)
    canned = {
        "tests": [
            {"name": "sum", "input": "2 3\n", "output": "5\n"},
            {"name": "mismatch", "input": "10 20\n", "output": "999\n"},
        ]
    }
    agent = MockAgent(responses=[canned])
    result = generate_and_validate(agent, root, "HW1", n=2, max_reference_submissions=2)
    assert result["generated"] == 2
    assert result["references"] == 2
    assert result["summary"] == {"generated": 2, "validated": 2, "rejected": 0, "references": 2}
    first, second = result["tests"]
    assert first["validated"] is True
    assert first["output"] == "5"
    assert first["model_output_matches"] is True
    assert second["validated"] is True
    # The accepted submission is the oracle, not the model.
    assert second["output"] == "30"
    assert second["model_output_matches"] is False
    assert "replaced" in second["validation"]
    # The prompt contained the real problem statement.
    assert "Add Two Numbers" in agent.calls[0]["prompt"]
    out = tmp_path / "generated.json"
    write_generated_tests(result, out)
    assert json.loads(out.read_text(encoding="utf-8"))["generated"] == 2


def test_generate_rejects_disagreeing_references(tmp_path):
    root = make_snapshot(tmp_path)
    payload = json.loads((root / "submissions" / "r3.json").read_text())
    payload["detail"]["code"] = "print(999)\n"
    _write(root / "submissions" / "r3.json", json.dumps(payload))
    result = validate_tests(
        root,
        "HW1",
        [{"name": "conflict", "input": "2 3\n", "output": "5\n"}],
        max_reference_submissions=2,
    )
    assert result["tests"] == []
    assert len(result["rejected"]) == 1
    assert "disagree" in result["rejected"][0]["reason"]
    assert len(result["rejected"][0]["candidate_outputs"]) == 2


def test_generate_without_accepted_references_is_unvalidated(tmp_path):
    root = make_snapshot(tmp_path)
    result = validate_tests(
        root,
        "HW1",
        [{"name": "t", "input": "2 3\n", "output": "5\n"}],
        max_reference_submissions=0,
    )
    assert result["references"] == 0
    assert result["tests"][0]["validated"] is False
    assert "no accepted submissions" in result["tests"][0]["validation"]


def test_generate_handles_bad_agent_json(tmp_path):
    root = make_snapshot(tmp_path)
    agent = MockAgent(responses=["this is not JSON"])
    result = generate_and_validate(agent, root, "HW1", n=3)
    assert result["tests"] == []
    assert result["generated"] == 0
    assert result["error"]
    assert "JSON" in result["error"] or "usable" in result["error"]


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------
def test_diagnose_rid_evaluates_and_prompts(tmp_path):
    root = make_snapshot(tmp_path)
    agent = MockAgent()
    result = diagnose_rid(agent, root, "r2")
    prompt = agent.calls[0]["prompt"]
    assert "Add Two Numbers" in prompt
    assert "print(0)" in prompt
    assert "first mismatch" in prompt
    assert "Wrong Answer" in prompt
    assert result.provider == "mock"
    assert result.data["category"] == "unknown"
    assert result.data["suggestions"] == []


def test_diagnose_rid_with_precomputed_run_result(tmp_path):
    root = make_snapshot(tmp_path)
    agent = MockAgent()
    run_result = {
        "status": "Wrong Answer",
        "score": 0,
        "cases": [
            {
                "name": "1-1",
                "status": "WA",
                "message": "expected '3', got '0' at line 1",
            }
        ],
    }
    diagnose_rid(agent, root, "r2", run_result=run_result, evaluate=False)
    prompt = agent.calls[0]["prompt"]
    assert "expected '3', got '0'" in prompt
    assert "1-1" in prompt


# ---------------------------------------------------------------------------
# CLI integration hooks (cli.py calls these directly)
# ---------------------------------------------------------------------------
def test_cli_hook_signatures_are_stable():
    import inspect

    rid_params = inspect.signature(evaluate_rid).parameters
    for name in ("snapshot_root", "rid", "problem_key", "language"):
        assert name in rid_params
    snapshot_params = inspect.signature(evaluate_snapshot).parameters
    for name in (
        "problem_key", "rids", "limit", "accepted_only", "language", "out",
        "on_event", "workers",
    ):
        assert name in snapshot_params
    combined_params = inspect.signature(evaluate_and_crossvalidate).parameters
    assert "workers" in combined_params
    cross_params = inspect.signature(crossvalidate).parameters
    for name in ("snapshot_root", "local_results", "oj_status", "rids"):
        assert name in cross_params


def test_evaluate_snapshot_emits_progress_events(tmp_path):
    root = make_snapshot(tmp_path)
    events: list[dict] = []
    evaluate_snapshot(root, problem_key="HW1", limit=2, on_event=events.append)
    assert len(events) == 2
    assert events[0]["kind"] == "progress"
    assert events[-1]["done"] == 2
    assert events[-1]["total"] == 2


def test_crossvalidate_accepts_evaluate_rid_result_list(tmp_path):
    """Mirrors cli.py: evaluate_rid dicts are passed straight to crossvalidate."""
    root = make_snapshot(tmp_path)
    results = [evaluate_rid(root, "r1"), evaluate_rid(root, "r2")]
    report = crossvalidate(root, results)
    assert report["compared"] == 2
    assert report["agreement"] == 2
    assert report["local_totals"] == {"Accepted": 1, "Wrong Answer": 1}
    assert report["oj_totals"] == {"Accepted": 1, "Wrong Answer": 1}


# ---------------------------------------------------------------------------
# Parallel batch evaluation (workers)
# ---------------------------------------------------------------------------
def test_evaluate_snapshot_workers_match_sequential(tmp_path):
    root = make_snapshot(tmp_path)
    sequential = evaluate_snapshot(root)
    parallel = evaluate_snapshot(root, workers=4)

    def signature(payload):
        return [
            (
                row["rid"],
                row.get("status"),
                row.get("score"),
                row.get("passed"),
                row.get("total"),
                row.get("error"),
            )
            for row in payload["results"]
        ]

    assert signature(parallel) == signature(sequential)
    assert [row["rid"] for row in parallel["results"]] == [
        row["rid"] for row in sequential["results"]
    ]
    assert parallel["summary"]["workers"] == 4
    assert parallel["summary"]["errors"] == sequential["summary"]["errors"]
    assert parallel["summary"]["status_counts"] == sequential["summary"]["status_counts"]


def test_evaluate_snapshot_workers_are_parallel_ordered_and_monotonic(
    tmp_path, monkeypatch
):
    import time

    import oj_sentinel.judge.evaluate as evaluate_module

    root = tmp_path / "batch"
    records = [
        {
            "rid": f"x{index:02d}",
            "problem_key": "HW1",
            "uid": index,
            "username": f"u{index}",
            "status_text": "Accepted",
            "status": "ok",
            "score": 100,
        }
        for index in range(8)
    ]
    _write(root / "homework.json", json.dumps({"id": "hw1", "domain": "TEST"}))
    _write(root / "records.json", json.dumps(records))

    def fake_evaluate_rid(snapshot_root, rid, *, language=None, **kwargs):
        time.sleep(0.12)
        return {
            "rid": rid,
            "problem": "HW1",
            "uid": int(rid[1:]),
            "username": rid,
            "oj_status": "Accepted",
            "status": ACCEPTED,
            "score": 100.0,
            "cases": [],
            "passed": 1,
            "total": 1,
            "elapsed_ms": 120,
        }

    monkeypatch.setattr(evaluate_module, "evaluate_rid", fake_evaluate_rid)
    expected_order = [f"x{index:02d}" for index in range(8)]

    sequential_events: list[dict] = []
    started = time.perf_counter()
    sequential = evaluate_snapshot(root, workers=1, on_event=sequential_events.append)
    sequential_seconds = time.perf_counter() - started

    parallel_events: list[dict] = []
    started = time.perf_counter()
    parallel = evaluate_snapshot(root, workers=6, on_event=parallel_events.append)
    parallel_seconds = time.perf_counter() - started

    # Original order is preserved even though workers finish out of order.
    assert [row["rid"] for row in sequential["results"]] == expected_order
    assert [row["rid"] for row in parallel["results"]] == expected_order
    assert [event["done"] for event in sequential_events] == list(range(1, 9))
    assert [event["done"] for event in parallel_events] == list(range(1, 9))
    assert all(event["kind"] == "progress" for event in parallel_events)
    assert parallel["summary"]["workers"] == 6
    assert parallel["summary"]["evaluated"] == 8
    # 8 x 0.12 s over 6 workers; allow generous CI noise while still proving
    # real concurrency (the requirement target is >=4x on the real workload).
    assert parallel_seconds < sequential_seconds * 0.6, (
        f"parallel={parallel_seconds:.2f}s sequential={sequential_seconds:.2f}s"
    )

    combined = evaluate_and_crossvalidate(root, workers=4)
    assert combined["crossvalidation"]["compared"] == 8


def test_evaluate_snapshot_workers_one_matches_sequential_progress(tmp_path):
    root = make_snapshot(tmp_path)
    events: list[dict] = []
    evaluate_snapshot(root, problem_key="HW1", workers=1, on_event=events.append)
    assert [event["done"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert events[-1]["total"] == len(events)
