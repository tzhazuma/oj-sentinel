"""Failure diagnosis helpers (judge layer, interface section 5).

Thin snapshot-aware wrappers around
:func:`oj_sentinel.ai.prompts.diagnose_submission`: they assemble the problem,
the student's code and the local ``RunResult`` and return an ``AgentResult``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..ai.agent import Agent, AgentResult
from ..ai.prompts import diagnose_submission
from .evaluate import (
    evaluate_rid,
    find_record,
    load_problem,
    load_submission_payload,
)


def diagnose_failure(
    agent: Agent,
    problem: Any,
    code: str,
    run_result: Any,
    *,
    submission: Mapping[str, Any] | None = None,
) -> AgentResult:
    """Diagnose a failing submission from an already-known run result."""

    payload: dict[str, Any] = dict(submission or {})
    payload.setdefault("code", code or "")
    return diagnose_submission(agent, problem, payload, run_result)


def diagnose_rid(
    agent: Agent,
    snapshot_root: Path,
    rid: str,
    *,
    problem_key: str | None = None,
    run_result: Any = None,
    evaluate: bool = True,
    language: str | None = None,
    **evaluate_kwargs: Any,
) -> AgentResult:
    """Load one submission, optionally judge it, then ask the agent why it failed."""

    root = Path(snapshot_root)
    record = find_record(root, rid)
    payload = load_submission_payload(root, rid)
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else {}
    if record is None and not summary:
        raise FileNotFoundError(f"submission not found in snapshot: {rid}")
    record = record or dict(summary)
    code = str(detail.get("code") or "")
    if not code:
        raise ValueError(f"submission {rid} has no stored code")
    key = str(problem_key or record.get("problem_key") or "")
    problem = load_problem(root, key)
    submission = {
        "rid": rid,
        "uid": int(record.get("uid") or 0),
        "language": detail.get("language") or record.get("language") or "Python",
        "status": str(record.get("status_text") or record.get("status") or ""),
        "score": record.get("score", 0),
        "code": code,
    }
    if run_result is None and evaluate:
        run_result = evaluate_rid(
            root,
            rid,
            problem_key=key or None,
            language=language,
            **evaluate_kwargs,
        )
    return diagnose_submission(agent, problem, submission, run_result or {})


__all__ = ["diagnose_failure", "diagnose_rid"]
