"""HTML parsers for Hydro v4 pages.

Hydro server-renders its pages, so a handful of stable selectors are enough.
Every parser returns plain dataclasses from :mod:`oj_sentinel.hydro.models`.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from lxml import html as LH

from .models import (
    CaseResult,
    Homework,
    Problem,
    ProblemRef,
    RecordDetail,
    RecordSummary,
    Scoreboard,
    ScoreboardRow,
    TestFile,
)


class ParseError(RuntimeError):
    pass


def _doc(page: str | bytes) -> LH.HtmlElement:
    if isinstance(page, bytes):
        page = page.decode("utf-8", "replace")
    return LH.fromstring(page)


def text_of(node) -> str:
    """Visible text with sane whitespace (keeps line breaks inside pre)."""
    if isinstance(node, (list, tuple)):
        node = node[0] if node else None
    if node is None:
        return ""
    raw = node.text_content() or ""
    raw = raw.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def one_line(node) -> str:
    if isinstance(node, (list, tuple)):
        node = node[0] if node else None
    return re.sub(r"\s+", " ", text_of(node)).strip()


def page_error(page: str) -> str | None:
    """Return the Hydro error title if this is an error page."""
    m = re.search(r"<title>\s*error\s*-\s*Hydro\s*</title>", page, re.I)
    if not m:
        return None
    doc = _doc(page)
    body = one_line(doc.xpath('//div[contains(@class,"error") or contains(@class,"section__body")]'))
    return body or "Hydro returned an error page"


def _to_int(value: str, default: int = 0) -> int:
    m = re.search(r"-?\d+", value or "")
    return int(m.group()) if m else default


def _user_from_anchor(a) -> tuple[int, str, str]:
    """(uid, display_name, username) from a Hydro profile link."""
    label = one_line(a)
    uid = 0
    m = re.search(r"/user/(\d+)", a.get("href", ""))
    if m:
        uid = int(m.group(1))
    display, username = label, ""
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", label)
    if m:
        display, username = m.group(1).strip(), m.group(2).strip()
    elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,}", label):
        username = label
    return uid, display, username


def _homework_id_from_href(href: str) -> str:
    m = re.search(r"/homework/([0-9a-f]{24})", href or "")
    return m.group(1) if m else ""


def _problem_key_from_href(href: str) -> str:
    m = re.search(r"/p/([^/?#]+)", href or "")
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# Homework list / detail
# --------------------------------------------------------------------------
def parse_homework_list(page: str) -> list[Homework]:
    doc = _doc(page)
    seen: dict[str, Homework] = {}
    for a in doc.xpath('//a[contains(@href,"/homework/")]'):
        hid = _homework_id_from_href(a.get("href", ""))
        if not hid:
            continue
        title = one_line(a)
        if not title or title.lower() in {"create homework", "homework"}:
            continue
        if hid not in seen:
            seen[hid] = Homework(id=hid, title=title)
    # Prefer the title that is the card heading, not a nested link.
    for hid, hw in seen.items():
        heading = doc.xpath(f'//a[contains(@href,"/homework/{hid}")][.//text()[normalize-space()]]')
        for node in heading:
            t = one_line(node)
            if t and len(t) >= len(hw.title):
                hw.title = t
    return list(seen.values())


def parse_homework_detail(page: str, homework_id: str) -> Homework:
    doc = _doc(page)
    title_nodes = doc.xpath('//div[contains(@class,"section__title")]')
    title = ""
    for node in title_nodes:
        t = one_line(node)
        if t and t.lower() not in {"homework introduction", "problem"}:
            title = t
            break
    if not title:
        title = one_line(doc.xpath('//h1[contains(@class,"section__title")]'))
    intro = ""
    for node in doc.xpath('//div[contains(@class,"section__body")]'):
        t = text_of(node)
        if t:
            intro = t
            break
    hw = Homework(id=homework_id, title=title, description_text=intro)
    for tr in doc.xpath('//table//tr[.//a[contains(@href,"/p/")]]'):
        a = tr.xpath('.//a[contains(@href,"/p/")]')[0]
        href = a.get("href", "")
        key = _problem_key_from_href(href)
        t = one_line(a)
        points = 0
        m = re.search(r"\((\d+)\s*points?\)", t, re.I)
        if m:
            points = int(m.group(1))
        name = re.sub(r"\((\d+)\s*points?\)", "", t, flags=re.I).strip()
        name = re.sub(rf"^{re.escape(key)}\s*", "", name).strip()
        hw.problems.append(ProblemRef(key=key, title=name, points=points, href=href))
    return hw


# --------------------------------------------------------------------------
# Problem / testdata
# --------------------------------------------------------------------------
def parse_problem(page: str, key: str, files_page: str | None = None) -> Problem:
    doc = _doc(page)
    title = one_line(doc.xpath('//div[contains(@class,"problem-content-container")]//h1[contains(@class,"section__title")]'))
    if not title:
        title = one_line(doc.xpath('//h1[contains(@class,"section__title")]'))
    title = re.sub(r"^#[A-Za-z0-9]+\s*\.\s*", "", title).strip()
    points = 0
    m = re.search(r"\((\d+)\s*points?\)", title, re.I)
    if m:
        points = int(m.group(1))
    container = doc.xpath('//div[contains(@class,"problem-content-container")]')
    if not container:
        container = doc.xpath('//div[contains(@class,"problem-content")]')
    if container:
        desc_html = "\n".join(LH.tostring(x, encoding="unicode") for x in container[:1])
        desc_text = text_of(container[0])
    else:
        desc_html = desc_text = ""

    full_text = text_of(doc)
    limits = {}
    tag_texts = [one_line(x) for x in doc.xpath('//*[contains(@class,"problem__tag-item")]')]
    for t in tag_texts:
        m = re.match(r"(\d+\s*(?:ms|s|MiB|MB|KiB))$", t, re.I)
        if not m:
            continue
        val = m.group(1)
        if re.search(r"(ms|s)$", val, re.I) and "time" not in limits:
            limits["time"] = val
        elif re.search(r"(MiB|MB|KiB)$", val, re.I) and "memory" not in limits:
            limits["memory"] = val
    for label in ("Difficulty",):
        m = re.search(rf"{label}\s+([^\s]+)", full_text)
        if m:
            limits[label.lower()] = m.group(1).strip()
    tags = [one_line(a) for a in doc.xpath('//a[contains(@href,"/problem/tag") or contains(@href,"tag=")]')]
    sub_count = acc_count = 0
    m = re.search(r"#\s*Submissions\s+(\d+)", full_text)
    if m:
        sub_count = int(m.group(1))
    m = re.search(r"Accepted\s+(\d+)", full_text)
    if m:
        acc_count = int(m.group(1))

    numeric_id = 0
    m = re.search(r"/p/(\d+)/files", page)
    if m:
        numeric_id = int(m.group(1))
    if not numeric_id:
        m = re.search(r'data-pid="(\d+)"', page)
        if m:
            numeric_id = int(m.group(1))

    problem = Problem(
        key=key,
        title=re.sub(r"\s*\(\d+\s*points?\)\s*$", "", title, flags=re.I).strip() or title,
        numeric_id=numeric_id,
        points=points,
        description_html=desc_html,
        description_text=desc_text,
        time_limit=limits.get("time", ""),
        memory_limit=limits.get("memory", ""),
        difficulty=limits.get("difficulty", ""),
        tags=tags,
        submission_count=sub_count,
        accepted_count=acc_count,
    )
    if files_page:
        files, extras, numeric_id = parse_files(files_page)
        problem.files = files
        problem.additional_files = extras
        problem.numeric_id = numeric_id or problem.numeric_id
    return problem


def parse_files(page: str) -> tuple[list[TestFile], list[TestFile], int]:
    """Parse /p/<alias>/files. Returns (testdata, additional files, numeric id)."""
    doc = _doc(page)
    files: list[TestFile] = []
    extras: list[TestFile] = []
    numeric = 0
    seen: set[tuple[str, str]] = set()
    for a in doc.xpath('//a[contains(@href,"/file/")]'):
        href = a.get("href", "")
        m = re.search(r"/p/(\d+)/file/([^?]+)\?type=(testdata|additional_file)", href)
        if not m:
            continue
        numeric = int(m.group(1))
        name = m.group(2)
        kind = m.group(3)
        if (name, kind) in seen:
            continue
        seen.add((name, kind))
        row = a
        for _ in range(3):
            if row.getparent() is None:
                break
            row = row.getparent()
        size = 0
        sm = re.search(r"(\d+(?:\.\d+)?)\s*(B|KiB|MiB|KB|MB)", text_of(row))
        if sm:
            size = int(float(sm.group(1)) * {"B": 1, "KiB": 1024, "MiB": 1024**2, "KB": 1000, "MB": 1000**2}.get(sm.group(2), 1))
        entry = TestFile(name=name, size=size, kind=kind, url=href)
        (files if kind == "testdata" else extras).append(entry)
    files.sort(key=lambda f: (len(f.name), f.name))
    extras.sort(key=lambda f: f.name)
    return files, extras, numeric


# --------------------------------------------------------------------------
# Scoreboard
# --------------------------------------------------------------------------
def parse_scoreboard(page: str, homework_id: str) -> Scoreboard:
    doc = _doc(page)
    table = doc.xpath('//table[contains(@class,"data-table")]')
    if not table:
        return Scoreboard(homework_id=homework_id)
    table = table[0]
    headers = [one_line(th) for th in table.xpath('.//thead//th')]
    problems: list[dict[str, str]] = []
    for h in headers[4:]:
        m = re.match(r"^([A-Za-z])\s*(\d+)\s*/\s*(\d+)", h)
        letters = m.group(1) if m else (h[:1] if h else "?")
        problems.append({"letter": letters, "stats": h})
    sb = Scoreboard(homework_id=homework_id, problems=problems)
    for tr in table.xpath('.//tbody/tr'):
        tds = tr.xpath('./td')
        if len(tds) < 4:
            continue
        ua = tr.xpath('.//a[contains(@class,"user-profile-name")]')
        if not ua:
            continue
        uid, display, username = _user_from_anchor(ua[0])
        row = ScoreboardRow(
            rank=one_line(tds[0]),
            uid=uid,
            display_name=display,
            username=username,
            score=one_line(tds[2]),
            total_time=one_line(tds[3]),
            cells=[],
        )
        for i, td in enumerate(tds[4:]):
            parts = [x.strip() for x in td.itertext() if x and x.strip()]
            score = parts[0] if parts else ""
            tm = parts[1] if len(parts) > 1 else ""
            if not tm:
                m = re.match(r"^(-?\d+(?:\.\d+)?)(\d{1,2}:\d{2})$", score or "")
                if m:
                    score, tm = m.group(1), m.group(2)
            rid = ""
            links = td.xpath('.//a[contains(@href,"/record/")]')
            if links:
                rid = links[0].get("href", "").rstrip("/").split("/")[-1]
            letter = problems[i]["letter"] if i < len(problems) else str(i)
            row.cells.append({"letter": letter, "score": score, "time": tm, "rid": rid})
        sb.rows.append(row)
    return sb


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------
def parse_record_list(page: str) -> list[RecordSummary]:
    doc = _doc(page)
    out: list[RecordSummary] = []
    for tr in doc.xpath('//tr[@data-rid]'):
        rid = tr.get("data-rid", "")
        tds = tr.xpath('./td')
        if not tds or not rid:
            continue
        status_td = tr.xpath('./td[contains(@class,"col--status")]')
        score = 0
        status = ""
        status_text = ""
        if status_td:
            txt = text_of(status_td[0])
            m = re.match(r"^(-?\d+)\s*(.*)$", txt, re.S)
            if m:
                score = int(m.group(1))
                status_text = m.group(2).strip().splitlines()[0] if m.group(2).strip() else ""
            else:
                status_text = txt.splitlines()[0] if txt else ""
            cls = status_td[0].get("class", "")
            cm = re.search(r"record-status--(?:border|text)\s+(\w+)", cls)
            if cm:
                status = cm.group(1)
            else:
                icon = status_td[0].xpath('.//*[contains(@class,"record-status--icon")]')
                if icon:
                    icm = re.search(r"record-status--icon\s+(\w+)", icon[0].get("class", ""))
                    if icm:
                        status = icm.group(1)
            if not status:
                status = status_text.lower().replace(" ", "_")
        prob_td = tr.xpath('./td[contains(@class,"col--problem")]')
        problem_key = problem_title = ""
        if prob_td:
            link = prob_td[0].xpath('.//a[contains(@href,"/p/")]')
            if link:
                problem_key = _problem_key_from_href(link[0].get("href", ""))
                problem_title = one_line(link[0])
        ua = tr.xpath('.//a[contains(@class,"user-profile-name")]')
        uid, display, username = _user_from_anchor(ua[0]) if ua else (0, "", "")
        tds_text = [text_of(td) for td in tds]
        time_ms = _to_int(tds_text[3]) if len(tds_text) > 3 else 0
        memory = tds_text[4] if len(tds_text) > 4 else ""
        language = tds_text[5] if len(tds_text) > 5 else ""
        submit_at = ""
        submit_ts = 0
        stamp = tr.xpath('.//*[@data-timestamp]')
        if stamp:
            submit_ts = _to_int(stamp[0].get("data-timestamp"))
            submit_at = stamp[0].get("title") or one_line(stamp[0])
        if submit_ts and (not submit_at or re.fullmatch(r"[\d\-: ]+", submit_at.strip())):
            submit_at = datetime.fromtimestamp(submit_ts).strftime("%Y-%m-%d %H:%M:%S")
        out.append(
            RecordSummary(
                rid=rid,
                status=status,
                status_text=status_text,
                score=score,
                problem_key=problem_key,
                problem_title=problem_title,
                uid=uid,
                display_name=display,
                username=username,
                time_ms=time_ms,
                memory=memory,
                language=language,
                submit_at=submit_at,
                submit_ts=submit_ts,
            )
        )
    return out


def parse_record_detail(page: str, rid: str) -> RecordDetail:
    doc = _doc(page)
    detail = RecordDetail(rid=rid)
    code_nodes = doc.xpath('//pre/code')
    if code_nodes:
        detail.code = code_nodes[0].text_content()
        cls = code_nodes[0].get("class", "")
        m = re.search(r"language-(\w+)", cls)
        if m:
            detail.language = m.group(1)
    title = one_line(doc.xpath('//div[contains(@class,"section")][1]//h1[contains(@class,"section__title")]'))
    if not title:
        title = one_line(doc.xpath('//h1[contains(@class,"section__title")]'))
    detail.status_text = re.sub(r"^\s*-?\d+\s*", "", title).strip() or title
    m = re.match(r"^\s*(-?\d+)", title)
    if m:
        detail.score = float(m.group(1))
    # status class
    icon = doc.xpath('//h1[contains(@class,"section__title")]//*[contains(@class,"record-status--icon")]')
    if icon:
        im = re.search(r"record-status--icon\s+(\w+)", icon[0].get("class", ""))
        if im:
            detail.status = im.group(1)
    elif detail.status_text:
        detail.status = detail.status_text.lower().replace(" ", "_")
    for tr in doc.xpath('//tr[contains(@class,"subtask-case")]'):
        tds = tr.xpath('./td')
        if len(tds) < 2:
            continue
        idx = _to_int(text_of(tds[0]))
        st = one_line(tds[1].xpath('.//*[contains(@class,"record-status--text")]'))
        msg = text_of(tds[1].xpath('.//*[contains(@class,"message")]'))
        score_el = tds[1].xpath('.//*[contains(@class,"float-right")]')
        score = 0.0
        if score_el:
            sm = re.search(r"-?\d+(?:\.\d+)?", one_line(score_el[0]))
            if sm:
                score = float(sm.group())
        detail.tests.append(
            CaseResult(
                index=idx,
                status=st,
                score=score,
                time_ms=_to_int(text_of(tds[2])) if len(tds) > 2 else 0,
                memory=text_of(tds[3]) if len(tds) > 3 else "",
                message=msg,
            )
        )
    # compile error / system message may live in a section body
    body = text_of(doc.xpath('//div[contains(@class,"section__body")]'))
    if "Compile Error" in body or "compiler" in body.lower():
        lines = [ln for ln in body.splitlines() if ln.strip()]
        keep = []
        capture = False
        for ln in lines:
            if "Compile" in ln or "compiler" in ln.lower():
                capture = True
            if capture:
                keep.append(ln)
        detail.compile_error = "\n".join(keep[:60])
    detail.judge_text = body[:20000]
    return detail
