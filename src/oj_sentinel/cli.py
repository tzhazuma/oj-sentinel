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
        codebert=bool(getattr(args, "codebert", False)),
        codebert_model=getattr(args, "codebert_model", "microsoft/codebert-base"),
        codebert_weight=float(getattr(args, "codebert_weight", 0.15)),
        codebert_max_pairs=int(getattr(args, "codebert_max_pairs", 300)),
        codebert_cache_dir=getattr(args, "codebert_cache_dir", None),
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




def _snapshot_root(args) -> Path:
    from .ingest import load_snapshot

    if getattr(args, "snapshot", None):
        return Path(args.snapshot)
    settings = Settings.load()
    domain = args.domain or settings.domain
    hid = args.homework
    if not domain or not hid:
        raise SystemExit("missing --snapshot or --homework/--domain")
    return Path(args.data_dir or settings.data_dir) / "snapshots" / domain / hid


def cmd_judge(args):
    from .judge.evaluate import evaluate_rid, evaluate_and_crossvalidate

    root = _snapshot_root(args)
    if not args.rid and not args.all and args.limit is None:
        raise SystemExit("use --rid for one submission, or --all / --limit N for batch evaluation")
    if args.rid:
        result = evaluate_rid(root, args.rid, checker=False if args.no_checker else None)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return
    # Batch mode: evaluate every fetched submission (or a filtered subset).
    out = Path(args.output) if args.output else Path(args.data_dir or "data") / "judge_batch.json"
    payload = evaluate_and_crossvalidate(
        root,
        problem_key=args.problem,
        limit=args.limit,
        accepted_only=args.accepted_only,
        out=out,
        on_event=lambda ev: print(ev.get("message", ""), file=sys.stderr, flush=True),
        checker=False if args.no_checker else None,
        workers=args.workers,
    )
    summary = payload.get("summary", {})
    cross = payload.get("crossvalidation") or payload.get("crossvalidate") or {}
    csv_path = Path(args.csv) if getattr(args, "csv", None) else out.with_suffix(".csv")
    import csv as _csv

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = _csv.writer(fh)
        w.writerow(["rid", "problem", "uid", "username", "language", "local_status", "local_score",
                    "oj_status", "passed", "total", "elapsed_ms", "matches_oj", "error"])
        for row in payload.get("results", []):
            matches = str(row.get("status", "")).strip().lower() == str(row.get("oj_status", "")).strip().lower()
            w.writerow([row.get("rid"), row.get("problem"), row.get("uid"), row.get("username"),
                        row.get("language"), row.get("status"), row.get("score"), row.get("oj_status"),
                        row.get("passed"), row.get("total"), row.get("elapsed_ms"), matches, row.get("error", "")])
    print(json.dumps({"output": str(out), "csv": str(csv_path), **summary,
                      "agreement": cross.get("agreement"),
                      "agreement_rate": cross.get("agreement_rate")}, ensure_ascii=False, indent=2))


def cmd_crossvalidate(args):
    from .judge.crossvalidate import crossvalidate, write_crossvalidation
    from .judge.evaluate import evaluate_rid, load_records, submission_code

    root = _snapshot_root(args)
    records = load_records(root)
    if args.problem:
        records = [r for r in records if r.get("problem_key") == args.problem]
    records = [r for r in records if submission_code(root, r.get("rid", ""))]
    if args.status:
        wanted = set(x.strip() for x in args.status.split(","))
        records = [r for r in records if r.get("status_text") in wanted]
    if not args.all:
        records = records[: args.limit]
    results = []
    for i, rec in enumerate(records, 1):
        try:
            results.append(evaluate_rid(root, rec["rid"]))
        except Exception as exc:  # noqa: BLE001
            results.append({"rid": rec["rid"], "status": "Local Error", "error": str(exc)})
        if i % 20 == 0:
            print(f"evaluated {i}/{len(records)}", file=sys.stderr)
    report = crossvalidate(root, results)
    out = Path(args.output) if args.output else Path(args.data_dir or "data") / "crossvalidation.json"
    write_crossvalidation(report, out)
    summary = {k: report.get(k) for k in ("compared", "agreement", "agreement_rate", "local_totals", "oj_totals", "by_problem") if k in report}
    print(json.dumps({"output": str(out), **summary}, ensure_ascii=False, indent=2))


