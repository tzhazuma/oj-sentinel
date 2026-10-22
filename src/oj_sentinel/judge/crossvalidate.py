"""Cross-validation: local judge verdicts vs the OJ's recorded verdicts.

The matrix is intentionally a plain nested dict so it serialises to JSON and
is easy to render in the Web GUI::

    {"Accepted": {"Accepted": 12, "Wrong Answer": 1},
     "Wrong Answer": {"Wrong Answer": 9, "Runtime Error": 2}}

Nothing here contacts the OJ: OJ statuses come from the snapshot records (or an
explicit ``oj_status`` mapping a caller already collected).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evaluate import load_records
from .runner import (
    ACCEPTED,
    COMPILE_ERROR,
    RUNTIME_ERROR,
    TIME_EXCEEDED,
    WRONG_ANSWER,
)

STATUS_ORDER = [ACCEPTED, WRONG_ANSWER, RUNTIME_ERROR, TIME_EXCEEDED, COMPILE_ERROR]


def normalize_status(value: Any) -> str:
    """Normalise OJ/local status spellings to the five verdict constants."""

    text = str(value or "").strip()
    if not text:
        return ""
    if text in STATUS_ORDER:
        return text
    low = text.lower()
    compact = low.replace("_", " ").replace("-", " ").strip()
    if compact in {"ac", "accepted", "ok", "pass", "passed"}:
        return ACCEPTED
    if compact in {"wa", "wrong answer", "wrong", "fail", "failed"}:
        return WRONG_ANSWER
    if compact in {"re", "runtime error", "runtime"}:
        return RUNTIME_ERROR
    if compact in {"tle", "time limit exceeded", "time exceeded", "timeout", "timed out"}:
        return TIME_EXCEEDED
    if compact in {"ce", "compile error", "compilation error", "compile failed"}:
        return COMPILE_ERROR
    if "accept" in low:
        return ACCEPTED
    if ("time" in low and ("limit" in low or "exceed" in low)) or compact == "tle":
        return TIME_EXCEEDED
    if "runtime" in low or compact == "re":
        return RUNTIME_ERROR
    if "compile" in low or "compilation" in low:
        return COMPILE_ERROR
    if "wrong" in low or compact == "wa":
        return WRONG_ANSWER
    return text


def _extract_local_status(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return normalize_status(value)
    if isinstance(value, Mapping):
        for key in ("status", "local_status", "local", "verdict"):
            if value.get(key):
                return normalize_status(value[key])
        return ""
    status = getattr(value, "status", None)
    if status is not None:
        return normalize_status(status)
    return normalize_status(str(value))


def _local_message(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    message = str(value.get("message") or "")
    if message:
        return message
    for case in value.get("cases") or []:
        if isinstance(case, Mapping) and case.get("status") not in (None, "", "AC"):
            return str(case.get("message") or "")[:500]
    return ""


def _order_statuses(statuses: Iterable[str]) -> list[str]:
    present = [status for status in STATUS_ORDER if status in set(statuses)]
    extras = sorted({status for status in statuses if status not in STATUS_ORDER})
    return present + extras


def _empty_matrix() -> dict[str, dict[str, int]]:
    return {}


def crossvalidate(
    snapshot_root: Path,
    local_results: Mapping[str, Any] | Iterable[Any],
    *,
    oj_status: Mapping[str, Any] | None = None,
    rids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compare local verdicts with OJ statuses and return a matrix.

    ``local_results`` is ``{rid: RunResult | {"status": ...} | "Accepted"}`` or
    an iterable of result dicts carrying ``rid``.  ``oj_status`` optionally
    overrides the status stored in ``records.json``.  ``rids`` controls the
    universe used to report ``missing_local``.
    """

    root = Path(snapshot_root)
    records = load_records(root)
    by_rid: dict[str, dict[str, Any]] = {
        str(record.get("rid")): record for record in records if record.get("rid")
    }
    homework = {}
    try:
        homework = json.loads((root / "homework.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        homework = {}

    local_map: dict[str, Any] = {}
    if isinstance(local_results, Mapping):
        local_map = {str(rid): value for rid, value in local_results.items()}
    else:
        for row in local_results or []:
            if isinstance(row, Mapping) and row.get("rid"):
                local_map[str(row["rid"])] = row

    overrides = {str(rid): value for rid, value in (oj_status or {}).items()}
    universe = [str(rid) for rid in rids] if rids is not None else list(local_map.keys())

    matrix: dict[str, dict[str, int]] = {}
    local_totals: dict[str, int] = {}
    oj_totals: dict[str, int] = {}
    disagreements: list[dict[str, Any]] = []
    by_problem: dict[str, dict[str, Any]] = {}
    missing_local: list[str] = []
    compared = 0
    agreement = 0

    for rid in universe:
        local_value = local_map.get(rid)
        local_status = _extract_local_status(local_value)
        record = by_rid.get(rid)
        oj_value = overrides.get(rid)
        if oj_value is None and record is not None:
            oj_value = record.get("status_text") or record.get("status")
        oj_status_text = normalize_status(oj_value)

        if not local_status and rid not in local_map:
            missing_local.append(rid)
            continue
        if not local_status or not oj_status_text:
            continue

        compared += 1
        local_totals[local_status] = local_totals.get(local_status, 0) + 1
        oj_totals[oj_status_text] = oj_totals.get(oj_status_text, 0) + 1
        row = matrix.setdefault(local_status, {})
        row[oj_status_text] = row.get(oj_status_text, 0) + 1
        is_agreement = local_status == oj_status_text
        if is_agreement:
            agreement += 1

        problem_key = str(
            (record or {}).get("problem_key")
            or (local_value.get("problem") if isinstance(local_value, Mapping) else "")
            or "?"
        )
        problem = by_problem.setdefault(
            problem_key,
            {
                "matrix": _empty_matrix(),
                "compared": 0,
                "agreement": 0,
                "disagreements": [],
            },
        )
        problem["compared"] += 1
        problem_row = problem["matrix"].setdefault(local_status, {})
        problem_row[oj_status_text] = problem_row.get(oj_status_text, 0) + 1
        if is_agreement:
            problem["agreement"] += 1
        else:
            disagreement = {
                "rid": rid,
                "problem": problem_key,
                "uid": int((record or {}).get("uid") or 0),
                "username": (record or {}).get("username", ""),
                "local": local_status,
                "oj": oj_status_text,
                "message": _local_message(local_value),
            }
            disagreements.append(disagreement)
            problem["disagreements"].append(disagreement)

    for problem in by_problem.values():
        problem["agreement_rate"] = (
            round(problem["agreement"] / problem["compared"], 4)
            if problem["compared"]
            else 0.0
        )

    # Local results that have no OJ record at all are reported regardless of
    # the ``rids`` universe (they usually mean a stale or mismatched snapshot).
    unmatched_local = sorted(
        rid for rid in local_map if rid not in by_rid and rid not in overrides
    )

    all_statuses = list(local_totals) + list(oj_totals)
    return {
        "schema_version": 1,
        "domain": homework.get("domain", "") if isinstance(homework, Mapping) else "",
        "homework_id": homework.get("id", "") if isinstance(homework, Mapping) else "",
        "matrix": matrix,
        "by_problem": by_problem,
        "status_order": _order_statuses(all_statuses),
        "totals": {"local": local_totals, "oj": oj_totals},
        # Flat aliases kept for the CLI summary (`ojs crossvalidate`).
        "local_totals": local_totals,
        "oj_totals": oj_totals,
        "compared": compared,
        "agreement": agreement,
        "disagreement": compared - agreement,
        "agreement_rate": round(agreement / compared, 4) if compared else 0.0,
        "disagreements": disagreements,
        "missing_local": missing_local,
        "unmatched_local": unmatched_local,
    }


def write_crossvalidation(result: Mapping[str, Any], path: Path) -> Path:
    """Write the matrix (and disagreements) to a JSON file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


__all__ = [
    "normalize_status",
    "crossvalidate",
    "write_crossvalidation",
    "STATUS_ORDER",
]
