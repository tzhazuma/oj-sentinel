"""HTML/CSV report generation from a completed analysis."""
from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..ingest import load_snapshot

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _load(analysis: dict[str, Any] | Path) -> dict[str, Any]:
    if isinstance(analysis, Path):
        return json.loads(analysis.read_text(encoding="utf-8"))
    return analysis


def _anonymise(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with student identities and code snippets removed."""
    out = json.loads(json.dumps(data, ensure_ascii=False))
    id_map: dict[str, str] = {}

    def alias(uid: int, username: str = "") -> str:
        key = str(uid) or username
        if key not in id_map:
            id_map[key] = f"Student-{len(id_map) + 1:03d}"
        return id_map[key]

    for row in out.get("final_scores", []):
        row["display_name"] = alias(row.get("uid", 0), row.get("username", ""))
        row["username"] = row["display_name"]
    for p in out.get("pairs", []):
        p["username_a"] = alias(p.get("uid_a", 0), p.get("username_a", ""))
        p["username_b"] = alias(p.get("uid_b", 0), p.get("username_b", ""))
        p["evidence"] = []
    for a in out.get("anomalies", []):
        a["display_name"] = alias(a.get("uid", 0), a.get("username", ""))
        a["username"] = a["display_name"]
    return out


def _write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        w.writerows(rows)


def build_reports(
    analysis: dict[str, Any] | Path,
    snapshot_root: Path,
    *,
    out_dir: Path | None = None,
    top_pairs: int = 300,
    redact: bool = False,
) -> dict[str, str]:
    data = _load(analysis)
    if redact:
        data = _anonymise(data)
    snap = load_snapshot(Path(snapshot_root), load_submissions=False)
    domain, hid = data.get("domain", ""), data.get("homework_id", "")
    target = Path(out_dir) if out_dir else Path("data") / "analysis" / domain / hid
    target.mkdir(parents=True, exist_ok=True)

    problem_meta = sorted(snap.problems.values(), key=lambda p: p.get("key", ""))
    problems = [
        {"key": p["key"], "title": p.get("title", ""), "points": int(p.get("points") or 0)}
        for p in problem_meta
    ]
    names: dict[str, str] = {}
    for rid, sub in snap.submissions.items():
        s = sub.get("summary", {})
        names[rid] = s.get("username", "")

    pairs = data.get("pairs", [])[:top_pairs]
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("report.html.j2")
    html_text = template.render(
        title=data.get("title", "作业查重报告"),
        domain=domain,
        homework_id=hid,
        generated_at=data.get("generated_at", ""),
        stats=data.get("stats", {}),
        problems=problems,
        final_scores=data.get("final_scores", []),
        pairs=pairs,
        clusters=data.get("clusters", []),
        anomalies=data.get("anomalies", []),
        names=names,
        schema_version=data.get("schema_version", 1),
    )
    html_path = target / "report.html"
    html_path.write_text(html_text, encoding="utf-8")

    # final score CSV: one row per student, one column pair per problem
    headers = ["uid", "display_name", "username", "total"]
    for p in problems:
        headers += [f"{p['key']}_score", f"{p['key']}_status", f"{p['key']}_rid"]
    headers.append("total_points")
    rows = []
    for row in data.get("final_scores", []):
        line: list[Any] = [row.get("uid"), row.get("display_name"), row.get("username"), row.get("total")]
        for p in problems:
            c = (row.get("problems") or {}).get(p["key"], {})
            line += [c.get("score", 0), c.get("status", ""), c.get("rid", "")]
        line.append(row.get("total_points", 0))
        rows.append(line)
    final_csv = target / "final_scores.csv"
    _write_csv(final_csv, headers, rows)

    pair_headers = ["problem", "rid_a", "rid_b", "username_a", "username_b", "uid_a", "uid_b",
                    "score", "jaccard", "containment", "ast_cosine", "shared", "exact", "same_user",
                    "reasons", "evidence_blocks"]
    pair_rows = []
    for p in data.get("pairs", []):
        pair_rows.append([
            p.get("problem"), p.get("rid_a"), p.get("rid_b"), p.get("username_a"), p.get("username_b"),
            p.get("uid_a"), p.get("uid_b"), p.get("score"), p.get("jaccard"), p.get("containment"),
            p.get("ast_cosine"), p.get("shared"), p.get("exact"), p.get("same_user"),
            " | ".join(p.get("reasons", [])), len(p.get("evidence", [])),
        ])
    pairs_csv = target / "pairs.csv"
    _write_csv(pairs_csv, pair_headers, pair_rows)

    an_headers = ["uid", "display_name", "username", "score", "reasons", "evidence"]
    an_rows = []
    for a in data.get("anomalies", []):
        an_rows.append([
            a.get("uid"), a.get("display_name"), a.get("username"), a.get("score"),
            " | ".join(f"{r.get('code')}: {r.get('detail')}" for r in a.get("reasons", [])),
            " ".join(a.get("evidence", [])),
        ])
    anomalies_csv = target / "anomalies.csv"
    _write_csv(anomalies_csv, an_headers, an_rows)

    return {
        "html": str(html_path),
        "final_scores_csv": str(final_csv),
        "pairs_csv": str(pairs_csv),
        "anomalies_csv": str(anomalies_csv),
        "dir": str(target),
    }
