"""Offline tests for :mod:`oj_sentinel.ai.agent`.

No network and no real provider is used: DSH is exercised through an injected
runner, and the OpenAI-compatible client is tested with a fake ``requests.post``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from oj_sentinel.ai import agent as agent_module  # noqa: E402
from oj_sentinel.ai.agent import (  # noqa: E402
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
from oj_sentinel.ai.schemas import VERDICT_SCHEMA  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    @property
    def text(self) -> str:
        return json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


def _verdict_payload(verdict: str = "unclear") -> dict:
    return {
        "verdict": verdict,
        "confidence": 0.75,
        "summary": "summary",
        "evidence": ["shared helper"],
        "recommended_action": "talk to both students",
    }


def _chat_response(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "model": "returned-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


# ---------------------------------------------------------------------------
# Config / result
# ---------------------------------------------------------------------------
def test_agent_config_defaults_match_interface():
    config = AgentConfig()
    assert config.provider == "dsh"
    assert config.model == "deepseek-v4.1-flash"
    assert config.base_url is None
    assert config.api_key_env == "OPENCODE_GO_2_API_KEY"
    assert config.timeout == 180.0
    assert config.reasoning_effort == "max"


def test_agent_result_ok_and_to_dict():
    result = AgentResult(text="x", data={"a": 1}, provider="mock", model="m", usage={})
    assert result.ok is True
    assert result.to_dict()["data"] == {"a": 1}
    failed = AgentResult("", None, "dsh", "m", {}, error="boom")
    assert failed.ok is False


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------
def test_extract_json_strips_fences_and_prose():
    fenced = 'Here you go:\n```json\n{"verdict": "unclear"}\n```\nDone.'
    assert extract_json(fenced) == {"verdict": "unclear"}
    assert extract_json('prefix {"a": [1, 2]} suffix') == {"a": [1, 2]}
    assert extract_json("no json here") is None
    assert extract_json("") is None
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------
def test_mock_agent_is_deterministic_and_records_calls():
    agent = MockAgent()
    first = agent.complete("prompt one", system="sys", json_schema=VERDICT_SCHEMA)
    second = agent.complete("prompt two", json_schema=VERDICT_SCHEMA)
    assert first.data == second.data
    assert first.data["verdict"] == "unclear"
    assert first.data["confidence"] == 0.5
    assert first.provider == "mock"
    assert len(agent.calls) == 2
    assert agent.calls[0]["system"] == "sys"
    assert agent.calls[0]["json_schema"] is VERDICT_SCHEMA
    assert agent.last_prompt == "prompt two"


def test_mock_agent_canned_fenced_response_and_invalid_json():
    fenced = (
        "```json\n"
        + json.dumps(_verdict_payload("likely_independent"))
        + "\n```"
    )
    agent = MockAgent(responses=[fenced])
    result = agent.complete("p", json_schema=VERDICT_SCHEMA)
    assert result.data["verdict"] == "likely_independent"
    assert result.error is None

    broken = MockAgent(responses=["this is not json"])
    result = broken.complete("p", json_schema=VERDICT_SCHEMA)
    assert result.data is None
    assert "JSON" in result.error


def test_mock_agent_responder_receives_schema():
    seen = {}

    def responder(prompt, system, schema):
        seen.update(prompt=prompt, system=system, schema=schema)
        return {"verdict": "ai_generated", "confidence": 0.9, "summary": "s",
                "evidence": [], "recommended_action": "review"}

    agent = MockAgent(responder=responder)
    result = agent.complete("hello", system="s", json_schema=VERDICT_SCHEMA)
    assert seen["schema"] is VERDICT_SCHEMA
    assert result.data["verdict"] == "ai_generated"


# ---------------------------------------------------------------------------
# DSH overlay and command
# ---------------------------------------------------------------------------
def test_build_dsh_overlay_declares_provider_and_default_model():
    overlay = build_dsh_overlay(AgentConfig())
    assert "opencode-go-2:" in overlay
    assert 'apiKeyEnv: "OPENCODE_GO_2_API_KEY"' in overlay
    assert "api: openai-completions" in overlay
    assert 'baseURL: "https://opencode.ai/zen/go/v1"' in overlay
    assert 'x-opencode-session: "oj-sentinel"' in overlay
    assert 'id: "deepseek-v4.1-flash"' in overlay
    assert "maxTokens: 384000" in overlay
    assert "- id: agent-default-model" in overlay
    assert 'provider: "opencode-go-2"' in overlay
    assert 'model: "deepseek-v4.1-flash"' in overlay
    assert 'reasoningEffort: "max"' in overlay
    # The shipped headless profile disables credentials; our overlay re-enables.
    assert "- id: credentials" in overlay
    assert "disabled: false" in overlay
    # Never write a secret-looking value into the overlay.
    assert "sk-" not in overlay


def test_build_dsh_overlay_respects_config():
    overlay = build_dsh_overlay(
        AgentConfig(model="other-model", api_key_env="OTHER_KEY", dsh_session="session-x",
                    dsh_base_url="http://127.0.0.1:1234/v1", reasoning_effort="high")
    )
    assert '"other-model"' in overlay
    assert '"OTHER_KEY"' in overlay
    assert '"session-x"' in overlay
    assert '"http://127.0.0.1:1234/v1"' in overlay
    assert 'high: "high"' in overlay


def test_dsh_agent_invokes_headless_profile_and_parses_json(tmp_path):
    captured: dict[str, object] = {}

    def runner(command, prompt, timeout):
        captured["command"] = command
        captured["prompt"] = prompt
        captured["timeout"] = timeout
        overlay_path = Path(command[command.index("--patch") + 1])
        captured["overlay"] = overlay_path.read_text(encoding="utf-8")
        return 0, json.dumps(_verdict_payload()), ""

    agent = DshAgent(AgentConfig(timeout=12), runner=runner)
    result = agent.complete("Review this pair", system="SYSTEM", json_schema=VERDICT_SCHEMA)
    command = captured["command"]
    assert command[:4] == ["dsh", "--profile", "headless", "--patch"]
    assert command[-1] == "-"
    assert captured["timeout"] == 12
    assert "SYSTEM" in captured["prompt"]
    assert "Review this pair" in captured["prompt"]
    assert "opencode-go-2" in captured["overlay"]
    assert result.provider == "dsh"
    assert result.data["verdict"] == "unclear"
    assert result.error is None


def test_dsh_agent_failure_falls_back_to_openai(monkeypatch):
    monkeypatch.setenv("OJS_TEST_KEY", "sekret-value-12345")
    calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(_chat_response(json_module.dumps(_verdict_payload("plausible_plagiarism"))))

    json_module = json
    monkeypatch.setattr(agent_module.requests, "post", fake_post)

    config = AgentConfig(
        provider="dsh",
        base_url="http://example.invalid/v1",
        api_key_env="OJS_TEST_KEY",
        timeout=5,
    )
    agent = DshAgent(config, runner=lambda *args: (1, "", "MISSING_CREDENTIAL: no key"))

    result = agent.complete("p", json_schema=VERDICT_SCHEMA)
    assert result.provider == "openai"
    assert result.data["verdict"] == "plausible_plagiarism"
    assert result.usage["fallback_from"] == "dsh"
    assert "dsh_error" in result.usage
    assert len(calls) == 1
    assert calls[0]["url"] == "http://example.invalid/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sekret-value-12345"
    # The API key must never leak into a result rendered by the GUI.
    assert "sekret-value-12345" not in json.dumps(result.to_dict())


def test_dsh_agent_without_fallback_returns_redacted_error(monkeypatch):
    monkeypatch.setenv("OJS_TEST_KEY", "sekret-value-12345")
    agent = DshAgent(
        AgentConfig(api_key_env="OJS_TEST_KEY"),
        runner=lambda *args: (2, "", "auth failed for sekret-value-12345"),
    )
    result = agent.complete("p")
    assert result.provider == "dsh"
    assert result.data is None
    assert result.error is not None
    assert "dsh exited with status 2" in result.error
    assert "sekret-value-12345" not in result.error
    assert "<redacted>" in json.dumps(result.to_dict())


def test_dsh_agent_empty_output_falls_back_or_errors():
    agent = DshAgent(AgentConfig(), runner=lambda *args: (0, "   ", ""))
    result = agent.complete("p")
    assert result.data is None
    assert "empty output" in result.error


def test_available_dsh_returns_bool():
    assert isinstance(available_dsh("definitely-not-a-real-dsh-binary"), bool)
    assert available_dsh("definitely-not-a-real-dsh-binary") is False


# ---------------------------------------------------------------------------
# OpenAI-compatible client
# ---------------------------------------------------------------------------
def test_openai_compat_posts_schema_payload(monkeypatch):
    monkeypatch.setenv("OJS_TEST_KEY", "sekret-value-12345")
    calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(_chat_response(json_module.dumps(_verdict_payload("unclear"))))

    json_module = json
    monkeypatch.setattr(agent_module.requests, "post", fake_post)
    agent = OpenAICompatAgent(
        AgentConfig(
            provider="openai",
            base_url="http://localhost:9999/v1/",
            api_key_env="OJS_TEST_KEY",
            model="test-model",
            timeout=9,
        )
    )
    result = agent.complete("hello", system="sys", json_schema=VERDICT_SCHEMA)
    assert calls[0]["url"] == "http://localhost:9999/v1/chat/completions"
    assert calls[0]["timeout"] == 9
    assert calls[0]["headers"]["Authorization"] == "Bearer sekret-value-12345"
    payload = calls[0]["json"]
    assert payload["model"] == "test-model"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    assert payload["messages"][1] == {"role": "user", "content": "hello"}
    assert result.data["verdict"] == "unclear"
    assert result.usage["total_tokens"] == 15
    assert result.model == "returned-model"
    assert "sekret-value-12345" not in json.dumps(result.to_dict())


def test_openai_compat_missing_key_does_not_call_http(monkeypatch):
    monkeypatch.delenv("OJS_ABSENT_KEY", raising=False)
    called = []
    monkeypatch.setattr(
        agent_module.requests, "post", lambda *a, **k: called.append(1) or FakeResponse({})
    )
    agent = OpenAICompatAgent(
        AgentConfig(provider="openai", base_url="http://localhost/v1", api_key_env="OJS_ABSENT_KEY")
    )
    result = agent.complete("p", json_schema=VERDICT_SCHEMA)
    assert result.data is None
    assert "OJS_ABSENT_KEY" in result.error
    assert called == []


def test_openai_compat_missing_base_url_errors():
    agent = OpenAICompatAgent(AgentConfig(provider="openai", base_url=None))
    result = agent.complete("p")
    assert result.data is None
    assert "base_url" in result.error


def test_openai_compat_retries_with_json_object(monkeypatch):
    monkeypatch.setenv("OJS_TEST_KEY", "x" * 12)
    calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"json": json})
        if len(calls) == 1:
            return FakeResponse({"error": "schema unsupported"}, status_code=400)
        return FakeResponse(_chat_response(json_module.dumps(_verdict_payload("not_ai"))))

    json_module = json
    monkeypatch.setattr(agent_module.requests, "post", fake_post)
    agent = OpenAICompatAgent(
        AgentConfig(
            provider="openai", base_url="http://localhost/v1", api_key_env="OJS_TEST_KEY"
        )
    )
    result = agent.complete("p", json_schema=VERDICT_SCHEMA)
    assert len(calls) == 2
    assert calls[1]["json"]["response_format"]["type"] == "json_object"
    assert result.data["verdict"] == "not_ai"


def test_openai_compat_http_error_is_reported(monkeypatch):
    monkeypatch.setenv("OJS_TEST_KEY", "x" * 12)
    monkeypatch.setattr(
        agent_module.requests,
        "post",
        lambda *a, **k: FakeResponse({"error": "nope"}, status_code=500),
    )
    agent = OpenAICompatAgent(
        AgentConfig(provider="openai", base_url="http://localhost/v1", api_key_env="OJS_TEST_KEY")
    )
    result = agent.complete("p")
    assert result.data is None
    assert "HTTP 500" in result.error


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_build_agent_factory():
    assert isinstance(build_agent(AgentConfig(provider="mock")), MockAgent)
    assert isinstance(build_agent(AgentConfig(provider="openai")), OpenAICompatAgent)
    assert isinstance(build_agent(AgentConfig(provider="dsh")), DshAgent)
    offline = build_agent(AgentConfig(provider="offline"))
    assert isinstance(offline, OfflineAgent)
    assert "disabled" in offline.complete("p").error.lower()
    with pytest.raises(ValueError):
        build_agent(AgentConfig(provider="nope"))
