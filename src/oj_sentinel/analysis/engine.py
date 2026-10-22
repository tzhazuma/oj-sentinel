"""End-to-end analysis: profiles -> pairs -> clusters -> anomalies -> final scores."""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ..ingest import Snapshot, load_snapshot
from .normalize import CodeProfile, build_profile
from .similarity import Pair, cluster_pairs, find_pairs

SCHEMA_VERSION = 1


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_profiles(snap: Snapshot, *, on_event: Callable[[str], None] | None = None) -> list[CodeProfile]:
    profiles: list[CodeProfile] = []
    rows = [r for r in snap.rows() if r.code and len(r.code.strip()) >= 20]
    for i, sub in enumerate(rows, 1):
        prof = build_profile(sub.rid, sub.uid, sub.problem_key, sub.code, sub.language)
        profiles.append(prof)
        if on_event and i % 500 == 0:
            on_event(f"profiles {i}/{len(rows)}")
    return profiles


def compute_final_scores(snap: Snapshot) -> list[dict[str, Any]]:
    """Final score per student/problem = score of the LAST submission.

    All submissions are still analysed for plagiarism; this table is only the
    grading view requested by the teacher.
    """
    problems = list(snap.problems.values())
    problem_order = [p["key"] for p in problems]
    points = {p["key"]: int(p.get("points") or 0) for p in problems}
    names = {p["key"]: p.get("title", p["key"]) for p in problems}

    users: dict[int, dict[str, Any]] = {}
    for rec in snap.scoreboard.get("rows", []):
        uid = int(rec.get("uid") or 0)
        if uid:
            users.setdefault(uid, {"uid": uid, "username": rec.get("username", ""),
                                   "display_name": rec.get("display_name", ""), "subs": []})
    for rec in snap.records:
        uid = int(rec.get("uid") or 0)
        users.setdefault(uid, {"uid": uid, "username": rec.get("username", ""),
                               "display_name": rec.get("display_name", ""), "subs": []})
        users[uid]["subs"].append(rec)

    out: list[dict[str, Any]] = []
    for uid, u in users.items():
        by_prob: dict[str, dict[str, Any]] = {}
        for rec in u["subs"]:
            pk = rec.get("problem_key")
            if not pk:
                continue
            cur = by_prob.get(pk)
            if cur is None or (int(rec.get("submit_ts") or 0), rec.get("rid", "")) > (
                int(cur.get("submit_ts") or 0), cur.get("rid", "")
            ):
                by_prob[pk] = rec
        row: dict[str, Any] = {
            "uid": uid,
            "username": u["username"] or u["display_name"],
            "display_name": u["display_name"] or u["username"],
            "total": 0,
            "total_points": sum(points.values()),
            "problems": {},
        }
        for pk in problem_order:
            rec = by_prob.get(pk)
            cell = {
                "problem": pk,
                "title": names.get(pk, pk),
                "points": points.get(pk, 0),
                "score": int(rec.get("score") or 0) if rec else 0,
                "status": (rec.get("status_text") or "未提交") if rec else "未提交",
                "rid": rec.get("rid", "") if rec else "",
                "submit_at": rec.get("submit_at", "") if rec else "",
                "last_submission": bool(rec),
            }
            row["problems"][pk] = cell
            row["total"] += cell["score"]
        out.append(row)
    out.sort(key=lambda r: (-r["total"], r["username"]))
    return out


def final_score_summary(final_scores: list[dict[str, Any]], problems: list[dict[str, Any]]) -> dict[str, Any]:
    agg: dict[str, list[int]] = {p["key"]: [] for p in problems}
    for row in final_scores:
        for pk, cell in row["problems"].items():
            agg.setdefault(pk, []).append(int(cell.get("score") or 0))
    dist: dict[str, dict[str, Any]] = {}
    for pk, scores in agg.items():
        if not scores:
            continue
        full = max(scores) if scores else 0
        dist[pk] = {
            "students": len(scores),
            "mean": round(sum(scores) / len(scores), 2),
            "full_score": full,
            "full_marks": sum(1 for s in scores if full and s >= full),
            "zero": sum(1 for s in scores if s == 0),
        }
    return dist


