"""Tests for the local FastAPI server and Web GUI API.

The tests never touch the real OJ: authentication is mocked at the
``HydroClient`` class boundary and every snapshot lives in a temporary data
directory.
"""
from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import oj_sentinel.server.app as server_app  # noqa: E402
from oj_sentinel.hydro.session import HydroAuthError, HydroClient  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _record(rid: str, problem: str, uid: int, username: str, display: str,
            score: int, ts: int, status: str = "Wrong Answer") -> dict:
    return {
        "rid": rid,
        "status": "correct" if status == "Accepted" else "wrong",
        "status_text": status,
        "score": score,
        "problem_key": problem,
        "problem_title": f"题目 {problem}",
        "uid": uid,
        "display_name": display,
        "username": username,
        "time_ms": 10,
        "memory": "9 MB",
        "language": "Python 3",
        "submit_at": f"2026-01-01 10:00:{ts % 60:02d}",
        "submit_ts": ts,
    }


def make_snapshot(data_dir: Path, *, with_analysis: bool = True) -> Path:
    domain, hid = "DOM", "HID"
    root = data_dir / "snapshots" / domain / hid
    problems = [
        {"key": "HW1", "title": "两数之和", "numeric_id": 11, "points": 100},
        {"key": "HW2", "title": "字符串统计", "numeric_id": 12, "points": 100},
    ]
    _write_json(root / "problems" / "HW1.json", problems[0])
    _write_json(root / "problems" / "HW2.json", problems[1])
    _write_json(
        root / "homework.json",
        {
            "id": hid,
            "domain": domain,
            "title": "演示作业",
            "base_url": "http://demo.invalid",
            "problems": [{"key": p["key"], "title": p["title"], "points": p["points"], "href": ""} for p in problems],
            "problems_detail": problems,
        },
    )
    records = [
        # Deliberately not sorted: the last submission (r1, ts=200) is not the
        # highest-scoring submission (r2, ts=100).  Final score must be 30.
        _record("r1", "HW1", 1, "alice", "Alice 张", 30, 200, "Wrong Answer"),
        _record("r2", "HW1", 1, "alice", "Alice 张", 100, 100, "Accepted"),
        _record("r3", "HW1", 2, "bob", "Bob 李", 80, 150, "Wrong Answer"),
        _record("r4", "HW2", 2, "bob", "Bob 李", 100, 300, "Accepted"),
        _record("r5", "HW2", 1, "alice", "Alice 张", 0, 400, "Runtime Error"),
    ]
    _write_json(root / "records.json", records)
    code_by_rid = {
        "r1": "print(30)\n",
        "r2": "print(100)\n",
        "r3": "print(80)\n",
        "r4": "print('hello')\n",
        "r5": "raise SystemExit(1)\n",
    }
    for rec in records:
        _write_json(
            root / "submissions" / f"{rec['rid']}.json",
            {
                "summary": rec,
                "detail": {
                    "rid": rec["rid"],
                    "code": code_by_rid[rec["rid"]],
                    "language": "Python 3",
                    "status": rec["status"],
                    "status_text": rec["status_text"],
                    "score": float(rec["score"]),
                    "tests": [{"index": 1, "status": "AC" if rec["score"] == 100 else "WA",
                               "score": rec["score"], "time_ms": 5, "memory": "9 MB", "message": ""}],
                    "compile_error": "",
                    "judge_text": "演示",
                },
            },
        )
    _write_json(
        root / "scoreboard.json",
        {
            "homework_id": hid,
            "problems": [{"key": p["key"], "title": p["title"]} for p in problems],
            "rows": [
                {"rank": "1", "uid": 2, "display_name": "Bob 李", "username": "bob", "score": "", "total_time": "", "cells": []},
                {"rank": "2", "uid": 1, "display_name": "Alice 张", "username": "alice", "score": "", "total_time": "", "cells": []},
            ],
        },
    )
    _write_json(
        root / "manifest.json",
        {
            "domain": domain,
            "homework_id": hid,
            "homework_title": "演示作业",
            "problems": ["HW1", "HW2"],
            "record_count": len(records),
            "detail_count": len(records),
            "testdata_files": 4,
            "failed": 0,
            "completed_at": "2026-01-01T12:00:00+0800",
        },
    )

    analysis_dir = data_dir / "analysis" / domain / hid
    if with_analysis:
        pairs = [
            {
                "problem": "HW1", "rid_a": "r1", "rid_b": "r3",
                "uid_a": 1, "uid_b": 2, "username_a": "alice", "username_b": "bob",
                "score": 0.91, "jaccard": 0.8, "containment": 0.9, "ast_cosine": 0.85,
                "shared": 30, "size_a": 32, "size_b": 33,
                "same_user": False, "exact": False, "method": "winnowing+ast",
                "evidence": [{"a_start": 1, "a_end": 1, "b_start": 1, "b_end": 1, "lines": 1,
                              "snippet": "print(30)"}],
                "reasons": ["共享 30 个指纹", "匹配代码块 1 段"],
            },
            {
                "problem": "HW2", "rid_a": "r4", "rid_b": "r3",
                "uid_a": 2, "uid_b": 2, "username_a": "bob", "username_b": "bob",
                "score": 0.40, "jaccard": 0.4, "containment": 0.5, "ast_cosine": 0.4,
                "shared": 12, "size_a": 20, "size_b": 22,
                "same_user": True, "exact": False, "method": "winnowing+ast",
                "evidence": [],
                "reasons": ["同用户提交"],
            },
        ]
        analysis = {
            "schema_version": 1,
            "domain": domain,
            "homework_id": hid,
            "title": "演示作业",
            "generated_at": "2026-01-01T12:30:00+08:00",
            "config": {"threshold": 0.6, "with_evidence": True},
            "stats": {"profiles": 5, "pairs": 2, "clusters": 1, "anomalies": 1},
            "per_problem": {
                "HW1": {"submissions": 3, "profiles": 3, "pairs": 1, "top_score": 0.91},
                "HW2": {"submissions": 2, "profiles": 2, "pairs": 1, "top_score": 0.4},
            },
            "pairs": pairs,
            "clusters": [{"root": "r1", "members": ["r1", "r3"], "size": 2}],
            "anomalies": [
                {
                    "uid": 1,
                    "username": "alice",
                    "display_name": "Alice 张",
                    "score": 0.82,
                    "reasons": [{"code": "burst", "weight": 0.5, "detail": "10 分钟内 4 次提交"}],
                    "evidence": ["r1", "r2"],
                    "metrics": {"submissions": 3, "max_10min": 4},
                }
            ],
            "final_scores": [
                {
                    "uid": 1, "username": "alice", "display_name": "Alice 张",
                    "total": 30, "total_points": 200,
                    "problems": {
                        "HW1": {"problem": "HW1", "title": "两数之和", "points": 100,
                                 "score": 30, "status": "Wrong Answer", "rid": "r1",
                                 "submit_at": "2026-01-01 10:03:20", "last_submission": True},
                        "HW2": {"problem": "HW2", "title": "字符串统计", "points": 100,
                                 "score": 0, "status": "Runtime Error", "rid": "r5",
                                 "submit_at": "2026-01-01 10:06:40", "last_submission": True},
                    },
                },
                {
                    "uid": 2, "username": "bob", "display_name": "Bob 李",
                    "total": 180, "total_points": 200,
                    "problems": {
                        "HW1": {"problem": "HW1", "title": "两数之和", "points": 100,
                                 "score": 80, "status": "Wrong Answer", "rid": "r3",
                                 "submit_at": "2026-01-01 10:02:30", "last_submission": True},
                        "HW2": {"problem": "HW2", "title": "字符串统计", "points": 100,
                                 "score": 100, "status": "Accepted", "rid": "r4",
                                 "submit_at": "2026-01-01 10:05:00", "last_submission": True},
                    },
                },
            ],
            "final_score_summary": {"HW1": {"students": 2, "mean": 55, "full_score": 80}},
        }
        _write_json(analysis_dir / "analysis.json", analysis)
        (analysis_dir / "report.html").write_text(
            "<html><body>演示报告</body></html>", encoding="utf-8"
        )
        (analysis_dir / "pairs.csv").write_text("problem,score\nHW1,0.91\n", encoding="utf-8")
        # A private file in the report directory must NOT be downloadable even
        # though it physically exists.
        (analysis_dir / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return root


@pytest.fixture()
def app(tmp_path: Path):
    return server_app.create_app(tmp_path)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# health, snapshots, static SPA
# --------------------------------------------------------------------------- #
def test_health_and_empty_snapshot_list(client, tmp_path):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == server_app.VERSION
    assert data["data_dir"] == str(tmp_path.resolve())
    assert data["snapshots"] == 0

    response = client.get("/api/snapshots")
    assert response.status_code == 200
    assert response.json() == []

    # The SPA must render even with an empty data directory and must not use a
    # CDN or node build.
    response = client.get("/")
    assert response.status_code == 200
    assert "OJ Sentinel" in response.text
    assert "/static/app.js" in response.text
    assert "/static/styles.css" in response.text
    assert "cdn." not in response.text.lower()
    for asset in ("/static/app.js", "/static/styles.css"):
        response = client.get(asset)
        assert response.status_code == 200
        assert len(response.text) > 500


def test_snapshot_listing_and_stats(tmp_path):
    make_snapshot(tmp_path)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/snapshots")
        assert response.status_code == 200
        snapshots = response.json()
        assert len(snapshots) == 1
        snapshot = snapshots[0]
        assert snapshot["domain"] == "DOM"
        assert snapshot["hid"] == "HID"
        assert snapshot["title"] == "演示作业"
        assert snapshot["records"] == 5
        assert snapshot["details"] == 5
        assert snapshot["problems"] == 2
        assert snapshot["users"] == 2
        assert snapshot["analysis_available"] is True
        assert snapshot["analysis"]["available"] is True
        assert {report["name"] for report in snapshot["reports"]} >= {"report.html", "pairs.csv"}

        response = client.get("/api/snapshots/DOM/HID/stats")
        assert response.status_code == 200
        stats = response.json()
        assert stats["records"] == 5
        assert stats["details"] == 5
        assert stats["users"] == 2
        assert stats["per_problem"]["HW1"]["submissions"] == 3
        assert stats["problems"]["HW1"]["title"] == "两数之和"
        assert "description_html" not in stats["problems"]["HW1"]


def test_missing_snapshot_and_analysis_return_json_errors(client):
    response = client.get("/api/snapshots/NOPE/HID/stats")
    assert response.status_code == 404
    assert "detail" in response.json()

    response = client.get("/api/snapshots/DOM/HID/analysis")
    assert response.status_code == 404
    body = response.json()
    assert body["available"] is False
    assert "hint" in body


# --------------------------------------------------------------------------- #
# analysis endpoints
# --------------------------------------------------------------------------- #
def test_analysis_endpoints(tmp_path):
    make_snapshot(tmp_path)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        summary = client.get("/api/snapshots/DOM/HID/analysis").json()
        assert summary["available"] is True
        assert summary["counts"] == {"pairs": 2, "clusters": 1, "anomalies": 1, "final_scores": 2}
        assert summary["stats"]["pairs"] == 2

        pairs_response = client.get("/api/snapshots/DOM/HID/pairs").json()
        assert pairs_response["available"] is True
        assert pairs_response["total"] == 2
        assert pairs_response["pairs"][0]["score"] == 0.91

        filtered = client.get("/api/snapshots/DOM/HID/pairs", params={"min_score": 0.8}).json()
        assert filtered["matched"] == 1
        assert filtered["pairs"][0]["rid_a"] == "r1"

        by_problem = client.get("/api/snapshots/DOM/HID/pairs", params={"problem": "HW2"}).json()
        assert by_problem["matched"] == 1
        assert by_problem["pairs"][0]["problem"] == "HW2"

        limited = client.get("/api/snapshots/DOM/HID/pairs", params={"limit": 1}).json()
        assert len(limited["pairs"]) == 1

        anomalies = client.get("/api/snapshots/DOM/HID/anomalies").json()
        assert anomalies["available"] is True
        assert anomalies["total"] == 1
        assert anomalies["anomalies"][0]["reasons"][0]["code"] == "burst"

        clusters = client.get("/api/snapshots/DOM/HID/clusters").json()
        assert clusters["available"] is True
        assert clusters["clusters"][0]["size"] == 2
        assert "alice" in clusters["clusters"][0]["usernames"]

        finals = client.get("/api/snapshots/DOM/HID/final-scores").json()
        assert finals["available"] is True
        assert finals["source"] == "analysis"
        alice = next(row for row in finals["students"] if row["uid"] == 1)
        assert alice["problems"]["HW1"]["score"] == 30
        assert alice["problems"]["HW1"]["rid"] == "r1"

        submission = client.get("/api/submissions/r1", params={"domain": "DOM", "hid": "HID"}).json()
        assert submission["rid"] == "r1"
        assert submission["summary"]["rid"] == "r1"
        assert submission["detail"]["code"] == "print(30)\n"
        assert "tests" in submission["detail"]
        assert submission["language"] == "Python 3"
        assert submission["problem"]["title"] == "两数之和"

        missing = client.get("/api/submissions/does-not-exist", params={"domain": "DOM", "hid": "HID"})
        assert missing.status_code == 404


def test_final_scores_computed_from_records_uses_last_submission(tmp_path):
    make_snapshot(tmp_path, with_analysis=False)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        finals = client.get("/api/snapshots/DOM/HID/final-scores").json()
        assert finals["available"] is True
        assert finals["source"] == "records"
        alice = next(row for row in finals["students"] if row["uid"] == 1)
        # r2 (100) came before r1 (30): the later submission wins.
        assert alice["problems"]["HW1"]["score"] == 30
        assert alice["problems"]["HW1"]["rid"] == "r1"
        bob = next(row for row in finals["students"] if row["uid"] == 2)
        assert bob["problems"]["HW1"]["score"] == 80
        assert bob["problems"]["HW2"]["score"] == 100

        # The analysis endpoint still returns a structured 404, not a crash.
        response = client.get("/api/snapshots/DOM/HID/pairs")
        assert response.status_code == 404
        assert response.json()["available"] is False


# --------------------------------------------------------------------------- #
# attachment / path traversal
# --------------------------------------------------------------------------- #
def test_submission_response_shape_never_path_traverses(tmp_path):
    make_snapshot(tmp_path)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/submissions/r1", params={"domain": "DOM", "hid": "HID"})
        assert response.status_code == 200
        body = response.json()
        assert {"summary", "detail", "problem"} <= set(body)
        assert isinstance(body["detail"], dict)

        for rid in ("..", "%2e%2e", "../secret", "..%2Fsecret"):
            response = client.get(f"/api/submissions/{rid}", params={"domain": "DOM", "hid": "HID"})
            assert response.status_code in (404, 400), rid

        response = client.get("/api/submissions/r1", params={"domain": "../..", "hid": "HID"})
        assert response.status_code in (404, 400)
        response = client.get("/api/submissions/r1", params={"domain": "DOM", "hid": "../../etc"})
        assert response.status_code in (404, 400)


def test_report_download_whitelist_and_traversal(tmp_path):
    make_snapshot(tmp_path)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/report/DOM/HID/report.html")
        assert response.status_code == 200
        assert "演示报告" in response.text

        response = client.get("/api/report/DOM/HID/analysis.json")
        assert response.status_code == 200
        assert response.json()["schema_version"] == 1

        # Existing but not whitelisted.
        response = client.get("/api/report/DOM/HID/secret.txt")
        assert response.status_code == 404

        # Traversal / encoded traversal / other directories.
        for name in ("%2e%2e%2fsecret.txt", "..%2Fanalysis.json", "..", "%2Fetc%2Fpasswd"):
            response = client.get(f"/api/report/DOM/HID/{name}")
            assert response.status_code in (404, 400), name
        response = client.get("/api/report/%2e%2e/HID/report.html")
        assert response.status_code in (404, 400)
        response = client.get("/api/report/DOM/%2e%2e/report.html")
        assert response.status_code in (404, 400)
        response = client.get("/api/snapshots/%2e%2e/%2e%2e/stats")
        assert response.status_code in (404, 400)


# --------------------------------------------------------------------------- #
# authentication (HydroClient is mocked; no network is used)
# --------------------------------------------------------------------------- #
class FakeHomework:
    def __init__(self, hid: str, title: str) -> None:
        self.id = hid
        self.title = title

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title}


