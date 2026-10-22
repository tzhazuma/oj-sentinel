# OJ Sentinel — internal interfaces (v0.1)

This document is the contract between the data/analysis core, the Web GUI, the AI
agent layer and the judge.  Read it before writing a module; propose a change here
first if a field is missing.

## 1. Hard rules

1. **Read-only OJ**: the tool may `GET` pages/files and `POST /login` only. Never
   submit, rejudge, cancel, upload or edit anything on the OJ.
2. **Privacy**: student names, accounts, code, test data and reports stay under
   `data/` (gitignored). Never put credentials or student data in git, logs, screenshots
   or PR text.
3. **No third-party upload**: plagiarism computation is local. The AI layer only
   sends the code snippets the teacher explicitly reviews, and can be disabled.
4. **Single source of truth**: snapshot JSON files. SQLite and `analysis.json` are
   derived and may be rebuilt.
5. Python 3.10+; core deps are requests/lxml/fastapi/uvicorn/jinja2. Desktop/Android
   extras are optional.

## 2. Snapshot layout

```
data/snapshots/<domain>/<homework_id>/
  homework.json        # Homework + "problems_detail": [Problem]
  problems/<KEY>.json  # Problem
  scoreboard.json      # Scoreboard
  records.json         # [RecordSummary]
  submissions/<rid>.json  # { "summary": RecordSummary, "detail": RecordDetail }
  testdata/<numeric_id>/  # raw judge data + config.yaml/checker.py
  manifest.json
```

Python models: `oj_sentinel.hydro.models` (`Homework`, `Problem`, `RecordSummary`,
`RecordDetail`, `Scoreboard`, `Submission`).

`Snapshot` (`oj_sentinel.ingest.load_snapshot`) exposes `homework`, `problems`,
`records`, `submissions`, `manifest`, and `rows()` -> `list[Submission]`.

## 3. Analysis output

Written to `data/analysis/<domain>/<homework_id>/analysis.json`:

```json
{
  "schema_version": 1,
  "domain": "YOUR_DOMAIN",
  "homework_id": "6acd...",
  "title": "Example Homework",
  "generated_at": "2026-10-23T01:30:00+08:00",
  "config": {"threshold": 0.35, "evidence": true},
  "stats": {"profiles": 1696, "pairs": 210, "clusters": 3, "anomalies": 12},
  "per_problem": {
    "HW2001": {"submissions": 807, "profiles": 281, "pairs": 12, "top_score": 0.94}
  },
  "pairs": [{
    "problem": "HW2001", "rid_a": "...", "rid_b": "...",
    "uid_a": 1, "uid_b": 2, "username_a": "...", "username_b": "...",
    "score": 0.87, "jaccard": 0.71, "containment": 0.89, "ast_cosine": 0.83,
    "shared": 120, "size_a": 150, "size_b": 160,
    "same_user": false, "exact": false, "method": "winnowing+ast",
    "evidence": [{"a_start": 3, "a_end": 9, "b_start": 4, "b_end": 10,
                  "lines": 6, "snippet": "..."}]
  }],
  "clusters": [{"root": "rid", "members": ["rid", "..."], "size": 2}],
  "anomalies": [{
    "uid": 123, "username": "...", "display_name": "...", "score": 0.82,
    "reasons": [{"code": "rapid_distinct", "weight": 0.4, "detail": "..."}],
    "evidence": ["rid1", "rid2"],
    "metrics": {"submissions": 9, "max_10min": 4, "burst_ratio": 0.7}
  }],
  "final_scores": [{
    "uid": 123, "username": "...", "display_name": "...",
    "total": 75, "total_points": 100,
    "problems": {
      "HW2001": {"problem": "HW2001", "title": "Basic Functions", "points": 25,
                 "score": 25, "status": "Accepted", "rid": "...",
                 "submit_at": "...", "last_submission": true}
    }
  }],
  "final_score_summary": {"HW2001": {"students": 340, "mean": 24.1, "full_score": 25}}
}
```

**Grading rule (user requirement):** the final score for each student/problem is the
score of the **last submission** ordered by `(submit_ts, rid)` — not the best
historical score.  Plagiarism and behaviour analysis still examine **all**
submissions, including earlier attempts that were later overwritten.

**Plagiarism pairing rule:** only pairs of **different students** (`uid_a != uid_b`)
are plagiarism candidates.  A student's own repeated submissions are never
reported as self-plagiarism; they feed the behaviour timeline (burst, rapid
divergence, style drift) instead.

Pairs must contain a human-readable `reasons` list (e.g. exact-normalised copy,
shared fingerprint counts, AST similarity, matched line blocks).

## 4. AI layer

`oj_sentinel.ai.agent`:

