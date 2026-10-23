"""AI provider abstraction.

The default provider is :class:`DshAgent`, which boots the local DeepSeek
Harness headless profile with a generated patch overlay.  Student code is only
sent when a teacher explicitly runs an AI action; nothing here uploads data on
import or during local analysis.

Provider matrix
---------------
``dsh``      local ``dsh`` CLI, falling back to an OpenAI-compatible endpoint
``openai``   any OpenAI-compatible ``/chat/completions`` server
``mock``     deterministic canned JSON, used by the offline test-suite
``offline``  explicit "AI disabled" marker
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from .dsh_discovery import discover_dsh, resolve_dsh
from .schemas import VERDICT_SCHEMA

DEFAULT_DSH_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_DSH_PROVIDER = "opencode-go-2"
DEFAULT_DSH_SESSION = "oj-sentinel"
DEFAULT_DSH_PROFILE = "headless"
DEFAULT_MAX_TOKENS = 384000
DEFAULT_CONTEXT_WINDOW = 1_000_000

_JSON_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class AgentConfig:
    """Provider configuration (interface section 4).

    Extra fields at the end are optional tuning knobs; every default keeps the
    documented behaviour byte-for-byte compatible.
    """

    provider: str = "dsh"
    model: str = "deepseek-v4.1-flash"
    base_url: str | None = None
    api_key_env: str = "OPENCODE_GO_2_API_KEY"
    timeout: float = 180.0
    reasoning_effort: str = "max"
    # -- DSH specifics ------------------------------------------------------
    dsh_command: str = "dsh"
    dsh_profile: str = DEFAULT_DSH_PROFILE
    dsh_provider: str = DEFAULT_DSH_PROVIDER
    dsh_base_url: str = DEFAULT_DSH_BASE_URL
    dsh_session: str = DEFAULT_DSH_SESSION
    max_tokens: int = DEFAULT_MAX_TOKENS
    context_window: int = DEFAULT_CONTEXT_WINDOW
    use_credentials_service: bool = True


@dataclass
class AgentResult:
    """Normalised result returned by every provider and task helper."""

    text: str
    data: dict[str, Any] | None
    provider: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "data": self.data,
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage,
            "error": self.error,
        }


class Agent:
    """Base class: one blocking completion call, optionally JSON-schema'd."""

    config: AgentConfig

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> AgentResult:  # pragma: no cover - interface only
        raise NotImplementedError


# ---------------------------------------------------------------------------
# JSON extraction (robust against markdown fences and chatty preambles)
# ---------------------------------------------------------------------------
def extract_json(text: str | None) -> Any | None:
    """Return the first JSON object/array embedded in *text*, or ``None``.

    Handles the usual LLM failure modes: markdown code fences, a sentence
    before the JSON, and trailing commentary after it.  Only complete values
    are returned; a partial ``{"a": 1`` is ignored.
    """

    if not text:
        return None
    raw = text.lstrip("\ufeff").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        if isinstance(value, (dict, list)):
            return value
    except ValueError:
        pass

    candidates: list[str] = []
    for match in _JSON_FENCE.finditer(raw):
        candidates.append(match.group(1).strip())
    candidates.append(raw)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        for opener in ("{", "["):
            index = candidate.find(opener)
            while index != -1:
                try:
                    value, _end = decoder.raw_decode(candidate[index:])
                except ValueError:
                    value = None
                if isinstance(value, (dict, list)):
                    return value
                index = candidate.find(opener, index + 1)
    return None


def strip_fences(text: str) -> str:
    """Remove a single surrounding markdown fence, if present."""

    match = _JSON_FENCE.search(text or "")
    if match and (text or "").lstrip().startswith("```"):
        return match.group(1).strip()
    return (text or "").strip()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _schema_name(schema: dict[str, Any] | None) -> str:
    if schema is VERDICT_SCHEMA:
        return "oj_sentinel_verdict"
    properties = (schema or {}).get("properties") or {}
    if "verdict" in properties:
        return "oj_sentinel_verdict"
    if "tests" in properties:
        return "oj_sentinel_tests"
    if "category" in properties:
        return "oj_sentinel_diagnosis"
    return "oj_sentinel_result"