def test_session_cookie_login_and_logout(tmp_path, monkeypatch):
    captured = {}

    def fake_login(self, username, password):
        captured["username"] = username
        captured["password"] = password
        captured["base_url"] = self.base_url
        captured["domain"] = self.domain
        self.username = username
        self._logged_in = True
        return True

    def fake_homeworks(self):
        return [FakeHomework("HID", "演示作业")]

    monkeypatch.setattr(HydroClient, "login", fake_login)
    monkeypatch.setattr(HydroClient, "list_homeworks", fake_homeworks)

    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/session",
            json={
                "base_url": "http://oj.invalid",
                "domain": "DOM",
                "username": "teacher",
                "password": "super-secret",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["user"]["username"] == "teacher"
        assert data["homeworks"][0]["id"] == "HID"
        assert captured == {
            "username": "teacher",
            "password": "super-secret",
            "base_url": "http://oj.invalid",
            "domain": "DOM",
        }
        set_cookie = response.headers["set-cookie"]
        assert server_app.COOKIE_NAME in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "max-age=28800" in set_cookie.lower()
        assert "super-secret" not in response.text

        response = client.get("/api/session")
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "teacher"

        # Credentials must never be persisted under the data directory.
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert "super-secret" not in path.read_text(encoding="utf-8", errors="ignore")

        response = client.delete("/api/session")
        assert response.status_code == 200
        assert response.json()["authenticated"] is False
        assert client.get("/api/session").status_code == 401
        # The client also refuses to start acquisition after logout.
        assert client.post("/api/acquire", json={"homework_id": "HID"}).status_code == 401


def test_login_failure_returns_401(tmp_path, monkeypatch):
    def fail_login(self, username, password):
        raise HydroAuthError("用户名或密码错误")

    monkeypatch.setattr(HydroClient, "login", fail_login)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/session",
            json={
                "base_url": "http://oj.invalid",
                "domain": "DOM",
                "username": "teacher",
                "password": "wrong",
            },
        )
        assert response.status_code == 401
        assert "登录失败" in response.json()["detail"]
        assert client.get("/api/session").status_code == 401