def run_analysis(
    snapshot_root: Path,
    *,
    out_dir: Path | None = None,
    threshold: float = 0.6,
    max_pairs: int = 20000,
    with_evidence: bool = True,
    on_event: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    started = time.time()
    root = Path(snapshot_root)
    snap = load_snapshot(root)
    log = on_event or (lambda _m: None)

    log(f"loaded snapshot: {len(snap.records)} submissions, {len(snap.submissions)} details")
    profiles = build_profiles(snap, on_event=log)
    log(f"built {len(profiles)} code profiles")

    by_problem: dict[str, list[CodeProfile]] = defaultdict(list)
    for p in profiles:
        by_problem[p.problem].append(p)

    pairs: list[Pair] = []
    per_problem: dict[str, dict[str, Any]] = {}
    for idx, (pk, profs) in enumerate(sorted(by_problem.items()), 1):
        log(f"comparing {pk} ({idx}/{len(by_problem)}, {len(profs)} profiles)")
        found = find_pairs(profs, threshold=threshold, with_evidence=with_evidence,
                           max_pairs=max_pairs)
        pairs.extend(found)
        per_problem[pk] = {
            "submissions": sum(1 for r in snap.records if r.get("problem_key") == pk),
            "profiles": len(profs),
            "pairs": len(found),
            "top_score": round(max((p.score for p in found), default=0.0), 4),
        }
    log(f"found {len(pairs)} suspicious pairs")

    # annotate usernames on pairs
    # fill usernames from snapshot rows
    meta = {r.rid: r for r in snap.rows()}
    for p in pairs:
        if p.rid_a in meta:
            p.username_a = meta[p.rid_a].username
        if p.rid_b in meta:
            p.username_b = meta[p.rid_b].username

    # Plagiarism is only between different students; a student's own repeated
    # submissions are handled by behaviour analytics instead.
    pairs = [p for p in pairs if p.uid_a != p.uid_b and not p.same_user]
    clusters = cluster_pairs(pairs, threshold=max(0.55, threshold))
    anomalies: list[dict[str, Any]] = []
    try:
        from .behavior import analyze_behavior

        anomalies = analyze_behavior(snap, profiles, pairs)
        log(f"behaviour: {len(anomalies)} users flagged")
    except ImportError as exc:
        log(f"behaviour module unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001
        log(f"behaviour analysis failed: {exc}")

    final_scores = compute_final_scores(snap)
    summary = final_score_summary(final_scores, list(snap.problems.values()))

    pair_dicts = [p.to_dict() for p in pairs]
    # Human-readable reason for every pair (teacher-facing).
    for d in pair_dicts:
        reasons = []
        if d["exact"]:
            reasons.append("归一化后代码完全一致（精确复制）")
        reasons.append(
            f"共享 {d['shared']} 个指纹：containment {d['containment']:.2f}，"
            f"Jaccard {d['jaccard']:.2f}，AST 相似度 {d['ast_cosine']:.2f}"
        )
        if d["evidence"]:
            total_lines = sum(e["lines"] for e in d["evidence"])
            reasons.append(f"匹配代码块 {len(d['evidence'])} 段，共 {total_lines} 行")
        d["reasons"] = reasons

    stats = {
        "profiles": len(profiles),
        "pairs": len(pairs),
        "clusters": len(clusters),
        "anomalies": len(anomalies),
        "submissions": len(snap.records),
        "details": len(snap.submissions),
        "users": len({r.uid for r in snap.rows()}),
        "elapsed_s": round(time.time() - started, 1),
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "domain": snap.domain,
        "homework_id": snap.homework_id,
        "title": snap.homework.get("title", ""),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {"threshold": threshold, "max_pairs": max_pairs, "with_evidence": with_evidence},
        "stats": stats,
        "per_problem": per_problem,
        "pairs": pair_dicts,
        "clusters": clusters,
        "anomalies": anomalies,
        "final_scores": final_scores,
        "final_score_summary": summary,
    }
    target_dir = Path(out_dir) if out_dir else (Path("data") / "analysis" / snap.domain / snap.homework_id)
    target = target_dir / "analysis.json"
    _atomic_json(target, payload)
    log(f"analysis written to {target}")
    payload["_path"] = str(target)
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Run OJ Sentinel analysis on a snapshot")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--max-pairs", type=int, default=20000)
    args = ap.parse_args(argv)
    run_analysis(Path(args.snapshot), out_dir=Path(args.out_dir) if args.out_dir else None,
                 threshold=args.threshold, max_pairs=args.max_pairs,
                 on_event=lambda m: print(m, flush=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