def _json_instruction(json_schema: dict[str, Any] | None) -> str:
    if not json_schema:
        return ""
    return (
        "Return exactly one JSON object that validates against this JSON Schema. "
        "Do not wrap it in markdown fences and do not add prose before or after:\n"
        + json.dumps(json_schema, ensure_ascii=False, separators=(",", ":"))
    )


def _compose_prompt(
    prompt: str, system: str | None, json_schema: dict[str, Any] | None
) -> str:
    parts: list[str] = []
    if system:
        parts.append(system.strip())
    parts.append((prompt or "").strip())
    instruction = _json_instruction(json_schema)
    if instruction:
        parts.append(instruction)
    return "\n\n".join(part for part in parts if part)


def _redact(text: str | None, config: AgentConfig | None = None) -> str:
    """Strip a known API key from diagnostics before they reach logs/GUI.

    ``config`` is duck-typed on purpose: callbacks/tests may pass any object
    with ``api_key_env``, or a plain mapping, and diagnostics must never crash
    because a provider object is unusual.
    """

    if not text:
        return ""
    out = str(text)
    api_key_env = ""
    if config is not None:
        api_key_env = getattr(config, "api_key_env", "") or ""
        if not api_key_env and isinstance(config, Mapping):
            api_key_env = str(config.get("api_key_env") or "")
    if api_key_env:
        secret = os.environ.get(str(api_key_env), "")
        if secret and len(secret) >= 8:
            out = out.replace(secret, "<redacted>")
    return out


def _last_line(text: str | None, limit: int = 400) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:limit]


