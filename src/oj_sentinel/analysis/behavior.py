"""Behavioural anomaly analytics for OJ Sentinel.

The module consumes a raw :class:`oj_sentinel.ingest.Snapshot` plus the
normalised :class:`~oj_sentinel.analysis.normalize.CodeProfile` objects and
optional :class:`~oj_sentinel.analysis.similarity.Pair` objects produced by the
similarity stage.  It is deliberately stateless, offline and deterministic:
the same inputs always produce the same (JSON-serialisable) output.

Signals
-------
``burst``
    Many submissions inside a 5/10/30/60 minute window, together with a short
    median gap.
``rapid_distinct``
    Two consecutive submissions on the same problem whose normalised code is
    almost unrelated but whose score jumps sharply (usually into Accepted).
``first_submission_high``
    A full score on the very first attempt for a problem, especially when it
    happens shortly after the homework opens.
``style_drift``
    Consecutive submissions on one problem whose style features move sharply
    (comment density, typing, function structure, line length, ...).
``ai_paste``
    Heuristics for pasted LLM output: markdown fences, "Here is / Explanation"
    comments (English or Chinese), a docstring on every function, advanced
    constructs in a beginner assignment and suspiciously uniform comments.
``cross_user_near_identical``
    Membership of a cluster of users connected by very similar pairs.  The
    default gate is a pair score of 0.90; transitive components larger than
    8 users are treated as shared-template noise and are never reported (their
    size remains visible in ``metrics``).

Public API
----------
``analyze_behavior(snap, profiles, pairs=None, config=None)``
    Returns a list of per-user anomaly dicts sorted by descending score.
``analyze_user(uid, submissions, profiles=None, pairs=None, config=None)``
    Runs the same signal set for one already-grouped user history.  It is
    useful for GUI drill-down and for unit tests; unlike ``analyze_behavior``
    it returns a report even when the user scores zero.

Every report contains ``uid``, ``username``, ``display_name``, ``score``,
``reasons`` (``code`` / ``weight`` / ``detail`` / ``evidence``), ``evidence``,
``metrics``, ``timeline``, ``problems`` and ``top_reasons``.  All values are
plain JSON types.

Scoring and defaults
--------------------
The report score is ``strongest_reason + 0.30 * sum(other_reasons)`` capped at
0.95, so one weak signal cannot saturate the score and corroboration matters
but cannot drown the strongest explanation.  Defaults are tuned for a short
revision queue (``min_score=0.30``, ``max_anomalies=80``); an engine that
already filters similarity pairs may override them via ``config``.
"""
from __future__ import annotations

import difflib
import math
import re
import statistics
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from ..hydro.models import Submission
from ..ingest import Snapshot
from .normalize import CodeProfile, style_distance
from .similarity import Pair

#: Standard burst windows (minutes).  These are always reported in metrics even
#: when a caller narrows ``config["windows"]``.
WINDOW_MINUTES: tuple[int, ...] = (5, 10, 30, 60)

REASON_BURST = "burst"
REASON_RAPID_DISTINCT = "rapid_distinct"
REASON_FIRST_SUBMISSION_HIGH = "first_submission_high"
REASON_STYLE_DRIFT = "style_drift"
REASON_AI_PASTE = "ai_paste"
REASON_CROSS_USER = "cross_user_near_identical"

REASON_CODES: tuple[str, ...] = (
    REASON_BURST,
    REASON_RAPID_DISTINCT,
    REASON_FIRST_SUBMISSION_HIGH,
    REASON_STYLE_DRIFT,
    REASON_AI_PASTE,
    REASON_CROSS_USER,
)

DEFAULT_CONFIG: dict[str, Any] = {
    # burst
    "windows": WINDOW_MINUTES,
    "burst_min_counts": {5: 5, 10: 7, 30: 15, 60: 22},
    "burst_ratio_min": 0.35,
    "burst_median_gap_max": 240.0,
    # rapid divergent submissions
    "rapid_similarity_max": 0.20,
    "rapid_score_jump": 20.0,
    "rapid_window_seconds": 1800.0,
    # first submission high / fast solve
    "first_high_score": 95.0,
    "first_fast_seconds": 900.0,
    "first_very_fast_seconds": 180.0,
    # style drift
    "style_drift_threshold": 0.35,
    # cross-user duplicates: 0.80 pairs are usually shared templates; only
    # very close copies, and clusters small enough to be meaningful, are
    # reported.  Large transitive components are template noise.
    "pair_threshold": 0.90,
    "pair_max_cluster_size": 8,
    # AI-paste heuristics
    "ai_min_advanced_constructs": 2,
    # output: a 340-student class should yield a short review queue, not
    # hundreds of rows.  The engine may override these thresholds.
    "min_score": 0.30,
    "max_anomalies": 80,
    # beginner-assignment context
    "beginner": True,
    "beginner_problems": None,
    "advanced_problems": (),
    # optional epoch/ISO timestamp for the homework opening time
    "open_ts": None,
}

ADVANCED_TAGS = frozenset({"hard", "advanced", "challenge", "困难", "进阶", "高难"})
ADVANCED_DIFFICULTIES = frozenset({"hard", "difficult", "advanced", "困难", "进阶"})

#: Labels used to explain style drift in a human-readable way.
STYLE_LABELS: tuple[tuple[str, str], ...] = (
    ("avg_line_len", "avg line length"),
    ("comment_ratio", "comment ratio"),
    ("snake_case", "snake_case names"),
    ("camel_case", "camelCase names"),
    ("def_count", "def count"),
    ("docstrings", "docstrings"),
    ("fstrings", "f-strings"),
    ("type_hints", "type hints"),
    ("main_guard", "main guard"),
    ("sys_stdin", "sys.stdin"),
    ("try_except", "try/except"),
)

#: Comment styles frequently produced by chat assistants.  Patterns are checked
#: against extracted comments, never against arbitrary string literals.
LLM_COMMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Here is", re.compile(r"\bhere\s+(?:is|are|'s)\b", re.I)),
    ("Explanation", re.compile(r"\bexplanation\b|\bexplain(?:s|ed|ing)?\b", re.I)),
    (
        "This function/code",
        re.compile(r"\bthis\s+(?:function|code|program|script|method|approach)\b", re.I),
    ),
    ("The following", re.compile(r"\bthe following\b", re.I)),
    (
        "Approach/implementation",
        re.compile(r"\b(?:approach|implementation|algorithm)\b", re.I),
    ),
    ("Note that", re.compile(r"\bnote that\b", re.I)),
    ("We can/will", re.compile(r"\bwe\s+(?:can|will|need|should)\b", re.I)),
    (
        "Chinese explanation",
        re.compile(
            r"(?:这里|下面|如下|解释|说明|思路|这段(?:代码)?|上述|我们可以|"
            r"首先.{0,20}然后|定义了一个|用于(?:实现|计算|统计|判断|读取))"
        ),
    ),
)

