"""HTML/CSV report generation from a completed analysis."""
from __future__ import annotations

import csv
import difflib
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
    out["domain"] = "REDACTED"
    out["homework_id"] = "REDACTED"
    out["title"] = "作业查重报告（脱敏）"
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


def _line_evidence(code_a: str, code_b: str, limit: int = 8) -> list[dict[str, Any]]:
    """Matching line blocks, used when the analysis did not store evidence."""
    a = code_a.splitlines()
    b = code_b.splitlines()
    an = [ln.strip() for ln in a]
    bn = [ln.strip() for ln in b]
    sm = difflib.SequenceMatcher(None, an, bn, autojunk=False)
    blocks: list[dict[str, Any]] = []
    for m in sm.get_matching_blocks():
        if m.size < 2:
            continue
        snippet = "\n".join(a[m.a : m.a + min(m.size, 12)])
        if not snippet.strip():
            continue
        blocks.append({"a_start": m.a + 1, "a_end": m.a + m.size,
                       "b_start": m.b + 1, "b_end": m.b + m.size,
                       "lines": m.size, "snippet": snippet[:1200]})
    blocks.sort(key=lambda x: -x["lines"])
    return blocks[:limit]


def _code_lines(code: str, ranges: list[tuple[int, int]], *, max_lines: int = 800) -> list[dict[str, Any]]:
    lines = code.splitlines()[:max_lines]
    marks: set[int] = set()
    for start, end in ranges:
        for n in range(max(1, start), min(len(lines), end) + 1):
            marks.add(n)
    out = []
    for i, text in enumerate(lines, 1):
        out.append({"n": i, "text": text[:400], "hl": i in marks})
    if len(code.splitlines()) > max_lines:
        out.append({"n": max_lines + 1, "text": f"... ({len(code.splitlines()) - max_lines} more lines)", "hl": False})
    return out


def _attach_code(pairs: list[dict[str, Any]], snap) -> None:
    """Attach full source of both sides + highlighted matching line ranges."""
    for p in pairs:
        sub_a = snap.submissions.get(p.get("rid_a", ""), {})
        sub_b = snap.submissions.get(p.get("rid_b", ""), {})
        code_a = sub_a.get("detail", {}).get("code", "")
        code_b = sub_b.get("detail", {}).get("code", "")
        evidence = p.get("evidence") or []
        if not evidence and code_a and code_b:
            evidence = _line_evidence(code_a, code_b)
            p["evidence"] = evidence
        ranges_a = [(e.get("a_start", 1), e.get("a_end", 1)) for e in evidence]
        ranges_b = [(e.get("b_start", 1), e.get("b_end", 1)) for e in evidence]
        p["code_a"] = _code_lines(code_a, ranges_a)
        p["code_b"] = _code_lines(code_b, ranges_b)
        p["language_a"] = sub_a.get("summary", {}).get("language", "")
        p["language_b"] = sub_b.get("summary", {}).get("language", "")
        p["display_a"] = sub_a.get("summary", {}).get("display_name", "") or p.get("username_a", "")
        p["display_b"] = sub_b.get("summary", {}).get("display_name", "") or p.get("username_b", "")


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
    snap = load_snapshot(Path(snapshot_root), load_submissions=not redact)
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

    all_pairs = data.get("pairs", [])
    substantial = [p for p in all_pairs if p.get("substantial", True)]
    pairs = (substantial or all_pairs)[:top_pairs]
    if not redact:
        _attach_code(pairs, snap)
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
        redact=redact,
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
                    "score", "jaccard", "containment", "ast_cosine", "codebert_cosine", "longest_fragment", "token_coverage",
                    "distinctive_ratio", "template_only", "shared", "exact", "same_user",
                    "reasons", "evidence_blocks"]
    pair_rows = []
    for p in data.get("pairs", []):
        pair_rows.append([
            p.get("problem"), p.get("rid_a"), p.get("rid_b"), p.get("username_a"), p.get("username_b"),
            p.get("uid_a"), p.get("uid_b"), p.get("score"), p.get("jaccard"), p.get("containment"),
            p.get("ast_cosine"), p.get("codebert_cosine"), p.get("longest_fragment"), p.get("token_coverage"),
            p.get("distinctive_ratio"), p.get("template_only"), p.get("shared"), p.get("exact"), p.get("same_user"),
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
