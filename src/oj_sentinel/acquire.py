"""Orchestrate a resumable snapshot of one Hydro homework."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .hydro.session import HydroClient


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def snapshot_root(data_dir: Path, domain: str, homework_id: str) -> Path:
    return Path(data_dir) / "snapshots" / domain / homework_id


@dataclass
class AcquireOptions:
    homework_id: str
    data_dir: Path = Path("data")
    workers: int = 6
    download_testdata: bool = True
    download_records: bool = True
    limit_records: int | None = None
    max_pages: int | None = None
    raw_html: bool = False
    force: bool = False


@dataclass
class AcquireResult:
    root: Path
    homework: dict[str, Any] = field(default_factory=dict)
    problem_count: int = 0
    record_count: int = 0
    detail_count: int = 0
    failed: int = 0
    testdata_files: int = 0
    skipped: int = 0
    elapsed: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["root"] = str(self.root)
        return d


class Reporter:
    def __init__(self, callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.callback = callback
        self._lock = threading.Lock()

    def __call__(self, kind: str, message: str, **extra: Any) -> None:
        event = {"kind": kind, "message": message, "ts": time.time(), **extra}
        if self.callback:
            with self._lock:
                try:
                    self.callback(event)
                except Exception:  # noqa: BLE001
                    pass


def _write_submission(root: Path, summary: Any, detail: Any) -> None:
    payload = {"summary": summary.to_dict(), "detail": detail.to_dict()}
    _atomic_json(root / "submissions" / f"{summary.rid}.json", payload)


def repair_details(client: HydroClient, opts: AcquireOptions,
                   on_event: Callable[[dict[str, Any]], None] | None = None) -> AcquireResult:
    """Re-fetch record details whose stored code/status is empty.

    Hydro rate-limits aggressive parallel scraping with HTTP-200 error pages;
    a slow repair pass is both safer and idempotent.
    """
    started = time.time()
    report = Reporter(on_event)
    root = snapshot_root(opts.data_dir, client.domain, opts.homework_id)
    result = AcquireResult(root=root)
    rec_path = root / "records.json"
    if not rec_path.exists():
        raise FileNotFoundError(f"records.json missing in {root}")
    summaries = json.loads(rec_path.read_text(encoding="utf-8"))
    result.record_count = len(summaries)
    todo = []
    for rec in summaries:
        f = root / "submissions" / f"{rec['rid']}.json"
        bad = True
        if f.exists() and not opts.force:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                det = data.get("detail", {})
                bad = not (det.get("code") or det.get("status_text"))
            except (OSError, ValueError):
                bad = True
        if bad:
            todo.append(rec)
    report("info", f"repair: {len(todo)} / {len(summaries)} details need refetch")
    lock = threading.Lock()
    stats = {"ok": 0, "failed": 0, "empty": 0}

    def work(rec):
        last = None
        for attempt in range(3):
            try:
                detail = client.get_record(rec["rid"])
                if detail.code or detail.status_text:
                    _write_submission(root, _SummaryAdapter(rec), detail)
                    return detail
                last = "empty detail page"
            except Exception as exc:  # noqa: BLE001
                last = exc
            time.sleep(0.6 + attempt)
        raise RuntimeError(f"{rec['rid']}: {last}")

    def progress(done: int, total: int, _label: str) -> None:
        if done % 50 == 0 or done == total:
            report("progress", f"repair {done}/{total}", done=done, total=total)

    if todo:
        results = client.fetch_many(todo, work, workers=opts.workers, label="repair", progress=progress)
        for _item, _value, err in results:
            with lock:
                if err:
                    stats["failed"] += 1
                else:
                    stats["ok"] += 1
        report("info", f"repair: {stats['ok']} ok, {stats['failed']} failed")
    result.detail_count = stats["ok"]
    result.failed = stats["failed"]
    result.elapsed = time.time() - started
    report("done", f"repair complete in {result.elapsed:.1f}s")
    return result


class _SummaryAdapter:
    """Accept a plain summary dict where acquire expects an object."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data

    def __getattr__(self, item: str) -> Any:
        return self._data[item]