#: Constructs that are unusual in a first programming assignment.
ADVANCED_CONSTRUCT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "typing",
        re.compile(r"(?m)^\s*(?:from\s+typing\s+import|import\s+typing)\b"),
    ),
    (
        "dataclasses",
        re.compile(r"(?m)^\s*(?:from\s+dataclasses\s+import|import\s+dataclasses)\b|@dataclass\b"),
    ),
    (
        "itertools",
        re.compile(r"(?m)^\s*(?:from\s+itertools\s+import|import\s+itertools)\b"),
    ),
    (
        "functools",
        re.compile(r"(?m)^\s*(?:from\s+functools\s+import|import\s+functools)\b"),
    ),
    (
        "collections",
        re.compile(r"(?m)^\s*(?:from\s+collections(?:\.abc)?\s+import|import\s+collections)\b"),
    ),
    ("sys.stdin", re.compile(r"\bsys\.stdin\b")),
    ("try/except", re.compile(r"(?m)^\s*try\s*:")),
)

DOCSTRING_DEF_RE = re.compile(
    r"def\s+[A-Za-z_]\w*\s*\([^)]*\)[^:\n]*:\s*\n\s*"
    r"(?:[rRuUbBfF]{0,2})(?:\"\"\"|''')"
)
HASH_COMMENT_RE = re.compile(r"#(.*)$")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _fmt_num(value: float) -> str:
    value = float(value)
    if value.is_integer():
        return f"{value:.0f}"
    if abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.2f}"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a mapping or a plain dataclass-like object."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge a user config onto :data:`DEFAULT_CONFIG` and normalise values."""
    cfg: dict[str, Any] = dict(DEFAULT_CONFIG)
    cfg["burst_min_counts"] = dict(DEFAULT_CONFIG["burst_min_counts"])
    if isinstance(config, Mapping):
        for key, value in config.items():
            if key == "burst_min_counts" and isinstance(value, Mapping):
                for minutes, count in value.items():
                    try:
                        cfg["burst_min_counts"][int(minutes)] = int(count)
                    except (TypeError, ValueError):
                        continue
            elif key in cfg:
                cfg[key] = value

    windows: list[int] = []
    for value in cfg.get("windows") or WINDOW_MINUTES:
        minutes = _to_int(value, 0)
        if minutes > 0:
            windows.append(minutes)
    cfg["windows"] = tuple(sorted(set(windows))) or WINDOW_MINUTES

    cfg["burst_ratio_min"] = _clamp01(_to_float(cfg.get("burst_ratio_min"), 0.35))
    cfg["burst_median_gap_max"] = max(0.0, _to_float(cfg.get("burst_median_gap_max"), 240.0))
    cfg["rapid_similarity_max"] = _clamp01(_to_float(cfg.get("rapid_similarity_max"), 0.2))
    cfg["rapid_score_jump"] = max(0.0, _to_float(cfg.get("rapid_score_jump"), 20.0))
    cfg["rapid_window_seconds"] = max(0.0, _to_float(cfg.get("rapid_window_seconds"), 1800.0))
    cfg["first_high_score"] = max(0.0, _to_float(cfg.get("first_high_score"), 95.0))
    cfg["first_fast_seconds"] = max(0.0, _to_float(cfg.get("first_fast_seconds"), 900.0))
    cfg["first_very_fast_seconds"] = max(
        0.0, _to_float(cfg.get("first_very_fast_seconds"), 180.0)
    )
    cfg["style_drift_threshold"] = max(
        0.0, _to_float(cfg.get("style_drift_threshold"), 0.35)
    )
    cfg["pair_threshold"] = _clamp01(_to_float(cfg.get("pair_threshold"), 0.9))
    cfg["pair_max_cluster_size"] = max(1, _to_int(cfg.get("pair_max_cluster_size"), 8))
    cfg["ai_min_advanced_constructs"] = max(
        1, _to_int(cfg.get("ai_min_advanced_constructs"), 2)
    )
    cfg["min_score"] = _clamp01(_to_float(cfg.get("min_score"), 0.3))
    max_anomalies = _to_int(cfg.get("max_anomalies"), 0)
    cfg["max_anomalies"] = max_anomalies if max_anomalies > 0 else None
    cfg["beginner"] = bool(cfg.get("beginner", True))
    return cfg


def _window_threshold(cfg: Mapping[str, Any], minutes: int) -> int:
    counts = cfg.get("burst_min_counts") or {}
    if minutes in counts:
        return max(2, _to_int(counts[minutes], 2))
    # A defensible fallback for caller-supplied windows: roughly one
    # submission every two minutes sustained across the whole window.
    return max(3, int(round(minutes / 2.0)))


def _parse_ts(value: Any) -> int | None:
    """Parse an epoch number or ISO-8601 string into epoch seconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        try:
            return int(numeric) if numeric > 0 else None
        except (ValueError, OverflowError):
            return None

    text = text.replace("Z", "+00:00")
    # Python 3.10's fromisoformat does not accept "+0800"; normalise it.
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    try:
        return int(parsed.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _is_accepted(status: str, score: float = 0.0) -> bool:
    text = (status or "").strip().lower()
    if "accept" in text or "通过" in text or "正确" in text:
        return True
    return float(score) >= 99.5


def _max_window_count(timestamps: Sequence[int], minutes: int) -> int:
    """Largest number of timestamps inside any ``minutes``-long window."""
    if not timestamps:
        return 0
    window = int(minutes) * 60
    best = 0
    left = 0
    for right in range(len(timestamps)):
        while timestamps[right] - timestamps[left] > window:
            left += 1
        best = max(best, right - left + 1)
    return best


def _busiest_rids(subs: Sequence[Submission], minutes: int) -> list[str]:
    """Rids of the submissions in the busiest window (deterministic)."""
    ordered = sorted(
        (s for s in subs if s.submit_ts > 0), key=lambda s: (s.submit_ts, s.rid)
    )
    if not ordered:
        return []
    window = int(minutes) * 60
    best: list[Submission] = []
    left = 0
    for right in range(len(ordered)):
        while ordered[right].submit_ts - ordered[left].submit_ts > window:
            left += 1
        current = ordered[left : right + 1]
        if len(current) > len(best):
            best = list(current)
        elif len(current) == len(best):
            # Prefer the earlier window on ties for deterministic evidence.
            if current and best and current[0].submit_ts < best[0].submit_ts:
                best = list(current)
    return sorted({s.rid for s in best if s.rid})


def _median_gap(timestamps: Sequence[int]) -> float:
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:]) if b >= a]
    return float(statistics.median(gaps)) if gaps else 0.0


def _sub_sort_key(sub: Submission) -> tuple[int, str]:
    return (sub.submit_ts if sub.submit_ts > 0 else 0, sub.rid)


def _dedupe_submissions(subs: Iterable[Submission]) -> list[Submission]:
    seen: set[str] = set()
    out: list[Submission] = []
    for sub in subs:
        if not isinstance(sub, Submission):
            continue
        key = sub.rid or f"__anonymous_{len(out)}"
        if key in seen:
            continue
        seen.add(key)
        out.append(sub)
    out.sort(key=_sub_sort_key)
    return out


