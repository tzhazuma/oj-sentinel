from pathlib import Path

from oj_sentinel.hydro import parser as P

RECORD_LIST = """
<html><body><table><tbody>
<tr data-rid="aaaaaaaaaaaaaaaaaaaaaaaa">
 <td class="col--status record-status--border fail"><div><a class="record-status--text fail"><span style="color:#f00">1</span> Runtime Error</a></div></td>
 <td class="col--problem"><a href="/d/D/p/HW2001"><b>HW2001</b>&nbsp;&nbsp;Basic Functions (25 points)</a></td>
 <td class="col--submit-by"><a class="user-profile-name uname--lv2" href="/d/D/user/42">张三 (zhangsan2026)</a></td>
 <td class="col--time">401ms</td><td class="col--memory">3.9 MiB</td>
 <td class="col--lang">Python 3</td>
 <td class="col--submit-at"><span class="time relative" data-timestamp="1792502063">2026-10-20 21:14:23</span></td>
</tr>
<tr data-rid="bbbbbbbbbbbbbbbbbbbbbbbb">
 <td class="col--status record-status--border pass"><a class="record-status--text pass"><span>25</span> Accepted</a></td>
 <td class="col--problem"><a href="/d/D/p/HW2002"><b>HW2002</b>&nbsp;&nbsp;Inventory (25 points)</a></td>
 <td class="col--submit-by"><a class="user-profile-name" href="/d/D/user/43">lisi2026</a></td>
 <td class="col--time">12ms</td><td class="col--memory">1 MiB</td>
 <td class="col--lang">Python 3</td>
 <td class="col--submit-at"><span class="time relative" data-timestamp="1792500000">x</span></td>
</tr>
</tbody></table></body></html>
"""

RECORD_DETAIL = """
<html><body>
<div class="section"><div class="section__header"><h1 class="section__title">
<span class="icon record-status--icon pass"></span><span>25</span>
<span class="record-status--text pass">Accepted</span></h1></div>
<div class="section__body"><table class="data-table record_detail__table"><tbody>
<tr class="subtask-case"><td class="col--case">#1</td>
<td class="col--status"><span class="record-status--text pass">Accepted</span>
<span class="float-right record-status--text pass">5</span></td>
<td class="col--time">10ms</td><td class="col--memory">1 MiB</td></tr>
</tbody></table></div></div>
<div class="section"><div class="section__body"><pre class="line-numbers"><code class="language-python">print(&quot;hi&quot;)
</code></pre></div></div>
</body></html>
"""

FILES = """
<html><body><table class="data-table"><tbody>
<tr><td><a href="/d/D/p/15/file/a1.in?type=testdata">a1.in</a></td><td>14 Bytes</td></tr>
<tr><td><a href="/d/D/p/15/file/a1.out?type=testdata">a1.out</a></td><td>11 Bytes</td></tr>
<tr><td><a href="/d/D/p/15/file/ref.py?type=additional_file">ref.py</a></td><td>408 Bytes</td></tr>
</tbody></table></body></html>
"""


def test_parse_record_list():
    rows = P.parse_record_list(RECORD_LIST)
    assert len(rows) == 2
    a = rows[0]
    assert a.rid == "a" * 24
    assert a.score == 1 and a.status_text == "Runtime Error"
    assert a.problem_key == "HW2001" and "Basic Functions" in a.problem_title
    assert a.uid == 42 and a.username == "zhangsan2026" and a.display_name == "张三"
    assert a.submit_ts == 1792502063
    assert rows[1].username == "lisi2026"
    assert rows[1].score == 25 and rows[1].status == "pass"


def test_parse_record_detail_code_and_cases():
    d = P.parse_record_detail(RECORD_DETAIL, "rid")
    assert d.code == 'print("hi")\n'
    assert d.language == "python"
    assert d.status_text == "Accepted" and d.score == 25
    assert len(d.tests) == 1 and d.tests[0].status == "Accepted"


def test_parse_files_and_probability(tmp_path: Path):
    files, extras, numeric = P.parse_files(FILES)
    assert numeric == 15
    assert [f.name for f in files] == ["a1.in", "a1.out"]
    assert extras[0].kind == "additional_file"
    assert files[0].size == 14


def test_parse_scoreboard():
    page = """
    <table class="data-table"><thead><tr><th>Rank</th><th>User</th><th>Score</th>
    <th>Total Time</th><th>A340/807</th><th>B340/1074</th></tr></thead><tbody>
    <tr><td class="col--rank">1</td><td><a class="user-profile-name" href="/d/D/user/7">Alice (alice)</a></td>
    <td>25</td><td>0:09</td>
    <td class="col--problem"><a href="/d/D/record/cccccccccccccccccccccccc"><span>25<br>0:09</span></a></td>
    <td class="col--problem">0<br>-</td></tr>
    </tbody></table>
    """
    sb = P.parse_scoreboard(page, "hid")
    assert [p["letter"] for p in sb.problems] == ["A", "B"]
    assert len(sb.rows) == 1
    row = sb.rows[0]
    assert row.uid == 7 and row.username == "alice" and row.score == "25"
    assert row.cells[0]["rid"] == "cccccccccccccccccccccccc"
    assert row.cells[0]["score"] == "25" and row.cells[0]["time"] == "0:09"


def test_parse_homework_detail_problems():
    page = """
    <div class="section"><div class="section__title">Homework Introduction</div>
    <div class="section__body">Do it alone.</div></div>
    <table><tr><th>Status</th><th>Problem</th></tr>
    <tr><td>No Submissions</td><td><a href="/d/D/p/HW2001?tid=x"><b>HW2001</b>&nbsp;&nbsp;Basic Functions (25 points)</a></td></tr>
    <tr><td>No Submissions</td><td><a href="/d/D/p/HW2002?tid=x"><b>HW2002</b>&nbsp;&nbsp;Inventory (25 points)</a></td></tr>
    </table>
    """
    hw = P.parse_homework_detail(page, "x")
    assert [p.key for p in hw.problems] == ["HW2001", "HW2002"]
    assert hw.problems[0].points == 25
    assert hw.description_text.startswith("Do it alone")
