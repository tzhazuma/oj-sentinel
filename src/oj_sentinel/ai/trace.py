"""Transparent AI call tracing for the GUI and exports.

Only local diagnostics: prompts may contain student code, so traces are written
under ``data/ai-traces`` and never uploaded automatically.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import Agent, AgentResult


@dataclass
class TraceCall:
    index: int
    started_at: str
    duration_ms: int
    provider: str = ""
    model: str = ""
    command: list[str] = field(default_factory=list)
    prompt: str = ""
    system: str = ""
    json_schema: Any = None
    ok: bool = False
    raw_text: str = ""
    parsed: Any = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    stream_text: str = ""
    stream_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "provider": self.provider,
            "model": self.model,
            "command": self.command,
            "prompt": self.prompt,
            "system": self.system,
            "json_schema": self.json_schema,
            "ok": self.ok,
            "raw_text": self.raw_text,
            "parsed": self.parsed,
            "error": self.error,
            "usage": self.usage,
            "stream_text": self.stream_text,
            "stream_events": self.stream_events[-200:],
        }


@dataclass
class TraceRecorder:
    task: str
    meta: dict[str, Any] = field(default_factory=dict)
    calls: list[TraceCall] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    done: bool = False
    on_event: Any | None = None

    def wrap(self, agent: Agent) -> "TracedAgent":
        return TracedAgent(agent, self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "meta": self.meta,
            "started_at": self.started_at,
            "done": self.done,
            "calls": [c.to_dict() for c in self.calls],
        }

    def save(self, directory: str | Path) -> Path:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = target / f"{stamp}-{self.task}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class TracedAgent(Agent):
    """Thin wrapper that records every ``complete`` call for display/export."""

    def __init__(self, inner: Agent, recorder: TraceRecorder) -> None:
        self.inner = inner
        self.recorder = recorder
        self.config = getattr(inner, "config", None)

    def complete(self, prompt: str, *, system: str | None = None,
                 json_schema: dict[str, Any] | None = None) -> AgentResult:
        started = time.perf_counter()
        call = TraceCall(
            index=len(self.recorder.calls) + 1,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            duration_ms=0,
            prompt=str(prompt or ""),
            system=str(system or ""),
            json_schema=json_schema,
        )
        self.recorder.calls.append(call)

        def handle_stream(event: dict[str, Any]) -> None:
            call.stream_events.append(event)
            if event.get("type") == "text" and event.get("text"):
                call.stream_text += str(event.get("text"))
            if self.recorder.on_event is not None:
                try:
                    self.recorder.on_event(event)
                except Exception:  # noqa: BLE001
                    pass

        try:
            try:
                result = self.inner.complete(
                    prompt, system=system, json_schema=json_schema, on_stream=handle_stream
                )
            except TypeError:
                result = self.inner.complete(prompt, system=system, json_schema=json_schema)
        except Exception as exc:  # noqa: BLE001 - record and re-raise
            call.duration_ms = int((time.perf_counter() - started) * 1000)
            call.error = f"{type(exc).__name__}: {exc}"
            raise
        call.duration_ms = int((time.perf_counter() - started) * 1000)
        call.provider = str(getattr(result, "provider", "") or "")
        call.model = str(getattr(result, "model", "") or "")
        call.command = list(getattr(self.inner, "last_command", []) or [])
        call.raw_text = str(getattr(result, "text", "") or "")
        call.parsed = getattr(result, "data", None)
        call.error = getattr(result, "error", None)
        call.usage = getattr(result, "usage", {}) or {}
        call.ok = call.error is None
        return result


__all__ = ["TraceCall", "TraceRecorder", "TracedAgent"]