class _UnionFind:
    """Tiny deterministic union-find used for cross-user clusters."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        parent = self.parent
        parent.setdefault(item, item)
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != item:
            nxt = parent[item]
            parent[item] = root
            item = nxt
        return root

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        # Keeping the smaller uid as root makes the structure reproducible.
        if root_a < root_b:
            self.parent[root_b] = root_a
        else:
            self.parent[root_a] = root_b


@dataclass
class _Context:
    """Pre-computed, read-only context shared by all per-user analyses."""

    config: dict[str, Any]
    profiles_by_rid: dict[str, CodeProfile] = field(default_factory=dict)
    pair_info: dict[int, dict[str, Any]] = field(default_factory=dict)
    cluster_sizes: dict[int, int] = field(default_factory=dict)
    open_ts: int | None = None
    beginner_problems: set[str] = field(default_factory=set)
    # rid -> (normalised token 5-gram set, stable winnowed fingerprints)
    similarity_cache: dict[str, tuple[set[tuple[str, ...]], set[int]]] = field(
        default_factory=dict
    )


# ---------------------------------------------------------------------------
# snapshot / profile plumbing
# ---------------------------------------------------------------------------


def _collect_submissions(
    snap: Snapshot | None, profiles_by_rid: Mapping[str, CodeProfile]
) -> list[Submission]:
    """Join snapshot records with their code details and profile fallbacks."""
    records = list(getattr(snap, "records", None) or [])
    details = getattr(snap, "submissions", None) or {}
    homework_id = str(getattr(snap, "homework_id", "") or "")
    out: list[Submission] = []
    seen: set[str] = set()

    for record in records:
        rid = str(_get(record, "rid", "") or "")
        if not rid or rid in seen:
            continue
        profile = profiles_by_rid.get(rid)
        detail = _get(details.get(rid) if isinstance(details, Mapping) else None, "detail", {})
        code = str(_get(detail, "code", "") or "")
        if not code:
            code = str(_get(record, "code", "") or "")
        if not code and profile is not None:
            code = profile.code
        uid = _to_int(_get(record, "uid"), _to_int(getattr(profile, "user_id", 0), 0))
        problem = str(
            _get(record, "problem_key", "") or getattr(profile, "problem", "") or ""
        )
        status = str(
            _get(record, "status_text", "")
            or _get(record, "status", "")
            or _get(detail, "status_text", "")
            or _get(detail, "status", "")
            or ""
        )
        score = _to_float(_get(record, "score"), _to_float(_get(detail, "score"), 0.0))
        language = str(
            _get(record, "language", "")
            or _get(detail, "language", "")
            or getattr(profile, "language", "")
            or ""
        )
        out.append(
            Submission(
                rid=rid,
                homework_id=homework_id,
                problem_key=problem,
                uid=uid,
                username=str(_get(record, "username", "") or ""),
                display_name=str(_get(record, "display_name", "") or ""),
                status=status,
                score=score,
                language=language,
                submit_ts=_to_int(_get(record, "submit_ts"), 0),
                submit_at=str(_get(record, "submit_at", "") or ""),
                code=code,
                time_ms=_to_int(_get(record, "time_ms"), 0),
                memory=str(_get(record, "memory", "") or ""),
            )
        )
        seen.add(rid)

    # Profiles whose rid is not present in records still carry useful code
    # signals (useful with partial fixtures); include them without timing.
    for rid in sorted(profiles_by_rid):
        if rid in seen:
            continue
        profile = profiles_by_rid[rid]
        out.append(
            Submission(
                rid=rid,
                homework_id=homework_id,
                problem_key=profile.problem,
                uid=int(profile.user_id),
                username="",
                display_name="",
                status="",
                score=0.0,
                language=profile.language,
                submit_ts=0,
                submit_at="",
                code=profile.code,
            )
        )
        seen.add(rid)
    return out


def _resolve_open_ts(snap: Snapshot | None, cfg: Mapping[str, Any]) -> int | None:
    explicit = _parse_ts(cfg.get("open_ts"))
    if explicit is not None:
        return explicit
    homework = getattr(snap, "homework", None) or {}
    manifest = getattr(snap, "manifest", None) or {}
    for source in (homework, manifest):
        for key in ("open_at", "openAt", "start_at", "startAt", "begin_at"):
            parsed = _parse_ts(_get(source, key))
            if parsed is not None:
                return parsed
    return None


def _as_str_set(value: Any) -> set[str]:
    """Normalise a string or iterable of strings into a set (strings are atomic)."""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    return {str(item) for item in value if str(item)}


def _resolve_beginner_problems(
    snap: Snapshot | None,
    cfg: Mapping[str, Any],
    problem_keys: Iterable[str],
) -> set[str]:
    """Return the set of problems considered "beginner" for AI-paste checks."""
    explicit = cfg.get("beginner_problems")
    if explicit is not None:
        return _as_str_set(explicit)
    if not cfg.get("beginner", True):
        return set()

    advanced_config = _as_str_set(cfg.get("advanced_problems"))
    problem_meta = getattr(snap, "problems", None) or {}
    result: set[str] = set()
    for key in sorted({str(k) for k in problem_keys if str(k)}):
        if key in advanced_config:
            continue
        meta = _get(problem_meta, key, {}) or {}
        difficulty = str(_get(meta, "difficulty", "") or "").strip().lower()
        tags = {str(tag).strip().lower() for tag in (_get(meta, "tags", []) or [])}
        if difficulty in ADVANCED_DIFFICULTIES or tags & ADVANCED_TAGS:
            continue
        result.add(key)
    return result


def _build_pair_index(
    pairs: Iterable[Pair] | None, threshold: float
) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    """Aggregate qualifying pairs per uid and compute cross-user clusters."""
    pair_info: dict[int, dict[str, Any]] = {}
    union_find = _UnionFind()

    for pair in pairs or []:
        score = _to_float(_get(pair, "score"), 0.0)
        if score < threshold:
            continue
        uid_a = _to_int(_get(pair, "uid_a"), 0)
        uid_b = _to_int(_get(pair, "uid_b"), 0)
        if uid_a <= 0 or uid_b <= 0 or uid_a == uid_b:
            continue
        if bool(_get(pair, "same_user", False)):
            continue
        rid_a = str(_get(pair, "rid_a", "") or "")
        rid_b = str(_get(pair, "rid_b", "") or "")
        problem = str(_get(pair, "problem", "") or "")
        username_a = str(_get(pair, "username_a", "") or "")
        username_b = str(_get(pair, "username_b", "") or "")
        union_find.union(uid_a, uid_b)

        sides = (
            (uid_a, rid_a, username_a, uid_b, rid_b, username_b),
            (uid_b, rid_b, username_b, uid_a, rid_a, username_a),
        )
        for uid, own_rid, own_name, other_uid, other_rid, other_name in sides:
            info = pair_info.get(uid)
            if info is None:
                info = {
                    "max_score": 0.0,
                    "count": 0,
                    "partners": set(),
                    "evidence": set(),
                    "problems": set(),
                    "username": "",
                    "best": None,
                }
                pair_info[uid] = info
            info["count"] += 1
            info["partners"].add(other_uid)
            if problem:
                info["problems"].add(problem)
            if own_name and not info["username"]:
                info["username"] = own_name
            if own_rid:
                info["evidence"].add(own_rid)
            if other_rid:
                info["evidence"].add(other_rid)
            candidate = (score, own_rid, -other_uid, other_rid, problem, other_name)
            current = info["best"]
            if current is None or candidate > current:
                info["best"] = candidate
            if score > info["max_score"]:
                info["max_score"] = score

    cluster_members: dict[int, int] = {}
    for uid in pair_info:
        root = union_find.find(uid)
        cluster_members[root] = cluster_members.get(root, 0) + 1
    cluster_sizes = {
        uid: cluster_members[union_find.find(uid)] for uid in pair_info
    }
    for uid, info in pair_info.items():
        info["cluster_size"] = cluster_sizes.get(uid, 1)
    return pair_info, cluster_sizes


# ---------------------------------------------------------------------------
# code helpers
# ---------------------------------------------------------------------------


def _token_grams(tokens: Sequence[str], k: int = 5) -> list[tuple[str, ...]]:
    if len(tokens) < k:
        return [tuple(tokens)] if tokens else []
    return [tuple(tokens[index : index + k]) for index in range(len(tokens) - k + 1)]


def _winnow_hashes(hashes: Sequence[int], window: int = 4) -> set[int]:
    if not hashes:
        return set()
    if len(hashes) <= window:
        return {min(hashes)}
    return {
        min(hashes[index : index + window])
        for index in range(len(hashes) - window + 1)
    }


def _profile_similarity_sets(
    profile: CodeProfile,
    cache: dict[str, tuple[set[tuple[str, ...]], set[int]]],
) -> tuple[set[tuple[str, ...]], set[int]]:
    """Stable n-gram/fingerprint sets derived from dictionary token hashes.

    ``CodeProfile.kgrams``/``fingerprints`` use Python's randomised ``hash()``;
    deriving the sets locally with CRC32 keeps rapid-similarity results stable
    across process restarts while matching the same winnowing idea.
    """
    cached = cache.get(profile.rid)
    if cached is not None:
        return cached
    grams = _token_grams(profile.tokens)
    gram_set = set(grams)
    hashes = [zlib.crc32(repr(gram).encode("utf-8")) for gram in grams]
    value = (gram_set, _winnow_hashes(hashes))
    if profile.rid:
        cache[profile.rid] = value
    return value


def _code_similarity(
    a: CodeProfile | None,
    b: CodeProfile | None,
    cache: dict[str, tuple[set[tuple[str, ...]], set[int]]] | None = None,
) -> float | None:
    """Similarity of two profiles using normalised token/fingerprint sets.

    ``None`` means the comparison is not meaningful (no tokens at all).
    """
    if a is None or b is None or not a.tokens or not b.tokens:
        return None
    if a.normalized_text and a.normalized_text == b.normalized_text:
        return 1.0
    if len(a.tokens) < 5 or len(b.tokens) < 5:
        return difflib.SequenceMatcher(None, a.tokens, b.tokens, autojunk=False).ratio()

    cache = cache if cache is not None else {}
    grams_a, fps_a = _profile_similarity_sets(a, cache)
    grams_b, fps_b = _profile_similarity_sets(b, cache)
    jaccard = len(grams_a & grams_b) / len(grams_a | grams_b) if grams_a and grams_b else 0.0
    containment = (
        len(fps_a & fps_b) / min(len(fps_a), len(fps_b)) if fps_a and fps_b else 0.0
    )
    return _clamp01(0.5 * jaccard + 0.5 * containment)


def _comment_texts(profile: CodeProfile) -> list[str]:
    """Return de-duplicated comment bodies, tolerating tokenizer failures."""
    out: list[str] = []
    seen: set[str] = set()

    def push(raw: str) -> None:
        text = raw.lstrip("#").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for comment in profile.comments or []:
        push(comment)
    # ``tokenize`` may have stopped early on pasted markdown; scan raw lines too.
    for line in profile.code.splitlines():
        match = HASH_COMMENT_RE.search(line)
        if match:
            push(match.group(1))
    return out


def _llm_comment_markers(profile: CodeProfile) -> list[str]:
    found: set[str] = set()
    for comment in _comment_texts(profile):
        for label, pattern in LLM_COMMENT_PATTERNS:
            if pattern.search(comment):
                found.add(label)
    order = {label: index for index, (label, _pattern) in enumerate(LLM_COMMENT_PATTERNS)}
    return sorted(found, key=lambda item: order.get(item, len(order)))


def _function_docstring_count(code: str) -> int:
    return len(DOCSTRING_DEF_RE.findall(code))


def _advanced_constructs(profile: CodeProfile) -> list[str]:
    """Detect advanced imports/constructs; comments are stripped first."""
    code = re.sub(r"#[^\n]*", "", profile.code or "")
    found = [name for name, pattern in ADVANCED_CONSTRUCT_PATTERNS if pattern.search(code)]
    # Fall back to the pre-computed style flags when the source is unparsable
    # and the textual patterns were hidden behind formatting oddities.
    style = profile.style or {}
    if "sys.stdin" not in found and style.get("sys_stdin"):
        found.append("sys.stdin")
    if "try/except" not in found and style.get("try_except"):
        found.append("try/except")
    order = {name: index for index, (name, _pattern) in enumerate(ADVANCED_CONSTRUCT_PATTERNS)}
    return sorted(set(found), key=lambda item: order.get(item, len(order)))


def _uniform_comment_density(profiles: Sequence[CodeProfile]) -> list[str]:
    """Profiles sharing a near-identical, fairly high comment density."""
    candidates = [
        profile
        for profile in profiles
        if profile.code and len(profile.comments or []) >= 2 and profile.style
    ]
    if len(candidates) < 2:
        return []
    ratios = [_to_float(profile.style.get("comment_ratio"), 0.0) for profile in candidates]
    if min(ratios) < 0.10 or max(ratios) > 0.35:
        return []
    if max(ratios) - min(ratios) > 0.04:
        return []
    return [profile.rid for profile in candidates if profile.rid]


def _style_delta_parts(
    before: Mapping[str, float], after: Mapping[str, float], limit: int = 3
) -> list[str]:
    deltas: list[tuple[float, str, str, float, float]] = []
    for key, label in STYLE_LABELS:
        left = _to_float(before.get(key), 0.0)
        right = _to_float(after.get(key), 0.0)
        delta = abs(left - right) / max(1.0, abs(left), abs(right))
        deltas.append((delta, key, label, left, right))
    deltas.sort(key=lambda item: (-item[0], item[1]))
    parts: list[str] = []
    for delta, _key, label, left, right in deltas:
        if delta <= 0.0:
            continue
        parts.append(f"{label} {_fmt_num(left)} -> {_fmt_num(right)}")
        if len(parts) >= limit:
            break
    return parts


def _name_for(uid: int, subs: Sequence[Submission], ctx: _Context) -> tuple[str, str]:
    username = ""
    display_name = ""
    for sub in subs:
        if not username and sub.username:
            username = sub.username
        if not display_name and sub.display_name:
            display_name = sub.display_name
        if username and display_name:
            break
    if not username:
        username = str((ctx.pair_info.get(uid) or {}).get("username", "") or "")
    return username, display_name


# ---------------------------------------------------------------------------
# individual signals: each returns (reason | None, metrics, evidence)
# ---------------------------------------------------------------------------


def _burst_signal(
    subs: Sequence[Submission], cfg: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    timestamps = sorted(s.submit_ts for s in subs if s.submit_ts > 0)
    metric_windows = tuple(sorted(set(WINDOW_MINUTES) | set(cfg["windows"])))
    all_counts = {minutes: _max_window_count(timestamps, minutes) for minutes in metric_windows}
    counts = {minutes: all_counts[minutes] for minutes in cfg["windows"]}

    submissions = len(subs)
    ratio = (max(counts.values()) / submissions) if submissions and counts else 0.0
    median_gap = _median_gap(timestamps)
    metrics: dict[str, Any] = {}
    for minutes in metric_windows:
        metrics[f"max_{minutes}min"] = all_counts[minutes]
    metrics.update(
        {
            "timed_submissions": len(timestamps),
            "burst_ratio": _round(ratio),
            "median_gap_seconds": _round(median_gap, 1),
            "max_window_minutes": 0,
            "max_window_count": 0,
        }
    )
    if not timestamps:
        return None, metrics, set()

    candidates: list[tuple[float, int, int, int]] = []
    for minutes in cfg["windows"]:
        count = counts.get(minutes, 0)
        threshold = _window_threshold(cfg, minutes)
        if (
            count >= threshold
            and ratio >= cfg["burst_ratio_min"]
            and median_gap <= cfg["burst_median_gap_max"]
        ):
            excess = (count - threshold) / threshold
            severity = excess + max(0.0, ratio - cfg["burst_ratio_min"])
            candidates.append((severity, count, -minutes, minutes))

    if not candidates:
        return None, metrics, set()

    _severity, best_count, _negative_minutes, best_minutes = max(candidates)
    threshold = _window_threshold(cfg, best_minutes)
    excess = max(0.0, (best_count - threshold) / threshold)
    weight = 0.30 + 0.18 * min(1.0, excess)
    weight += 0.07 * max(0.0, ratio - 0.5) / 0.5
    if median_gap <= 60.0:
        weight += 0.03
    weight = min(0.55, weight)

    evidence = set(_busiest_rids(subs, best_minutes))
    detail = (
        f"{best_count} submissions in {best_minutes} min (threshold {threshold}); "
        f"median gap {median_gap:.0f}s; {ratio:.0%} of {submissions} submissions "
        "fall in the busiest window"
    )
    metrics.update(
        {
            "max_window_minutes": best_minutes,
            "max_window_count": best_count,
        }
    )
    reason = {
        "code": REASON_BURST,
        "weight": _round(weight),
        "detail": detail,
        "evidence": sorted(evidence),
    }
    return reason, metrics, evidence


def _first_high_signal(
    subs: Sequence[Submission], cfg: Mapping[str, Any], open_ts: int | None
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    first_by_problem: dict[str, Submission] = {}
    for sub in sorted(subs, key=_sub_sort_key):
        if sub.submit_ts <= 0:
            continue
        first_by_problem.setdefault(sub.problem_key, sub)

    events: list[dict[str, Any]] = []
    for problem in sorted(first_by_problem):
        first = first_by_problem[problem]
        if first.score < cfg["first_high_score"]:
            continue
        delay: int | None = None
        if open_ts is not None and open_ts > 0:
            raw_delay = first.submit_ts - open_ts
            # A negative delay usually means a timezone-naive open_at; do not
            # claim the submission was fast in that case.
            if raw_delay >= -300:
                delay = max(0, raw_delay)
        if delay is None:
            weight = 0.20
        elif delay <= cfg["first_very_fast_seconds"]:
            weight = 0.45
        elif delay <= cfg["first_fast_seconds"]:
            weight = 0.40
        else:
            weight = 0.22
        events.append(
            {
                "problem": problem,
                "rid": first.rid,
                "score": first.score,
                "status": first.status,
                "delay": delay,
                "weight": weight,
            }
        )

    fast_delays = [
        event["delay"]
        for event in events
        if event["delay"] is not None and event["delay"] <= cfg["first_fast_seconds"]
    ]
    metrics = {
        "first_try_full_solves": len(events),
        "fast_first_solves": len(fast_delays),
        "fastest_first_solve_seconds": min(fast_delays) if fast_delays else None,
    }
    if not events:
        return None, metrics, set()

    events.sort(key=lambda event: (-event["weight"], event["problem"], event["rid"]))
    best = events[0]
    weight = min(0.50, best["weight"] + 0.05 * (len(events) - 1))
    status = f" ({best['status']})" if best["status"] else ""
    if best["delay"] is not None:
        detail = (
            f"first attempt on {best['problem']} scored {_fmt_num(best['score'])}{status} "
            f"{best['delay'] / 60.0:.1f} min after the homework opened"
        )
    else:
        detail = (
            f"first attempt on {best['problem']} scored {_fmt_num(best['score'])}{status} "
            "with no prior attempt"
        )
    if len(events) > 1:
        detail += f"; {len(events)} problems solved at full score on the first attempt"
    evidence = {event["rid"] for event in events if event["rid"]}
    reason = {
        "code": REASON_FIRST_SUBMISSION_HIGH,
        "weight": _round(weight),
        "detail": detail,
        "evidence": sorted(evidence),
    }
    return reason, metrics, evidence


def _rapid_signal(
    subs: Sequence[Submission],
    profiles: Mapping[str, CodeProfile],
    cfg: Mapping[str, Any],
    similarity_cache: dict[str, tuple[set[tuple[str, ...]], set[int]]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    by_problem: dict[str, list[Submission]] = defaultdict(list)
    for sub in subs:
        if sub.submit_ts > 0 and sub.problem_key:
            by_problem[sub.problem_key].append(sub)

    events: list[dict[str, Any]] = []
    for problem in sorted(by_problem):
        ordered = sorted(by_problem[problem], key=_sub_sort_key)
        for previous, current in zip(ordered, ordered[1:]):
            gap = current.submit_ts - previous.submit_ts
            if gap < 0 or gap > cfg["rapid_window_seconds"]:
                continue
            similarity = _code_similarity(
                profiles.get(previous.rid), profiles.get(current.rid), similarity_cache
            )
            if similarity is None or similarity > cfg["rapid_similarity_max"]:
                continue
            jump = current.score - previous.score
            accepted_now = _is_accepted(current.status, current.score)
            accepted_before = _is_accepted(previous.status, previous.score)
            status_jump = (
                (not accepted_before)
                and (accepted_now or current.score >= cfg["first_high_score"])
                and jump >= 10
            )
            if jump < cfg["rapid_score_jump"] and not status_jump:
                continue
            events.append(
                {
                    "problem": problem,
                    "prev_rid": previous.rid,
                    "cur_rid": current.rid,
                    "gap": gap,
                    "similarity": similarity,
                    "jump": jump,
                    "before": previous.score,
                    "after": current.score,
                }
            )

    metrics = {
        "rapid_distinct_events": len(events),
        "rapid_min_similarity": (
            _round(min(event["similarity"] for event in events)) if events else None
        ),
        "rapid_max_jump": _round(max(event["jump"] for event in events))
        if events
        else 0.0,
    }
    if not events:
        return None, metrics, set()

    events.sort(
        key=lambda event: (
            -event["jump"],
            event["similarity"],
            event["problem"],
            event["prev_rid"],
        )
    )
    best = events[0]
    # Base 0.30 keeps a single clear rewrite visible at the default review
    # threshold; extra events add corroboration but never dominate the report.
    weight = 0.30 + 0.06 * (len(events) - 1)
    weight += 0.05 * min(1.0, max(0.0, best["jump"] - cfg["rapid_score_jump"]) / 80.0)
    if best["similarity"] < 0.1:
        weight += 0.03
    weight = min(0.55, weight)

    if len(events) == 1:
        detail = (
            f"rapid rewrite on {best['problem']} {best['gap'] / 60.0:.1f} min after a "
            f"{_fmt_num(best['before'])}-point attempt: code similarity "
            f"{best['similarity']:.2f}, score {_fmt_num(best['before'])} -> "
            f"{_fmt_num(best['after'])}"
        )
    else:
        detail = (
            f"{len(events)} rapid divergent rewrites (best: {best['problem']} after "
            f"{best['gap'] / 60.0:.1f} min, similarity {best['similarity']:.2f}, "
            f"score {_fmt_num(best['before'])} -> {_fmt_num(best['after'])})"
        )
    evidence = {
        rid
        for event in events
        for rid in (event["prev_rid"], event["cur_rid"])
        if rid
    }
    reason = {
        "code": REASON_RAPID_DISTINCT,
        "weight": _round(weight),
        "detail": detail,
        "evidence": sorted(evidence),
    }
    return reason, metrics, evidence


def _style_signal(
    subs: Sequence[Submission],
    profiles: Mapping[str, CodeProfile],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    by_problem: dict[str, list[Submission]] = defaultdict(list)
    for sub in subs:
        if sub.problem_key and sub.rid in profiles:
            by_problem[sub.problem_key].append(sub)

    events: list[dict[str, Any]] = []
    for problem in sorted(by_problem):
        ordered = sorted(by_problem[problem], key=_sub_sort_key)
        for previous, current in zip(ordered, ordered[1:]):
            before = profiles.get(previous.rid)
            after = profiles.get(current.rid)
            if before is None or after is None:
                continue
            distance = style_distance(before.style or {}, after.style or {})
            if distance < cfg["style_drift_threshold"]:
                continue
            gap = (
                current.submit_ts - previous.submit_ts
                if previous.submit_ts > 0 and current.submit_ts > 0
                else None
            )
            events.append(
                {
                    "problem": problem,
                    "prev_rid": previous.rid,
                    "cur_rid": current.rid,
                    "distance": distance,
                    "before": before.style or {},
                    "after": after.style or {},
                    "gap": gap,
                }
            )

    metrics = {
        "style_drift_events": len(events),
        "max_style_drift": (
            _round(max(event["distance"] for event in events)) if events else 0.0
        ),
    }
    if not events:
        return None, metrics, set()

    events.sort(
        key=lambda event: (
            -event["distance"],
            event["problem"],
            event["prev_rid"],
        )
    )
    best = events[0]
    weight = 0.15 + min(0.12, best["distance"] - cfg["style_drift_threshold"])
    weight += 0.04 * (len(events) - 1)
    weight = min(0.40, weight)

    parts = _style_delta_parts(best["before"], best["after"])
    delta_text = ", ".join(parts) if parts else "no dominant feature change"
    timing = f"{best['gap'] / 60.0:.1f} min apart" if best["gap"] is not None else "consecutive"
    prefix = f"{len(events)} style swings; best: " if len(events) > 1 else ""
    detail = (
        f"{prefix}consecutive {best['problem']} submissions {timing} differ in style "
        f"(distance {best['distance']:.2f} >= {cfg['style_drift_threshold']:.2f}): "
        f"{delta_text}"
    )
    evidence = {
        rid
        for event in events
        for rid in (event["prev_rid"], event["cur_rid"])
        if rid
    }
    reason = {
        "code": REASON_STYLE_DRIFT,
        "weight": _round(weight),
        "detail": detail,
        "evidence": sorted(evidence),
    }
    return reason, metrics, evidence


def _ai_paste_signal(
    subs: Sequence[Submission],
    profiles: Mapping[str, CodeProfile],
    cfg: Mapping[str, Any],
    beginner_problems: set[str],
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    user_profiles = [profiles[sub.rid] for sub in subs if sub.rid in profiles]
    user_profiles.sort(key=lambda profile: profile.rid)

    fence_rids: list[str] = []
    comment_rids: list[str] = []
    docstring_rids: list[str] = []
    advanced_rids: list[str] = []
    advanced_names: set[str] = set()
    marker_names: set[str] = set()

    for profile in user_profiles:
        rid = profile.rid
        if not rid:
            continue
        style = profile.style or {}
        if style.get("markdown_fence") or "```" in (profile.code or ""):
            fence_rids.append(rid)

        markers = _llm_comment_markers(profile)
        if markers:
            comment_rids.append(rid)
            marker_names.update(markers)

        def_count = _to_int(style.get("def_count"), 0)
        documented = _function_docstring_count(profile.code or "")
        if def_count >= 2 and documented >= def_count:
            docstring_rids.append(rid)

        problem = profile.problem or next(
            (sub.problem_key for sub in subs if sub.rid == rid), ""
        )
        if problem in beginner_problems:
            constructs = _advanced_constructs(profile)
            if len(constructs) >= cfg["ai_min_advanced_constructs"]:
                advanced_rids.append(rid)
                advanced_names.update(constructs)

    uniform_rids = _uniform_comment_density(user_profiles)
    indicators: list[str] = []
    weight = 0.0
    evidence: set[str] = set()

    if fence_rids:
        indicators.append(f"markdown code fence ({len(fence_rids)} submission(s))")
        weight += 0.16
        evidence.update(fence_rids)
    if comment_rids:
        markers = ", ".join(sorted(marker_names))
        indicators.append(f"LLM-style comments: {markers} ({len(comment_rids)} submission(s))")
        weight += 0.10 + min(0.12, 0.04 * max(0, len(marker_names) - 1))
        evidence.update(comment_rids)
    if docstring_rids:
        indicators.append(f"a docstring on every function ({len(docstring_rids)} submission(s))")
        weight += 0.12
        evidence.update(docstring_rids)
    if advanced_rids:
        names = ", ".join(sorted(advanced_names))
        indicators.append(
            f"advanced constructs in a beginner assignment: {names} "
            f"({len(advanced_rids)} submission(s))"
        )
        weight += 0.04 * min(3, max(1, len(advanced_names)))
        evidence.update(advanced_rids)
    # Uniform comment density alone is common in introductory courses; it only
    # reinforces an AI-paste hypothesis when another heuristic already fired.
    if uniform_rids and indicators:
        indicators.append(
            f"near-uniform comment density across {len(uniform_rids)} submissions"
        )
        weight += 0.08
        evidence.update(uniform_rids)

    metrics = {
        "ai_signals": len(indicators),
        "ai_markdown_fences": len(fence_rids),
        "ai_llm_comments": len(comment_rids),
        "ai_docstring_every_def": len(docstring_rids),
        "ai_advanced_constructs": len(advanced_rids),
        "ai_uniform_comments": len(uniform_rids),
    }
    if not indicators:
        return None, metrics, set()

    weight = min(0.60, weight)
    reason = {
        "code": REASON_AI_PASTE,
        "weight": _round(weight),
        "detail": "; ".join(indicators),
        "evidence": sorted(evidence),
    }
    return reason, metrics, evidence


def _pair_signal(
    uid: int, ctx: _Context
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    cfg = ctx.config
    info = ctx.pair_info.get(uid)
    if not info:
        return (
            None,
            {
                "pair_max_score": 0.0,
                "pair_count": 0,
                "pair_cluster_size": 1,
                "pair_template_cluster": 0,
                "pair_partners": [],
                "pair_problems": [],
            },
            set(),
        )

    max_score = _to_float(info.get("max_score"), 0.0)
    count = _to_int(info.get("count"), 0)
    cluster_size = _to_int(info.get("cluster_size"), ctx.cluster_sizes.get(uid, 1))
    partners = sorted(int(partner) for partner in info.get("partners", set()))
    metrics = {
        "pair_max_score": _round(max_score),
        "pair_count": count,
        "pair_cluster_size": cluster_size,
        "pair_template_cluster": 0,
        "pair_partners": partners,
        "pair_problems": sorted(str(item) for item in info.get("problems", set()) if item),
    }
    evidence = {str(item) for item in info.get("evidence", set()) if item}

    if max_score < cfg["pair_threshold"]:
        return None, metrics, set()

    # Common templates and copied starter files create long transitive chains.
    # A component larger than the configured maximum is treated as boilerplate:
    # its size remains visible in metrics but it never becomes an anomaly.
    if cluster_size > cfg["pair_max_cluster_size"]:
        metrics["pair_template_cluster"] = 1
        return None, metrics, set()

    # A near-identical pair is meaningful but is a medium-confidence signal.
    # Keep the weight in the 0.30-0.35 band so one pair cannot dominate a report.
    if max_score >= 0.98:
        weight = 0.35
    elif max_score >= 0.95:
        weight = 0.33
    else:
        weight = 0.30
    if 3 <= cluster_size <= cfg["pair_max_cluster_size"]:
        weight = min(0.35, weight + 0.02)

    best = info.get("best") or (max_score, "", 0, "", "", "")
    _score, own_rid, negative_other, other_rid, problem, _other_name = best
    other_uid = -negative_other
    partner_text = f"uid {other_uid}" if other_uid else "another user"
    problem_text = f" on {problem}" if problem else ""
    cluster_text = (
        f"; member of a {cluster_size}-user near-identical cluster"
        if cluster_size >= 3
        else ""
    )
    detail = (
        f"near-identical submissions with {partner_text}{problem_text} "
        f"(pair score {max_score:.2f}); {count} qualifying pair(s){cluster_text}"
    )
    reason = {
        "code": REASON_CROSS_USER,
        "weight": _round(weight),
        "detail": detail,
        "evidence": sorted(evidence),
    }
    return reason, metrics, evidence


# ---------------------------------------------------------------------------
# per-user report
# ---------------------------------------------------------------------------


def _combined_score(reasons: Sequence[Mapping[str, Any]]) -> float:
    """Combine reason weights without letting weak corroboration saturate.

    The strongest signal contributes its full weight; every other signal adds
    only 30% of its weight.  A single weak signal therefore keeps a low score
    and a report never reaches 1.0 from a pile of medium-confidence reasons.
    """
    if not reasons:
        return 0.0
    weights = sorted(
        (_to_float(reason.get("weight"), 0.0) for reason in reasons), reverse=True
    )
    combined = weights[0] + 0.30 * sum(weights[1:])
    return _round(min(0.95, combined))


def _analyze_history(
    uid: int, submissions: Iterable[Submission], ctx: _Context
) -> dict[str, Any]:
    subs = _dedupe_submissions(submissions)
    cfg = ctx.config

    reasons: list[dict[str, Any]] = []
    evidence: set[str] = set()
    metrics: dict[str, Any] = {
        "submissions": len(subs),
        "problems": len({sub.problem_key for sub in subs if sub.problem_key}),
        "timed_submissions": 0,
        "first_ts": 0,
        "last_ts": 0,
        "span_minutes": 0.0,
    }

    burst_reason, burst_metrics, burst_evidence = _burst_signal(subs, cfg)
    metrics.update(burst_metrics)
    if burst_reason:
        reasons.append(burst_reason)
        evidence.update(burst_evidence)

    first_reason, first_metrics, first_evidence = _first_high_signal(
        subs, cfg, ctx.open_ts
    )
    metrics.update(first_metrics)
    if first_reason:
        reasons.append(first_reason)
        evidence.update(first_evidence)

    rapid_reason, rapid_metrics, rapid_evidence = _rapid_signal(
        subs, ctx.profiles_by_rid, cfg, ctx.similarity_cache
    )
    metrics.update(rapid_metrics)
    if rapid_reason:
        reasons.append(rapid_reason)
        evidence.update(rapid_evidence)

    style_reason, style_metrics, style_evidence = _style_signal(
        subs, ctx.profiles_by_rid, cfg
    )
    metrics.update(style_metrics)
    if style_reason:
        reasons.append(style_reason)
        evidence.update(style_evidence)

    ai_reason, ai_metrics, ai_evidence = _ai_paste_signal(
        subs, ctx.profiles_by_rid, cfg, ctx.beginner_problems
    )
    metrics.update(ai_metrics)
    if ai_reason:
        reasons.append(ai_reason)
        evidence.update(ai_evidence)

    pair_reason, pair_metrics, pair_evidence = _pair_signal(uid, ctx)
    metrics.update(pair_metrics)
    if pair_reason:
        reasons.append(pair_reason)
        evidence.update(pair_evidence)

    # Timing summary is computed from positive timestamps only.
    timestamps = [sub.submit_ts for sub in subs if sub.submit_ts > 0]
    if timestamps:
        metrics["timed_submissions"] = len(timestamps)
        metrics["first_ts"] = min(timestamps)
        metrics["last_ts"] = max(timestamps)
        metrics["span_minutes"] = _round((max(timestamps) - min(timestamps)) / 60.0, 2)

    # Timeline / per-problem rollup for the GUI.
    timeline: list[dict[str, Any]] = []
    problems: dict[str, dict[str, Any]] = {}
    previous_ts: int | None = None
    for sub in subs:
        gap = (
            sub.submit_ts - previous_ts
            if previous_ts is not None
            and sub.submit_ts > 0
            and previous_ts > 0
            and sub.submit_ts >= previous_ts
            else 0
        )
        timeline.append(
            {
                "rid": sub.rid,
                "problem": sub.problem_key,
                "ts": sub.submit_ts,
                "submit_at": sub.submit_at,
                "score": _round(sub.score, 2),
                "status": sub.status,
                "language": sub.language,
                "gap_seconds": int(gap),
            }
        )
        if sub.submit_ts > 0:
            previous_ts = sub.submit_ts
        bucket = problems.get(sub.problem_key)
        if bucket is None:
            bucket = {
                "submissions": 0,
                "best_score": 0.0,
                "first_ts": 0,
                "last_ts": 0,
            }
            problems[sub.problem_key] = bucket
        bucket["submissions"] += 1
        bucket["best_score"] = _round(max(_to_float(bucket["best_score"]), sub.score), 2)
        if sub.submit_ts > 0:
            if not bucket["first_ts"] or sub.submit_ts < bucket["first_ts"]:
                bucket["first_ts"] = sub.submit_ts
            if sub.submit_ts > bucket["last_ts"]:
                bucket["last_ts"] = sub.submit_ts
    problems = {key: problems[key] for key in sorted(problems)}

    reasons.sort(key=lambda reason: (-reason["weight"], reason["code"]))
    score = _combined_score(reasons)
    username, display_name = _name_for(uid, subs, ctx)
    evidence_sorted = sorted(evidence)
    top_reasons = [
        {"code": reason["code"], "weight": reason["weight"]} for reason in reasons[:3]
    ]

    return {
        "uid": int(uid),
        "username": username,
        "display_name": display_name,
        "score": score,
        "reasons": reasons,
        "evidence": evidence_sorted,
        "metrics": metrics,
        "timeline": timeline,
        "problems": problems,
        "top_reasons": top_reasons,
    }


def analyze_user(
    uid: int,
    submissions: Iterable[Submission],
    profiles: Sequence[CodeProfile] | None = None,
    pairs: Sequence[Pair] | None = None,
    config: Mapping[str, Any] | None = None,
    *,
    open_ts: Any = None,
    beginner_problems: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Analyse one user's submissions and always return a full report.

    ``profiles`` may contain profiles for other users as well; lookups are by
    rid.  ``pairs`` are used only for the cross-user signal and cluster size.
    When ``beginner_problems`` is omitted, every problem in ``submissions`` is
    treated as beginner (the safe default for an introductory course).
    ``open_ts`` accepts an epoch number or an ISO-8601 string.
    """
    cfg = _as_config(config)
    subs = _dedupe_submissions(submissions)
    profiles_by_rid = {
        profile.rid: profile for profile in (profiles or []) if getattr(profile, "rid", "")
    }
    pair_info, cluster_sizes = _build_pair_index(pairs, cfg["pair_threshold"])
    if beginner_problems is None:
        beginner = {sub.problem_key for sub in subs if sub.problem_key}
    else:
        beginner = _as_str_set(beginner_problems)
    ctx = _Context(
        config=cfg,
        profiles_by_rid=profiles_by_rid,
        pair_info=pair_info,
        cluster_sizes=cluster_sizes,
        open_ts=_parse_ts(open_ts),
        beginner_problems=beginner,
    )
    return _analyze_history(int(uid), subs, ctx)


