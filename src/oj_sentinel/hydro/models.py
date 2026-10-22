"""Typed records shared by the Hydro client, store, analysis and report layers."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class Homework:
    id: str
    title: str
    status: str = ""
    open_at: str = ""
    deadline: str = ""
    description_html: str = ""
    description_text: str = ""
    problems: list["ProblemRef"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class ProblemRef:
    key: str  # HW2001 (URL alias)
    title: str
    points: int = 0
    href: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Problem:
    key: str
    title: str
    numeric_id: int = 0
    points: int = 0
    description_html: str = ""
    description_text: str = ""
    time_limit: str = ""
    memory_limit: str = ""
    difficulty: str = ""
    tags: list[str] = field(default_factory=list)
    submission_count: int = 0
    accepted_count: int = 0
    files: list["TestFile"] = field(default_factory=list)
    additional_files: list["TestFile"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestFile:
    name: str
    size: int = 0
    kind: str = "testdata"  # testdata | additional_file
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecordSummary:
    rid: str
    status: str
    status_text: str
    score: int
    problem_key: str
    problem_title: str
    uid: int
    display_name: str
    username: str
    time_ms: int
    memory: str
    language: str
    submit_at: str
    submit_ts: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseResult:
    index: int
    status: str
    score: float = 0.0
    time_ms: int = 0
    memory: str = ""
    message: str = ""


@dataclass
class RecordDetail:
    rid: str
    code: str = ""
    language: str = ""
    status: str = ""
    status_text: str = ""
    score: float = 0.0
    tests: list[CaseResult] = field(default_factory=list)
    compile_error: str = ""
    judge_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreboardRow:
    rank: str
    uid: int
    display_name: str
    username: str
    score: str
    total_time: str
    cells: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Scoreboard:
    homework_id: str
    problems: list[dict[str, str]] = field(default_factory=list)
    rows: list[ScoreboardRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Submission:
    """A submission joined with its code, ready for analysis."""

    rid: str
    homework_id: str
    problem_key: str
    uid: int
    username: str
    display_name: str
    status: str
    score: float
    language: str
    submit_ts: int
    submit_at: str
    code: str
    time_ms: int = 0
    memory: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
