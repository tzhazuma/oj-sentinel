"""Snapshot loading and SQLite indexing.

The snapshot is the source of truth; SQLite is a derived, disposable index that
makes the GUI and cross-run analysis fast.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .hydro.models import Submission


@dataclass
class Snapshot:
    root: Path
    homework: dict[str, Any]
    problems: dict[str, dict[str, Any]] = field(default_factory=dict)
    scoreboard: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def homework_id(self) -> str:
        return self.homework.get("id") or self.manifest.get("homework_id", "")

    @property
    def domain(self) -> str:
        return self.homework.get("domain") or self.manifest.get("domain", "")

    def rows(self, include_empty: bool = False) -> list[Submission]:
        out: list[Submission] = []
        for rec in self.records:
            rid = rec.get("rid", "")
            sub = self.submissions.get(rid)
            code = ""
            detail = {}
            if sub:
                detail = sub.get("detail", {})
                code = detail.get("code", "")
            if not code and not include_empty:
                continue
            out.append(
                Submission(
                    rid=rid,
                    homework_id=self.homework_id,
                    problem_key=rec.get("problem_key", ""),
                    uid=int(rec.get("uid", 0)),
                    username=rec.get("username", ""),
                    display_name=rec.get("display_name", ""),
                    status=rec.get("status_text", rec.get("status", "")),
                    score=float(rec.get("score", 0)),
                    language=rec.get("language", ""),
                    submit_ts=int(rec.get("submit_ts", 0)),
                    submit_at=rec.get("submit_at", ""),
                    code=code,
                    time_ms=int(rec.get("time_ms", 0)),
                    memory=rec.get("memory", ""),
                )
            )
        return out


def load_snapshot(root: Path, *, load_submissions: bool = True) -> Snapshot:
    root = Path(root)
    hw_path = root / "homework.json"
    if not hw_path.exists():
        raise FileNotFoundError(f"not a snapshot directory (missing homework.json): {root}")
    hw = json.loads(hw_path.read_text(encoding="utf-8"))
    snap = Snapshot(root=root, homework=hw)
    problems_dir = root / "problems"
    if problems_dir.exists():
        for p in sorted(problems_dir.glob("*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            snap.problems[data.get("key", p.stem)] = data
    sb = root / "scoreboard.json"
    if sb.exists():
        snap.scoreboard = json.loads(sb.read_text(encoding="utf-8"))
    rec_path = root / "records.json"
    if rec_path.exists():
        snap.records = json.loads(rec_path.read_text(encoding="utf-8"))
    man = root / "manifest.json"
    if man.exists():
        snap.manifest = json.loads(man.read_text(encoding="utf-8"))
    if load_submissions:
        sdir = root / "submissions"
        if sdir.exists():
            for f in sdir.glob("*.json"):
                try:
                    snap.submissions[f.stem] = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
    return snap


def stats(snap: Snapshot) -> dict[str, Any]:
    per_problem: dict[str, dict[str, int]] = {}
    status: dict[str, int] = {}
    users: set[int] = set()
    code_lens: list[int] = []
    ts: list[int] = []
    for rec in snap.records:
        pk = rec.get("problem_key", "?")
        pp = per_problem.setdefault(pk, {"submissions": 0, "accepted": 0, "users": 0, "_u": set()})  # type: ignore[assignment]
        pp["submissions"] += 1
        if "accept" in (rec.get("status_text") or "").lower() or int(rec.get("score", 0)) >= 100:
            pp["accepted"] += 1
        pp["_u"].add(rec.get("uid"))
        users.add(int(rec.get("uid") or 0))
        status[rec.get("status_text") or "?"] = status.get(rec.get("status_text") or "?", 0) + 1
        if rec.get("submit_ts"):
            ts.append(int(rec["submit_ts"]))
        sub = snap.submissions.get(rec.get("rid", ""))
        if sub:
            code = sub.get("detail", {}).get("code", "")
            if code:
                code_lens.append(len(code))
    for pp in per_problem.values():
        pp["users"] = len(pp.pop("_u"))
    code_lens.sort()

    def pct(p: float) -> int:
        return code_lens[min(len(code_lens) - 1, int(len(code_lens) * p))] if code_lens else 0

    return {
        "homework_id": snap.homework_id,
        "title": snap.homework.get("title", ""),
        "domain": snap.domain,
        "problems": snap.problems,
        "records": len(snap.records),
        "details": len(snap.submissions),
        "users": len(users),
        "per_problem": per_problem,
        "status": dict(sorted(status.items(), key=lambda kv: -kv[1])),
        "code_length": {"min": code_lens[0] if code_lens else 0, "p50": pct(0.5), "p90": pct(0.9), "max": code_lens[-1] if code_lens else 0},
        "time_range": {
            "from": min(ts) if ts else 0,
            "to": max(ts) if ts else 0,
        },
    }


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS problems (
  domain TEXT, homework_id TEXT, key TEXT, title TEXT, numeric_id INTEGER,
  points INTEGER, time_limit TEXT, memory_limit TEXT, json TEXT,
  PRIMARY KEY (domain, homework_id, key)
);
CREATE TABLE IF NOT EXISTS submissions (
  rid TEXT PRIMARY KEY,
  domain TEXT, homework_id TEXT, problem_key TEXT,
  uid INTEGER, username TEXT, display_name TEXT,
  status TEXT, status_text TEXT, score REAL,
  language TEXT, submit_ts INTEGER, submit_at TEXT,
  time_ms INTEGER, memory TEXT, code TEXT, code_len INTEGER,
  detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sub_problem ON submissions(domain, homework_id, problem_key);
CREATE INDEX IF NOT EXISTS idx_sub_user ON submissions(domain, homework_id, uid);
CREATE TABLE IF NOT EXISTS pairs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT, homework_id TEXT, problem_key TEXT,
  rid_a TEXT, rid_b TEXT, uid_a INTEGER, uid_b INTEGER,
  score REAL, jaccard REAL, containment REAL, ast_cosine REAL,
  method TEXT, evidence_json TEXT, created_at TEXT,
  UNIQUE(domain, homework_id, problem_key, rid_a, rid_b)
);
CREATE INDEX IF NOT EXISTS idx_pair_score ON pairs(domain, homework_id, score);
CREATE TABLE IF NOT EXISTS anomalies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT, homework_id TEXT, uid INTEGER, username TEXT,
  score REAL, reasons_json TEXT, evidence_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_analysis (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT, homework_id TEXT, subject TEXT, subject_id TEXT,
  provider TEXT, model TEXT, verdict TEXT, confidence REAL,
  summary TEXT, raw_json TEXT, created_at TEXT
);
"""