def analyze_behavior(
    snap: Snapshot,
    profiles: Sequence[CodeProfile],
    pairs: Sequence[Pair] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return behaviour anomalies for a snapshot, sorted by score descending.

    The result is JSON-serialisable and sorted by score descending.  Users
    whose combined score is below ``config["min_score"]`` (default 0.30) are
    omitted; pass ``min_score=0`` to keep every user with at least one signal.
    ``config["max_anomalies"]`` (default 80) limits the returned list; pass
    ``None`` to disable the cap.  ``config["pair_threshold"]`` (default 0.90)
    and ``config["pair_max_cluster_size"]`` (default 8) tune the cross-user
    signal against shared-template components.
    """
    cfg = _as_config(config)
    profile_list = list(profiles or [])
    profiles_by_rid = {
        profile.rid: profile
        for profile in profile_list
        if getattr(profile, "rid", "")
    }
    submissions = _collect_submissions(snap, profiles_by_rid)

    histories: dict[int, list[Submission]] = defaultdict(list)
    for sub in submissions:
        # uid 0 means the record did not carry an account id; grouping those
        # together would fabricate a super-user, so they are ignored here.
        if sub.uid <= 0:
            continue
        histories[int(sub.uid)].append(sub)

    problem_keys = {sub.problem_key for sub in submissions if sub.problem_key}
    beginner = _resolve_beginner_problems(snap, cfg, problem_keys)
    pair_info, cluster_sizes = _build_pair_index(pairs, cfg["pair_threshold"])
    ctx = _Context(
        config=cfg,
        profiles_by_rid=profiles_by_rid,
        pair_info=pair_info,
        cluster_sizes=cluster_sizes,
        open_ts=_resolve_open_ts(snap, cfg),
        beginner_problems=beginner,
    )

    results: list[dict[str, Any]] = []
    for uid in sorted(histories):
        report = _analyze_history(uid, histories[uid], ctx)
        if not report["reasons"]:
            continue
        if report["score"] < cfg["min_score"]:
            continue
        results.append(report)

    results.sort(key=lambda report: (-report["score"], report["uid"]))
    limit = cfg.get("max_anomalies")
    if limit:
        results = results[: int(limit)]
    return results


__all__ = [
    "DEFAULT_CONFIG",
    "REASON_CODES",
    "REASON_AI_PASTE",
    "REASON_BURST",
    "REASON_CROSS_USER",
    "REASON_FIRST_SUBMISSION_HIGH",
    "REASON_RAPID_DISTINCT",
    "REASON_STYLE_DRIFT",
    "WINDOW_MINUTES",
    "analyze_behavior",
    "analyze_user",
]
