"""Offline tests for prompt builders and AI task helpers.

All provider calls go through :class:`oj_sentinel.ai.agent.MockAgent`; nothing
leaves the machine.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from oj_sentinel.ai import (  # noqa: E402
    DIAGNOSIS_SCHEMA,
    PROJECT_EVALUATION_SCHEMA,
    SYSTEM_PROMPT,
    TEST_GENERATION_SCHEMA,
    VERDICT_SCHEMA,
    MockAgent,
    analyze_authorship,
    diagnose_submission,
    evaluate_game_project,
    evaluate_project,
    extract_json,
    generate_tests,
    parse_project_evaluation,
    parse_verdict,
    review_pair,
)

PROBLEM = {
    "key": "HW2001",
    "title": "Basic Functions",
    "description_text": "Implement is_even(n) and sum_odd(a, b).",
    "time_limit": "1s",
    "memory_limit": "64MB",
}

SUB_A = {
    "rid": "ridA",
    "uid": 11,
    "language": "Python 3",
    "code": "def helper(x):\n    return x * 42  # MARKER_A\n",
}

SUB_B = {
    "rid": "ridB",
    "uid": 22,
    "language": "Python 3",
    "code": "def helper2(x):\n    return x * 42  # MARKER_B\n",
}

PAIR = {
    "problem": "HW2001",
    "rid_a": "ridA",
    "rid_b": "ridB",
    "score": 0.91,
    "jaccard": 0.8,
    "containment": 0.95,
    "ast_cosine": 0.7,
    "shared": 12,
    "size_a": 14,
    "size_b": 15,
    "same_user": False,
    "exact": False,
    "method": "winnowing+ast",
    "evidence": [{"snippet": "return x * 42", "lines": 1}],
}


def test_review_pair_uses_verdict_schema_and_includes_inputs():
    agent = MockAgent()
    result = review_pair(agent, PROBLEM, SUB_A, SUB_B, PAIR)
    prompt = agent.calls[0]["prompt"]
    assert agent.calls[0]["json_schema"] is VERDICT_SCHEMA
    assert agent.calls[0]["system"] == SYSTEM_PROMPT
    assert "Basic Functions" in prompt
    assert "MARKER_A" in prompt and "MARKER_B" in prompt
    assert "winnowing+ast" in prompt
    assert "return x * 42" in prompt
    assert "plausible_plagiarism" in prompt
    assert result.error is None
    assert result.data["verdict"] == "unclear"
    assert isinstance(result.data["evidence"], list)
    verdict = parse_verdict(result.data)
    assert verdict is not None
    assert 0 <= verdict["confidence"] <= 1


def test_review_pair_handles_model_objects_with_to_dict():
    class Model:
        def __init__(self, payload):
            self._payload = payload

        def to_dict(self):
            return dict(self._payload)

    agent = MockAgent()
    result = review_pair(agent, Model(PROBLEM), Model(SUB_A), Model(SUB_B), Model(PAIR))
    assert result.data["verdict"] == "unclear"
    assert "MODEL" not in agent.calls[0]["prompt"]  # plain dicts, not reprs


def test_generate_tests_helper():
    canned = {
        "tests": [
            {"name": "zero", "input": "0 0\n", "output": "0\n", "rationale": "empty sum"},
            {"name": "neg", "input": "-1 1\n", "output": "0\n"},
        ]
    }
    agent = MockAgent(responses=[canned])
    result = generate_tests(agent, PROBLEM, n=2)
    assert agent.calls[0]["json_schema"] is TEST_GENERATION_SCHEMA
    assert "at most 2" in agent.calls[0]["prompt"]
    assert result.data == canned
    assert len(result.data["tests"]) == 2


def test_diagnose_submission_contains_failing_case_details():
    run_result = {
        "status": "Wrong Answer",
        "score": 50.0,
        "stdout": "0\n",
        "stderr": "",
        "cases": [
            {
                "name": "1-1",
                "status": "AC",
                "time_ms": 12,
                "memory_kb": 100,
                "message": "",
            },
            {
                "name": "1-2",
                "status": "WA",
                "time_ms": 14,
                "memory_kb": 100,
                "message": "first mismatch at output line 1: expected '3', got '0'",
            },
        ],
    }
    agent = MockAgent()
    result = diagnose_submission(agent, PROBLEM, SUB_A, run_result)
    prompt = agent.calls[0]["prompt"]
    assert agent.calls[0]["json_schema"] is DIAGNOSIS_SCHEMA
    assert "first mismatch" in prompt
    assert "expected '3'" in prompt
    assert "MARKER_A" in prompt
    assert "Implement is_even" in prompt
    assert result.data["category"] == "unknown"
    assert isinstance(result.data["suggestions"], list)


def test_analyze_authorship_includes_all_submissions():
    submissions = [
        dict(SUB_A, rid="rid1", code="# FIRST\nprint(1)\n"),
        dict(SUB_A, rid="rid2", code="# SECOND\nprint(2)\n"),
    ]
    agent = MockAgent()
    result = analyze_authorship(agent, PROBLEM, submissions)
    prompt = agent.calls[0]["prompt"]
    assert agent.calls[0]["json_schema"] is VERDICT_SCHEMA
    assert "rid1" in prompt and "rid2" in prompt
    assert "FIRST" in prompt and "SECOND" in prompt
    assert "ai_generated" in prompt
    assert result.data["verdict"] in {"unclear", "ai_generated", "not_ai"}


def test_helpers_turn_agent_exceptions_into_errors():
    class ExplodingAgent:
        config = type("Config", (), {"provider": "mock", "model": "m"})()

        def complete(self, *args, **kwargs):
            raise RuntimeError("provider exploded")

    result = review_pair(ExplodingAgent(), PROBLEM, SUB_A, SUB_B, PAIR)
    assert result.data is None
    assert "provider exploded" in result.error


def test_parse_verdict_clamps_and_validates():
    parsed = parse_verdict(
        {"verdict": "ai_generated", "confidence": 7, "summary": "s",
         "evidence": "single string", "recommended_action": "review"}
    )
    assert parsed["confidence"] == 1.0
    assert parsed["evidence"] == ["single string"]
    assert parse_verdict({"verdict": "not-a-verdict"}) is None
    assert parse_verdict(None) is None


def test_extract_json_handles_array_and_nested_fences():
    assert extract_json('```\n[1, 2, 3]\n```') == [1, 2, 3]
    text = 'noise {"tests": [{"input": "1"}]} trailing {"other": 2}'
    assert extract_json(text) == {"tests": [{"input": "1"}]}


# ---------------------------------------------------------------------------
# Open-ended / game project grading (advisory)
# ---------------------------------------------------------------------------
def test_evaluate_project_returns_advisory_rubric():
    payload = {
        "score": 87.5,
        "rubric_items": [
            {"criterion": "Playable", "points": 50, "max_points": 50,
             "comment": "Runs and completes a game."},
            {"criterion": "Code quality", "points": 37.5, "max_points": 50,
             "comment": "Readable, but no tests."},
        ],
        "strengths": ["Complete game loop"],
        "weaknesses": ["No error handling"],
        "evidence": ["game.py: main loop at line 42"],
        "confidence": 0.66,
        "needs_human_review": True,
    }
    agent = MockAgent(responses=[payload])
    result = evaluate_project(
        agent,
        {"title": "Snake", "description_text": "Build a small Snake game."},
        {"game.py": "def main():\n    pass\n", "README.md": "Snake"},
        ["Game is playable", {"criterion": "Code quality", "max_points": 50}],
        {"status": "not auto-graded", "manual_notes": "demoed in class"},
    )
    prompt = agent.calls[0]["prompt"]
    assert agent.calls[0]["json_schema"] is PROJECT_EVALUATION_SCHEMA
    assert "Build a small Snake game" in prompt
    assert "game.py" in prompt
    assert "Game is playable" in prompt
    assert "advisory" in prompt.lower()
    assert "needs_human_review" in prompt
    assert result.data["score"] == 87.5
    parsed = parse_project_evaluation(result.data)
    assert parsed["score"] == 87.5
    assert parsed["needs_human_review"] is True
    assert len(parsed["rubric_items"]) == 2


def test_evaluate_game_project_mentions_screenshots_but_not_pixels():
    agent = MockAgent()
    result = evaluate_game_project(
        agent,
        "Build a platformer.",
        {"main.py": "print('game')"},
        [{"criterion": "Visual polish", "max_points": 20}],
        {"status": "ran 30s"},
        screenshots=[{"name": "title.png", "caption": "Title screen with score"},
                     {"name": "level1.png", "caption": "Player standing on a platform"}],
        logs=["INFO loaded level 1", "ERROR missing asset jump.wav"],
    )
    prompt = agent.calls[0]["prompt"]
    assert "cannot see their pixels" in prompt
    assert "title.png" in prompt and "Title screen with score" in prompt
    assert "missing asset jump.wav" in prompt
    assert "Advisory" in prompt or "advisory" in prompt
    assert agent.calls[0]["json_schema"] is PROJECT_EVALUATION_SCHEMA
    # MockAgent's schema-aware default is deliberately conservative.
    assert result.data["needs_human_review"] is True
    assert result.data["score"] == 0.0


def test_parse_project_evaluation_clamps_values():
    parsed = parse_project_evaluation(
        {
            "score": 140,
            "confidence": -2,
            "needs_human_review": False,
            "rubric_items": [{"criterion": "x", "points": "5", "max_points": 10,
                              "comment": "ok"}],
            "strengths": "one",
            "weaknesses": [],
            "evidence": ["e"],
        }
    )
    assert parsed["score"] == 100.0
    assert parsed["confidence"] == 0.0
    assert parsed["strengths"] == ["one"]
    assert parsed["rubric_items"][0]["points"] == 5.0
    assert parse_project_evaluation("nope") is None