def _yaml_key(value: str) -> str:
    value = str(value)
    if re.fullmatch(r"[A-Za-z0-9_.\-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


# ---------------------------------------------------------------------------
# DSH
# ---------------------------------------------------------------------------
def build_dsh_overlay(config: AgentConfig | None = None) -> str:
    """Build the temporary DSH patch overlay for the configured route.

    The overlay adds the ``opencode-go-2`` provider and makes
    ``deepseek-v4.1-flash`` (reasoning effort ``max``) the agent default.  It
    also re-enables the local credentials service: the shipped ``headless``
    profile disables it, which otherwise prevents ``apiKeyEnv`` from resolving
    keys already stored by DSH Desktop.  No secret is ever written into the
    overlay -- only the environment variable *name*.
    """

    cfg = config or AgentConfig()
    provider = cfg.dsh_provider or DEFAULT_DSH_PROVIDER
    model = cfg.model
    session = cfg.dsh_session or DEFAULT_DSH_SESSION
    base_url = cfg.dsh_base_url or DEFAULT_DSH_BASE_URL
    lines: list[str] = []
    if cfg.use_credentials_service:
        lines += [
            "# Let the headless profile resolve API keys stored by DSH Desktop.",
            "- id: credentials",
            "  name: '@deepseek-ai/dsh-credentials-local'",
            "  disabled: false",
        ]
    lines += [
        "- id: llm-pi-ai",
        "  name: '@deepseek-ai/dsh-llm-pi-ai'",
        "  config:",
        "    providers:",
        f"      {_yaml_key(provider)}:",
        "        displayName: OpenCode Go 2",
        f"        apiKeyEnv: {_yaml_value(cfg.api_key_env)}",
        "        api: openai-completions",
        f"        baseURL: {_yaml_value(base_url)}",
        "        headers:",
        f"          x-opencode-session: {_yaml_value(session)}",
        "        models:",
        f"          - id: {_yaml_value(model)}",
        f"            name: DeepSeek V4.1 Flash",
        f"            contextWindow: {int(cfg.context_window)}",
        f"            maxTokens: {int(cfg.max_tokens)}",
        "            reasoningEfforts:",
        f"              {_yaml_key(cfg.reasoning_effort)}: {_yaml_value(cfg.reasoning_effort)}",
        "- id: agent-default-model",
        "  name: '@deepseek-ai/dsh-agent-default-model'",
        "  config:",
        f"    provider: {_yaml_value(provider)}",
        f"    model: {_yaml_value(model)}",
        f"    reasoningEffort: {_yaml_value(cfg.reasoning_effort)}",
    ]
    return "\n".join(lines) + "\n"


def available_dsh(command: str = "dsh") -> bool:
    """True when the given command or an auto-discovered dsh is usable."""
    value = (command or "dsh").strip()
    if value and value not in {"dsh", "dsh.exe", "dsh.cmd"}:
        return shutil.which(value) is not None or Path(value).expanduser().exists()
    return bool(resolve_dsh("dsh"))


class DshAgent(Agent):
    """Run one task through ``dsh --profile headless --patch <overlay> -``."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        fallback: Agent | None = None,
        runner: Callable[[list[str], str, float], tuple[int, str, str]] | None = None,
    ) -> None:
        self.config = config or AgentConfig(provider="dsh")
        self.fallback = fallback
        #: Last command used, exposed for transparent AI traces.
        self.last_command: list[str] = []
        #: Test seam: ``runner(cmd, prompt, timeout) -> (returncode, stdout, stderr)``.
        self._runner = runner

    # -- public ---------------------------------------------------------
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> AgentResult:
        cfg = self.config
        composed = _compose_prompt(prompt, system, json_schema)
        overlay = build_dsh_overlay(cfg)
        started = time.perf_counter()
        tmpdir = tempfile.mkdtemp(prefix="ojs-dsh-overlay-")
        try:
            overlay_path = Path(tmpdir) / "overlay.yml"
            overlay_path.write_text(overlay, encoding="utf-8")
            command_path = cfg.dsh_command or "dsh"
            if self._runner is None:
                resolved = resolve_dsh(command_path)
                if not resolved:
                    info = discover_dsh()
                    return self._fallback_result(
                        prompt, system, json_schema,
                        "找不到 dsh 后端。"
                        + (f" 自动检测结果：{info.error}" if info.error else "")
                        + " 请在 Web「模型设置」中指定 dsh 路径，或设置 OJS_DSH_COMMAND。",
                    )
                command_path = resolved
            command = [
                command_path,
                "--profile",
                cfg.dsh_profile or DEFAULT_DSH_PROFILE,
                "--patch",
                str(overlay_path),
                "-",
            ]
            self.last_command = list(command)
            try:
                if self._runner is not None:
                    returncode, stdout, stderr = self._runner(
                        command, composed, cfg.timeout
                    )
                else:
                    proc = subprocess.run(
                        command,
                        input=composed,
                        text=True,
                        capture_output=True,
                        timeout=cfg.timeout,
                        env=os.environ.copy(),
                    )
                    returncode, stdout, stderr = (
                        proc.returncode,
                        proc.stdout or "",
                        proc.stderr or "",
                    )
            except FileNotFoundError:
                return self._fallback_result(
                    prompt, system, json_schema,
                    f"dsh executable not found: {cfg.dsh_command or 'dsh'}",
                )
            except subprocess.TimeoutExpired:
                return self._fallback_result(
                    prompt, system, json_schema,
                    f"dsh timed out after {float(cfg.timeout):.0f}s",
                )
            except OSError as exc:
                return self._fallback_result(
                    prompt, system, json_schema,
                    f"dsh could not start: {type(exc).__name__}",
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if returncode != 0:
            reason = _redact(_last_line(stderr) or "no stderr", cfg)
            return self._fallback_result(
                prompt, system, json_schema,
                f"dsh exited with status {returncode}: {reason}",
            )
        text = (stdout or "").strip()
        if not text:
            return self._fallback_result(
                prompt, system, json_schema, "dsh returned empty output"
            )
        parsed = extract_json(text)
        data = parsed if isinstance(parsed, dict) else None
        error = None
        if json_schema is not None and data is None:
            error = "dsh: model response was not a valid JSON object"
        return AgentResult(
            text=text,
            data=data,
            provider="dsh",
            model=cfg.model,
            usage={"elapsed_ms": elapsed_ms, "profile": cfg.dsh_profile},
            error=error,
        )

    # -- internals ------------------------------------------------------
    def _fallback_agent(self) -> Agent | None:
        if self.fallback is not None:
            return self.fallback
        cfg = self.config
        if cfg.base_url and os.environ.get(cfg.api_key_env):
            return OpenAICompatAgent(replace(cfg, provider="openai"))
        return None

    def _fallback_result(
        self,
        prompt: str,
        system: str | None,
        json_schema: dict[str, Any] | None,
        reason: str,
    ) -> AgentResult:
        cfg = self.config
        reason = _redact(reason, cfg)
        fallback = self._fallback_agent()
        if fallback is None:
            return AgentResult(
                text="",
                data=None,
                provider="dsh",
                model=cfg.model,
                usage={"fallback_attempted": False},
                error=(
                    f"dsh unavailable ({reason}); no OpenAI-compatible fallback "
                    "configured (set base_url and the api key env var, or install dsh)"
                ),
            )
        try:
            result = fallback.complete(prompt, system=system, json_schema=json_schema)
        except Exception as exc:  # noqa: BLE001 - providers must not crash callers
            return AgentResult(
                text="",
                data=None,
                provider="openai",
                model=cfg.model,
                usage={"fallback_from": "dsh", "dsh_error": reason},
                error=f"fallback provider failed: {type(exc).__name__}: {_redact(str(exc), cfg)}",
            )
        result.usage = {
            **(result.usage or {}),
            "fallback_from": "dsh",
            "dsh_error": reason,
        }
        return result


# ---------------------------------------------------------------------------
# OpenAI-compatible HTTP
# ---------------------------------------------------------------------------
class OpenAICompatAgent(Agent):
    """Minimal OpenAI-compatible chat-completions client (``requests`` only)."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig(provider="openai")

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> AgentResult:
        cfg = self.config
        base_url = (cfg.base_url or "").strip().rstrip("/")
        if not base_url:
            return AgentResult(
                "", None, "openai", cfg.model, {},
                error="openai-compatible provider requires base_url",
            )
        api_key = os.environ.get(cfg.api_key_env, "")
        if not api_key:
            return AgentResult(
                "", None, "openai", cfg.model, {},
                error=f"missing API key: environment variable {cfg.api_key_env} is not set",
            )
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(json_schema),
                    "strict": True,
                    "schema": json_schema,
                },
            }
        url = base_url + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=cfg.timeout
            )
        except requests.RequestException as exc:
            return AgentResult(
                "", None, "openai", cfg.model, {},
                error=f"request failed: {type(exc).__name__}",
            )

        # Some compatible servers only implement ``json_object``.  Retry once
        # when the strict schema request is rejected.
        if response.status_code >= 400 and json_schema is not None:
            retry_payload = dict(payload)
            retry_messages = [dict(message) for message in messages]
            retry_messages[-1]["content"] += (
                "\n\nReturn a single JSON object with the requested fields. "
                "No markdown fences, no prose."
            )
            retry_payload["messages"] = retry_messages
            retry_payload["response_format"] = {"type": "json_object"}
            try:
                response = requests.post(
                    url, json=retry_payload, headers=headers, timeout=cfg.timeout
                )
            except requests.RequestException as exc:
                return AgentResult(
                    "", None, "openai", cfg.model, {},
                    error=f"request failed: {type(exc).__name__}",
                )

        if response.status_code >= 400:
            body = _redact((response.text or "")[:500], cfg)
            return AgentResult(
                "", None, "openai", cfg.model, {"status": response.status_code},
                error=f"HTTP {response.status_code}: {body}",
            )
        try:
            payload_data = response.json()
        except ValueError:
            return AgentResult(
                "", None, "openai", cfg.model, {"status": response.status_code},
                error="response body was not JSON",
            )
        choices = payload_data.get("choices") or []
        message = choices[0].get("message") if choices else None
        content = _content_text((message or {}).get("content"))
        usage = payload_data.get("usage")
        parsed = extract_json(content)
        data = parsed if isinstance(parsed, dict) else None
        error = None
        if json_schema is not None and data is None:
            error = "openai: model response was not a valid JSON object"
        return AgentResult(
            text=content,
            data=data,
            provider=cfg.provider or "openai",
            model=payload_data.get("model") or cfg.model,
            usage=usage if isinstance(usage, dict) else {},
            error=error,
        )


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# Mock / offline
# ---------------------------------------------------------------------------
def _default_for_schema(json_schema: dict[str, Any] | None) -> Any:
    properties = (json_schema or {}).get("properties") or {}
    if "verdict" in properties:
        return {
            "verdict": "unclear",
            "confidence": 0.5,
            "summary": "mock verdict",
            "evidence": [],
            "recommended_action": "manual review",
        }
    if "tests" in properties:
        return {
            "tests": [
                {"name": "mock-1", "input": "1 2\n", "output": "3\n", "rationale": "mock"}
            ]
        }
    if "category" in properties:
        return {
            "category": "unknown",
            "diagnosis": "mock diagnosis",
            "confidence": 0.5,
            "evidence": [],
            "suggestions": [],
        }
    if "score" in properties and "rubric_items" in properties:
        return {
            "score": 0.0,
            "rubric_items": [],
            "strengths": [],
            "weaknesses": [],
            "evidence": [],
            "confidence": 0.0,
            "needs_human_review": True,
        }
    return {"ok": True}


