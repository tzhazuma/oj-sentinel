"""FastAPI application and local Web GUI for OJ Sentinel.

The server is intentionally small and local-first:

* student data stays under ``data_dir`` and is only read from disk;
* OJ credentials live in an in-process session store keyed by a random,
  HttpOnly cookie and are never written to disk;
* long running acquisition/analysis work happens in background threads and is
  polled through ``/api/jobs``.

The module can be used either as an app factory::

    uvicorn oj_sentinel.server.app:create_app --factory --port 8765

or through the module level ``app`` object::

    uvicorn oj_sentinel.server.app:app --port 8765
"""
from __future__ import annotations

import base64
import csv
import dataclasses
import importlib
import inspect
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..config import default_data_dir
from ..ingest import Snapshot, load_snapshot
from ..ingest import stats as snapshot_stats

VERSION = "0.1.0"
COOKIE_NAME = "ojs_session"
SESSION_TTL = 8 * 60 * 60  # 8 hours, absolute
LOG_CAP = 500
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Only these files may be served by /api/report/{domain}/{hid}/{name}.
REPORT_WHITELIST = (
    "report.html",
    "report.json",
    "report.csv",
    "report.md",
    "index.html",
    "summary.json",
    "analysis.json",
    "stats.json",
    "pairs.csv",
    "anomalies.csv",
    "clusters.csv",
    "final_scores.csv",
    "final-scores.csv",
    "crossvalidation.json",
    "cross_validation.csv",
    "cross-validate.csv",
    "diagnosis.json",
    "judge.csv",
    "judge_batch.json",
    "judge_batch.csv",
    "ai_review.json",
)

_SAFE_SEGMENT_RE = re.compile(r"^[^/\\\x00]+$")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else _now()).astimezone()
    return dt.isoformat(timespec="seconds")


def _safe_segment(value: Any, label: str = "路径") -> str:
    """Validate one URL/filesystem path segment (no separators, no dot dots)."""
    if not isinstance(value, str):
        raise HTTPException(status_code=404, detail=f"无效的{label}")
    value = value.strip()
    if (
        not value
        or value in {".", ".."}
        or len(value) > 200
        or value.startswith(".")
        or not _SAFE_SEGMENT_RE.match(value)
    ):
        raise HTTPException(status_code=404, detail=f"无效的{label}")
    return value


def _inside(base: Path, target: Path) -> Path:
    try:
        target.resolve().relative_to(base.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="路径越界") from None
    return target