```python
@dataclass
class AgentConfig:
    provider: str = "dsh"          # dsh | openai | mock | offline
    model: str = "deepseek-v4.1-flash"
    base_url: str | None = None    # openai-compatible endpoint
    api_key_env: str = "OPENCODE_GO_2_API_KEY"
    timeout: float = 180.0
    reasoning_effort: str = "max"

@dataclass
class AgentResult:
    text: str
    data: dict | None
    provider: str
    model: str
    usage: dict
    error: str | None = None

class Agent:
    def complete(self, prompt: str, *, system: str | None = None,
                 json_schema: dict | None = None) -> AgentResult: ...
```

- `DshAgent` (default): runs `dsh --profile headless --patch <overlay> ...`
  with the configured provider/model, falls back to OpenAI-compatible HTTP.
- `OpenAICompatAgent`: `POST {base_url}/chat/completions`, OpenAI JSON schema
  via response_format when supported.
- `MockAgent`: deterministic canned JSON for tests/CI.
- All prompts live in `oj_sentinel.ai.prompts`; verdicts must be JSON with
  `verdict`, `confidence` (0..1), `summary`, `evidence` fields.

Task helpers (stable function names, keep signatures):

```python
review_pair(agent, problem, sub_a, sub_b, pair) -> AgentResult
diagnose_submission(agent, problem, submission, run_result) -> AgentResult
generate_tests(agent, problem, n=10) -> AgentResult   # JSON {"tests":[...]}
analyze_authorship(agent, problem, user_submissions) -> AgentResult
```

## 5. Judge layer

`oj_sentinel.judge.runner`:

```python
@dataclass
class CaseResult:
    name: str; status: str; time_ms: int; memory_kb: int; message: str = ""

@dataclass
class RunResult:
    status: str            # Accepted/Wrong Answer/Runtime Error/Time Exceeded/Compile Error
    score: float
    cases: list[CaseResult]
    stdout: str
    stderr: str
    compile_output: str = ""
    elapsed_ms: int = 0

def load_cases(snapshot_root: Path, problem_key: str) -> tuple[list[dict], dict]
def run_python(code: str, cases: list[dict], *, time_limit_ms=5000,
               memory_limit_mb=256, checker: dict | None = None) -> RunResult
```

`load_cases` reads `testdata/<numeric_id>/config.yaml` (fallback: `*.in`/`*.out`
pairs). `run_python` writes a temp dir, runs `python3` (or a configured
interpreter) with per-case stdin, enforces wall time and output cap, compares
with OJ-style normalized diff, and honours a Hydro checker when present.
Cross-validation compares local `RunResult.status` with the OJ record status and
writes a matrix.

## 6. Web server API

`oj_sentinel.server.app:create_app(data_dir)`; local-only by default.

Auth: `POST /api/session` `{base_url, domain, username, password}` -> sets
`ojs_session` HttpOnly cookie; credentials kept in memory only, 8h TTL.
`DELETE /api/session` logs out.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | version/status |
| POST | `/api/session` | login + homeworks |
| GET | `/api/session` | current session/homeworks |
| GET | `/api/snapshots` | local snapshots with stats |
| POST | `/api/acquire` | start acquisition job `{homework_id, options}` |
| GET | `/api/jobs` `/api/jobs/{id}` | job list/detail + log |
| GET | `/api/snapshots/{domain}/{hid}/stats` | snapshot stats |
| POST | `/api/analyze` | start analysis job `{domain, hid, threshold}` |
| GET | `/api/snapshots/{domain}/{hid}/analysis` | summary |
| GET | `/api/snapshots/{domain}/{hid}/pairs?min_score=&problem=&limit=` | pairs |
| GET | `/api/snapshots/{domain}/{hid}/anomalies` | anomalies |
| GET | `/api/snapshots/{domain}/{hid}/final-scores` | per-student per-problem last-submission scores |
| GET | `/api/submissions/{rid}?domain=&hid=` | code + detail |
| POST | `/api/ai/review` | `{domain,hid,rid_a,rid_b}` -> AI verdict (sync/queued) |
| POST | `/api/judge/run` | `{domain,hid,rid}` -> local verdict |
| GET | `/api/report/{domain}/{hid}/{name}` | download generated report |
| GET | `/` | single-page GUI |

Jobs are in-process threads; each job has `{id, kind, status, progress, log[],
created_at, finished_at, error, result}` and is polled by the GUI.

## 7. File ownership (parallel work)

| Area | Owner | Files |
| --- | --- | --- |
| Hydro + snapshot + ingest | Lead | `hydro/**`, `acquire.py`, `ingest.py`, `cli.py` |
| Normalisation/similarity | Lead | `analysis/normalize.py`, `analysis/similarity.py` |
| Behaviour analytics | behaviour teammate | `analysis/behavior.py`, `tests/test_behavior.py` |
| AI + judge | ai teammate | `ai/**`, `judge/**`, `tests/test_ai_*.py`, `tests/test_judge_*.py` |
| Web GUI | web teammate | `server/**`, `tests/test_server.py` |
| Research | research teammate | `docs/research/**` |
| Packaging/Android | packaging teammate | `packaging/**`, `android/**`, `.github/workflows/**` |
| Report builder | Lead | `report/**` |

Do not edit another owner's files; write a message instead.
