"""AI-proposed extra tests, validated against accepted submissions.

The model is never trusted as an oracle: every proposed test is executed
locally against accepted submissions from the snapshot.  When those references
agree the reference output replaces the model's guess; when they disagree the
test is rejected instead of shipped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..ai.agent import Agent, extract_json
from ..ai.prompts import generate_tests as ai_generate_tests
from .evaluate import accepted_submissions, load_cases, load_problem
from .runner import (
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    detect_language_from_text,
    normalize_output,
    parse_memory_mb,
    parse_time_ms,
    run_stdin,
)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalise_tests(data: Any, limit: int) -> list[dict[str, Any]]:
    """Accept ``{"tests": [...]}``, a bare list, or list-of-pairs."""

    raw: Any = None
    if isinstance(data, Mapping):
        raw = data.get("tests")
    elif isinstance(data, list):
        raw = data
    if isinstance(raw, Mapping):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    tests: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if len(tests) >= limit:
            break
        if isinstance(item, Mapping):
            if "input" not in item or "output" not in item:
                continue
            name = str(item.get("name") or f"ai-{index + 1}")
            test = {
                "name": name,
                "input": _as_text(item.get("input")),
                "output": _as_text(item.get("output")),
                "model_output": _as_text(item.get("output")),
                "rationale": str(item.get("rationale") or ""),
            }
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            test = {
                "name": f"ai-{index + 1}",
                "input": _as_text(item[0]),
                "output": _as_text(item[1]),
                "model_output": _as_text(item[1]),
                "rationale": "",
            }
        else:
            continue
        tests.append(test)
    return tests


def validate_tests(
    snapshot_root: Path,
    problem_key: str,
    tests: Iterable[Mapping[str, Any]],
    *,
    language: str | None = None,
    max_reference_submissions: int = 5,
    time_limit_ms: int | None = None,
    memory_limit_mb: int | None = None,
) -> dict[str, Any]:
    """Run proposed tests against accepted submissions and fix expected output."""

    root = Path(snapshot_root)
    cases, meta = load_cases(root, problem_key)
    del cases  # only metadata is needed here
    references = accepted_submissions(
        root, problem_key, limit=max(0, int(max_reference_submissions))
    )
    effective_time = (
        time_limit_ms
        or parse_time_ms(meta.get("time_limit_ms"))
        or 5000
    )
    effective_memory = (
        memory_limit_mb
        or parse_memory_mb(meta.get("memory_limit_mb"))
        or DEFAULT_MEMORY_LIMIT_MB
    )
    extra_source = str(meta.get("extra_source") or "")

    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, test in enumerate(tests):
        input_text = _as_text(test.get("input"))
        model_output = normalize_output(test.get("output"))
        candidates: list[str] = []
        runs: list[dict[str, Any]] = []
        for reference in references:
            ref_language = language or detect_language_from_text(
                reference.get("language") or "python"
            )
            info = run_stdin(
                reference["code"],
                input_text,
                time_limit_ms=int(effective_time),
                memory_limit_mb=int(effective_memory),
                output_limit_bytes=DEFAULT_OUTPUT_LIMIT_BYTES,
                extra_source=extra_source if ref_language == "python" else "",
                language=ref_language,
            )
            runs.append(
                {
                    "rid": reference["rid"],
                    "uid": reference.get("uid", 0),
                    "status": info.get("status", ""),
                    "timed_out": bool(info.get("timed_out")),
                }
            )
            if info.get("status") != "Accepted":
                continue
            candidates.append(normalize_output(info.get("stdout")))

        row = {
            "name": str(test.get("name") or f"ai-{index + 1}"),
            "input": input_text,
            "output": model_output,
            "model_output": model_output,
            "rationale": str(test.get("rationale") or ""),
            "validated": False,
            "validation": "",
            "references": runs,
        }
        if not references:
            row["validation"] = "no accepted submissions available to validate against"
        elif not candidates:
            row["validation"] = "accepted submissions could not run this test"
        else:
            distinct = sorted(set(candidates))
            if len(distinct) > 1:
                rejected.append(
                    {
                        **row,
                        "reason": "accepted submissions disagree on the expected output",
                        "candidate_outputs": distinct[:5],
                    }
                )
                continue
            reference_output = distinct[0]
            matches = model_output == reference_output
            row["output"] = reference_output
            row["validated"] = True
            row["model_output_matches"] = matches
            row["reference_count"] = len(candidates)
            row["validation"] = (
                "model output matched accepted submissions"
                if matches
                else "expected output replaced by accepted-submission output"
            )
        validated.append(row)

    return {
        "tests": validated,
        "rejected": rejected,
        "references": len(references),
        "language": language or "auto",
        "extra_source": bool(extra_source),
    }


def generate_and_validate(
    agent: Agent,
    snapshot_root: Path,
    problem_key: str,
    *,
    n: int = 10,
    max_reference_submissions: int = 5,
    language: str | None = None,
    time_limit_ms: int | None = None,
    memory_limit_mb: int | None = None,
) -> dict[str, Any]:
    """Ask the agent for extra tests, then validate them locally."""

    root = Path(snapshot_root)
    problem = load_problem(root, problem_key)
    result = ai_generate_tests(agent, problem, n=n)
    output: dict[str, Any] = {
        "schema_version": 1,
        "problem": problem_key,
        "requested": int(n),
        "generated": 0,
        "tests": [],
        "rejected": [],
        "references": 0,
        "agent": {
            "provider": result.provider,
            "model": result.model,
            "usage": result.usage,
            "error": result.error,
        },
        "error": result.error,
    }
    data = result.data
    if data is None:
        parsed = extract_json(result.text)
        if isinstance(parsed, dict):
            data = parsed
    tests = _normalise_tests(data, max(1, int(n)))
    if not tests:
        if not output["error"]:
            output["error"] = "agent did not return any usable tests"
        return output
    validation = validate_tests(
        root,
        problem_key,
        tests,
        language=language,
        max_reference_submissions=max_reference_submissions,
        time_limit_ms=time_limit_ms,
        memory_limit_mb=memory_limit_mb,
    )
    output["generated"] = len(tests)
    output["tests"] = validation["tests"]
    output["rejected"] = validation["rejected"]
    output["references"] = validation["references"]
    output["summary"] = {
        "generated": len(tests),
        "validated": sum(1 for test in validation["tests"] if test.get("validated")),
        "rejected": len(validation["rejected"]),
        "references": validation["references"],
    }
    return output


def write_generated_tests(result: Mapping[str, Any], path: Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


__all__ = [
    "generate_and_validate",
    "validate_tests",
    "write_generated_tests",
]
