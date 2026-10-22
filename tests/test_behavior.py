"""Synthetic, offline tests for :mod:`oj_sentinel.analysis.behavior`.

No real snapshot, student name or student code is used here: every fixture is
constructed in-memory from hand-written snippets.  The tests exercise each
required signal and make sure ordinary students stay at a low score.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# The project uses a src/ layout and is not always installed in test runners.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from oj_sentinel.analysis.behavior import (  # noqa: E402
    DEFAULT_CONFIG,
    REASON_AI_PASTE,
    REASON_BURST,
    REASON_CODES,
    REASON_CROSS_USER,
    REASON_FIRST_SUBMISSION_HIGH,
    REASON_RAPID_DISTINCT,
    REASON_STYLE_DRIFT,
    analyze_behavior,
    analyze_user,
)
from oj_sentinel.analysis.normalize import CodeProfile, build_profile  # noqa: E402
from oj_sentinel.analysis.similarity import Pair  # noqa: E402
from oj_sentinel.hydro.models import Submission  # noqa: E402
from oj_sentinel.ingest import Snapshot  # noqa: E402

T0 = 1_700_000_000  # 2023-11-14T22:13:20Z, a fixed epoch for determinism
ISO_OPEN = datetime.fromtimestamp(T0, timezone.utc).isoformat()

NORMAL_SUM = """n = int(input())
total = 0
for i in range(n):
    total += int(input())
print(total)
"""

NORMAL_SUM_ALT = """n = int(input())
total = 0
for i in range(n):
    value = int(input())
    total = total + value
print(total)
"""

NORMAL_CN_1 = """# 读入数据
n = int(input())
total = 0
for i in range(n):
    total += int(input())
# 输出结果
print(total)
"""

NORMAL_CN_2 = """# 读入数据
n = int(input())
values = [int(input()) for _ in range(n)]
# 输出结果
print(sum(values))
"""

WRONG_SUM = """n = int(input())
i = 0
s = 0
while i < n:
    s = s + int(input())
    i = i + 1
print(s)
"""

RIGHT_SUM = """data = list(map(int, __import__('sys').stdin.read().split()))
n = data[0]
nums = data[1:1 + n]
print(sum(x for x in nums))
"""

LEGIT_FIX_A = """n = int(input())
total = 0
for i in range(n):
    total += int(input())
print(total)
"""

LEGIT_FIX_B = """n = int(input())
total = 0
for i in range(n):
    total += int(input())
print(total + 0)
"""

STYLE_PLAIN = """n = int(input())
total = 0
for i in range(n):
    total += int(input())
print(total)
"""

STYLE_STYLED = """import sys

def read_all():
    data = sys.stdin.read().split()
    return [int(x) for x in data]

def solve(values):
    \"\"\"Compute the answer.\"\"\"
    total = 0
    for value in values:
        total += value
    return total


def main():
    values = read_all()
    print(f"total={solve(values)}")


if __name__ == "__main__":
    main()
"""

AI_CODE = """# ```python
# Here is the solution:
# Explanation: we use functools.
# 下面这段代码的思路如下
from typing import List
from dataclasses import dataclass
from functools import reduce

@dataclass
class Stats:
    \"\"\"Stats container.\"\"\"

    total: int = 0

def compute(values: List[int]) -> int:
    \"\"\"Compute total.\"\"\"
    return reduce(lambda a, b: a + b, values, 0)

def main() -> None:
    \"\"\"Entrypoint.\"\"\"
    n = int(input())
    values = [int(input()) for _ in range(n)]
    print(Stats(compute(values)).total)

if __name__ == "__main__":
    main()