def acquire(client: HydroClient, opts: AcquireOptions,
            on_event: Callable[[dict[str, Any]], None] | None = None) -> AcquireResult:
    started = time.time()
    report = Reporter(on_event)
    domain = client.domain
    root = snapshot_root(opts.data_dir, domain, opts.homework_id)
    root.mkdir(parents=True, exist_ok=True)
    result = AcquireResult(root=root)

    # 1. homework + problems
    report("phase", "Fetching homework", phase="homework")
    hw = client.get_homework(opts.homework_id)
    report("info", f"Homework: {hw.title} ({len(hw.problems)} problems)")

    problems = []
    for ref in hw.problems:
        report("info", f"Fetching problem {ref.key} {ref.title}", problem=ref.key)
        try:
            prob = client.get_problem(ref.key, opts.homework_id, fetch_files=True)
        except Exception as exc:  # noqa: BLE001
            report("warning", f"problem {ref.key} failed: {exc}")
            continue
        if not prob.points:
            prob.points = ref.points
        problems.append(prob)
        pdir = root / "problems"
        _atomic_json(pdir / f"{ref.key}.json", prob.to_dict())

        # testdata
        if opts.download_testdata and prob.files:
            tdir = root / "testdata" / (str(prob.numeric_id) if prob.numeric_id else ref.key)
            for entry in prob.files:
                dest = tdir / Path(entry.name).name
                if dest.exists() and dest.stat().st_size == entry.size and not opts.force:
                    result.skipped += 1
                    continue
                try:
                    size = client.download(entry.url, dest)
                    result.testdata_files += 1
                    if entry.size and size != entry.size:
                        report("warning", f"size mismatch for {entry.name}: {size} != {entry.size}")
                except Exception as exc:  # noqa: BLE001
                    result.failed += 1
                    report("error", f"testdata {entry.name}: {exc}")
            for entry in prob.additional_files:
                dest = tdir / "additional" / Path(entry.name).name
                if dest.exists() and not opts.force:
                    result.skipped += 1
                    continue
                try:
                    client.download(entry.url, dest)
                    result.testdata_files += 1
                except Exception as exc:  # noqa: BLE001
                    result.failed += 1
                    report("error", f"additional file {entry.name}: {exc}")

    hw_payload = hw.to_dict()
    hw_payload["problems_detail"] = [p.to_dict() for p in problems]
    hw_payload["domain"] = domain
    hw_payload["base_url"] = client.base_url
    _atomic_json(root / "homework.json", hw_payload)
    result.homework = hw_payload
    result.problem_count = len(problems)
    report("phase", f"{len(problems)} problems saved", phase="scoreboard")

    # 2. scoreboard
    try:
        scoreboard = client.get_scoreboard(opts.homework_id)
        _atomic_json(root / "scoreboard.json", scoreboard.to_dict())
        report("info", f"Scoreboard: {len(scoreboard.rows)} users")
    except Exception as exc:  # noqa: BLE001
        report("warning", f"scoreboard failed: {exc}")

    # 3. record list
    report("phase", "Listing submissions", phase="records")
    summaries = []
    if opts.download_records:
        for summary in client.list_records(
            opts.homework_id,
            max_pages=opts.max_pages,
            on_page=lambda page, n: report("page", f"record page {page}: {n} rows", page=page),
        ):
            summaries.append(summary)
            if opts.limit_records and len(summaries) >= opts.limit_records:
                break
        _atomic_json(root / "records.json", [s.to_dict() for s in summaries])
        result.record_count = len(summaries)
        report("info", f"{len(summaries)} submissions listed")

        # 4. record details, resumable and concurrent
        todo = [s for s in summaries if opts.force or not (root / "submissions" / f"{s.rid}.json").exists()]
        result.skipped += len(summaries) - len(todo)
        counter = {"done": 0, "failed": 0}
        lock = threading.Lock()

        def work(summary):
            detail = client.get_record(summary.rid)
            _write_submission(root, summary, detail)
            return detail

        def progress(done: int, total: int, _label: str) -> None:
            if done % 25 == 0 or done == total:
                report("progress", f"submission details {done}/{total}", done=done, total=total)

        if todo:
            results = client.fetch_many(todo, work, workers=opts.workers, label="details", progress=progress)
            for _item, _value, err in results:
                with lock:
                    if err:
                        counter["failed"] += 1
                    else:
                        counter["done"] += 1
            result.detail_count = counter["done"]
            result.failed += counter["failed"]
            report("info", f"details: {counter['done']} ok, {counter['failed']} failed")
        else:
            result.detail_count = 0
            report("info", "all submission details already present")

    result.elapsed = time.time() - started
    manifest = {
        "domain": domain,
        "base_url": client.base_url,
        "homework_id": opts.homework_id,
        "homework_title": hw.title,
        "problems": [p.key for p in problems],
        "record_count": result.record_count,
        "detail_count": result.detail_count,
        "testdata_files": result.testdata_files,
        "failed": result.failed,
        "elapsed": round(result.elapsed, 2),
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "fetch_options": {
            "workers": opts.workers,
            "download_records": opts.download_records,
            "download_testdata": opts.download_testdata,
        },
    }
    _atomic_json(root / "manifest.json", manifest)
    report("done", f"snapshot complete: {result.record_count} submissions in {result.elapsed:.1f}s")
    return result