def test_acquire_requires_login(client):
    response = client.post("/api/acquire", json={"homework_id": "HID"})
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def test_analyze_job_runs_in_background_and_reports_progress(tmp_path, monkeypatch):
    make_snapshot(tmp_path, with_analysis=False)

    fake_engine = types.ModuleType("oj_sentinel.analysis.engine")

    def fake_run(snapshot_root, *, out_dir=None, threshold=0.6, max_pairs=20000, on_event=None):
        root = Path(snapshot_root)
        assert (root / "homework.json").exists()
        if on_event:
            on_event("profiles 1/2")
            on_event("profiles 2/2")
        target = Path(out_dir or (tmp_path / "analysis"))
        target.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "domain": "DOM",
            "homework_id": "HID",
            "title": "演示作业",
            "generated_at": "2026-01-01T00:00:00+08:00",
            "stats": {"profiles": 2, "pairs": 1, "clusters": 0, "anomalies": 0},
            "per_problem": {},
            "pairs": [{"problem": "HW1", "rid_a": "r1", "rid_b": "r3", "score": 0.9, "evidence": []}],
            "clusters": [],
            "anomalies": [],
            "final_scores": [],
        }
        (target / "analysis.json").write_text(json.dumps(payload), encoding="utf-8")
        return payload

    fake_engine.run_analysis = fake_run
    monkeypatch.setitem(sys.modules, "oj_sentinel.analysis.engine", fake_engine)

    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            json={"domain": "DOM", "hid": "HID", "threshold": 0.6},
        )
        assert response.status_code == 202
        job = response.json()
        assert job["kind"] == "analyze"
        assert job["id"]

        for _ in range(100):
            current = client.get(f"/api/jobs/{job['id']}").json()
            if current["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        else:  # pragma: no cover - defensive
            pytest.fail("analysis job did not finish")

        assert current["status"] == "succeeded", current
        assert current["progress"] == 100
        assert current["error"] is None
        assert any("profiles 1/2" in line for line in current["log"])
        assert (tmp_path / "analysis" / "DOM" / "HID" / "analysis.json").is_file()

        jobs = client.get("/api/jobs").json()
        assert any(item["id"] == job["id"] for item in jobs)

        summary = client.get("/api/snapshots/DOM/HID/analysis").json()
        assert summary["available"] is True
        assert summary["counts"]["pairs"] == 1


def test_job_log_is_capped_at_500_lines(app):
    jobs = app.state.jobs
    job = jobs.create("test", "日志上限")
    for index in range(650):
        jobs.add_log(job, f"第 {index} 行")
    assert len(jobs.get_raw(job.id).log) == server_app.LOG_CAP
    dumped = jobs.get(job.id)
    assert len(dumped["log"]) == 500
    assert "649" in dumped["log"][-1]


def test_job_404(client):
    response = client.get("/api/jobs/not-a-real-job")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# AI / project / judge availability handling
# --------------------------------------------------------------------------- #
def test_project_evaluation_returns_501_when_helper_missing(client, monkeypatch):
    monkeypatch.setattr(
        server_app,
        "_load_project_helper",
        lambda: (None, "测试：evaluate_project 未提供"),
    )
    response = client.post(
        "/api/ai/evaluate-project",
        json={
            "title": "贪吃蛇",
            "description": "用 Python 实现一个贪吃蛇小游戏",
            "rubric": "功能 50 分；代码质量 30 分；创意 20 分",
            "files": [{"name": "snake.py", "text": "print('demo')\n"}],
        },
    )
    assert response.status_code == 501
    body = response.json()
    assert body["available"] is False
    assert body["error"] == "作品评测后端尚未就绪"
    assert body["endpoint"] == "/api/ai/evaluate-project"
    assert body["received"]["files"] == 1


def test_project_evaluation_with_mock_agent(client):
    pytest.importorskip("oj_sentinel.ai.agent")
    response = client.post(
        "/api/ai/evaluate-project",
        json={
            "title": "贪吃蛇",
            "student": "alice",
            "description": "用 Python 实现一个贪吃蛇小游戏",
            "rubric": "功能 50 分；代码质量 30 分；创意 20 分",
            "files": [{"name": "snake.py", "text": "print('demo')\n"}],
            "options": {"provider": "mock"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["advisory"] is True
    assert body["provider"] == "mock"
    assert "evaluation" in body
    assert "score" in body["evaluation"]


def test_ai_review_returns_501_when_helper_missing(tmp_path, monkeypatch):
    make_snapshot(tmp_path)
    monkeypatch.setattr(
        server_app,
        "_load_ai_review_helper",
        lambda: (None, "测试：review_pair 未提供"),
    )
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/ai/review",
            json={"domain": "DOM", "hid": "HID", "rid_a": "r1", "rid_b": "r3"},
        )
        assert response.status_code == 501
        body = response.json()
        assert body["available"] is False
        assert "AI" in body["error"]


def test_ai_review_with_mock_agent(tmp_path, monkeypatch):
    make_snapshot(tmp_path)
    pytest.importorskip("oj_sentinel.ai.agent")
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/ai/review",
            json={
                "domain": "DOM",
                "hid": "HID",
                "rid_a": "r1",
                "rid_b": "r3",
                "options": {"provider": "mock"},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["available"] is True
        assert body["provider"] == "mock"
        assert body["verdict"]["verdict"]
        assert body["pair"]["rid_a"] == "r1"


def test_judge_returns_501_when_runner_missing(tmp_path, monkeypatch):
    make_snapshot(tmp_path)
    real_import = server_app._import_optional

    def fake_import(module_name, attr=None):
        if module_name == "oj_sentinel.judge.runner":
            return None, "测试：judge.runner 未提供"
        return real_import(module_name, attr)

    monkeypatch.setattr(server_app, "_import_optional", fake_import)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/judge/run",
            json={"domain": "DOM", "hid": "HID", "rid": "r1"},
        )
        assert response.status_code == 501
        body = response.json()
        assert body["available"] is False
        assert "本地评测" in body["error"]


def test_batch_judge_returns_501_when_helper_missing(tmp_path, monkeypatch):
    make_snapshot(tmp_path)
    real_import = server_app._import_optional

    def fake_import(module_name, attr=None):
        if module_name == "oj_sentinel.judge.evaluate":
            return None, "测试：judge.evaluate 未提供"
        return real_import(module_name, attr)

    monkeypatch.setattr(server_app, "_import_optional", fake_import)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/judge/batch",
            json={"domain": "DOM", "hid": "HID", "limit": 5},
        )
        assert response.status_code == 501
        body = response.json()
        assert body["available"] is False
        assert "批量评测" in body["error"]


def test_batch_judge_job_exports_and_progress(tmp_path, monkeypatch):
    make_snapshot(tmp_path)
    captured: dict = {}

    fake_module = types.ModuleType("oj_sentinel.judge.evaluate")

    def fake_batch(snapshot_root, *, problem_key=None, limit=None, accepted_only=False,
                   language=None, out=None, on_event=None, workers=1):
        captured.update({
            "snapshot_root": str(snapshot_root),
            "problem_key": problem_key,
            "limit": limit,
            "accepted_only": accepted_only,
            "language": language,
            "out": str(out),
            "workers": workers,
        })
        results = [
            {
                "rid": "r1", "problem": "HW1", "uid": 1, "username": "alice",
                "language": "python", "status": "Wrong Answer", "score": 30.0,
                "passed": 1, "total": 2, "elapsed_ms": 12,
                "oj_status": "Wrong Answer", "error": "",
            },
            {
                "rid": "r2", "problem": "HW1", "uid": 1, "username": "alice",
                "language": "python", "status": "Accepted", "score": 100.0,
                "passed": 2, "total": 2, "elapsed_ms": 15,
                "oj_status": "Accepted", "error": "",
            },
        ]
        if on_event:
            on_event({"kind": "progress", "done": 1, "total": 2, "message": "judged 1/2"})
            on_event({"kind": "progress", "done": 2, "total": 2, "message": "judged 2/2"})
        payload = {
            "schema_version": 1,
            "results": results,
            "summary": {
                "evaluated": 2,
                "status_counts": {"Wrong Answer": 1, "Accepted": 1},
                "local_accepted": 1,
                "oj_accepted": 1,
                "errors": 0,
            },
            "crossvalidation": {
                "compared": 2,
                "agreement": 1,
                "disagreement": 1,
                "agreement_rate": 0.5,
            },
        }
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(json.dumps(payload), encoding="utf-8")
        return payload

    fake_module.evaluate_and_crossvalidate = fake_batch
    monkeypatch.setitem(sys.modules, "oj_sentinel.judge.evaluate", fake_module)

    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/judge/batch",
            json={
                "domain": "DOM",
                "hid": "HID",
                "problem": "HW1",
                "all": True,
                "accepted_only": False,
                "language": "python",
            },
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["kind"] == "judge_batch"

        for _ in range(100):
            current = client.get(f"/api/jobs/{job['id']}").json()
            if current["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        else:  # pragma: no cover - defensive
            pytest.fail("batch judge job did not finish")

        assert current["status"] == "succeeded", current
        assert current["progress"] == 100
        assert any("judged 2/2" in line for line in current["log"])
        result = current["result"]
        assert result["evaluated"] == 2
        assert result["status_counts"] == {"Wrong Answer": 1, "Accepted": 1}
        assert result["local_accepted"] == 1
        assert result["oj_accepted"] == 1
        assert result["agreement_rate"] == 0.5
        assert result["output_path"].endswith("judge_batch.json")
        assert result["csv_url"].endswith("judge_batch.csv")
        assert len(result["rows"]) == 2
        assert result["rows"][0]["matched"] is True
        assert captured["problem_key"] == "HW1"
        assert captured["limit"] is None
        assert captured["accepted_only"] is False
        assert captured["language"] == "python"

        json_export = client.get("/api/report/DOM/HID/judge_batch.json")
        assert json_export.status_code == 200
        assert json_export.json()["summary"]["evaluated"] == 2
        csv_export = client.get("/api/report/DOM/HID/judge_batch.csv")
        assert csv_export.status_code == 200
        assert "r1" in csv_export.text
        assert "OJ" not in csv_export.text


def test_security_headers_present(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers
    assert response.headers["cache-control"] == "no-store"


def test_judge_doctor_and_codebert_status(tmp_path):
    make_snapshot(tmp_path)
    app = server_app.create_app(tmp_path)
    with TestClient(app) as client:
        doctor = client.post(
            "/api/judge/doctor",
            json={"domain": "DOM", "hid": "HID"},
        )
        assert doctor.status_code == 200, doctor.text
        body = doctor.json()
        assert body["available"] is True
        assert body["python_ok"] is True
        assert body["python_interpreter"]
        assert any(p.get("problem") == "HW1" for p in body["problems"])

        status = client.post(
            "/api/ai/codebert/status",
            json={"model": "microsoft/codebert-base"},
        )
        assert status.status_code == 200, status.text
        data = status.json()
        assert data["available"] is True
        assert data["bundled"] is False
        assert "path" in data