def _jsonable(value: Any) -> Any:
    """Best-effort conversion to JSON-friendly primitives."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _jsonable(to_dict())
        except Exception:  # noqa: BLE001 - defensive for third-party objects
            pass
    return str(value)


def _short(value: Any, limit: int = 240) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _flag(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _score_value(value: Any) -> int | float:
    number = _number(value, 0.0)
    if number.is_integer():
        return int(number)
    return round(number, 2)


class _FileCache:
    """Tiny mtime/size based JSON cache with a bounded number of entries."""

    def __init__(self, max_entries: int = 128) -> None:
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[tuple[int, int], Any]] = OrderedDict()
        self._lock = threading.RLock()

    def get_json(self, path: Path | str, default: Any = None) -> Any:
        path = Path(path)
        try:
            stat = path.stat()
        except OSError:
            return default
        key = str(path)
        signature = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            entry = self._data.get(key)
            if entry is not None and entry[0] == signature:
                self._data.move_to_end(key)
                return entry[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
        with self._lock:
            self._data[key] = (signature, data)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
        return data


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
@dataclass
class SessionRecord:
    token: str
    client: Any
    username: str
    base_url: str
    domain: str
    created_at: float
    last_used: float
    homeworks: list[dict[str, Any]] | None = None
    homeworks_error: str | None = None
    homework_refreshed_at: float = 0.0

    @property
    def expires_at(self) -> float:
        return self.created_at + SESSION_TTL


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionRecord] = {}

    def purge(self, now: float | None = None) -> None:
        now = _now() if now is None else now
        with self._lock:
            for token, record in list(self._sessions.items()):
                if record.expires_at <= now:
                    self._sessions.pop(token, None)

    def create(
        self,
        *,
        client: Any,
        username: str,
        base_url: str,
        domain: str,
        homeworks: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        self.purge()
        token = secrets.token_urlsafe(32)
        now = _now()
        record = SessionRecord(
            token=token,
            client=client,
            username=username,
            base_url=base_url,
            domain=domain,
            created_at=now,
            last_used=now,
            homeworks=homeworks,
            homework_refreshed_at=now,
        )
        with self._lock:
            self._sessions[token] = record
        return record

    def get(self, token: str | None) -> SessionRecord | None:
        if not token:
            return None
        now = _now()
        self.purge(now)
        with self._lock:
            record = self._sessions.get(token)
            if record is None:
                return None
            if record.expires_at <= now:
                self._sessions.pop(token, None)
                return None
            record.last_used = now
            return record

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def count(self) -> int:
        self.purge()
        with self._lock:
            return len(self._sessions)


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "queued"
    progress: int = 0
    message: str = ""
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_CAP))
    created_at: str = ""
    created_ts: float = 0.0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: Any = None
    thread: threading.Thread | None = None


class JobManager:
    """Thread-safe in-memory job registry.

    Logs are capped at :data:`LOG_CAP` lines so a noisy acquisition cannot grow
    the process without bound.
    """

    def __init__(self, *, keep: int = 60) -> None:
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self.keep = keep

    # -- lifecycle ---------------------------------------------------------
    def create(self, kind: str, title: str = "") -> Job:
        job = Job(
            id=uuid.uuid4().hex[:16],
            kind=kind,
            title=title or kind,
            created_at=_iso(),
            created_ts=_now(),
        )
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self.keep:
                oldest = next(iter(self._jobs))
                if self._jobs[oldest].status in {"queued", "running"} and len(self._jobs) > 1:
                    # Do not evict the only record of a running job; drop the
                    # oldest finished job instead.
                    for jid, candidate in self._jobs.items():
                        if candidate.status not in {"queued", "running"}:
                            self._jobs.pop(jid, None)
                            break
                    else:
                        break
                else:
                    self._jobs.pop(oldest, None)
        return job

    def start(self, job: Job, target: Callable[[], Any]) -> threading.Thread:
        def wrapper() -> None:
            with self._lock:
                if job.status == "queued":
                    job.status = "running"
                    job.started_at = _iso()
                    job.message = job.message or "运行中"
            try:
                result = target()
            except Exception as exc:  # noqa: BLE001 - surfaced through the job
                self.fail(job, exc)
            else:
                self.finish(job, result)

        with self._lock:
            if job.status != "queued":
                raise RuntimeError("job already started")
            job.message = job.message or "排队中"
        thread = threading.Thread(
            target=wrapper,
            name=f"ojs-{job.kind}-{job.id[:8]}",
            daemon=True,
        )
        with self._lock:
            job.thread = thread
        thread.start()
        return thread

    def finish(self, job: Job, result: Any = None) -> None:
        with self._lock:
            job.status = "succeeded"
            job.progress = 100
            job.finished_at = _iso()
            job.message = "已完成"
            job.result = _jsonable(result)
            job.log.append(f"{time.strftime('%H:%M:%S')} 任务完成")
            self._trim_log(job)

    def fail(self, job: Job, exc: BaseException) -> None:
        message = f"{type(exc).__name__}: {_short(exc, 500)}"
        with self._lock:
            job.status = "failed"
            job.finished_at = _iso()
            job.error = message
            job.message = "任务失败"
            job.log.append(f"{time.strftime('%H:%M:%S')} [错误] {message}")
            try:
                stack = traceback.format_exc(limit=6)
            except Exception:  # noqa: BLE001
                stack = ""
            for line in stack.splitlines()[-8:]:
                job.log.append(f"{time.strftime('%H:%M:%S')}   {line}")
            self._trim_log(job)

    # -- updates -----------------------------------------------------------
    def add_log(self, job: Job, message: Any, level: str = "info") -> None:
        text = _short(message, 4000)
        if not text:
            return
        prefix = {"warning": "[警告] ", "error": "[错误] ", "success": "[完成] "}.get(level, "")
        with self._lock:
            job.log.append(f"{time.strftime('%H:%M:%S')} {prefix}{text}")
            self._trim_log(job)
            job.message = _short(prefix + text, 300)

    def set_progress(self, job: Job, percent: Any, message: str | None = None) -> None:
        value = max(0, min(99, int(_number(percent, 0))))
        with self._lock:
            job.progress = max(job.progress, value)
            if message:
                job.message = _short(message, 300)

    def set_message(self, job: Job, message: str) -> None:
        with self._lock:
            job.message = _short(message, 300)

    @staticmethod
    def _trim_log(job: Job) -> None:
        while len(job.log) > LOG_CAP:
            job.log.popleft()

    # -- reads -------------------------------------------------------------
    @staticmethod
    def _dump(job: Job) -> dict[str, Any]:
        return {
            "id": job.id,
            "kind": job.kind,
            "title": job.title,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "log": list(job.log),
            "log_lines": len(job.log),
            "log_capped": len(job.log) >= LOG_CAP,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "result": job.result,
        }

    def list(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_ts, reverse=True)
            if kind:
                jobs = [j for j in jobs if j.kind == kind]
            return [self._dump(j) for j in jobs[: max(1, min(int(limit), 500))]]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._dump(job) if job else None

    def get_raw(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def running(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._dump(j)
                for j in self._jobs.values()
                if j.status in {"queued", "running"}
            ]


# --------------------------------------------------------------------------- #
# server context
# --------------------------------------------------------------------------- #
class ServerContext:
    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.sessions = SessionStore()
        self.jobs = JobManager()
        self.files = _FileCache(max_entries=256)
        self.analysis_cache = _FileCache(max_entries=6)
        self._stats_lock = threading.RLock()
        self._stats_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}

    # -- paths -------------------------------------------------------------
    def snapshot_root(self, domain: str, hid: str, *, require: bool = True) -> Path:
        domain = _safe_segment(domain, "域名")
        hid = _safe_segment(hid, "作业 ID")
        root = self.data_dir / "snapshots" / domain / hid
        _inside(self.data_dir / "snapshots", root)
        if require and not (root / "homework.json").exists():
            raise HTTPException(status_code=404, detail="本地快照不存在")
        return root

    def analysis_dir(self, domain: str, hid: str) -> Path:
        domain = _safe_segment(domain, "域名")
        hid = _safe_segment(hid, "作业 ID")
        target = self.data_dir / "analysis" / domain / hid
        _inside(self.data_dir / "analysis", target)
        return target

    def analysis_path(self, domain: str, hid: str) -> Path:
        return self.analysis_dir(domain, hid) / "analysis.json"

    # -- discovery ---------------------------------------------------------
    def snapshot_dirs(self) -> list[tuple[str, str, Path]]:
        base = self.data_dir / "snapshots"
        out: list[tuple[str, str, Path]] = []
        if not base.is_dir():
            return out
        try:
            domains = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            return out
        for domain_dir in domains:
            try:
                homeworks = sorted(p for p in domain_dir.iterdir() if p.is_dir())
            except OSError:
                continue
            for root in homeworks:
                if (root / "manifest.json").exists() or (root / "homework.json").exists():
                    out.append((domain_dir.name, root.name, root))
        return out

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _iso_mtime(path: Path) -> str | None:
        try:
            return _iso(path.stat().st_mtime)
        except OSError:
            return None

    @staticmethod
    def _count_files(path: Path, suffix: str = ".json") -> int:
        try:
            return sum(1 for p in path.iterdir() if p.is_file() and p.suffix == suffix)
        except OSError:
            return 0

    def report_files(self, domain: str, hid: str) -> list[dict[str, Any]]:
        directory = self.analysis_dir(domain, hid)
        out: list[dict[str, Any]] = []
        for name in REPORT_WHITELIST:
            path = directory / name
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            out.append(
                {
                    "name": name,
                    "size": stat.st_size,
                    "updated_at": _iso(stat.st_mtime),
                    "url": f"/api/report/{domain}/{hid}/{name}",
                }
            )
        return out

    def snapshot_summaries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for domain, hid, root in self.snapshot_dirs():
            manifest = self.files.get_json(root / "manifest.json", {}) or {}
            homework = self.files.get_json(root / "homework.json", {}) or {}
            scoreboard = self.files.get_json(root / "scoreboard.json", {}) or {}

            title = str(homework.get("title") or manifest.get("homework_title") or hid)
            problem_keys = manifest.get("problems")
            if not isinstance(problem_keys, list) or not problem_keys:
                try:
                    problem_keys = sorted(p.stem for p in (root / "problems").glob("*.json"))
                except OSError:
                    problem_keys = []
            records = manifest.get("record_count")
            if records is None:
                records_data = self.files.get_json(root / "records.json", []) or []
                records = len(records_data) if isinstance(records_data, list) else 0
            details = manifest.get("detail_count")
            if details is None:
                details = self._count_files(root / "submissions")
            users = len(scoreboard.get("rows", [])) if isinstance(scoreboard, dict) else 0
            analysis_path = self.analysis_path(domain, hid)
            analysis_exists = analysis_path.is_file()
            analysis_available = analysis_exists
            reports = self.report_files(domain, hid)

            updated_ts = max(
                self._mtime(root / "homework.json"),
                self._mtime(root / "manifest.json"),
                self._mtime(root / "records.json"),
                self._mtime(analysis_path),
            )
            out.append(
                {
                    "id": f"{domain}/{hid}",
                    "domain": domain,
                    "hid": hid,
                    "title": title,
                    "records": int(records or 0),
                    "details": int(details or 0),
                    "problems": len(problem_keys),
                    "users": int(users or 0),
                    "counts": {
                        "records": int(records or 0),
                        "details": int(details or 0),
                        "problems": len(problem_keys),
                        "users": int(users or 0),
                    },
                    "problem_keys": list(problem_keys),
                    "analysis": {
                        "available": analysis_available,
                        "updated_at": self._iso_mtime(analysis_path) if analysis_exists else None,
                        "size": analysis_path.stat().st_size if analysis_exists else 0,
                    },
                    "analysis_available": analysis_available,
                    "reports": reports,
                    "partial": not bool(manifest),
                    "completed_at": manifest.get("completed_at"),
                    "updated_at": _iso(updated_ts) if updated_ts else None,
                    "updated_ts": updated_ts,
                }
            )
        out.sort(key=lambda item: item.get("updated_ts", 0), reverse=True)
        for item in out:
            item.pop("updated_ts", None)
        return out

    # -- stats -------------------------------------------------------------
    @staticmethod
    def _snapshot_signature(root: Path) -> tuple[Any, ...]:
        parts: list[Any] = []
        for path in (
            root / "homework.json",
            root / "records.json",
            root / "scoreboard.json",
            root / "problems",
            root / "submissions",
            root / "manifest.json",
        ):
            try:
                stat = path.stat()
                parts.append((path.name, stat.st_mtime_ns, stat.st_size if path.is_file() else None))
            except OSError:
                parts.append((path.name, None, None))
        parts.append(("submission_count", ServerContext._count_files(root / "submissions")))
        return tuple(parts)

    def stats(self, domain: str, hid: str) -> dict[str, Any]:
        root = self.snapshot_root(domain, hid)
        key = str(root)
        signature = self._snapshot_signature(root)
        with self._stats_lock:
            cached = self._stats_cache.get(key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        try:
            snapshot = load_snapshot(root)
            data = snapshot_stats(snapshot)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"读取快照统计失败：{_short(exc)}") from exc
        # Do not ship raw problem statements (huge, HTML heavy); the GUI only
        # needs identifying metadata.
        sanitized: dict[str, dict[str, Any]] = {}
        for key_, problem in (data.get("problems") or {}).items():
            if not isinstance(problem, dict):
                continue
            sanitized[key_] = {
                "key": problem.get("key", key_),
                "title": problem.get("title", key_),
                "numeric_id": problem.get("numeric_id", 0),
                "points": problem.get("points", 0),
                "time_limit": problem.get("time_limit", ""),
                "memory_limit": problem.get("memory_limit", ""),
                "difficulty": problem.get("difficulty", ""),
                "tags": problem.get("tags", []),
            }
        data["problems"] = sanitized
        with self._stats_lock:
            self._stats_cache[key] = (signature, data)
            while len(self._stats_cache) > 8:
                self._stats_cache.pop(next(iter(self._stats_cache)))
        return data

    # -- analysis ----------------------------------------------------------
    def load_analysis(self, domain: str, hid: str) -> dict[str, Any] | None:
        path = self.analysis_path(domain, hid)
        data = self.analysis_cache.get_json(path, None)
        return data if isinstance(data, dict) else None

    def analysis_summary(self, domain: str, hid: str) -> dict[str, Any] | None:
        data = self.load_analysis(domain, hid)
        if data is None:
            return None
        pairs = data.get("pairs") if isinstance(data.get("pairs"), list) else []
        anomalies = data.get("anomalies") if isinstance(data.get("anomalies"), list) else []
        clusters = data.get("clusters") if isinstance(data.get("clusters"), list) else []
        final_scores = data.get("final_scores") if isinstance(data.get("final_scores"), list) else []
        return {
            "available": True,
            "schema_version": data.get("schema_version", 1),
            "domain": data.get("domain") or domain,
            "homework_id": data.get("homework_id") or hid,
            "title": data.get("title") or "",
            "generated_at": data.get("generated_at"),
            "config": data.get("config") or {},
            "stats": data.get("stats") or {},
            "per_problem": data.get("per_problem") or {},
            "final_score_summary": data.get("final_score_summary") or {},
            "counts": {
                "pairs": len(pairs),
                "clusters": len(clusters),
                "anomalies": len(anomalies),
                "final_scores": len(final_scores),
            },
            "reports": self.report_files(domain, hid),
        }

    # -- records / submissions --------------------------------------------
    def records_map(self, root: Path) -> dict[str, dict[str, Any]]:
        records = self.files.get_json(root / "records.json", []) or []
        if not isinstance(records, list):
            return {}
        return {str(rec.get("rid")): rec for rec in records if isinstance(rec, dict) and rec.get("rid")}

    def problem_data(self, root: Path, problem_key: str) -> dict[str, Any]:
        if not problem_key:
            return {}
        problem = self.files.get_json(root / "problems" / f"{_safe_segment(problem_key, '题目')}.json", {}) or {}
        if not isinstance(problem, dict):
            return {}
        return {
            "key": problem.get("key", problem_key),
            "title": problem.get("title", problem_key),
            "numeric_id": problem.get("numeric_id", 0),
            "points": problem.get("points", 0),
            "time_limit": problem.get("time_limit", ""),
            "memory_limit": problem.get("memory_limit", ""),
            "difficulty": problem.get("difficulty", ""),
            "tags": problem.get("tags", []),
        }

    def find_submission(
        self, rid: str, domain: str | None = None, hid: str | None = None
    ) -> tuple[str, str, Path, dict[str, Any]] | None:
        rid = _safe_segment(rid, "提交记录")
        candidates: list[tuple[str, str, Path]] = []
        if domain and hid:
            root = self.snapshot_root(domain, hid)
            candidates.append((domain, hid, root))
        else:
            candidates.extend(self.snapshot_dirs())
        for dom, hw, root in candidates:
            path = root / "submissions" / f"{rid}.json"
            if path.is_file():
                payload = self.files.get_json(path, {}) or {}
                if isinstance(payload, dict):
                    return dom, hw, root, payload
        return None

    def submission_payload(
        self, rid: str, domain: str | None = None, hid: str | None = None
    ) -> dict[str, Any]:
        found = self.find_submission(rid, domain, hid)
        if found is None:
            raise HTTPException(status_code=404, detail="提交记录不存在")
        dom, hw, root, payload = found
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            summary = self.records_map(root).get(rid, {})
        detail = payload.get("detail")
        if not isinstance(detail, dict):
            detail = {}
        problem_key = summary.get("problem_key", "") or ""
        problem = self.problem_data(root, problem_key)
        analysis_ctx: dict[str, Any] = {"available": False, "related_pairs": [], "anomaly": None}
        analysis = self.load_analysis(dom, hw)
        if analysis is not None:
            pairs = [
                pair
                for pair in (analysis.get("pairs") or [])
                if isinstance(pair, dict) and rid in {pair.get("rid_a"), pair.get("rid_b")}
            ]
            pairs.sort(key=lambda p: _number(p.get("score")), reverse=True)
            anomalies = [
                anomaly
                for anomaly in (analysis.get("anomalies") or [])
                if isinstance(anomaly, dict)
                and (
                    rid in {str(x) for x in (anomaly.get("evidence") or [])}
                    or int(anomaly.get("uid") or 0) == int(summary.get("uid") or 0)
                )
            ]
            analysis_ctx = {
                "available": True,
                "related_pairs": pairs[:10],
                "anomaly": anomalies[0] if anomalies else None,
            }
        return {
            "rid": rid,
            "domain": dom,
            "hid": hw,
            "summary": summary,
            "detail": detail,
            "language": detail.get("language") or summary.get("language") or "",
            "problem": problem,
            "analysis": analysis_ctx,
        }

    # -- final scores ------------------------------------------------------
    def _problem_list(self, root: Path) -> list[dict[str, Any]]:
        snapshot = load_snapshot(root, load_submissions=False)
        problems = []
        for problem in sorted(snapshot.problems.values(), key=lambda p: str(p.get("key", ""))):
            problems.append(
                {
                    "key": problem.get("key", ""),
                    "title": problem.get("title", problem.get("key", "")),
                    "points": int(problem.get("points") or 0),
                }
            )
        return problems

    @staticmethod
    def _normalise_students(
        rows: Any, problems: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        if not isinstance(rows, list) or not rows:
            return None
        # Accept the canonical shape written by analysis.engine:
        #   [{uid, username, display_name, total, total_points, problems:{key:cell}}]
        normalised: list[dict[str, Any]] = []
        problem_meta = {p["key"]: p for p in problems}
        for row in rows:
            if not isinstance(row, dict):
                return None
            uid = int(row.get("uid") or 0)
            if not uid:
                return None
            cells = row.get("problems")
            if not isinstance(cells, dict):
                return None
            out_cells: dict[str, dict[str, Any]] = {}
            total = 0.0
            for key, meta in problem_meta.items():
                cell = cells.get(key) if isinstance(cells.get(key), dict) else {}
                if not cell:
                    continue
                score = _number(cell.get("score"))
                total += score
                out_cells[key] = {
                    "problem": key,
                    "title": cell.get("title") or meta.get("title", key),
                    "points": int(cell.get("points") or meta.get("points") or 0),
                    "score": _score_value(score),
                    "status": cell.get("status") or ("未提交" if not cell.get("rid") else ""),
                    "rid": cell.get("rid") or "",
                    "submit_at": cell.get("submit_at") or "",
                    "last_submission": bool(cell.get("rid")),
                }
            for key, cell in cells.items():
                if key in out_cells or not isinstance(cell, dict):
                    continue
                out_cells[key] = dict(cell)
            normalised.append(
                {
                    "uid": uid,
                    "username": row.get("username") or row.get("display_name") or "",
                    "display_name": row.get("display_name") or row.get("username") or "",
                    "total": _score_value(total if row.get("total") is None else _number(row.get("total"), total)),
                    "total_points": int(
                        row.get("total_points")
                        if row.get("total_points") is not None
                        else sum(int(p.get("points") or 0) for p in problems)
                    ),
                    "problems": out_cells,
                }
            )
        normalised.sort(key=lambda r: (-_number(r.get("total")), str(r.get("username"))))
        return normalised

    @staticmethod
    def _compute_final_scores(snapshot: Snapshot) -> list[dict[str, Any]]:
        problems = sorted(snapshot.problems.values(), key=lambda p: str(p.get("key", "")))
        problem_order = [str(p.get("key", "")) for p in problems]
        points = {str(p.get("key", "")): int(p.get("points") or 0) for p in problems}
        titles = {str(p.get("key", "")): p.get("title", p.get("key", "")) for p in problems}
        users: dict[int, dict[str, Any]] = {}
        for row in (snapshot.scoreboard.get("rows") or []):
            if not isinstance(row, dict):
                continue
            uid = int(row.get("uid") or 0)
            if uid:
                users.setdefault(
                    uid,
                    {
                        "uid": uid,
                        "username": row.get("username", ""),
                        "display_name": row.get("display_name", ""),
                        "subs": [],
                    },
                )
        extra_problems: set[str] = set()
        for rec in snapshot.records:
            if not isinstance(rec, dict):
                continue
            uid = int(rec.get("uid") or 0)
            user = users.setdefault(
                uid,
                {
                    "uid": uid,
                    "username": rec.get("username", ""),
                    "display_name": rec.get("display_name", ""),
                    "subs": [],
                },
            )
            user["subs"].append(rec)
            key = str(rec.get("problem_key") or "")
            if key and key not in points:
                extra_problems.add(key)
        for key in extra_problems:
            problem_order.append(key)
            points.setdefault(key, 0)
            titles.setdefault(key, key)
        out: list[dict[str, Any]] = []
        for uid, user in users.items():
            latest: dict[str, dict[str, Any]] = {}
            for rec in user["subs"]:
                key = str(rec.get("problem_key") or "")
                if not key:
                    continue
                current = latest.get(key)
                if current is None or (
                    int(rec.get("submit_ts") or 0),
                    str(rec.get("rid") or ""),
                ) > (
                    int(current.get("submit_ts") or 0),
                    str(current.get("rid") or ""),
                ):
                    latest[key] = rec
            cells: dict[str, dict[str, Any]] = {}
            total = 0.0
            for key in problem_order:
                rec = latest.get(key)
                score = _number(rec.get("score")) if rec else 0.0
                total += score
                cells[key] = {
                    "problem": key,
                    "title": titles.get(key, key),
                    "points": points.get(key, 0),
                    "score": _score_value(score),
                    "status": (rec.get("status_text") or rec.get("status") or "未提交") if rec else "未提交",
                    "rid": rec.get("rid", "") if rec else "",
                    "submit_at": rec.get("submit_at", "") if rec else "",
                    "last_submission": bool(rec),
                }
            out.append(
                {
                    "uid": uid,
                    "username": user["username"] or user["display_name"],
                    "display_name": user["display_name"] or user["username"],
                    "total": _score_value(total),
                    "total_points": sum(points.values()),
                    "problems": cells,
                }
            )
        out.sort(key=lambda r: (-_number(r.get("total")), str(r.get("username"))))
        return out

    @staticmethod
    def _final_summary(students: list[dict[str, Any]], problems: list[dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for problem in problems:
            key = problem["key"]
            scores = [
                _number((row.get("problems") or {}).get(key, {}).get("score"))
                for row in students
                if isinstance((row.get("problems") or {}).get(key), dict)
            ]
            if not scores:
                continue
            full = max(scores) if scores else 0
            summary[key] = {
                "students": len(scores),
                "mean": round(sum(scores) / len(scores), 2),
                "full_score": _score_value(full),
                "full_marks": sum(1 for value in scores if full and value >= full),
                "zero": sum(1 for value in scores if value == 0),
            }
        return summary

    def final_scores(self, domain: str, hid: str) -> dict[str, Any]:
        root = self.snapshot_root(domain, hid)
        problems = self._problem_list(root)
        analysis = self.load_analysis(domain, hid)
        source = "records"
        generated_at = None
        students: list[dict[str, Any]] | None = None
        summary: dict[str, Any] | None = None
        if analysis is not None:
            students = self._normalise_students(analysis.get("final_scores"), problems)
            if students:
                source = "analysis"
                generated_at = analysis.get("generated_at")
                summary = analysis.get("final_score_summary")
        if students is None:
            try:
                snapshot = load_snapshot(root, load_submissions=False)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"读取快照失败：{_short(exc)}") from exc
            students = self._compute_final_scores(snapshot)
        if summary is None:
            summary = self._final_summary(students, problems)
        payload = {
            "available": True,
            "source": source,
            "generated_at": generated_at,
            "domain": domain,
            "hid": hid,
            "problems": problems,
            "students": students,
            "final_scores": students,
            "summary": summary,
            "note": "每题最终分取该学生该题按 submit_ts 排序的最后一次提交；不使用历史最高分。",
        }
        return payload


# --------------------------------------------------------------------------- #
# request models
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    base_url: str
    domain: str
    username: str
    password: str


class AcquireRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    homework_id: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    domain: str
    hid: str = ""
    homework_id: str = ""
    threshold: float = 0.6
    max_pairs: int = 20000
    with_evidence: bool = True


class AIReviewRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    domain: str
    hid: str
    rid_a: str
    rid_b: str
    options: dict[str, Any] = Field(default_factory=dict)


class JudgeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    domain: str
    hid: str
    rid: str
    time_limit_ms: int = 5000
    memory_limit_mb: int = 256
    language: str = ""


class JudgeBatchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    domain: str
    hid: str
    problem: str = ""
    limit: int | None = 50
    all: bool = False
    accepted_only: bool = False
    language: str = ""
    workers: int = 1


class ProjectFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = ""
    size: int = 0
    content_base64: str = ""
    text: str = ""
    mime: str = ""


class ProjectEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    domain: str = ""
    hid: str = ""
    title: str = ""
    student: str = ""
    description: str = ""
    rubric: str = ""
    files: list[ProjectFile] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# request helpers
# --------------------------------------------------------------------------- #
def _normalise_base_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="OJ 地址不能为空")
    if "://" not in value:
        value = "http://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="OJ 地址必须是 http(s) URL")
    return value.rstrip("/")


def _serialise_homeworks(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items or []:
        try:
            if hasattr(item, "to_dict"):
                data = item.to_dict()
            elif isinstance(item, dict):
                data = dict(item)
            else:
                data = {
                    key: getattr(item, key)
                    for key in ("id", "title", "status", "open_at", "deadline", "description_text")
                    if hasattr(item, key)
                }
            out.append(_jsonable(data))
        except Exception:  # noqa: BLE001 - one broken row must not sink the list
            continue
    return out


def _session_payload(record: SessionRecord) -> dict[str, Any]:
    now = _now()
    return {
        "authenticated": True,
        "user": {
            "username": record.username,
            "base_url": record.base_url,
            "domain": record.domain,
        },
        "homeworks": record.homeworks or [],
        "homeworks_error": record.homeworks_error,
        "expires_in": max(0, int(record.expires_at - now)),
        "expires_at": _iso(record.expires_at),
    }


def _acquire_options(options: dict[str, Any], *, data_dir: Path, homework_id: str) -> Any:
    from ..acquire import AcquireOptions

    def integer(name: str, default: int, low: int, high: int) -> int:
        return max(low, min(high, int(_number(options.get(name), default))))

    def optional_int(name: str) -> int | None:
        raw = options.get(name)
        if raw in (None, "", 0, "0"):
            return None
        return max(1, int(_number(raw, 1)))

    return AcquireOptions(
        homework_id=homework_id,
        data_dir=data_dir,
        workers=integer("workers", 6, 1, 16),
        download_testdata=_flag(options.get("download_testdata"), True),
        download_records=_flag(options.get("download_records"), True),
        limit_records=optional_int("limit_records"),
        max_pages=optional_int("max_pages"),
        raw_html=_flag(options.get("raw_html"), False),
        force=_flag(options.get("force"), False),
    )


# --------------------------------------------------------------------------- #
# lazy module helpers (AI / judge must never break server import)
# --------------------------------------------------------------------------- #
def _import_optional(module_name: str, attr: str | None = None) -> tuple[Any | None, str | None]:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {_short(exc, 200)}"
    if attr is None:
        return module, None
    target = getattr(module, attr, None)
    if target is None:
        return None, f"{module_name} 中未找到 {attr}"
    return target, None


def _build_agent(options: dict[str, Any] | None = None) -> tuple[Any | None, str | None]:
    options = options or {}
    module, error = _import_optional("oj_sentinel.ai.agent")
    if module is None:
        return None, error
    config_class = getattr(module, "AgentConfig", None)
    config = None
    if callable(config_class):
        kwargs: dict[str, Any] = {}
        for key in ("provider", "model", "base_url", "api_key_env", "timeout", "reasoning_effort"):
            if options.get(key) is not None:
                kwargs[key] = options[key]
        try:
            config = config_class(**kwargs) if kwargs else config_class()
        except Exception:  # noqa: BLE001 - fall back to default construction
            try:
                config = config_class()
            except Exception as exc:  # noqa: BLE001
                return None, f"AgentConfig 初始化失败: {_short(exc)}"
    for factory_name in ("create_agent", "get_agent", "make_agent", "build_agent", "default_agent"):
        factory = getattr(module, factory_name, None)
        if callable(factory):
            try:
                return (factory(config) if config is not None else factory()), None
            except TypeError:
                try:
                    return factory(), None
                except Exception as exc:  # noqa: BLE001
                    return None, f"{factory_name} 失败: {_short(exc)}"
            except Exception as exc:  # noqa: BLE001
                return None, f"{factory_name} 失败: {_short(exc)}"
    agent_class = getattr(module, "Agent", None)
    if callable(agent_class):
        try:
            return (agent_class(config) if config is not None else agent_class()), None
        except TypeError:
            try:
                return agent_class(), None
            except Exception as exc:  # noqa: BLE001
                return None, f"Agent 构造失败: {_short(exc)}"
        except Exception as exc:  # noqa: BLE001
            return None, f"Agent 构造失败: {_short(exc)}"
    # A bare ai.agent module may already expose a ready instance.
    for attr in ("agent", "AGENT"):
        candidate = getattr(module, attr, None)
        if candidate is not None and callable(getattr(candidate, "complete", None)):
            return candidate, None
    return None, "oj_sentinel.ai.agent 未提供 Agent/工厂函数"


def _load_ai_review_helper() -> tuple[Any | None, str | None]:
    for module_name in ("oj_sentinel.ai.prompts", "oj_sentinel.ai.review", "oj_sentinel.ai"):
        helper, error = _import_optional(module_name, "review_pair")
        if helper is not None:
            return helper, None
    return None, "未找到 review_pair（oj_sentinel.ai.prompts 尚未提供）"


def _load_project_helper() -> tuple[Any | None, str | None]:
    for module_name in ("oj_sentinel.ai.project", "oj_sentinel.ai.evaluate", "oj_sentinel.ai.prompts", "oj_sentinel.ai"):
        helper, error = _import_optional(module_name, "evaluate_project")
        if helper is not None:
            return helper, None
    return None, "未找到 evaluate_project（作品评测后端尚未提供）"


def _normalise_agent_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        data = _jsonable(result)
    elif isinstance(result, dict):
        data = dict(result)
    else:
        data = {
            key: getattr(result, key)
            for key in ("text", "data", "provider", "model", "usage", "error")
            if hasattr(result, key)
        }
        data = _jsonable(data)
    text = data.get("text") if isinstance(data.get("text"), str) else ""
    verdict = data.get("data")
    if verdict is None and text:
        try:
            verdict = json.loads(text)
        except ValueError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    verdict = json.loads(text[start : end + 1])
                except ValueError:
                    verdict = None
    data["verdict"] = verdict
    if not data.get("provider"):
        data["provider"] = None
    if not data.get("model"):
        data["model"] = None
    return data


# --------------------------------------------------------------------------- #
# background runners
# --------------------------------------------------------------------------- #
class _AnalysisSignatureError(RuntimeError):
    pass


def _run_analysis_inprocess(
    context: ServerContext,
    job: Job,
    *,
    root: Path,
    domain: str,
    hid: str,
    threshold: float,
    max_pairs: int,
    with_evidence: bool,
) -> dict[str, Any]:
    engine, error = _import_optional("oj_sentinel.analysis.engine")
    if engine is None:
        raise ImportError(error or "analysis.engine 不可用")
    runner = getattr(engine, "run_analysis", None)
    if not callable(runner):
        raise ImportError("analysis.engine 未提供 run_analysis")
    out_dir = context.analysis_dir(domain, hid)
    out_dir.mkdir(parents=True, exist_ok=True)

    def on_event(event: Any) -> None:
        if isinstance(event, dict):
            message = event.get("message") or event.get("msg") or event.get("detail") or ""
            if event.get("done") is not None and event.get("total"):
                context.jobs.set_progress(job, 100 * _number(event.get("done")) / max(1, _number(event.get("total"))))
            if message:
                context.jobs.add_log(job, message)
            return
        if isinstance(event, (int, float)):
            context.jobs.set_progress(job, event)
            return
        message = str(event or "").strip()
        if not message:
            return
        context.jobs.add_log(job, message)
        match = re.search(r"(\d+)\s*/\s*(\d+)", message)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            context.jobs.set_progress(job, 100 * done / max(1, total))
        if "written to" in message or "analysis written" in message:
            context.jobs.set_progress(job, 99)

    signature = inspect.signature(runner)
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    mapping = {
        "snapshot_root": root,
        "snapshot": root,
        "root": root,
        "root_dir": root,
        "snapshot_dir": root,
        "path": root,
        "out_dir": out_dir,
        "output_dir": out_dir,
        "output_path": out_dir / "analysis.json",
        "out_path": out_dir / "analysis.json",
        "data_dir": context.data_dir,
        "domain": domain,
        "homework_id": hid,
        "hid": hid,
        "threshold": threshold,
        "min_score": threshold,
        "max_pairs": max_pairs,
        "with_evidence": with_evidence,
        "on_event": on_event,
        "progress": on_event,
        "on_progress": on_event,
        "callback": on_event,
    }
    try:
        for name, parameter in signature.parameters.items():
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL or parameter.kind is inspect.Parameter.VAR_KEYWORD:
                continue
            key = name.lower()
            if key in mapping:
                if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                    args.append(mapping[key])
                else:
                    kwargs[name] = mapping[key]
            elif parameter.default is inspect.Parameter.empty:
                raise _AnalysisSignatureError(f"不支持的必填参数: {name}")
    except _AnalysisSignatureError:
        raise
    except (TypeError, ValueError):
        # Builtins / C callables: just call with the canonical signature.
        args, kwargs = [root], {}
    context.jobs.add_log(job, f"调用分析引擎 run_analysis({', '.join(kwargs)})")
    payload = runner(*args, **kwargs)
    path = out_dir / "analysis.json"
    if not path.is_file() and isinstance(payload, dict):
        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        context.jobs.add_log(job, f"分析结果已写入 {path}")
    return _jsonable(payload) if isinstance(payload, dict) else {"path": str(path)}


def _search_subprocess_analysis(context: ServerContext, domain: str, hid: str, out_dir: Path) -> Path | None:
    """Find an analysis.json written by a subprocess with relative defaults."""
    candidates = [
        out_dir / "analysis.json",
        Path.cwd() / "data" / "analysis" / domain / hid / "analysis.json",
        context.data_dir / "analysis" / domain / hid / "analysis.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            if candidate.resolve() != (out_dir / "analysis.json").resolve():
                try:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / "analysis.json").write_bytes(candidate.read_bytes())
                except OSError:
                    continue
            return out_dir / "analysis.json"
    return None


def _run_analysis_subprocess(
    context: ServerContext,
    job: Job,
    *,
    root: Path,
    domain: str,
    hid: str,
    threshold: float,
) -> dict[str, Any]:
    out_dir = context.analysis_dir(domain, hid)
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "oj_sentinel.analysis.engine",
        "--snapshot",
        str(root),
        "--out-dir",
        str(out_dir),
        "--threshold",
        str(threshold),
    ]
    context.jobs.add_log(job, "未加载到 in-process 分析引擎，改用子进程：" + " ".join(command[1:]))
    env = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = package_parent + (os.pathsep + existing if existing else "")
    env.setdefault("OJS_DATA_DIR", str(context.data_dir))
    process = subprocess.Popen(  # noqa: S603 - fixed command, local tool
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        message = line.rstrip()
        if not message:
            continue
        level = "error" if re.search(r"\b(error|traceback|exception)\b", message, re.I) else "info"
        context.jobs.add_log(job, message, level=level)
        match = re.search(r"(\d+)\s*/\s*(\d+)", message)
        if match:
            context.jobs.set_progress(job, 100 * int(match.group(1)) / max(1, int(match.group(2))))
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(f"分析子进程退出码 {returncode}")
    found = _search_subprocess_analysis(context, domain, hid, out_dir)
    if found is None:
        raise FileNotFoundError("分析子进程结束，但未找到 analysis.json")
    return {"path": str(found)}


def _analysis_result_payload(context: ServerContext, domain: str, hid: str) -> dict[str, Any]:
    path = context.analysis_path(domain, hid)
    data = context.load_analysis(domain, hid)
    return {
        "analysis_path": str(path),
        "available": data is not None,
        "stats": (data or {}).get("stats") or {},
        "counts": {
            "pairs": len((data or {}).get("pairs") or []),
            "clusters": len((data or {}).get("clusters") or []),
            "anomalies": len((data or {}).get("anomalies") or []),
            "final_scores": len((data or {}).get("final_scores") or []),
        },
    }


def _status_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _compact_judge_rows(results: list[Any]) -> list[dict[str, Any]]:
    """Size-conscious result rows for the GUI and the CSV export."""
    rows: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        local_status = str(result.get("status") or "")
        error = str(result.get("error") or "")
        oj_status = str(result.get("oj_status") or "")
        if not local_status and error:
            local_status = "Error"
        rows.append(
            {
                "rid": str(result.get("rid") or ""),
                "problem": str(result.get("problem") or ""),
                "uid": int(result.get("uid") or 0),
                "username": str(result.get("username") or ""),
                "language": str(result.get("language") or ""),
                "local_status": local_status,
                "oj_status": oj_status,
                "matched": bool(local_status and oj_status and _status_key(local_status) == _status_key(oj_status)),
                "score": _score_value(result.get("score", 0)),
                "passed": int(result.get("passed") or 0),
                "total": int(result.get("total") or 0),
                "elapsed_ms": int(result.get("elapsed_ms") or 0),
                "error": _short(error, 500),
            }
        )
    return rows


def _write_judge_batch_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rid", "problem", "uid", "username", "language",
        "local_status", "oj_status", "matched", "score",
        "passed", "total", "elapsed_ms", "error",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)



# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
def _register_routes(app: FastAPI, context: ServerContext) -> None:
    # -- meta --------------------------------------------------------------
    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": "oj-sentinel",
            "version": VERSION,
            "data_dir": str(context.data_dir),
            "snapshots": len(context.snapshot_dirs()),
            "jobs_running": len(context.jobs.running()),
            "sessions": context.sessions.count(),
            "time": _iso(),
        }

    # -- auth --------------------------------------------------------------
    @app.post("/api/session", tags=["auth"])
    def create_session(payload: LoginRequest, request: Request, response: Response):
        base_url = _normalise_base_url(payload.base_url)
        domain = _safe_segment(payload.domain.strip().strip("/"), "域名")
        username = payload.username.strip()
        if not username or not payload.password:
            raise HTTPException(status_code=400, detail="用户名和密码不能为空")
        try:
            from ..hydro.session import HydroAuthError, HydroClient
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Hydro 客户端不可用：{_short(exc)}") from exc
        client = HydroClient(base_url, domain)
        try:
            # HydroClient.login performs the login POST *and* verify_login().
            client.login(username, payload.password)
        except HydroAuthError as exc:
            raise HTTPException(status_code=401, detail=f"登录失败：{_short(exc)}") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"无法连接 OJ：{_short(exc)}") from exc
        homeworks: list[dict[str, Any]] = []
        homeworks_error = None
        try:
            homeworks = _serialise_homeworks(client.list_homeworks())
        except Exception as exc:  # noqa: BLE001
            homeworks_error = f"作业列表获取失败：{_short(exc)}"
        record = context.sessions.create(
            client=client,
            username=username,
            base_url=base_url,
            domain=domain,
            homeworks=homeworks,
        )
        record.homeworks_error = homeworks_error
        response.set_cookie(
            key=COOKIE_NAME,
            value=record.token,
            max_age=SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        data = _session_payload(record)
        if homeworks_error:
            data["warning"] = homeworks_error
        return data

    @app.get("/api/session", tags=["auth"])
    def get_session(request: Request, refresh: bool = False):
        record = _require_session(context, request)
        now = _now()
        needs_refresh = (
            refresh
            or record.homeworks is None
            or (not record.homeworks and now - record.homework_refreshed_at > 30)
        )
        if needs_refresh:
            try:
                from ..hydro.session import HydroAuthError

                try:
                    record.homeworks = _serialise_homeworks(record.client.list_homeworks())
                    record.homeworks_error = None
                except HydroAuthError as exc:
                    context.sessions.delete(record.token)
                    raise HTTPException(status_code=401, detail=f"登录已失效：{_short(exc)}") from exc
                except Exception as exc:  # noqa: BLE001
                    record.homeworks = record.homeworks or []
                    record.homeworks_error = f"刷新作业列表失败：{_short(exc)}"
            finally:
                record.homework_refreshed_at = now
        return _session_payload(record)

    @app.delete("/api/session", tags=["auth"])
    def delete_session(request: Request, response: Response):
        token = request.cookies.get(COOKIE_NAME)
        context.sessions.delete(token)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True, "authenticated": False}

    # -- snapshots ---------------------------------------------------------
    @app.get("/api/snapshots", tags=["snapshots"])
    def list_snapshots() -> list[dict[str, Any]]:
        return context.snapshot_summaries()

    @app.get("/api/snapshots/{domain}/{hid}/stats", tags=["snapshots"])
    def get_stats(domain: str, hid: str) -> dict[str, Any]:
        return context.stats(domain, hid)

    # -- jobs / acquisition ------------------------------------------------
    @app.post("/api/acquire", tags=["jobs"])
    def start_acquire(payload: AcquireRequest, request: Request):
        record = _require_session(context, request)
        options = dict(payload.options or {})
        for key in (
            "workers",
            "download_testdata",
            "download_records",
            "limit_records",
            "max_pages",
            "raw_html",
            "force",
            "repair",
        ):
            if key in (payload.model_extra or {}):
                options[key] = (payload.model_extra or {})[key]
        homework_id = (payload.homework_id or options.pop("homework_id", "") or "").strip()
        homework_id = _safe_segment(homework_id, "作业 ID")
        acquire_options = _acquire_options(options, data_dir=context.data_dir, homework_id=homework_id)
        repair = _flag(options.get("repair"), False)
        job = context.jobs.create("acquire", f"采集 {homework_id}")
        context.jobs.set_message(job, "排队中")
        context.jobs.set_progress(job, 1)
        client = record.client
        data_dir = context.data_dir
        jobs = context.jobs

        def on_event(event: dict[str, Any]) -> None:
            if not isinstance(event, dict):
                jobs.add_log(job, event)
                return
            kind = str(event.get("kind") or "info")
            level = "error" if kind == "error" else "warning" if kind == "warning" else "info"
            message = str(event.get("message") or "").strip()
            if message:
                jobs.add_log(job, message, level=level)
            if event.get("done") is not None and event.get("total"):
                done, total = _number(event.get("done")), _number(event.get("total"))
                jobs.set_progress(job, 100 * done / max(1, total))
            elif kind in {"phase", "page"}:
                jobs.set_progress(job, min(95, job.progress + 2))

        def runner() -> dict[str, Any]:
            from ..acquire import acquire, repair_details

            jobs.add_log(job, f"开始{'修复' if repair else '采集'}：{homework_id} @ {record.domain}")
            if repair:
                result = repair_details(client, acquire_options, on_event)
            else:
                result = acquire(client, acquire_options, on_event)
            payload = result.to_dict() if hasattr(result, "to_dict") else _jsonable(result)
            jobs.add_log(
                job,
                f"快照完成：{payload.get('record_count', 0)} 条提交，"
                f"{payload.get('detail_count', 0)} 条详情",
                level="success",
            )
            return payload

        context.jobs.start(job, runner)
        return JSONResponse(status_code=202, content=_jsonable(job_dump(context, job)))

    @app.post("/api/analyze", tags=["jobs"])
    def start_analyze(payload: AnalyzeRequest):
        domain = _safe_segment(payload.domain, "域名")
        hid = _safe_segment(payload.hid or payload.homework_id, "作业 ID")
        root = context.snapshot_root(domain, hid)
        threshold = max(0.0, min(1.0, _number(payload.threshold, 0.6)))
        max_pairs = max(1, min(50000, int(_number(payload.max_pairs, 20000))))
        job = context.jobs.create("analyze", f"分析 {hid}")
        context.jobs.set_message(job, "排队中")
        context.jobs.set_progress(job, 1)
        jobs = context.jobs

        def runner() -> dict[str, Any]:
            jobs.add_log(job, f"开始分析：{domain}/{hid}（阈值 {threshold:.2f}）")
            jobs.set_progress(job, 5)
            in_process = True
            try:
                result = _run_analysis_inprocess(
                    context,
                    job,
                    root=root,
                    domain=domain,
                    hid=hid,
                    threshold=threshold,
                    max_pairs=max_pairs,
                    with_evidence=bool(payload.with_evidence),
                )
            except _AnalysisSignatureError as exc:
                jobs.add_log(job, f"分析引擎签名不兼容（{exc}），改用子进程", level="warning")
                in_process = False
            except ImportError as exc:
                jobs.add_log(job, f"分析引擎不可用（{_short(exc)}），改用子进程", level="warning")
                in_process = False
            if not in_process:
                result = _run_analysis_subprocess(
                    context, job, root=root, domain=domain, hid=hid, threshold=threshold
                )
            path = context.analysis_path(domain, hid)
            data = context.load_analysis(domain, hid)
            if data is None:
                raise FileNotFoundError(f"分析结束但未找到 {path}")
            jobs.set_progress(job, 99)
            stats = data.get("stats") or {}
            jobs.add_log(
                job,
                f"分析完成：{stats.get('pairs', 0)} 对可疑 / "
                f"{stats.get('anomalies', 0)} 个异常 / {stats.get('clusters', 0)} 个聚类",
                level="success",
            )
            payload_out = _analysis_result_payload(context, domain, hid)
            payload_out["engine"] = "in-process" if in_process else "subprocess"
            if isinstance(result, dict) and result.get("path"):
                payload_out["path"] = result["path"]
            return payload_out

        context.jobs.start(job, runner)
        return JSONResponse(status_code=202, content=_jsonable(job_dump(context, job)))

    @app.get("/api/jobs", tags=["jobs"])
    def list_jobs(kind: str | None = None, limit: int = Query(50, ge=1, le=500)):
        return context.jobs.list(kind=kind, limit=limit)

    @app.get("/api/jobs/{job_id}", tags=["jobs"])
    def get_job(job_id: str):
        job = context.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job

    # -- analysis ----------------------------------------------------------
    @app.get("/api/snapshots/{domain}/{hid}/analysis", tags=["analysis"])
    def get_analysis(domain: str, hid: str):
        summary = context.analysis_summary(domain, hid)
        if summary is None:
            return JSONResponse(
                status_code=404,
                content={
                    "available": False,
                    "detail": "尚未生成分析结果",
                    "hint": f"运行 `ojs analyze --homework {hid}`，或点击“运行分析”。",
                },
            )
        return summary

    @app.get("/api/snapshots/{domain}/{hid}/pairs", tags=["analysis"])
    def get_pairs(
        domain: str,
        hid: str,
        min_score: float = Query(0.0, ge=0.0, le=1.0),
        problem: str | None = None,
        limit: int = Query(200, ge=1, le=5000),
    ):
        analysis = context.load_analysis(domain, hid)
        if analysis is None:
            return JSONResponse(
                status_code=404,
                content={
                    "available": False,
                    "pairs": [],
                    "detail": "尚未生成分析结果",
                    "hint": "运行分析后即可查看可疑代码对。",
                },
            )
        pairs = [p for p in (analysis.get("pairs") or []) if isinstance(p, dict)]
        total = len(pairs)
        if problem:
            wanted = problem.strip().lower()
            pairs = [p for p in pairs if str(p.get("problem", "")).lower() == wanted]
        pairs = [p for p in pairs if _number(p.get("score")) >= min_score]
        pairs.sort(key=lambda p: _number(p.get("score")), reverse=True)
        matched = len(pairs)
        return {
            "available": True,
            "pairs": pairs[:limit],
            "total": total,
            "matched": matched,
            "returned": min(limit, matched),
            "min_score": min_score,
            "problem": problem or "",
            "limit": limit,
            "generated_at": analysis.get("generated_at"),
            "threshold": (analysis.get("config") or {}).get("threshold"),
        }

    @app.get("/api/snapshots/{domain}/{hid}/anomalies", tags=["analysis"])
    def get_anomalies(
        domain: str,
        hid: str,
        min_score: float = Query(0.0, ge=0.0, le=1.0),
        limit: int = Query(200, ge=1, le=2000),
    ):
        analysis = context.load_analysis(domain, hid)
        if analysis is None:
            return JSONResponse(
                status_code=404,
                content={
                    "available": False,
                    "anomalies": [],
                    "detail": "尚未生成分析结果",
                    "hint": "运行分析后即可查看异常行为。",
                },
            )
        anomalies = [a for a in (analysis.get("anomalies") or []) if isinstance(a, dict)]
        anomalies = [a for a in anomalies if _number(a.get("score")) >= min_score]
        anomalies.sort(key=lambda a: _number(a.get("score")), reverse=True)
        return {
            "available": True,
            "anomalies": anomalies[:limit],
            "total": len(anomalies),
            "min_score": min_score,
            "limit": limit,
            "generated_at": analysis.get("generated_at"),
        }

    @app.get("/api/snapshots/{domain}/{hid}/clusters", tags=["analysis"])
    def get_clusters(domain: str, hid: str):
        analysis = context.load_analysis(domain, hid)
        if analysis is None:
            return JSONResponse(
                status_code=404,
                content={
                    "available": False,
                    "clusters": [],
                    "detail": "尚未生成分析结果",
                    "hint": "运行分析后即可查看聚类团伙。",
                },
            )
        root = context.snapshot_root(domain, hid)
        records = context.records_map(root)
        clusters = []
        for cluster in analysis.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            item = dict(cluster)
            members = [str(x) for x in (cluster.get("members") or [])]
            item["members"] = members
            item["size"] = int(cluster.get("size") or len(members))
            item["usernames"] = [
                (records.get(rid) or {}).get("username")
                or (records.get(rid) or {}).get("display_name")
                or ""
                for rid in members
            ]
            clusters.append(item)
        clusters.sort(key=lambda c: (-int(c.get("size") or 0), str(c.get("root"))))
        return {"available": True, "clusters": clusters, "total": len(clusters)}

    @app.get("/api/snapshots/{domain}/{hid}/final-scores", tags=["analysis"])
    def get_final_scores(domain: str, hid: str):
        return context.final_scores(domain, hid)

    @app.get("/api/submissions/{rid}", tags=["analysis"])
    def get_submission(rid: str, domain: str | None = None, hid: str | None = None):
        return context.submission_payload(rid, domain, hid)

    # -- AI review / judge -------------------------------------------------
    @app.post("/api/ai/review", tags=["ai"])
    def ai_review(payload: AIReviewRequest):
        domain = _safe_segment(payload.domain, "域名")
        hid = _safe_segment(payload.hid, "作业 ID")
        root = context.snapshot_root(domain, hid)
        helper, helper_error = _load_ai_review_helper()
        if helper is None:
            return JSONResponse(
                status_code=501,
                content={
                    "available": False,
                    "error": "AI 复核模块尚未就绪",
                    "detail": helper_error,
                    "hint": "需要 oj_sentinel.ai.agent 与 oj_sentinel.ai.prompts.review_pair；本地查重仍可正常使用。",
                },
            )
        sub_a_payload = context.submission_payload(payload.rid_a, domain, hid)
        sub_b_payload = context.submission_payload(payload.rid_b, domain, hid)
        summary_a = sub_a_payload.get("summary") or {}
        summary_b = sub_b_payload.get("summary") or {}
        problem_key = summary_a.get("problem_key") or summary_b.get("problem_key") or ""
        snapshot = None
        try:
            snapshot = load_snapshot(root, load_submissions=False)
        except Exception:  # noqa: BLE001 - problem metadata is optional
            snapshot = None
        problem = context.problem_data(root, problem_key)
        analysis = context.load_analysis(domain, hid) or {}
        pair = next(
            (
                p
                for p in (analysis.get("pairs") or [])
                if isinstance(p, dict)
                and {p.get("rid_a"), p.get("rid_b")} == {payload.rid_a, payload.rid_b}
            ),
            None,
        )
        if pair is None:
            pair = {
                "problem": problem_key,
                "rid_a": payload.rid_a,
                "rid_b": payload.rid_b,
                "uid_a": summary_a.get("uid", 0),
                "uid_b": summary_b.get("uid", 0),
                "username_a": summary_a.get("username", ""),
                "username_b": summary_b.get("username", ""),
                "score": 0.0,
                "same_user": summary_a.get("uid") == summary_b.get("uid"),
                "exact": False,
                "evidence": [],
            }
        agent, agent_error = _build_agent(payload.options)
        if agent is None:
            return JSONResponse(
                status_code=501,
                content={
                    "available": False,
                    "error": "AI Agent 尚未就绪",
                    "detail": agent_error,
                    "hint": "配置 DSH/OpenAI 或使用 MockAgent 后重试；本次未发送任何学生代码。",
                },
            )
        sub_a = _submission_object(sub_a_payload, hid)
        sub_b = _submission_object(sub_b_payload, hid)
        try:
            result = helper(agent, problem, sub_a, sub_b, pair)
        except TypeError as exc:
            # Some helpers may prefer plain dicts; retry once with dicts.
            try:
                result = helper(
                    agent,
                    problem,
                    _jsonable(sub_a),
                    _jsonable(sub_b),
                    pair,
                )
            except Exception as retry_exc:  # noqa: BLE001
                return JSONResponse(
                    status_code=502,
                    content={
                        "available": True,
                        "error": "AI 复核失败",
                        "detail": _short(retry_exc or exc),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=502,
                content={"available": True, "error": "AI 复核失败", "detail": _short(exc)},
            )
        data = _normalise_agent_result(result)
        data["available"] = True
        data["helper"] = getattr(helper, "__name__", "review_pair")
        data["pair"] = {
            key: pair.get(key)
            for key in ("problem", "rid_a", "rid_b", "username_a", "username_b", "score", "exact")
        }
        if data.get("error"):
            return JSONResponse(status_code=502, content=_jsonable(data))
        return data

    @app.post("/api/ai/evaluate-project", tags=["ai"])
    def ai_evaluate_project(payload: ProjectEvaluateRequest):
        """Open-ended assignment / project evaluation.

        The UI may send file contents as base64 (zips included).  The backend
        helper is intentionally optional: until ``oj_sentinel.ai`` exposes an
        ``evaluate_project`` function this endpoint returns a structured 501 so
        the GUI can explain the missing capability instead of crashing.
        """
        helper, helper_error = _load_project_helper()
        if helper is None:
            return JSONResponse(
                status_code=501,
                content={
                    "available": False,
                    "error": "作品评测后端尚未就绪",
                    "detail": helper_error,
                    "endpoint": "/api/ai/evaluate-project",
                    "hint": "需要 oj_sentinel.ai 提供 evaluate_project（接收作业说明、量规与作品文件）；当前不会上传或外发任何文件。",
                    "received": {
                        "title": _short(payload.title, 120),
                        "student": _short(payload.student, 120),
                        "files": len(payload.files),
                        "description_chars": len(payload.description or ""),
                        "rubric_chars": len(payload.rubric or ""),
                    },
                },
            )
        agent, agent_error = _build_agent(payload.options)
        if agent is None:
            return JSONResponse(
                status_code=501,
                content={
                    "available": False,
                    "error": "AI Agent 尚未就绪",
                    "detail": agent_error,
                    "endpoint": "/api/ai/evaluate-project",
                },
            )
        assignment = {
            "title": payload.title,
            "description": payload.description,
            "student": payload.student,
            "domain": payload.domain,
            "homework_id": payload.hid,
        }
        files: list[dict[str, Any]] = []
        for project_file in payload.files:
            text_content = project_file.text or ""
            extra = project_file.model_extra or {}
            if not text_content and isinstance(extra.get("content"), str):
                text_content = extra["content"]
            if not text_content and project_file.content_base64:
                try:
                    raw = base64.b64decode(project_file.content_base64, validate=False)
                    try:
                        text_content = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        text_content = (
                            f"[二进制文件 {project_file.name or 'file'}，{len(raw)} 字节；"
                            "等待后端支持解压/附件分析]"
                        )
                except (ValueError, TypeError):
                    text_content = ""
            files.append(
                {
                    "name": project_file.name or "file",
                    "content": text_content,
                    "text": text_content,
                    "size": project_file.size,
                    "mime": project_file.mime,
                }
            )
        run_evidence = (payload.model_extra or {}).get("run_evidence")
        try:
            try:
                result = helper(agent, assignment, files, payload.rubric, run_evidence)
            except TypeError:
                result = helper(agent, assignment, files, payload.rubric)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=502,
                content={"available": True, "error": "作品评测失败", "detail": _short(exc)},
            )
        data = _normalise_agent_result(result)
        if data.get("error"):
            data["available"] = True
            data["endpoint"] = "/api/ai/evaluate-project"
            return JSONResponse(status_code=502, content=_jsonable(data))
        parse_evaluation = None
        for module_name in ("oj_sentinel.ai", "oj_sentinel.ai.prompts"):
            parse_evaluation, _ = _import_optional(module_name, "parse_project_evaluation")
            if parse_evaluation is not None:
                break
        evaluation = None
        if callable(parse_evaluation):
            try:
                evaluation = parse_evaluation(data.get("data") or data.get("verdict"))
            except Exception:  # noqa: BLE001 - display raw payload on parser issues
                evaluation = None
        if evaluation is None and isinstance(data.get("data"), dict):
            evaluation = data["data"]
        data["evaluation"] = evaluation
        if isinstance(evaluation, dict):
            for key in (
                "score",
                "rubric_items",
                "strengths",
                "weaknesses",
                "evidence",
                "confidence",
                "needs_human_review",
            ):
                if key not in data or data.get(key) is None:
                    data[key] = evaluation.get(key)
        data["available"] = True
        data["advisory"] = True
        data["endpoint"] = "/api/ai/evaluate-project"
        data["files"] = [
            {"name": project_file.get("name"), "size": project_file.get("size")}
            for project_file in files
        ]
        return _jsonable(data)

    @app.post("/api/judge/run", tags=["judge"])
    def judge_run(payload: JudgeRequest):
        domain = _safe_segment(payload.domain, "域名")
        hid = _safe_segment(payload.hid, "作业 ID")
        root = context.snapshot_root(domain, hid)
        runner, runner_error = _import_optional("oj_sentinel.judge.runner")
        if runner is None:
            return JSONResponse(
                status_code=501,
                content={
                    "available": False,
                    "error": "本地评测模块尚未就绪",
                    "detail": runner_error,
                    "hint": "需要 oj_sentinel.judge.runner；当前不影响采集、查重与成绩查看。",
                },
            )
        submission = context.submission_payload(payload.rid, domain, hid)
        summary = submission.get("summary") or {}
        detail = submission.get("detail") or {}
        code = detail.get("code") or ""
        language_raw = payload.language or detail.get("language") or summary.get("language") or ""
        if not code.strip():
            raise HTTPException(status_code=400, detail="该提交没有保存代码，无法本地评测")
        problem_key = summary.get("problem_key") or ""
        load_cases = getattr(runner, "load_cases", None)
        run_python = getattr(runner, "run_python", None)
        run_program = getattr(runner, "run_program", None)
        normalize_language = getattr(runner, "normalize_language", None)
        try:
            language = (
                normalize_language(language_raw)
                if callable(normalize_language)
                else (str(language_raw).strip().lower() or "python")
            )
        except Exception:  # noqa: BLE001
            language = str(language_raw).strip().lower() or "python"
        if not callable(run_python) and not callable(run_program):
            return JSONResponse(
                status_code=501,
                content={
                    "available": False,
                    "error": "本地评测模块缺少运行器",
                    "detail": runner_error,
                },
            )
        cases: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        if callable(load_cases):
            try:
                loaded = load_cases(root, problem_key)
                if isinstance(loaded, tuple) and len(loaded) >= 1:
                    cases = loaded[0] or []
                    meta = loaded[1] if len(loaded) > 1 and isinstance(loaded[1], dict) else {}
                elif isinstance(loaded, list):
                    cases = loaded
            except FileNotFoundError as exc:
                raise HTTPException(status_code=400, detail=f"未找到题目测试数据：{_short(exc)}") from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"读取测试数据失败：{_short(exc)}") from exc
        if not cases:
            raise HTTPException(status_code=400, detail="该题目没有可用的本地测试数据")
        checker = meta.get("checker")
        extra_source = str(meta.get("extra_source") or "")
        configured_time = int(_number(meta.get("time_limit_ms"), 5000)) if meta.get("time_limit_ms") else 5000
        configured_memory = int(_number(meta.get("memory_limit_mb"), 256)) if meta.get("memory_limit_mb") else 256
        time_limit_ms = (
            configured_time if int(payload.time_limit_ms) == 5000 else int(payload.time_limit_ms)
        )
        memory_limit_mb = (
            configured_memory if int(payload.memory_limit_mb) == 256 else int(payload.memory_limit_mb)
        )
        kwargs: dict[str, Any] = {
            "time_limit_ms": max(100, min(120000, time_limit_ms)),
            "memory_limit_mb": max(16, min(4096, memory_limit_mb)),
        }
        if checker:
            kwargs["checker"] = checker
        try:
            if language == "python" and callable(run_python):
                if extra_source:
                    kwargs["extra_source"] = extra_source
                result = run_python(code, cases, **kwargs)
            elif callable(run_program):
                result = run_program(language, code, cases, **kwargs)
            elif callable(run_python):
                return JSONResponse(
                    status_code=501,
                    content={
                        "available": False,
                        "error": "当前评测模块仅支持 Python",
                        "language": language,
                    },
                )
            else:  # pragma: no cover - guarded above
                raise RuntimeError("no judge runner")
        except TypeError as exc:
            kwargs.pop("extra_source", None)
            try:
                if language == "python" and callable(run_python):
                    result = run_python(code, cases, **kwargs)
                else:
                    result = run_program(language, code, cases, **kwargs)
            except Exception as retry_exc:  # noqa: BLE001
                return JSONResponse(
                    status_code=500,
                    content={
                        "available": True,
                        "error": "本地评测执行失败",
                        "detail": _short(retry_exc or exc),
                        "language": language,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=500,
                content={"available": True, "error": "本地评测执行失败", "detail": _short(exc)},
            )
        result_data = _jsonable(result)
        oj_status = summary.get("status_text") or summary.get("status") or ""
        local_status = str(result_data.get("status") or "")
        matches = None
        if oj_status and local_status:
            matches = oj_status.strip().lower() == local_status.strip().lower()
        return {
            "available": True,
            "rid": payload.rid,
            "problem": problem_key,
            "language": language,
            "language_raw": language_raw,
            "request": {
                "time_limit_ms": kwargs["time_limit_ms"],
                "memory_limit_mb": kwargs["memory_limit_mb"],
            },
            "result": result_data,
            "oj": {
                "status": oj_status,
                "score": summary.get("score", 0),
                "time_ms": summary.get("time_ms", 0),
                "memory": summary.get("memory", ""),
                "language": detail.get("language") or summary.get("language") or "",
            },
            "matches_oj": matches,
            "checked_at": _iso(),
        }

    @app.post("/api/judge/batch", tags=["judge"])
    def judge_batch(payload: JudgeBatchRequest):
        """Batch local judging + OJ cross-validation in a background job.

        The heavy lifting is delegated to
        ``oj_sentinel.judge.evaluate.evaluate_and_crossvalidate``; this route
        only validates the scope, creates the job, streams progress and writes
        JSON/CSV export files under the local analysis directory.
        """
        domain = _safe_segment(payload.domain, "域名")
        hid = _safe_segment(payload.hid, "作业 ID")
        root = context.snapshot_root(domain, hid)
        module, module_error = _import_optional("oj_sentinel.judge.evaluate")
        helper = getattr(module, "evaluate_and_crossvalidate", None) if module else None
        if not callable(helper):
            return JSONResponse(
                status_code=501,
                content={
                    "available": False,
                    "error": "批量评测模块尚未就绪",
                    "detail": module_error or "judge.evaluate 未提供 evaluate_and_crossvalidate",
                    "hint": "需要 oj_sentinel.judge.evaluate；单条评测与查重仍可正常使用。",
                },
            )
        problem = (payload.problem or "").strip()
        if problem:
            problem = _safe_segment(problem, "题目")
        all_flag = bool(payload.all)
        limit = None if all_flag else max(1, min(10000, int(_number(payload.limit, 50))))
        accepted_only = bool(payload.accepted_only)
        language = (payload.language or "").strip() or None
        workers = max(1, min(8, int(_number(payload.workers, 1))))
        out_dir = context.analysis_dir(domain, hid)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "judge_batch.json"
        csv_path = out_dir / "judge_batch.csv"
        for path in (json_path, csv_path):
            try:
                path.unlink()
            except OSError:
                pass

        scope = "全部题目" if not problem else problem
        job = context.jobs.create("judge_batch", f"批量评测 {scope}")
        jobs = context.jobs
        jobs.set_message(job, "排队中")
        jobs.set_progress(job, 1)

        def on_event(event: Any) -> None:
            if not isinstance(event, dict):
                jobs.add_log(job, event)
                return
            message = str(event.get("message") or event.get("msg") or "").strip()
            if message:
                jobs.add_log(job, message)
            if event.get("done") is not None and event.get("total"):
                done = _number(event.get("done"))
                total = max(1.0, _number(event.get("total")))
                jobs.set_progress(job, 100 * done / total)

        def runner() -> dict[str, Any]:
            jobs.add_log(
                job,
                f"开始批量评测：{domain}/{hid} · {scope} · "
                f"{'全部' if all_flag else f'前 {limit} 条'}"
                + (" · 仅 OJ 通过" if accepted_only else "")
                + f" · workers={workers}（评测引擎按顺序执行，参数保留给并行后端）",
            )
            result = helper(
                root,
                problem_key=problem or None,
                limit=limit,
                accepted_only=accepted_only,
                language=language,
                out=json_path,
                on_event=on_event,
            )
            if not isinstance(result, dict):
                raise RuntimeError("批量评测返回了非预期结果")
            if not json_path.is_file():
                json_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            results = [item for item in (result.get("results") or []) if isinstance(item, dict)]
            rows = _compact_judge_rows(results)
            _write_judge_batch_csv(csv_path, rows)
            summary = result.get("summary") or {}
            cross = result.get("crossvalidation") or {}
            status_counts = summary.get("status_counts") or {}
            if not isinstance(status_counts, dict):
                status_counts = {}
            if not status_counts:
                for row in rows:
                    key = str(row.get("local_status") or "Error")
                    status_counts[key] = status_counts.get(key, 0) + 1
            compared = int(cross.get("compared") or 0)
            agreement = int(cross.get("agreement") or 0)
            if not compared:
                comparable = [row for row in rows if row.get("local_status") and row.get("oj_status")]
                compared = len(comparable)
                agreement = sum(1 for row in comparable if row.get("matched"))
            agreement_rate = (
                float(cross.get("agreement_rate"))
                if cross.get("agreement_rate") is not None
                else (round(agreement / compared, 4) if compared else 0.0)
            )
            evaluated = int(summary.get("evaluated") or len(rows))
            local_accepted = int(summary.get("local_accepted") or status_counts.get("Accepted", 0))
            oj_accepted = int(
                summary.get("oj_accepted")
                or sum(1 for row in rows if "accept" in str(row.get("oj_status") or "").lower())
            )
            payload_out = {
                "evaluated": evaluated,
                "status_counts": status_counts,
                "local_accepted": local_accepted,
                "oj_accepted": oj_accepted,
                "agreement_rate": agreement_rate,
                "compared": compared,
                "agreement": agreement,
                "disagreement": compared - agreement,
                "errors": int(summary.get("errors") or 0),
                "domain": domain,
                "hid": hid,
                "output_path": str(json_path),
                "output_url": f"/api/report/{domain}/{hid}/judge_batch.json",
                "csv_path": str(csv_path),
                "csv_url": f"/api/report/{domain}/{hid}/judge_batch.csv",
                "problem": problem or "",
                "all": all_flag,
                "limit": limit,
                "accepted_only": accepted_only,
                "language": language or "",
                "workers": workers,
                "rows": rows,
                "generated_at": _iso(),
            }
            jobs.add_log(
                job,
                f"批量评测完成：{evaluated} 条 · 本地通过 {local_accepted} · "
                f"OJ 通过 {oj_accepted} · 一致率 {agreement_rate:.2%}",
                level="success",
            )
            return payload_out

        context.jobs.start(job, runner)
        return JSONResponse(status_code=202, content=_jsonable(job_dump(context, job)))

    # -- reports -----------------------------------------------------------
    @app.get("/api/report/{domain}/{hid}/{name}", tags=["reports"])
    def download_report(domain: str, hid: str, name: str, inline: bool = False):
        domain = _safe_segment(domain, "域名")
        hid = _safe_segment(hid, "作业 ID")
        name = _safe_segment(name, "报告文件")
        if name not in REPORT_WHITELIST:
            raise HTTPException(status_code=404, detail="报告文件不存在")
        directory = context.analysis_dir(domain, hid)
        path = _inside(directory, directory / name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="报告文件不存在")
        media_type, _encoding = mimetypes.guess_type(name)
        if name.endswith(".csv"):
            media_type = "text/csv; charset=utf-8"
        if name.endswith(".json"):
            media_type = "application/json; charset=utf-8"
        if inline:
            return FileResponse(
                path,
                media_type=media_type or "application/octet-stream",
                headers={"Content-Disposition": f'inline; filename="{name}"'},
            )
        return FileResponse(
            path,
            media_type=media_type or "application/octet-stream",
            filename=name,
        )

    # -- single page app ---------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> Response:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            return HTMLResponse("<h1>OJ Sentinel</h1><p>静态文件缺失。</p>", status_code=500)
        return FileResponse(
            index_path,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)


def job_dump(context: ServerContext, job: Job) -> dict[str, Any]:
    """Return the public job representation after a start call."""
    dump = context.jobs.get(job.id)
    return dump or {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "log": list(job.log),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "result": job.result,
    }


def _require_session(context: ServerContext, request: Request) -> SessionRecord:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录 OJ，请先在“采集”页登录")
    record = context.sessions.get(token)
    if record is None:
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")
    return record


def _submission_object(payload: dict[str, Any], hid: str) -> Any:
    """Build the hydro Submission object expected by the AI helpers."""
    summary = payload.get("summary") or {}
    detail = payload.get("detail") or {}
    try:
        from ..hydro.models import Submission

        return Submission(
            rid=str(payload.get("rid") or summary.get("rid") or ""),
            homework_id=hid,
            problem_key=str(summary.get("problem_key") or ""),
            uid=int(summary.get("uid") or 0),
            username=str(summary.get("username") or ""),
            display_name=str(summary.get("display_name") or ""),
            status=str(summary.get("status_text") or summary.get("status") or ""),
            score=_number(summary.get("score")),
            language=str(detail.get("language") or summary.get("language") or ""),
            submit_ts=int(summary.get("submit_ts") or 0),
            submit_at=str(summary.get("submit_at") or ""),
            code=str(detail.get("code") or ""),
            time_ms=int(summary.get("time_ms") or 0),
            memory=str(summary.get("memory") or ""),
        )
    except Exception:  # noqa: BLE001 - AI helper may accept plain dicts
        return {
            "rid": payload.get("rid", ""),
            "homework_id": hid,
            "problem_key": summary.get("problem_key", ""),
            "uid": summary.get("uid", 0),
            "username": summary.get("username", ""),
            "display_name": summary.get("display_name", ""),
            "status": summary.get("status_text", ""),
            "score": summary.get("score", 0),
            "language": detail.get("language") or summary.get("language") or "",
            "submit_ts": summary.get("submit_ts", 0),
            "submit_at": summary.get("submit_at", ""),
            "code": detail.get("code", ""),
            "time_ms": summary.get("time_ms", 0),
            "memory": summary.get("memory", ""),
        }


# --------------------------------------------------------------------------- #
# application factory
# --------------------------------------------------------------------------- #
def create_app(data_dir: Path | str | None = None) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    data_dir:
        Root of the local ``data/`` directory.  Defaults to ``OJS_DATA_DIR`` or
        ``./data``.  The directory is created lazily by acquisition/analysis,
        never at import time.
    """
    base = Path(data_dir) if data_dir is not None else default_data_dir()
    context = ServerContext(base)
    app = FastAPI(
        title="OJ Sentinel",
        version=VERSION,
        description="Hydro OJ 作业采集、查重、行为分析与本地评测的本地 Web 界面。",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.context = context
    app.state.data_dir = context.data_dir
    app.state.jobs = context.jobs
    app.state.sessions = context.sessions
    app.state.ctx = context

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'self'",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    _register_routes(app, context)
    return app


# Convenient module level ASGI app for ``uvicorn oj_sentinel.server.app:app``.
app = create_app()

__all__ = ["app", "create_app", "VERSION"]
