"""Snapshot-level local evaluation helpers.

Thin, explicit wrappers around :mod:`oj_sentinel.judge.runner`:

* :func:`evaluate_submission` -- one piece of code against a problem's cases
* :func:`evaluate_rid` -- one snapshot submission (code + language detected)
* :func:`evaluate_snapshot` -- batch evaluation with progress events
* :func:`evaluate_and_crossvalidate` -- batch + OJ agreement matrix

Everything reads the local snapshot only; the OJ is never contacted.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .runner import (
    CASE_AC,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    RunResult,
    detect_language_from_text,
    load_cases,
    normalize_language,
    parse_memory_mb,
    parse_time_ms,
    run_program,
)

# ---------------------------------------------------------------------------
# Snapshot readers (shared with generate/diagnose/crossvalidate)
# ---------------------------------------------------------------------------
def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_problem(snapshot_root: Path, problem_key: str) -> dict[str, Any]:
    data = read_json(Path(snapshot_root) / "problems" / f"{problem_key}.json", {})
    return data if isinstance(data, dict) else {"key": problem_key}


def load_records(snapshot_root: Path) -> list[dict[str, Any]]:
    data = read_json(Path(snapshot_root) / "records.json", [])
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def find_record(snapshot_root: Path, rid: str) -> dict[str, Any] | None:
    for record in load_records(snapshot_root):
        if record.get("rid") == rid:
            return record
    return None


def load_submission_payload(snapshot_root: Path, rid: str) -> dict[str, Any]:
    """Return ``{"summary": ..., "detail": ...}`` for one submission, or ``{}``."""

    data = read_json(Path(snapshot_root) / "submissions" / f"{rid}.json", {})
    return data if isinstance(data, dict) else {}


def submission_code(snapshot_root: Path, rid: str) -> str:
    payload = load_submission_payload(snapshot_root, rid)
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else {}
    return str(detail.get("code") or "")


def accepted_submissions(
    snapshot_root: Path, problem_key: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Accepted submissions with code, used as reference oracles."""

    out: list[dict[str, Any]] = []
    if int(limit) <= 0:
        return out
    for record in load_records(snapshot_root):
        if record.get("problem_key") != problem_key:
            continue
        status = str(record.get("status_text") or record.get("status") or "").lower()
        try:
            score = float(record.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if "accept" not in status and score < 100:
            continue
        rid = str(record.get("rid") or "")
        payload = load_submission_payload(snapshot_root, rid)
        detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else {}
        code = str(detail.get("code") or "")
        if not code:
            continue
        out.append(
            {
                "rid": rid,
                "uid": int(record.get("uid") or 0),
                "username": record.get("username", ""),
                "code": code,
                "language": record.get("language", "Python"),
                "status": str(record.get("status_text") or record.get("status") or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _checker_arg(checker: Any, meta: Mapping[str, Any]) -> Any:
    if checker is None:
        return meta.get("checker")
    if checker is False:
        return None
    return checker


def evaluate_submission(
    snapshot_root: Path,
    problem_key: str,
    code: str,
    *,
    language: str = "python",
    checker: Any = None,
    time_limit_ms: int | None = None,
    memory_limit_mb: int | None = None,
    extra_source: str | None = None,
    global_timeout_ms: int | None = None,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    interpreter: str | None = None,
) -> RunResult:
    """Run one submission locally against ``problem_key``'s cases.

    ``checker=None`` uses the checker found in testdata; pass ``checker=False``
    to force OJ-style line comparison.  ``extra_source`` defaults to the
    Hydro ``test.py`` wrapper for Python function problems.
    """

    root = Path(snapshot_root)
    cases, meta = load_cases(root, problem_key)
    language_key = normalize_language(language)
    effective_time = time_limit_ms or parse_time_ms(meta.get("time_limit_ms"))
    effective_memory = memory_limit_mb or parse_memory_mb(meta.get("memory_limit_mb"))
    wrapper = extra_source
    if wrapper is None:
        wrapper = str(meta.get("extra_source") or "") if language_key == "python" else ""
    source = code or ""
    if wrapper and language_key == "python":
        source = source.rstrip("\n") + "\n\n" + wrapper.lstrip("\n")
    return run_program(
        language_key,
        source,
        cases,
        time_limit_ms=int(effective_time or 5000),
        memory_limit_mb=int(effective_memory or DEFAULT_MEMORY_LIMIT_MB),
        checker=_checker_arg(checker, meta),
        global_timeout_ms=global_timeout_ms,
        output_limit_bytes=output_limit_bytes,
        interpreter=interpreter,
    )


def evaluate_rid(
    snapshot_root: Path,
    rid: str,
    *,
    problem_key: str | None = None,
    language: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Evaluate one snapshot submission and return a JSON-ready dict."""

    root = Path(snapshot_root)
    record = find_record(root, rid)
    payload = load_submission_payload(root, rid)
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    if record is None and not summary:
        raise FileNotFoundError(f"submission not found in snapshot: {rid}")
    record = record or dict(summary)
    code = str(detail.get("code") or "")
    if not code:
        raise ValueError(f"submission {rid} has no stored code")
    key = problem_key or record.get("problem_key") or summary.get("problem_key") or ""
    language_text = (
        language
        or detail.get("language")
        or record.get("language")
        or summary.get("language")
        or "Python"
    )
    result = evaluate_submission(root, str(key), code, language=language_text, **kwargs)
    return {
        "rid": rid,
        "problem": str(key),
        "uid": int(record.get("uid") or 0),
        "username": record.get("username", ""),
        "language": detect_language_from_text(language_text),
        "oj_status": str(record.get("status_text") or record.get("status") or ""),
        **result.to_dict(),
    }


def _error_result(
    record: Mapping[str, Any], rid: str, exc: Exception
) -> dict[str, Any]:
    """JSON-ready row for a submission that could not be evaluated."""

    return {
        "rid": rid,
        "problem": str(record.get("problem_key") or ""),
        "uid": int(record.get("uid") or 0),
        "username": record.get("username", ""),
        "oj_status": str(record.get("status_text") or record.get("status") or ""),
        "status": "",
        "score": 0.0,
        "cases": [],
        "stdout": "",
        "stderr": "",
        "compile_output": "",
        "elapsed_ms": 0,
        "passed": 0,
        "total": 0,
        "error": f"{type(exc).__name__}: {exc}",
    }


def _evaluate_record(
    root: Path,
    record: Mapping[str, Any],
    language: str | None,
    kwargs: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Evaluate one record; never lets one bad submission abort a batch.

    Uses the module-level ``evaluate_rid`` name so tests can substitute a fake
    batch evaluator without touching the runner.
    """

    rid = str(record.get("rid") or "")
    try:
        return evaluate_rid(root, rid, language=language, **kwargs), False
    except Exception as exc:  # noqa: BLE001 - batch isolation is intentional
        return _error_result(record, rid, exc), True


def _emit_judge_progress(
    on_event: Callable[[dict[str, Any]], None] | None, done: int, total: int
) -> None:
    if on_event is None:
        return
    try:
        on_event(
            {
                "kind": "progress",
                "phase": "judge",
                "message": f"judged {done}/{total}",
                "done": done,
                "total": total,
            }
        )
    except Exception:  # noqa: BLE001 - UI callbacks must not break judging
        pass


def evaluate_snapshot(
    snapshot_root: Path,
    *,
    problem_key: str | None = None,
    rids: Iterable[str] | None = None,
    limit: int | None = None,
    accepted_only: bool = False,
    language: str | None = None,
    out: Path | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    workers: int = 1,
    **kwargs: Any,
) -> dict[str, Any]:
    """Batch-evaluate stored submissions; never re-judges on the OJ.

    ``workers`` > 1 submits independent submissions to a
    :class:`~concurrent.futures.ThreadPoolExecutor` (each call spawns its own
    subprocesses and temp directory).  Guarantees that make batch judging safe
    for the GUI:

    * ``results`` always follow the original ``records.json`` order, regardless
      of completion order;
    * progress events are emitted from the coordinating thread only and their
      ``done`` counter is strictly monotonic up to ``total``;
    * one failing submission becomes an error row instead of aborting the run.

    ``memory_kb`` values are approximate under concurrency (POSIX
    ``RUSAGE_CHILDREN`` is process-wide); verdicts/scores are unaffected.

    Returns ``{"results": [...], "summary": {...}}`` and optionally writes the
    same payload to *out* (a local report path).
    """

    root = Path(snapshot_root)
    payload = read_json(root / "homework.json", {})
    domain = payload.get("domain", "") if isinstance(payload, Mapping) else ""
    homework_id = payload.get("id", "") if isinstance(payload, Mapping) else ""
    wanted = set(rids) if rids is not None else None
    records = [
        record
        for record in load_records(root)
        if (problem_key is None or record.get("problem_key") == problem_key)
        and (wanted is None or record.get("rid") in wanted)
    ]
    if accepted_only:
        records = [
            record
            for record in records
            if "accept" in str(record.get("status_text") or record.get("status") or "").lower()
            or float(record.get("score") or 0) >= 100
        ]
    if limit is not None:
        records = records[: max(0, int(limit))]

    try:
        workers = max(1, int(workers or 1))
    except (TypeError, ValueError):
        workers = 1
    total = len(records)
    results: list[dict[str, Any] | None] = [None] * total
    error_count = 0

    def store(index: int, outcome: tuple[dict[str, Any], bool]) -> None:
        nonlocal error_count
        result, failed = outcome
        results[index] = result
        if failed:
            error_count += 1

    if workers > 1 and total > 1:
        max_workers = min(workers, total)
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ojs-judge"
        ) as pool:
            futures = {
                pool.submit(_evaluate_record, root, record, language, kwargs): index
                for index, record in enumerate(records)
            }
            finished = 0
            for future in as_completed(futures):
                index = futures[future]
                try:
                    outcome = future.result()
                except Exception as exc:  # noqa: BLE001 - defensive only
                    record = records[index]
                    outcome = (
                        _error_result(record, str(record.get("rid") or ""), exc),
                        True,
                    )
                store(index, outcome)
                finished += 1
                _emit_judge_progress(on_event, finished, total)
    else:
        for index, record in enumerate(records):
            store(index, _evaluate_record(root, record, language, kwargs))
            _emit_judge_progress(on_event, index + 1, total)

    ordered_results = [result for result in results if result is not None]
    status_counts: dict[str, int] = {}
    for result in ordered_results:
        status = str(result.get("status") or "error")
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = {
        "domain": domain,
        "homework_id": homework_id,
        "problem": problem_key or "",
        "evaluated": len(ordered_results),
        "errors": error_count,
        "workers": workers,
        "status_counts": status_counts,
        "local_accepted": status_counts.get("Accepted", 0),
        "oj_accepted": sum(
            1
            for result in ordered_results
            if "accept" in str(result.get("oj_status") or "").lower()
        ),
        "cases": sum(int(result.get("total") or 0) for result in ordered_results),
    }
    output = {"schema_version": 1, "results": ordered_results, "summary": summary}
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def evaluate_and_crossvalidate(
    snapshot_root: Path,
    *,
    problem_key: str | None = None,
    rids: Iterable[str] | None = None,
    limit: int | None = None,
    accepted_only: bool = False,
    language: str | None = None,
    out: Path | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    workers: int = 1,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run :func:`evaluate_snapshot` then attach the OJ agreement matrix."""

    from .crossvalidate import crossvalidate

    evaluation = evaluate_snapshot(
        snapshot_root,
        problem_key=problem_key,
        rids=rids,
        limit=limit,
        accepted_only=accepted_only,
        language=language,
        out=out,
        on_event=on_event,
        workers=workers,
        **kwargs,
    )
    local = {result["rid"]: result for result in evaluation["results"] if result.get("status")}
    matrix = crossvalidate(
        snapshot_root,
        local,
        rids=rids if rids is not None else [result["rid"] for result in evaluation["results"]],
    )
    evaluation["crossvalidation"] = matrix
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8")
    return evaluation


__all__ = [
    "read_json",
    "load_problem",
    "load_records",
    "find_record",
    "load_submission_payload",
    "submission_code",
    "accepted_submissions",
    "evaluate_submission",
    "evaluate_rid",
    "evaluate_snapshot",
    "evaluate_and_crossvalidate",
    "CASE_AC",
]