"""


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def entry(
    rid: str,
    uid: int,
    problem: str = "HW2001",
    ts: int = T0,
    score: float = 0.0,
    status: str = "Wrong Answer",
    code: str = "",
    username: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    return {
        "rid": rid,
        "uid": uid,
        "problem_key": problem,
        "submit_ts": ts,
        "submit_at": "",
        "score": score,
        "status_text": status,
        "language": "Python 3",
        "username": username or f"student{uid}",
        "display_name": display_name or f"Student {uid}",
        "code": code,
    }


def make_snapshot(
    entries: Iterable[dict[str, Any]],
    *,
    problems: dict[str, Any] | None = None,
    homework: dict[str, Any] | None = None,
) -> Snapshot:
    entries = list(entries)
    records: list[dict[str, Any]] = []
    submissions: dict[str, Any] = {}
    for item in entries:
        rid = str(item["rid"])
        records.append({key: value for key, value in item.items() if key != "code"})
        submissions[rid] = {
            "summary": {},
            "detail": {"code": item.get("code", ""), "language": "Python 3"},
        }
    if problems is None:
        problems = {}
        for item in entries:
            key = str(item.get("problem_key") or "")
            problems.setdefault(
                key,
                {"key": key, "title": "Synthetic", "points": 100, "difficulty": "easy"},
            )
    return Snapshot(
        root=Path("synthetic-snapshot"),
        homework=homework
        or {"id": "hw-synthetic", "title": "Synthetic Homework", "domain": "test"},
        problems=problems,
        records=records,
        submissions=submissions,
    )


def make_profiles(entries: Iterable[dict[str, Any]]) -> list[CodeProfile]:
    return [
        build_profile(
            str(item["rid"]),
            int(item["uid"]),
            str(item["problem_key"]),
            str(item.get("code") or ""),
        )
        for item in entries
        if item.get("code")
    ]


def make_submissions(entries: Iterable[dict[str, Any]], uid: int) -> list[Submission]:
    out: list[Submission] = []
    for item in entries:
        if int(item["uid"]) != uid:
            continue
        out.append(
            Submission(
                rid=str(item["rid"]),
                homework_id="hw-synthetic",
                problem_key=str(item["problem_key"]),
                uid=int(item["uid"]),
                username=str(item.get("username") or ""),
                display_name=str(item.get("display_name") or ""),
                status=str(item.get("status") or item.get("status_text") or ""),
                score=float(item.get("score") or 0),
                language=str(item.get("language") or ""),
                submit_ts=int(item.get("submit_ts") or 0),
                submit_at=str(item.get("submit_at") or ""),
                code=str(item.get("code") or ""),
            )
        )
    return out


def report_for(results: list[dict[str, Any]], uid: int) -> dict[str, Any] | None:
    for report in results:
        if report["uid"] == uid:
            return report
    return None


def reason_for(report: dict[str, Any] | None, code: str) -> dict[str, Any] | None:
    if not report:
        return None
    for reason in report["reasons"]:
        if reason["code"] == code:
            return reason
    return None


# ---------------------------------------------------------------------------
# signal 1: burst
# ---------------------------------------------------------------------------


def test_burst_signal_and_normal_student_stays_low() -> None:
    entries: list[dict[str, Any]] = []
    for index in range(8):
        entries.append(
            entry(
                f"burst-{index}",
                1,
                ts=T0 + index * 60,
                score=index * 10,
                code=f"print({index})",
            )
        )
    normal_entries = [
        entry(f"normal-{index}", 2, ts=T0 + index * 86_400, score=index * 25,
              code="print(0)")
        for index in range(4)
    ]
    all_entries = entries + normal_entries
    snap = make_snapshot(all_entries)
    profiles = make_profiles(all_entries)

    results = analyze_behavior(snap, profiles)

    assert [report["uid"] for report in results] == [1]
    report = results[0]
    burst = reason_for(report, REASON_BURST)
    assert burst is not None
    assert REASON_BURST in REASON_CODES
    assert 0.3 <= burst["weight"] <= 0.55
    assert report["metrics"]["max_5min"] >= 5
    assert report["metrics"]["max_10min"] >= 7
    assert report["metrics"]["max_30min"] == 8
    assert report["metrics"]["max_60min"] == 8
    assert report["metrics"]["median_gap_seconds"] == 60.0
    assert report["metrics"]["burst_ratio"] == 1.0
    assert report["metrics"]["max_window_count"] >= 5
    assert len(report["timeline"]) == 8
    assert report["timeline"][1]["gap_seconds"] == 60
    assert report["top_reasons"][0]["code"] == REASON_BURST
    assert report["username"] == "student1"
    assert report["display_name"] == "Student 1"

    # A normal student with a slow, evenly spaced debug history is not flagged
    # and scores zero when inspected directly.
    normal_report = analyze_user(
        uid=2,
        submissions=make_submissions(normal_entries, 2),
        profiles=[profile for profile in profiles if profile.user_id == 2],
    )
    assert normal_report["score"] < 0.15
    assert normal_report["reasons"] == []
    assert normal_report["metrics"]["max_5min"] == 1

    # The same fixture with min_score=0 still omits users without reasons.
    assert analyze_behavior(snap, profiles, config={"min_score": 0.0}) == results


# ---------------------------------------------------------------------------
# signal 2: rapid divergent submissions
# ---------------------------------------------------------------------------


def test_rapid_divergent_submissions() -> None:
    entries = [
        entry("rapid-wrong", 3, ts=T0, score=0, code=WRONG_SUM),
        entry("rapid-right", 3, ts=T0 + 300, score=100, status="Accepted", code=RIGHT_SUM),
        entry("legit-a", 4, ts=T0, score=0, code=LEGIT_FIX_A),
        entry("legit-b", 4, ts=T0 + 300, score=100, status="Accepted", code=LEGIT_FIX_B),
    ]
    snap = make_snapshot(entries)
    profiles = make_profiles(entries)

    results = analyze_behavior(snap, profiles)

    assert [report["uid"] for report in results] == [3]
    report = results[0]
    rapid = reason_for(report, REASON_RAPID_DISTINCT)
    assert rapid is not None
    assert 0.2 <= rapid["weight"] <= 0.55
    assert set(rapid["evidence"]) == {"rapid-wrong", "rapid-right"}
    assert report["metrics"]["rapid_distinct_events"] == 1
    assert report["metrics"]["rapid_min_similarity"] <= 0.2
    assert report["metrics"]["rapid_max_jump"] == 100.0
    assert "similarity" in rapid["detail"]
    assert "rapid rewrite" in rapid["detail"]
    assert "0 -> 100" in rapid["detail"]

    # A high-similarity incremental fix with the same score jump is not flagged.
    assert report_for(results, 4) is None


# ---------------------------------------------------------------------------
# signal 3: first attempt high / very fast first solve
# ---------------------------------------------------------------------------


def test_first_submission_high_and_fast_solve() -> None:
    entries = [
        entry("first-fast", 5, ts=T0 + 120, score=100, status="Accepted", code=NORMAL_SUM),
        entry("first-normal", 6, ts=T0 + 120, score=40, code=NORMAL_SUM_ALT),
    ]
    snap = make_snapshot(entries, homework={"id": "hw-synthetic", "title": "HW", "open_at": ISO_OPEN})
    profiles = make_profiles(entries)

    results = analyze_behavior(snap, profiles)

    assert [report["uid"] for report in results] == [5]
    report = results[0]
    first = reason_for(report, REASON_FIRST_SUBMISSION_HIGH)
    assert first is not None
    assert 0.4 <= first["weight"] <= 0.5
    assert first["evidence"] == ["first-fast"]
    assert report["metrics"]["first_try_full_solves"] == 1
    assert report["metrics"]["fast_first_solves"] == 1
    assert report["metrics"]["fastest_first_solve_seconds"] == 120
    assert "2.0 min" in first["detail"]

    # The helper accepts an explicit open timestamp and finds the same signal.
    helper_report = analyze_user(
        5,
        make_submissions(entries, 5),
        [profile for profile in profiles if profile.user_id == 5],
        open_ts=T0,
    )
    helper_first = reason_for(helper_report, REASON_FIRST_SUBMISSION_HIGH)
    assert helper_first is not None
    assert helper_first["weight"] == first["weight"]

    # An explicit config override wins over snapshot homework metadata.
    results_override = analyze_behavior(snap, profiles, config={"open_ts": T0})
    assert report_for(results_override, 5) is not None


# ---------------------------------------------------------------------------
# signal 4: style drift
# ---------------------------------------------------------------------------


def test_style_drift_between_consecutive_submissions() -> None:
    entries = [
        entry("style-plain", 5, ts=T0, score=0, code=STYLE_PLAIN),
        entry("style-styled", 5, ts=T0 + 1_200, score=10, code=STYLE_STYLED),
        entry("stable-a", 6, ts=T0, score=0, code=LEGIT_FIX_A),
        entry("stable-b", 6, ts=T0 + 1_200, score=100, status="Accepted", code=LEGIT_FIX_B),
    ]
    snap = make_snapshot(entries)
    profiles = make_profiles(entries)

    # A style swing on its own sits just below the default review threshold
    # (0.30), so the batch API needs min_score=0 to surface it.  The signal
    # itself must still fire and remain fully explainable.
    assert analyze_behavior(snap, profiles) == []
    results = analyze_behavior(snap, profiles, config={"min_score": 0.0})

    assert [report["uid"] for report in results] == [5]
    report = results[0]
    drift = reason_for(report, REASON_STYLE_DRIFT)
    assert drift is not None
    assert set(REASON_CODES) >= {REASON_STYLE_DRIFT}
    assert drift["weight"] >= 0.15
    assert set(drift["evidence"]) == {"style-plain", "style-styled"}
    assert report["metrics"]["style_drift_events"] == 1
    assert report["metrics"]["max_style_drift"] >= 0.35
    assert "differ in style" in drift["detail"]
    assert "distance" in drift["detail"]
    assert report["score"] == drift["weight"]  # a lone signal is not inflated
    assert reason_for(report, REASON_AI_PASTE) is None
    assert report_for(results, 6) is None


# ---------------------------------------------------------------------------
# signal 5: AI-paste heuristics
# ---------------------------------------------------------------------------


def test_ai_paste_heuristics_and_beginner_context() -> None:
    entries = [
        entry("ai-1", 6, ts=T0, score=0, code=AI_CODE),
        entry("ai-2", 6, ts=T0 + 600, score=50, code=AI_CODE),
        entry("cn-1", 7, ts=T0, score=0, code=NORMAL_CN_1),
        entry("cn-2", 7, ts=T0 + 3_600, score=10, code=NORMAL_CN_2),
    ]
    problems = {
        "HW2001": {"key": "HW2001", "title": "Beginner", "points": 100, "difficulty": "easy"},
    }
    snap = make_snapshot(entries, problems=problems)
    profiles = make_profiles(entries)

    results = analyze_behavior(snap, profiles)

    assert [report["uid"] for report in results] == [6]
    report = results[0]
    ai = reason_for(report, REASON_AI_PASTE)
    assert ai is not None
    assert ai["weight"] >= 0.4
    assert set(ai["evidence"]) == {"ai-1", "ai-2"}
    metrics = report["metrics"]
    assert metrics["ai_signals"] == 5
    assert metrics["ai_markdown_fences"] == 2
    assert metrics["ai_llm_comments"] == 2
    assert metrics["ai_docstring_every_def"] == 2
    assert metrics["ai_advanced_constructs"] == 2
    assert metrics["ai_uniform_comments"] == 2
    for expected in (
        "markdown code fence",
        "LLM-style comments",
        "Here is",
        "Chinese explanation",
        "docstring on every function",
        "typing",
        "near-uniform comment density",
    ):
        assert expected in ai["detail"]

    # Ordinary Chinese comments must not be mistaken for LLM chatter, and a
    # uniformly commented pair of normal submissions is not flagged on its own.
    normal_report = analyze_user(
        7,
        make_submissions(entries, 7),
        [profile for profile in profiles if profile.user_id == 7],
    )
    assert reason_for(normal_report, REASON_AI_PASTE) is None
    assert normal_report["score"] < 0.15

    # Advanced constructs only count in a beginner assignment.
    non_beginner = analyze_behavior(snap, profiles, config={"beginner": False})
    non_beginner_report = report_for(non_beginner, 6)
    assert non_beginner_report is not None
    assert non_beginner_report["metrics"]["ai_advanced_constructs"] == 0
    assert non_beginner_report["metrics"]["ai_signals"] == 4
    assert "advanced constructs" not in reason_for(non_beginner_report, REASON_AI_PASTE)["detail"]

    explicit_beginner = analyze_behavior(
        snap, profiles, config={"beginner_problems": ["HW2001"]}
    )
    assert report_for(explicit_beginner, 6)["metrics"]["ai_advanced_constructs"] == 2

    advanced_problem = analyze_behavior(
        snap, profiles, config={"advanced_problems": ["HW2001"]}
    )
    assert report_for(advanced_problem, 6)["metrics"]["ai_advanced_constructs"] == 0


# ---------------------------------------------------------------------------
# signal 6: cross-user near-identical membership
# ---------------------------------------------------------------------------


def test_cross_user_near_identical_cluster() -> None:
    entries = [
        entry("pair-7", 7, ts=T0, score=50),
        entry("pair-8", 8, ts=T0, score=50),
        entry("pair-9", 9, ts=T0, score=50),
        entry("pair-10", 10, ts=T0, score=50),
    ]
    pairs = [
        Pair("HW2001", "pair-7", "pair-8", 7, 8, score=0.92),
        Pair("HW2001", "pair-8", "pair-9", 8, 9, score=0.91),
        Pair("HW2001", "pair-9", "pair-10", 9, 10, score=0.40),
    ]
    snap = make_snapshot(entries)

    results = analyze_behavior(snap, [], pairs)

    assert {report["uid"] for report in results} == {7, 8, 9}
    assert report_for(results, 10) is None
    for uid in (7, 8, 9):
        report = report_for(results, uid)
        assert report is not None
        near = reason_for(report, REASON_CROSS_USER)
        assert near is not None
        assert 0.30 <= near["weight"] <= 0.35
        assert report["metrics"]["pair_cluster_size"] == 3
        assert report["metrics"]["pair_template_cluster"] == 0
        assert report["metrics"]["pair_max_score"] >= 0.9
        assert "near-identical" in near["detail"]
        assert str(report["metrics"]["pair_max_score"]) != ""

    report_8 = report_for(results, 8)
    assert report_8 is not None
    assert report_8["metrics"]["pair_partners"] == [7, 9]
    assert report_8["metrics"]["pair_count"] == 2
    assert set(report_8["evidence"]) == {"pair-7", "pair-8", "pair-9"}

    # Raising the threshold above the pair scores removes the signal.
    assert analyze_behavior(snap, [], pairs, config={"pair_threshold": 0.95}) == []
    assert analyze_behavior(snap, [], pairs=None) == []


def test_template_chains_are_noise_but_an_exact_copy_flags() -> None:
    """Large transitive components are boilerplate; a tight copy is not."""
    chain_entries = [
        entry(f"chain-{index}", 100 + index, ts=T0, score=50) for index in range(10)
    ]
    chain_snap = make_snapshot(chain_entries)
    chain_085 = [
        Pair(
            "HW2001",
            f"chain-{index}",
            f"chain-{index + 1}",
            100 + index,
            101 + index,
            score=0.85,
        )
        for index in range(9)
    ]
    chain_097 = [
        Pair(
            "HW2001",
            f"chain-{index}",
            f"chain-{index + 1}",
            100 + index,
            101 + index,
            score=0.97,
        )
        for index in range(9)
    ]

    # 0.85 chain: below the default 0.90 gate, entirely ignored.
    assert analyze_behavior(chain_snap, [], chain_085) == []

    # 0.97 chain: above the gate, but a 10-user transitive component is
    # common-template noise and must not be reported as an anomaly.
    assert analyze_behavior(chain_snap, [], chain_097) == []
    suppressed = analyze_user(
        100, make_submissions(chain_entries, 100), pairs=chain_097
    )
    assert reason_for(suppressed, REASON_CROSS_USER) is None
    assert suppressed["metrics"]["pair_cluster_size"] == 10
    assert suppressed["metrics"]["pair_template_cluster"] == 1

    # A two-user 0.96 near-identical copy is a real signal and must flag.
    copy_entries = [
        entry("copy-a", 200, ts=T0, score=50),
        entry("copy-b", 201, ts=T0, score=50),
    ]
    copy_snap = make_snapshot(copy_entries)
    copy_pairs = [
        Pair("HW2001", "copy-a", "copy-b", 200, 201, score=0.96, exact=True)
    ]
    copy_results = analyze_behavior(copy_snap, [], copy_pairs)
    assert {report["uid"] for report in copy_results} == {200, 201}
    for uid in (200, 201):
        report = report_for(copy_results, uid)
        assert report is not None
        near = reason_for(report, REASON_CROSS_USER)
        assert near is not None
        assert 0.30 <= near["weight"] <= 0.35
        assert report["metrics"]["pair_cluster_size"] == 2
        assert report["metrics"]["pair_template_cluster"] == 0
        assert report["score"] == near["weight"]
        assert "near-identical" in near["detail"]


# ---------------------------------------------------------------------------
# output contract, determinism and per-user helper
# ---------------------------------------------------------------------------


def _mixed_fixture() -> tuple[Snapshot, list[CodeProfile], list[Pair]]:
    entries: list[dict[str, Any]] = []
    for index in range(8):
        entries.append(
            entry(f"mixed-burst-{index}", 1, ts=T0 + index * 60, score=index * 10,
                  code=f"print({index})")
        )
    entries += [
        entry("mixed-rapid-wrong", 2, ts=T0, score=0, code=WRONG_SUM),
        entry("mixed-rapid-right", 2, ts=T0 + 300, score=100, status="Accepted", code=RIGHT_SUM),
        entry("mixed-style-plain", 3, ts=T0, score=0, code=STYLE_PLAIN),
        entry("mixed-style-styled", 3, ts=T0 + 1_200, score=10, code=STYLE_STYLED),
        entry("mixed-ai-1", 4, ts=T0, score=0, code=AI_CODE),
        entry("mixed-ai-2", 4, ts=T0 + 600, score=50, code=AI_CODE),
        entry("mixed-pair-5", 5, ts=T0, score=50),
        entry("mixed-pair-6", 6, ts=T0, score=50),
        entry("mixed-normal-1", 7, ts=T0, score=0, code=NORMAL_SUM),
        entry("mixed-normal-2", 7, ts=T0 + 86_400, score=100, status="Accepted",
              code=NORMAL_SUM_ALT),
    ]
    pairs = [Pair("HW2001", "mixed-pair-5", "mixed-pair-6", 5, 6, score=0.93)]
    return make_snapshot(entries), make_profiles(entries), pairs


def test_output_contract_determinism_and_json_roundtrip() -> None:
    snap, profiles, pairs = _mixed_fixture()

    results = analyze_behavior(snap, profiles, pairs)
    repeated = analyze_behavior(snap, profiles, pairs)

    assert results == repeated
    assert results != []
    assert [report["score"] for report in results] == sorted(
        (report["score"] for report in results), reverse=True
    )
    assert report_for(results, 7) is None  # ordinary student stays out

    rids = {item["rid"] for item in snap.records}
    for report in results:
        for key in (
            "uid",
            "username",
            "display_name",
            "score",
            "reasons",
            "evidence",
            "metrics",
            "timeline",
            "problems",
            "top_reasons",
        ):
            assert key in report
        assert isinstance(report["uid"], int) and report["uid"] > 0
        assert 0.0 <= report["score"] <= 0.95
        assert report["reasons"]
        # A single reason is never inflated by corroboration; the strongest
        # reason sets the ceiling and extra reasons add only 30% each.
        if len(report["reasons"]) == 1:
            assert report["score"] == report["reasons"][0]["weight"]
        assert len(report["top_reasons"]) <= 3
        assert set(timeline_code["code"] for timeline_code in report["reasons"]) <= set(
            REASON_CODES
        )
        for reason in report["reasons"]:
            assert set(reason) >= {"code", "weight", "detail", "evidence"}
            assert 0.0 <= reason["weight"] <= 1.0
            assert isinstance(reason["detail"], str) and reason["detail"]
            assert isinstance(reason["evidence"], list)
            assert all(isinstance(rid, str) for rid in reason["evidence"])
        assert isinstance(report["evidence"], list)
        assert all(isinstance(rid, str) for rid in report["evidence"])
        assert set(report["evidence"]) <= rids
        assert isinstance(report["metrics"], dict)
        for key in (
            "submissions",
            "max_5min",
            "max_10min",
            "max_30min",
            "max_60min",
            "median_gap_seconds",
            "burst_ratio",
            "max_window_count",
            "pair_cluster_size",
            "pair_template_cluster",
        ):
            assert key in report["metrics"]
        assert isinstance(report["timeline"], list) and report["timeline"]
        positive_ts = [item["ts"] for item in report["timeline"] if item["ts"] > 0]
        assert positive_ts == sorted(positive_ts)
        assert report["problems"]
        assert all(bucket["best_score"] >= 0 for bucket in report["problems"].values())

    serialised = json.dumps(results, ensure_ascii=False)
    assert json.loads(serialised) == results


def test_defaults_and_anomaly_cap() -> None:
    assert DEFAULT_CONFIG["pair_threshold"] == 0.90
    assert DEFAULT_CONFIG["pair_max_cluster_size"] == 8
    assert DEFAULT_CONFIG["min_score"] == 0.30
    assert DEFAULT_CONFIG["max_anomalies"] == 80

    snap, profiles, pairs = _mixed_fixture()
    unlimited = analyze_behavior(snap, profiles, pairs, config={"max_anomalies": None})
    limited = analyze_behavior(snap, profiles, pairs, config={"max_anomalies": 2})
    assert len(unlimited) > 2
    assert len(limited) == 2
    assert limited[0]["score"] >= limited[1]["score"]


def test_analyze_user_helper_returns_full_report() -> None:
    entries = [
        entry("helper-wrong", 9, ts=T0, score=0, code=WRONG_SUM),
        entry("helper-right", 9, ts=T0 + 300, score=100, status="Accepted", code=RIGHT_SUM),
    ]
    submissions = make_submissions(entries, 9)
    profiles = make_profiles(entries)

    report = analyze_user(9, submissions, profiles)

    assert report["uid"] == 9
    assert report["score"] > 0
    assert reason_for(report, REASON_RAPID_DISTINCT) is not None
    assert report["metrics"]["submissions"] == 2
    assert report["metrics"]["problems"] == 1
    assert report["timeline"][1]["gap_seconds"] == 300
    assert report["problems"]["HW2001"]["submissions"] == 2
    assert report["problems"]["HW2001"]["best_score"] == 100.0
    assert report["top_reasons"][0]["code"] == REASON_RAPID_DISTINCT
    assert json.loads(json.dumps(report)) == report

    # min_score only filters the batch API; the per-user helper always reports.
    unfiltered = analyze_user(9, submissions, profiles, config={"min_score": 0.99})
    assert unfiltered["reasons"]
    assert unfiltered["score"] < 0.99

    # A quiet single-submission user gets a zero report, not an exception.
    quiet_entries = [entry("quiet", 10, ts=T0 + 86_400, score=40, code=NORMAL_SUM)]
    quiet = analyze_user(
        10, make_submissions(quiet_entries, 10), make_profiles(quiet_entries)
    )
    assert quiet["score"] == 0.0
    assert quiet["reasons"] == []
    assert quiet["evidence"] == []
    assert len(quiet["timeline"]) == 1

    # Missing profiles/pairs are tolerated (timing signals only).
    assert analyze_user(9, submissions)["score"] >= 0.0
