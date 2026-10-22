"""OJ Sentinel local judge layer (interface section 5).

Public surface::

    from oj_sentinel.judge import load_cases, run_python, evaluate_rid
    from oj_sentinel.judge import crossvalidate, diagnose_rid, generate_and_validate

Python execution is the primary path; :func:`run_program` also supports C and
C++ through the system compiler.  The judge never submits to the OJ.
"""
from __future__ import annotations

from .crossvalidate import (
    STATUS_ORDER,
    crossvalidate,
    normalize_status,
    write_crossvalidation,
)
from .diagnose import diagnose_failure, diagnose_rid
from .evaluate import (
    accepted_submissions,
    evaluate_and_crossvalidate,
    evaluate_rid,
    evaluate_snapshot,
    evaluate_submission,
    find_record,
    load_problem,
    load_records,
    load_submission_payload,
    read_json,
    submission_code,
)
from .generate import generate_and_validate, validate_tests, write_generated_tests
from .runner import (
    ACCEPTED,
    CASE_AC,
    CASE_CE,
    CASE_OLE,
    CASE_RE,
    CASE_SKIP,
    CASE_TLE,
    CASE_WA,
    COMPILE_ERROR,
    LANGUAGE_SPECS,
    RUNTIME_ERROR,
    TIME_EXCEEDED,
    WRONG_ANSWER,
    CaseResult,
    RunResult,
    detect_language_from_text,
    load_cases,
    normalize_language,
    normalize_output,
    outputs_match,
    parse_memory_mb,
    parse_simple_yaml,
    parse_time_ms,
    run_program,
    run_python,
    run_stdin,
    sandbox_env,
)

__all__ = [
    # runner
    "CaseResult",
    "RunResult",
    "load_cases",
    "run_program",
    "run_python",
    "run_stdin",
    "normalize_output",
    "outputs_match",
    "parse_simple_yaml",
    "parse_time_ms",
    "parse_memory_mb",
    "normalize_language",
    "detect_language_from_text",
    "sandbox_env",
    "LANGUAGE_SPECS",
    "ACCEPTED",
    "WRONG_ANSWER",
    "RUNTIME_ERROR",
    "TIME_EXCEEDED",
    "COMPILE_ERROR",
    "CASE_AC",
    "CASE_WA",
    "CASE_TLE",
    "CASE_RE",
    "CASE_CE",
    "CASE_OLE",
    "CASE_SKIP",
    # evaluate
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
    # generate
    "generate_and_validate",
    "validate_tests",
    "write_generated_tests",
    # diagnose
    "diagnose_failure",
    "diagnose_rid",
    # crossvalidate
    "normalize_status",
    "crossvalidate",
    "write_crossvalidation",
    "STATUS_ORDER",
]
