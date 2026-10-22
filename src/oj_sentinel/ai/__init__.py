"""OJ Sentinel AI layer (interface section 4).

Public surface::

    from oj_sentinel.ai import build_agent, AgentConfig, review_pair

Providers: ``dsh`` (default local DeepSeek Harness), ``openai`` (any
OpenAI-compatible HTTP endpoint), ``mock`` (offline tests), ``offline``
(explicitly disabled).  See ``docs/ai-setup.md`` for the DSH overlay details.
"""
from __future__ import annotations

from .agent import (
    DEFAULT_DSH_BASE_URL,
    DEFAULT_DSH_PROFILE,
    DEFAULT_DSH_PROVIDER,
    Agent,
    AgentConfig,
    AgentResult,
    DshAgent,
    MockAgent,
    OfflineAgent,
    OpenAICompatAgent,
    available_dsh,
    build_agent,
    build_dsh_overlay,
    extract_json,
    strip_fences,
)
from .prompts import (
    SYSTEM_PROMPT,
    analyze_authorship,
    build_authorship_prompt,
    build_diagnose_prompt,
    build_evaluate_project_prompt,
    build_generate_tests_prompt,
    build_review_prompt,
    diagnose_submission,
    evaluate_game_project,
    evaluate_project,
    generate_tests,
    parse_project_evaluation,
    parse_verdict,
    review_pair,
    verdict_of,
)
from .schemas import (
    DIAGNOSIS_SCHEMA,
    PROJECT_EVALUATION_SCHEMA,
    SCHEMA_BY_TASK,
    TEST_GENERATION_SCHEMA,
    VERDICT_DESCRIPTIONS,
    VERDICT_SCHEMA,
    VERDICT_VALUES,
)

__all__ = [
    # agent
    "Agent",
    "AgentConfig",
    "AgentResult",
    "DshAgent",
    "OpenAICompatAgent",
    "MockAgent",
    "OfflineAgent",
    "build_agent",
    "build_dsh_overlay",
    "available_dsh",
    "extract_json",
    "strip_fences",
    "DEFAULT_DSH_BASE_URL",
    "DEFAULT_DSH_PROVIDER",
    "DEFAULT_DSH_PROFILE",
    # schemas
    "VERDICT_VALUES",
    "VERDICT_DESCRIPTIONS",
    "VERDICT_SCHEMA",
    "DIAGNOSIS_SCHEMA",
    "TEST_GENERATION_SCHEMA",
    "PROJECT_EVALUATION_SCHEMA",
    "SCHEMA_BY_TASK",
    # prompts + helpers
    "SYSTEM_PROMPT",
    "build_review_prompt",
    "build_diagnose_prompt",
    "build_generate_tests_prompt",
    "build_authorship_prompt",
    "build_evaluate_project_prompt",
    "review_pair",
    "diagnose_submission",
    "generate_tests",
    "analyze_authorship",
    "evaluate_project",
    "evaluate_game_project",
    "parse_verdict",
    "verdict_of",
    "parse_project_evaluation",
]
