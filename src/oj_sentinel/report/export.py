"""Full report export: HTML/JSON/CSV + optional AI explanations and PDF.

The output is a single ZIP whose files are all local.  PDF generation uses
reportlab only when requested; the model/runtime is not imported otherwise.
"""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Mapping
from xml.sax.saxutils import escape

from ..ingest import load_snapshot

Event = Callable[[str], None] | None


def _emit(on_event: Event, message: str) -> None:
    if on_event:
        on_event(message)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _submission_code(snapshot_root: Path, rid: str) -> tuple[dict[str, Any], str]:
    payload = _read_json(Path(snapshot_root) / "submissions" / f"{rid}.json", {})
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else {}
    return dict(summary), str(detail.get("code") or "")


def _source_ext(summary: Mapping[str, Any], detail: Mapping[str, Any]) -> str:
    lang = str(detail.get("language") or summary.get("language") or "").lower()
    if "c++" in lang or "cpp" in lang:
        return "cpp"
    if lang.startswith("c") or lang == "c":
        return "c"
    if "java" in lang:
        return "java"
    if "python" in lang or lang.startswith("py"):
        return "py"
    return "txt"


def _safe_part(value: Any) -> str:
    text = str(value or "unknown")
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)[:80]


def _write_sources(snapshot_root: Path, pairs: list[Mapping[str, Any]], dest: Path) -> tuple[int, list[str]]:
    """Write both full source files for every suspicious pair (deduplicated by RID)."""
    dest.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        for side in ("a", "b"):
            rid = str(pair.get(f"rid_{side}") or "")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            summary, code = _submission_code(snapshot_root, rid)
            if not code:
                warnings.append(f"缺少源码：{rid}")
                continue
            problem = _safe_part(pair.get("problem") or summary.get("problem_key") or "UNKNOWN")
            username = _safe_part(pair.get(f"username_{side}") or summary.get("username") or "unknown")
            payload = _read_json(Path(snapshot_root) / "submissions" / f"{rid}.json", {})
            detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else {}
            ext = _source_ext(summary, detail)
            name = f"{problem}__{username}__{_safe_part(rid)}.{ext}"
            folder = dest / problem
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(code, encoding="utf-8")
            rows.append({
                "problem": pair.get("problem") or summary.get("problem_key") or "",
                "rid": rid, "username": pair.get(f"username_{side}") or summary.get("username") or "",
                "uid": summary.get("uid", ""), "language": summary.get("language", ""),
                "score": summary.get("score", ""), "status": summary.get("status_text", ""),
                "file": f"sources/{problem}/{name}", "code_chars": len(code),
            })
    with (dest / "index.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["problem", "rid", "username", "uid", "language", "score", "status", "file", "code_chars"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), warnings


def _pair_markdown(pair: Mapping[str, Any], ai: Mapping[str, Any] | None = None) -> str:
    lines = [
        f"## {pair.get('problem', '')} · {pair.get('username_a', '')} ↔ {pair.get('username_b', '')}",
        "",
        f"- RID: `{pair.get('rid_a')}` ↔ `{pair.get('rid_b')}`",
        f"- 综合分：{pair.get('score')}；containment：{pair.get('containment')}；"
        f"Jaccard：{pair.get('jaccard')}；AST：{pair.get('ast_cosine')}",
        f"- 最长连续匹配：{pair.get('longest_fragment', 0)} tokens；覆盖率：{pair.get('token_coverage', 0)}",
        "",
        "### 判定理由",
    ]
    for reason in pair.get("reasons") or []:
        lines.append(f"- {reason}")
    if pair.get("evidence"):
        lines.append("")
        lines.append("### 相似行证据")
        for block in pair.get("evidence", [])[:6]:
            lines.append(
                f"- A {block.get('a_start')}–{block.get('a_end')} ↔ B "
                f"{block.get('b_start')}–{block.get('b_end')}（{block.get('lines')} 行）"
            )
    if ai:
        lines += ["", "### AI 复核（建议，需人工确认）", ""]
        lines.append(f"- 结论：{ai.get('verdict', '')}；置信度：{ai.get('confidence', '')}")
        if ai.get("summary"):
            lines.append(f"- 摘要：{ai.get('summary')}")
        for item in ai.get("evidence") or []:
            lines.append(f"- 证据：{item}")
    lines.append("")
    return "\n".join(lines)


def _build_ai_section(
    snapshot_root: Path,
    analysis: Mapping[str, Any],
    pairs: list[Mapping[str, Any]],
    options: Mapping[str, Any] | None,
    *,
    max_pairs: int = 3,
    on_event: Event = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Run a few DSH reviews; failures are reported, never abort the export."""
    try:
        from ..ai import AgentConfig, build_agent
        from ..ai.prompts import review_pair
        from ..ai.trace import TraceRecorder
    except Exception as exc:  # noqa: BLE001
        return f"AI 复核不可用：{type(exc).__name__}: {exc}\n", [], {"error": str(exc)}
    opts = dict(options or {})
    kwargs: dict[str, Any] = {}
    for key in ("provider", "model", "base_url", "api_key_env", "timeout", "reasoning_effort",
                "dsh_command", "dsh_profile", "dsh_provider", "dsh_session", "max_tokens"):
        if opts.get(key) not in (None, ""):
            kwargs[key] = opts[key]
    try:
        agent = build_agent(AgentConfig(**kwargs) if kwargs else AgentConfig())
    except Exception as exc:  # noqa: BLE001
        return f"AI agent 初始化失败：{type(exc).__name__}: {exc}\n", [], {"error": str(exc)}
    trace = TraceRecorder(task="export_ai_explanations", meta={"max_pairs": max_pairs})
    agent = trace.wrap(agent)
    lines = ["# AI 详细解释（建议，需教师最终确认）", ""]
    rows: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs[: max(0, int(max_pairs))], 1):
        _emit(on_event, f"AI 复核 {index}/{min(len(pairs), max_pairs)}：{pair.get('problem')}")
        summary_a, code_a = _submission_code(snapshot_root, str(pair.get("rid_a")))
        summary_b, code_b = _submission_code(snapshot_root, str(pair.get("rid_b")))
        sub_a = {**summary_a, "rid": pair.get("rid_a"), "code": code_a}
        sub_b = {**summary_b, "rid": pair.get("rid_b"), "code": code_b}
        problem = {"key": pair.get("problem"), "title": pair.get("problem")}
        try:
            result = review_pair(agent, problem, sub_a, sub_b, dict(pair))
            data = result.to_dict() if hasattr(result, "to_dict") else result
            if not isinstance(data, Mapping):
                data = {"summary": str(data)}
            else:
                parsed = data.get("data")
                if isinstance(parsed, Mapping):
                    data = {**data, **parsed}
        except Exception as exc:  # noqa: BLE001
            data = {"verdict": "复核失败", "confidence": None,
                    "summary": f"{type(exc).__name__}: {exc}", "evidence": []}
        rows.append({"index": index, "pair": pair, "ai": dict(data)})
        lines.append(_pair_markdown(pair, data))
        if data.get("error"):
            lines.append(f"- 错误：{data.get('error')}")
        lines.append("")
    return "\n".join(lines), rows, trace.to_dict()


def _pdf_escape(value: Any) -> str:
    return escape(str(value or ""))


def build_pdf(
    analysis: Mapping[str, Any],
    *,
    ai_text: str = "",
    top_pairs: int = 20,
    out_path: Path,
    on_event: Event = None,
) -> Path:
    """Generate a Chinese-capable PDF with summary, pairs, anomalies and AI notes."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import (PageBreak, Paragraph, Preformatted, SimpleDocTemplate,
                                        Spacer, Table, TableStyle)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"PDF 依赖不可用：{type(exc).__name__}: {exc}") from exc

    font = "STSong-Light"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    except Exception:
        font = "Helvetica"
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        try:
            style.fontName = font
        except Exception:
            pass
    body = ParagraphStyle("body-cn", parent=styles["BodyText"], fontName=font, fontSize=9, leading=13)
    small = ParagraphStyle("small-cn", parent=body, fontSize=8, leading=11)
    h1 = ParagraphStyle("h1-cn", parent=styles["Heading1"], fontName=font, fontSize=18, leading=24)
    h2 = ParagraphStyle("h2-cn", parent=styles["Heading2"], fontName=font, fontSize=13, leading=18)

    stats = analysis.get("stats") or {}
    story: list[Any] = [
        Paragraph("OJ Sentinel 完整查重与异常行为报告", h1),
        Paragraph(f"作业：{_pdf_escape(analysis.get('title') or analysis.get('homework_id'))}", body),
        Paragraph(f"生成时间：{_pdf_escape(analysis.get('generated_at'))}", small),
        Spacer(1, 4 * mm),
    ]
    summary_rows = [
        ["指标", "数值"],
        ["提交记录", stats.get("submissions", 0)],
        ["学生数", stats.get("users", 0)],
        ["代码画像", stats.get("profiles", 0)],
        ["跨学生可疑对", stats.get("pairs", 0)],
        ["近复制簇", stats.get("clusters", 0)],
        ["异常行为线索", stats.get("anomalies", 0)],
    ]
    table = Table(summary_rows, colWidths=[55 * mm, 90 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f4f1")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [table, Spacer(1, 5 * mm)]
    story += [Paragraph("可疑对（按证据强度排序）", h2)]
    for pair in (analysis.get("pairs") or [])[: max(1, int(top_pairs))]:
        story.append(Paragraph(
            f"{_pdf_escape(pair.get('problem'))} · {_pdf_escape(pair.get('username_a'))} ↔ "
            f"{_pdf_escape(pair.get('username_b'))} · 综合分 {pair.get('score')}", body))
        reasons = "；".join(_pdf_escape(x) for x in (pair.get("reasons") or [])[:5])
        story.append(Paragraph(reasons or "（无理由）", small))
        snippet = ""
        for block in (pair.get("evidence") or [])[:1]:
            snippet = str(block.get("snippet") or "")[:1200]
        if snippet:
            story.append(Preformatted(snippet, ParagraphStyle("code-cn", parent=small, fontName=font, fontSize=7, leading=9)))
        story.append(Spacer(1, 3 * mm))
    anomalies = analysis.get("anomalies") or []
    if anomalies:
        story += [PageBreak(), Paragraph("异常行为线索", h2)]
        rows = [["学生", "风险分", "原因"]]
        for item in anomalies[:60]:
            rows.append([
                str(item.get("display_name") or item.get("username") or item.get("uid") or ""),
                str(item.get("score") or item.get("risk_score") or ""),
                "；".join(str(x) for x in (item.get("reasons") or [])[:3])[:180],
            ])
        table = Table(rows, colWidths=[35 * mm, 18 * mm, 95 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fff3e0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
    if ai_text.strip():
        story += [PageBreak(), Paragraph("AI 详细解释（建议，需人工确认）", h2)]
        for paragraph in ai_text.splitlines():
            if not paragraph.strip():
                story.append(Spacer(1, 2 * mm))
            elif paragraph.startswith("#"):
                story.append(Paragraph(_pdf_escape(paragraph.lstrip("# ")), body))
            else:
                story.append(Paragraph(_pdf_escape(paragraph), small))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="OJ Sentinel 查重报告", author="OJ Sentinel")
    doc.build(story)
    _emit(on_event, f"PDF 已生成：{out_path.name}")
    return out_path


def build_export(
    snapshot_root: Path,
    analysis_path: Path,
    out_dir: Path,
    *,
    include_pdf: bool = True,
    include_ai: bool = False,
    ai_options: Mapping[str, Any] | None = None,
    ai_max_pairs: int = 3,
    top_pairs: int = 30,
    on_event: Event = None,
) -> dict[str, Any]:
    """Create a ZIP with HTML/JSON/CSV, optional PDF and optional AI notes."""
    root = Path(snapshot_root)
    analysis_file = Path(analysis_path)
    analysis = _read_json(analysis_file, {})
    if not isinstance(analysis, dict) or not analysis:
        raise FileNotFoundError(f"analysis.json not found or empty: {analysis_file}")
    source_dir = analysis_file.parent
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = Path(tempfile.mkdtemp(prefix="ojs-export-", dir=str(out_dir)))
    warnings: list[str] = []
    try:
        # Core data files.  A freshly analyzed snapshot may not have the
        # HTML/CSV yet; generate them instead of exporting a ZIP that only
        # contains analysis.json (the bug seen in the desktop export).
        core_names = ("report.html", "final_scores.csv", "pairs.csv", "anomalies.csv")
        missing = [name for name in core_names if not (source_dir / name).exists()]
        if missing:
            _emit(on_event, "检测到缺失的 HTML/CSV，正在从快照生成完整报告…")
            generated = work / "_generated"
            try:
                from .builder import build_reports

                build_reports(
                    analysis_file,
                    Path(snapshot_root),
                    out_dir=generated,
                    top_pairs=max(300, int(top_pairs)),
                    include_all_pairs=True,
                )
                for name in core_names:
                    src = generated / name
                    if src.exists():
                        shutil.copy2(src, work / name)
                _emit(on_event, "已生成缺失的 report.html / CSV")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"自动生成 HTML/CSV 失败：{type(exc).__name__}: {exc}")
                _emit(on_event, warnings[-1])
            finally:
                shutil.rmtree(generated, ignore_errors=True)
        for name in ("report.html", "analysis.json", "final_scores.csv", "pairs.csv", "anomalies.csv"):
            if (work / name).exists():
                continue
            src = source_dir / name
            if not src.exists() and name == "analysis.json":
                src = analysis_file
            if src.exists():
                shutil.copy2(src, work / name)
            elif name != "analysis.json":
                warnings.append(f"缺少 {name}，已跳过")
        if (source_dir / "judge_batch.json").exists():
            shutil.copy2(source_dir / "judge_batch.json", work / "judge_batch.json")
        if (source_dir / "judge_batch.csv").exists():
            shutil.copy2(source_dir / "judge_batch.csv", work / "judge_batch.csv")
        source_count, source_warnings = _write_sources(
            Path(snapshot_root), list(analysis.get("pairs") or []), work / "sources"
        )
        warnings.extend(source_warnings)
        _emit(on_event, f"已收集 HTML/JSON/CSV 与 {source_count} 份完整源码")

        pairs = list(analysis.get("pairs") or [])
        ai_text = ""
        ai_rows: list[dict[str, Any]] = []
        if include_ai:
            _emit(on_event, "开始 AI 详细解释（会调用 DSH，可能需要数分钟）")
            ai_text, ai_rows, ai_trace = _build_ai_section(
                root, analysis, pairs, ai_options, max_pairs=max(1, int(ai_max_pairs)), on_event=on_event
            )
            (work / "ai-explanations.md").write_text(ai_text, encoding="utf-8")
            (work / "ai-explanations.json").write_text(
                json.dumps(ai_rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            (work / "ai-trace.json").write_text(
                json.dumps(ai_trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )

        summary = [
            "# 导出说明",
            "",
            f"- 作业：{analysis.get('title') or analysis.get('homework_id')}",
            f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 提交：{(analysis.get('stats') or {}).get('submissions', 0)}；"
            f"学生：{(analysis.get('stats') or {}).get('users', 0)}；"
            f"可疑对：{(analysis.get('stats') or {}).get('pairs', 0)}",
            "",
            "## 文件",
            "- report.html：完整可视化报告（双方源码、高亮、理由）",
            "- analysis.json：完整结构化数据",
            "- final_scores.csv：每生每题最后一次提交得分",
            "- pairs.csv：可疑对指标与理由",
            "- anomalies.csv：异常行为线索",
            f"- sources/：可疑对双方的完整源码文件（{source_count} 份），index.csv 为映射表",
        ]
        if include_pdf:
            summary.append("- report.pdf：详细 PDF（含理由、证据、异常与 AI 解释）")
        if include_ai:
            summary.append("- ai-explanations.md / .json：AI 复核详细解释（建议，需人工确认）")
        (work / "README.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
        (work / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "homework_id": analysis.get("homework_id"), "title": analysis.get("title"),
            "files": [], "source_files": source_count, "warnings": warnings,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        if include_pdf:
            _emit(on_event, "生成 PDF（中文字体、理由、异常、AI 解释）")
            try:
                build_pdf(analysis, ai_text=ai_text, top_pairs=min(80, max(1, int(top_pairs))),
                          out_path=work / "report.pdf")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"PDF 生成失败：{type(exc).__name__}: {exc}")
                _emit(on_event, warnings[-1])

        manifest_path = work / "manifest.json"
        manifest = _read_json(manifest_path, {})
        manifest["files"] = sorted(p.name for p in work.iterdir() if p.is_file())
        manifest["warnings"] = warnings
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        zip_path = out_dir / f"OJ-Sentinel-report-{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in sorted(work.rglob("*")):
                if item.is_file():
                    zf.write(item, arcname=str(item.relative_to(work)))
        _emit(on_event, f"导出压缩包：{zip_path.name}")
        return {
            "path": str(zip_path),
            "name": zip_path.name,
            "size": zip_path.stat().st_size,
            "files": manifest.get("files", []),
            "warnings": warnings,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


__all__ = ["build_export", "build_pdf"]
