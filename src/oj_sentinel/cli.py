"""OJ Sentinel command line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import Settings, default_data_dir, read_password
from .hydro.session import HydroAuthError, HydroClient, HydroError


def _client(args, settings: Settings) -> HydroClient:
    base = args.url or settings.base_url
    domain = args.domain or settings.domain
    user = args.username or settings.username
    if not base or not domain:
        raise SystemExit("missing --url/--domain (or run `ojs login` once to save them)")
    if not user:
        raise SystemExit("missing --username")
    client = HydroClient(base, domain)
    pw = read_password(getattr(args, "password", None), getattr(args, "password_stdin", False))
    try:
        client.login(user, pw)
    except HydroAuthError as exc:
        raise SystemExit(f"login failed: {exc}") from exc
    if getattr(args, "save", False):
        Settings(base_url=base, domain=domain, username=user, data_dir=Path(args.data_dir or settings.data_dir)).save()
    return client


def cmd_login(args):
    settings = Settings.load()
    client = _client(args, settings)
    hws = client.list_homeworks()
    print(f"logged in as {client.username} @ {client.base_url} / {client.domain}")
    for hw in hws:
        print(f"  {hw.id}  {hw.title}")
    if not args.save:
        Settings(base_url=args.url or settings.base_url, domain=args.domain or settings.domain,
                 username=client.username, data_dir=Path(args.data_dir or settings.data_dir)).save()


def cmd_homeworks(args):
    settings = Settings.load()
    client = _client(args, settings)
    for hw in client.list_homeworks():
        print(f"{hw.id}\t{hw.title}")
    if args.json:
        print(json.dumps([h.to_dict() for h in client.list_homeworks()], ensure_ascii=False))


def cmd_acquire(args):
    from .acquire import AcquireOptions, acquire, repair_details

    settings = Settings.load()
    client = _client(args, settings)

    def on_event(event):
        msg = event.get("message", "")
        if event.get("kind") in {"error", "warning"}:
            print(f"[{event['kind']}] {msg}", file=sys.stderr)
        elif args.quiet:
            return
        else:
            print(msg)

    opts = AcquireOptions(
        homework_id=args.homework,
        data_dir=Path(args.data_dir or settings.data_dir),
        workers=args.workers,
        download_testdata=not args.no_testdata,
        download_records=not args.no_records,
        limit_records=args.limit_records,
        max_pages=args.max_pages,
        raw_html=args.raw_html,
        force=args.force,
    )
    if args.repair:
        result = repair_details(client, opts, on_event)
    else:
        result = acquire(client, opts, on_event)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if args.save:
        Settings(base_url=args.url or settings.base_url, domain=args.domain or settings.domain,
                 username=client.username, data_dir=opts.data_dir).save()


def cmd_stats(args):
    from .ingest import load_snapshot, stats

    root = Path(args.snapshot) if args.snapshot else _snapshot_from_args(args)
    snap = load_snapshot(root)
    st = stats(snap)
    st["problems"] = {
        k: {kk: vv for kk, vv in v.items() if kk in ("key", "title", "points", "numeric_id", "time_limit", "memory_limit")}
        for k, v in st.get("problems", {}).items()
    }
    print(json.dumps(st, ensure_ascii=False, indent=2))


def cmd_ingest(args):
    from .ingest import ingest, load_snapshot

    root = Path(args.snapshot) if args.snapshot else _snapshot_from_args(args)
    snap = load_snapshot(root)
    db = Path(args.db) if args.db else Path(args.data_dir or "data") / "oj.db"
    result = ingest(snap, db, reset=args.reset)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_analyze(args):
    from .analysis.engine import run_analysis

    root = Path(args.snapshot) if args.snapshot else _snapshot_from_args(args)
    payload = run_analysis(
        root,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        threshold=args.threshold,
        max_pairs=args.max_pairs,
        with_evidence=not args.no_evidence,
        on_event=lambda m: (not args.quiet) and print(m, flush=True),
    )
    st = payload["stats"]
    print(json.dumps({"analysis": payload.get("_path"), **st}, ensure_ascii=False, indent=2))


def cmd_report(args):
    from .report.builder import build_reports

    root = Path(args.snapshot) if args.snapshot else _snapshot_from_args(args)
    analysis_path = Path(args.analysis) if args.analysis else (
        Path(args.data_dir or "data") / "analysis" / (args.domain or "") / (args.homework or "") / "analysis.json"
    )
    if not analysis_path.exists() and args.analysis is None:
        # Try the canonical path derived from the snapshot manifest.
        from .ingest import load_snapshot

        snap = load_snapshot(root, load_submissions=False)
        analysis_path = Path(args.data_dir or "data") / "analysis" / snap.domain / snap.homework_id / "analysis.json"
    out = build_reports(
        analysis_path,
        root,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        top_pairs=args.top_pairs,
        redact=args.redact,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.open:
        import subprocess

        subprocess.run(["open", out["html"]], check=False)


def cmd_final(args):
    from .ingest import load_snapshot

    root = Path(args.snapshot) if args.snapshot else _snapshot_from_args(args)
    snap = load_snapshot(root)
    from .analysis.engine import compute_final_scores

    rows = compute_final_scores(snap)
    if args.csv:
        import csv

        problems = sorted(snap.problems.values(), key=lambda p: p.get("key", ""))
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["uid", "display_name", "username", "total"] + [f"{p['key']}_score" for p in problems])
            for r in rows:
                w.writerow([r["uid"], r["display_name"], r["username"], r["total"]] +
                           [r["problems"].get(p["key"], {}).get("score", 0) for p in problems])
        print(f"wrote {args.csv}")
        return
    probs = sorted(snap.problems.values(), key=lambda x: x.get("key", ""))
    print(f"{'rank':>4}  {'name':<12} {'uid':>6}  " + "  ".join(f"{p['key']:>10}" for p in probs) + "  total")
    for i, r in enumerate(rows, 1):
        cells = "  ".join(f"{str(r['problems'].get(p['key'], {}).get('score', 0)):>10}" for p in probs)
        print(f"{i:>4}  {(r['display_name'] or '')[:12]:<12} {r['uid']:>6}  {cells}  {r['total']}")


def _snapshot_from_args(args) -> Path:
    settings = Settings.load()
    domain = args.domain or settings.domain
    if not domain:
        raise SystemExit("missing --domain or --snapshot")
    return Path(args.data_dir or settings.data_dir) / "snapshots" / domain / args.homework


def cmd_serve(args):
    import uvicorn

    from .server.app import create_app

    app = create_app(data_dir=Path(args.data_dir or default_data_dir()))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ojs", description="OJ Sentinel — Hydro OJ plagiarism & AI judging toolkit")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, password=True):
        sp.add_argument("--url", help="Hydro base URL, e.g. http://oj.example.edu")
        sp.add_argument("--domain", help="Hydro domain, e.g. YOUR_DOMAIN")
        sp.add_argument("--username", help="Hydro username")
        if password:
            sp.add_argument("--password", help="password (prefer --password-stdin / OJ_PASSWORD)")
            sp.add_argument("--password-stdin", action="store_true", help="read password from stdin")
        sp.add_argument("--data-dir", help="snapshot directory (default ./data)")
        sp.add_argument("--save", action="store_true", help="save non-secret settings to ~/.config/oj-sentinel")

    sp = sub.add_parser("login", help="verify credentials and list homeworks")
    common(sp, password=True)
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("homeworks", help="list accessible homeworks")
    common(sp, password=True)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_homeworks)

    sp = sub.add_parser("acquire", help="download a homework snapshot")
    common(sp, password=True)
    sp.add_argument("--homework", required=True, help="homework id (24 hex chars)")
    sp.add_argument("--workers", type=int, default=6)
    sp.add_argument("--no-testdata", action="store_true")
    sp.add_argument("--no-records", action="store_true")
    sp.add_argument("--limit-records", type=int)
    sp.add_argument("--max-pages", type=int)
    sp.add_argument("--raw-html", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--repair", action="store_true", help="only re-fetch details with empty code")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_acquire)

    sp = sub.add_parser("stats", help="show snapshot statistics")
    sp.add_argument("--snapshot", help="explicit snapshot directory")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("ingest", help="index a snapshot into SQLite")
    sp.add_argument("--snapshot")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.add_argument("--db")
    sp.add_argument("--reset", action="store_true")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("analyze", help="run plagiarism + behaviour analysis")
    sp.add_argument("--snapshot")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.add_argument("--out-dir")
    sp.add_argument("--threshold", type=float, default=0.6)
    sp.add_argument("--max-pairs", type=int, default=20000)
    sp.add_argument("--no-evidence", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("report", help="build HTML/CSV reports")
    sp.add_argument("--snapshot")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.add_argument("--analysis")
    sp.add_argument("--out-dir")
    sp.add_argument("--top-pairs", type=int, default=300)
    sp.add_argument("--redact", action="store_true", help="anonymise names and remove code snippets")
    sp.add_argument("--open", action="store_true")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("final", help="print final scores (last submission per problem)")
    sp.add_argument("--snapshot")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.add_argument("--csv")
    sp.set_defaults(func=cmd_final)

    sp = sub.add_parser("serve", help="start the Web GUI")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--data-dir")
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except HydroError as exc:
        print(f"hydro error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
