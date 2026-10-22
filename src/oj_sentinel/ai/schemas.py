"""Strict JSON schemas shared by every AI task helper.

The schemas are deliberately small and provider-neutral: they are used as the
OpenAI ``response_format.json_schema`` payload, embedded in DSH prompts, and
validated again by :func:`oj_sentinel.ai.prompts.parse_verdict`.
"""
from __future__ import annotations

from typing import Any

#: Allowed integrity verdicts.  The list is part of the public contract.
VERDICT_VALUES: tuple[str, ...] = (
    "plausible_plagiarism",
    "likely_independent",
    "unclear",
    "ai_generated",
    "not_ai",
)

#: Human-readable meaning, reused in prompts and in the Web GUI tooltips.
VERDICT_DESCRIPTIONS: dict[str, str] = {
    "plausible_plagiarism": (
        "Concrete, non-obvious shared structure suggests copying between the two "
        "students and cannot be explained by the problem statement or common idioms."
    ),
    "likely_independent": (
        "The shared parts are boilerplate, required by the statement, or otherwise "
        "consistent with independent work."
    ),
    "unclear": "The available evidence is insufficient or contradictory.",
    "ai_generated": (
        "The submission shows strong machine-generation signals (uniform style, "
        "LLM-style explanations, over-engineered structure for the task)."
    ),
    "not_ai": "The submission looks like ordinary student work.",
}

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICT_VALUES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "recommended_action": {"type": "string"},
    },
    "required": ["verdict", "confidence", "summary", "evidence", "recommended_action"],
    "additionalProperties": False,
}

DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [
                "wrong_answer",
                "runtime_error",
                "time_limit",
                "compile_error",
                "formatting",
                "edge_case",
                "unknown",
            ],
        },
        "diagnosis": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["category", "diagnosis", "confidence", "evidence", "suggestions"],
    "additionalProperties": False,
}

TEST_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "input": {"type": "string"},
                    "output": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["input", "output"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tests"],
    "additionalProperties": False,
}

PROJECT_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "rubric_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "points": {"type": "number"},
                    "max_points": {"type": "number"},
                    "comment": {"type": "string"},
                },
                "required": ["criterion", "comment"],
                "additionalProperties": False,
            },
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needs_human_review": {"type": "boolean"},
    },
    "required": [
        "score",
        "rubric_items",
        "strengths",
        "weaknesses",
        "evidence",
        "confidence",
        "needs_human_review",
    ],
    "additionalProperties": False,
}

#: Extra tests proposed by the model carry an expected output.  Validation in
#: :mod:`oj_sentinel.judge.generate` replaces it with the reference output when
#: accepted submissions agree, so the field is advisory, never trusted blindly.
SCHEMA_BY_TASK: dict[str, dict[str, Any]] = {
    "review_pair": VERDICT_SCHEMA,
    "analyze_authorship": VERDICT_SCHEMA,
    "diagnose_submission": DIAGNOSIS_SCHEMA,
    "generate_tests": TEST_GENERATION_SCHEMA,
    "evaluate_project": PROJECT_EVALUATION_SCHEMA,
    "evaluate_game_project": PROJECT_EVALUATION_SCHEMA,
}

__all__ = [
    "VERDICT_VALUES",
    "VERDICT_DESCRIPTIONS",
    "VERDICT_SCHEMA",
    "DIAGNOSIS_SCHEMA",
    "TEST_GENERATION_SCHEMA",
    "PROJECT_EVALUATION_SCHEMA",
    "SCHEMA_BY_TASK",
]