def ingest(snap: Snapshot, db_path: Path, *, reset: bool = False) -> dict[str, int]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        domain, hid = snap.domain, snap.homework_id
        con.execute("DELETE FROM submissions WHERE domain=? AND homework_id=?", (domain, hid))
        con.execute("DELETE FROM problems WHERE domain=? AND homework_id=?", (domain, hid))
        for key, prob in snap.problems.items():
            con.execute(
                "INSERT OR REPLACE INTO problems(domain,homework_id,key,title,numeric_id,points,time_limit,memory_limit,json) VALUES (?,?,?,?,?,?,?,?,?)",
                (domain, hid, key, prob.get("title", ""), prob.get("numeric_id", 0), prob.get("points", 0),
                 prob.get("time_limit", ""), prob.get("memory_limit", ""), json.dumps(prob, ensure_ascii=False)),
            )
        n = 0
        for rec in snap.records:
            sub = snap.submissions.get(rec.get("rid", ""), {})
            detail = sub.get("detail", {})
            code = detail.get("code", "")
            con.execute(
                "INSERT OR REPLACE INTO submissions(rid,domain,homework_id,problem_key,uid,username,display_name,status,status_text,score,language,submit_ts,submit_at,time_ms,memory,code,code_len,detail_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec.get("rid"), domain, hid, rec.get("problem_key"), rec.get("uid"), rec.get("username"),
                 rec.get("display_name"), rec.get("status"), rec.get("status_text"), rec.get("score", 0),
                 rec.get("language"), rec.get("submit_ts"), rec.get("submit_at"), rec.get("time_ms"),
                 rec.get("memory"), code, len(code), json.dumps(detail, ensure_ascii=False) if detail else ""),
            )
            n += 1
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (f"ingested:{domain}:{hid}", str(n)))
        con.commit()
    finally:
        con.close()
    return {"submissions": len(snap.records), "problems": len(snap.problems), "db": str(db_path)}


def read_submissions(db_path: Path, domain: str, homework_id: str, *, problem: str | None = None) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM submissions WHERE domain=? AND homework_id=?"
    args: list[Any] = [domain, homework_id]
    if problem:
        q += " AND problem_key=?"
        args.append(problem)
    rows = [dict(r) for r in con.execute(q, args)]
    con.close()
    return rows