class MockAgent(Agent):
    """Deterministic agent for tests, CI and UI demos.

    ``responses`` is a FIFO queue of canned strings/dicts.  When exhausted, the
    optional ``responder(prompt, system, json_schema)`` callable is used, then
    a schema-aware default.  Every call is recorded in :attr:`calls`.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        responses: list[Any] | None = None,
        responder: Callable[[str, str | None, dict[str, Any] | None], Any] | None = None,
        provider: str = "mock",
        model: str = "mock",
    ) -> None:
        self.config = config or AgentConfig(provider=provider, model=model)
        self.config.provider = provider
        self.config.model = model
        self._responses: list[Any] = list(responses or [])
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    @property
    def last_prompt(self) -> str:
        return self.calls[-1]["prompt"] if self.calls else ""

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> AgentResult:
        self.calls.append(
            {"prompt": prompt, "system": system, "json_schema": json_schema}
        )
        item: Any = None
        if self._responses:
            item = self._responses.pop(0)
        elif self.responder is not None:
            item = self.responder(prompt, system, json_schema)
        if item is None:
            item = _default_for_schema(json_schema)
        if isinstance(item, dict):
            data: dict[str, Any] | None = item
            text = json.dumps(item, ensure_ascii=False)
        elif isinstance(item, list):
            data, text = None, json.dumps(item, ensure_ascii=False)
        else:
            text = str(item)
            parsed = extract_json(text)
            data = parsed if isinstance(parsed, dict) else None
        error = None
        if json_schema is not None and data is None:
            error = "mock: canned response was not a valid JSON object"
        return AgentResult(
            text=text,
            data=data,
            provider="mock",
            model=self.config.model,
            usage={"mock": True},
            error=error,
        )


class OfflineAgent(Agent):
    """Explicit "AI disabled" provider: never contacts anything."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig(provider="offline")

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> AgentResult:
        return AgentResult(
            text="",
            data=None,
            provider="offline",
            model=self.config.model,
            usage={},
            error="AI is disabled (provider=offline)",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_agent(config: AgentConfig | None = None) -> Agent:
    """Create the provider named by ``config.provider``."""

    cfg = config or AgentConfig()
    provider = (cfg.provider or "dsh").strip().lower()
    if provider in {"dsh", "dsh-cli", "deepseek-harness"}:
        return DshAgent(cfg)
    if provider in {"openai", "openai-compatible", "openai_compatible", "http", "opencode"}:
        return OpenAICompatAgent(cfg)
    if provider in {"mock", "test"}:
        return MockAgent(cfg)
    if provider in {"offline", "none", "disabled"}:
        return OfflineAgent(cfg)
    raise ValueError(f"unknown AI provider: {cfg.provider!r}")


__all__ = [
    "AgentConfig",
    "AgentResult",
    "Agent",
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
]