def cmd_dsh_check(args):
    from .ai.dsh_discovery import discover_dsh

    info = discover_dsh(args.path or None)
    print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
    return 0 if info.found else 2


def cmd_ai(args):
    from .ai import AgentConfig, build_agent
    import os

    provider = args.provider or os.environ.get("OJS_AI_PROVIDER", "dsh")
    kwargs = {
        "provider": provider,
        "model": args.model or "deepseek-v4.1-flash",
        "timeout": args.timeout,
    }
    if getattr(args, "dsh_path", None):
        kwargs["dsh_command"] = args.dsh_path
    config = AgentConfig(**kwargs)
    agent = build_agent(config)

    if args.action in {"review", "diagnose", "tests"}:
        root = _snapshot_root(args)

    if args.action == "review":
        from .ai import review_pair, verdict_of
        from .judge.evaluate import load_problem
        import json as _json

        analysis_path = Path(args.data_dir or "data") / "analysis" / (args.domain or "") / (args.homework or "") / "analysis.json"
        if not analysis_path.exists():
            raise SystemExit(f"analysis.json not found: {analysis_path}; run `ojs analyze` first")
        data = _json.loads(analysis_path.read_text(encoding="utf-8"))
        pair = None
        if args.pair_index is not None:
            pair = data["pairs"][args.pair_index]
        else:
            if not (args.rid_a and args.rid_b):
                raise SystemExit("use --pair-index or --rid-a/--rid-b")
            pair = next((p for p in data.get("pairs", []) if {p["rid_a"], p["rid_b"]} == {args.rid_a, args.rid_b}), None)
        if pair is None:
            raise SystemExit("pair not found in analysis.json")
        problem = load_problem(root, pair["problem"])
        def _sub(rid):
            payload = _json.loads((root / "submissions" / f"{rid}.json").read_text(encoding="utf-8"))
            return {"rid": rid, **payload.get("summary", {}), "code": payload.get("detail", {}).get("code", "")}
        result = review_pair(agent, problem, _sub(pair["rid_a"]), _sub(pair["rid_b"]), pair)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    elif args.action == "diagnose":
        from .ai import diagnose_submission
        from .judge.evaluate import evaluate_rid, find_record, load_problem, load_submission_payload

        record = find_record(root, args.rid)
        if record is None:
            raise SystemExit("submission not found")
        run = evaluate_rid(root, args.rid)
        problem = load_problem(root, record["problem_key"])
        payload = load_submission_payload(root, args.rid)
        detail = payload.get("detail", {})
        submission = {"rid": args.rid, **payload.get("summary", {}), "code": detail.get("code", "")}
        result = diagnose_submission(agent, problem, submission, run)
        print(json.dumps({"local_run": run, **result.to_dict()}, ensure_ascii=False, indent=2, default=str))
    elif args.action == "tests":
        from .ai import generate_tests
        from .judge.evaluate import load_problem

        problem = load_problem(root, args.problem)
        if args.validate:
            from .judge.generate import generate_and_validate, write_generated_tests

            payload = generate_and_validate(agent, root, args.problem, n=args.n)
            out = Path(args.output) if args.output else Path(args.data_dir or "data") / "generated" / f"{args.problem}.json"
            write_generated_tests(payload, out)
            summary = dict(payload.get("summary") or {})
            print(json.dumps({"output": str(out), **summary, "error": payload.get("error")},
                             ensure_ascii=False, indent=2))
            if args.run_rid:
                from .judge.runner import normalize_language, run_program
                from .judge.evaluate import load_submission_payload, find_record

                rec = find_record(root, args.run_rid) or {}
                detail = load_submission_payload(root, args.run_rid).get("detail", {})
                code = detail.get("code", "")
                cases = [t for t in payload.get("tests", []) if t.get("validated") or t.get("input")]
                lang = normalize_language(detail.get("language") or rec.get("language") or "python")
                result = run_program(lang, code, cases)
                print(json.dumps({"rid": args.run_rid, "status": result.status, "score": result.score,
                                  "passed": sum(1 for c in result.cases if c.status == "AC"),
                                  "total": len(result.cases)}, ensure_ascii=False, indent=2))
            return
        result = generate_tests(agent, problem, args.n)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    elif args.action in {"evaluate-project", "evaluate-game"}:
        from .ai import evaluate_game_project, evaluate_project
        import json as _json

        assignment = Path(args.assignment).read_text(encoding="utf-8") if args.assignment and Path(args.assignment).exists() else (args.assignment or "")
        rubric = Path(args.rubric).read_text(encoding="utf-8") if args.rubric and Path(args.rubric).exists() else (args.rubric or "")
        files = []
        for pattern in args.files or []:
            p = Path(pattern)
            if p.is_file():
                files.append({"name": p.name, "content": p.read_text(encoding="utf-8", errors="replace")})
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and f.stat().st_size < 200_000 and ".git" not in f.parts:
                        files.append({"name": str(f.relative_to(p)), "content": f.read_text(encoding="utf-8", errors="replace")})
        run_evidence = _json.loads(Path(args.run_evidence).read_text(encoding="utf-8")) if args.run_evidence else None
        logs = Path(args.logs).read_text(encoding="utf-8", errors="replace") if args.logs else None
        screenshots = [{"name": s, "caption": s} for s in (args.screenshots or [])]
        if args.action == "evaluate-game":
            result = evaluate_game_project(agent, assignment, files, rubric, run_evidence,
                                           screenshots=screenshots, logs=logs)
        else:
            result = evaluate_project(agent, assignment, files, rubric, run_evidence)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        raise SystemExit(f"unknown ai action: {args.action}")


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
    sp.add_argument("--codebert", action="store_true",
                    help="optional CodeBERT semantic signal (needs [codebert] extra)")
    sp.add_argument("--codebert-model", default="microsoft/codebert-base")
    sp.add_argument("--codebert-weight", type=float, default=0.15)
    sp.add_argument("--codebert-max-pairs", type=int, default=300)
    sp.add_argument("--codebert-cache-dir", help="model download cache (default: data/models)")
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

    sp = sub.add_parser("judge", help="run one submission locally against OJ testdata")
    sp.add_argument("--snapshot")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.add_argument("--rid", help="one submission id; omit for batch mode")
    sp.add_argument("--all", action="store_true", help="batch-evaluate every fetched submission")
    sp.add_argument("--limit", type=int, help="max submissions in batch mode")
    sp.add_argument("--problem", help="only this problem (batch mode)")
    sp.add_argument("--accepted-only", action="store_true")
    sp.add_argument("--no-checker", action="store_true")
    sp.add_argument("--output", help="JSON output path")
    sp.add_argument("--csv", help="CSV output path (batch mode)")
    sp.add_argument("--workers", type=int, default=1, help="parallel submissions in batch mode")
    sp.set_defaults(func=cmd_judge)

    sp = sub.add_parser("crossvalidate", help="local verdict vs OJ verdict matrix")
    sp.add_argument("--snapshot")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.add_argument("--problem")
    sp.add_argument("--status", help="comma separated OJ statuses to include")
    sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--output")
    sp.set_defaults(func=cmd_crossvalidate)

    sp = sub.add_parser("ai", help="AI review / diagnose / test generation / project grading")
    sp.add_argument("action", choices=["review", "diagnose", "tests", "evaluate-project", "evaluate-game"])
    sp.add_argument("--snapshot")
    sp.add_argument("--homework")
    sp.add_argument("--domain")
    sp.add_argument("--data-dir")
    sp.add_argument("--provider", help="dsh (default) | openai | mock | offline")
    sp.add_argument("--dsh-path", help="custom dsh executable (auto-discovered when omitted)")
    sp.add_argument("--model", default="deepseek-v4.1-flash")
    sp.add_argument("--timeout", type=float, default=300.0)
    sp.add_argument("--rid")
    sp.add_argument("--rid-a")
    sp.add_argument("--rid-b")
    sp.add_argument("--pair-index", type=int)
    sp.add_argument("--problem")
    sp.add_argument("--n", type=int, default=10)
    sp.add_argument("--assignment")
    sp.add_argument("--rubric")
    sp.add_argument("--files", action="append")
    sp.add_argument("--validate", action="store_true", help="validate generated tests against accepted submissions")
    sp.add_argument("--run-rid", help="run generated tests on one submission")
    sp.add_argument("--output")
    sp.add_argument("--run-evidence")
    sp.add_argument("--logs")
    sp.add_argument("--screenshots", action="append")
    sp.set_defaults(func=cmd_ai)

    sp = sub.add_parser("dsh-check", help="probe auto-discovered / custom dsh backends")
    sp.add_argument("--path", help="custom dsh executable path")
    sp.set_defaults(func=cmd_dsh_check)

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
