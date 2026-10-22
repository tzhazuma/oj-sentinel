# AI & judge setup (OJ Sentinel)

This document covers the AI provider layer (`oj_sentinel.ai`) and the local
judge (`oj_sentinel.judge`). The default AI provider is the **local DSH CLI**
with the `opencode-go-2` route and `deepseek-v4.1-flash` (reasoning effort
`max`). No student code is uploaded unless a teacher explicitly runs an AI
action.

## 1. DSH provider (default)

`DshAgent` writes a temporary patch overlay and pipes the task to stdin:

```bash
dsh --profile headless --patch <tmp>/overlay.yml -
```

The generated overlay is equivalent to:

```yaml
# Let the headless profile resolve API keys stored by DSH Desktop.
- id: credentials
  name: '@deepseek-ai/dsh-credentials-local'
  disabled: false
- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      opencode-go-2:
        displayName: OpenCode Go 2
        apiKeyEnv: "OPENCODE_GO_2_API_KEY"
        api: openai-completions
        baseURL: "https://opencode.ai/zen/go/v1"
        headers:
          x-opencode-session: "oj-sentinel"
        models:
          - id: "deepseek-v4.1-flash"
            name: DeepSeek V4.1 Flash
            contextWindow: 1000000
            maxTokens: 384000
            reasoningEfforts:
              max: "max"
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: "opencode-go-2"
    model: "deepseek-v4.1-flash"
    reasoningEffort: "max"
```

Only the **environment variable name** is written to the overlay, never a key.

### Credentials behaviour (important)

The shipped `headless` profile disables the credentials service. Without
re-enabling it, a machine that has the key stored through DSH Desktop
(`~/.dsh/.credentials.yaml`) still fails with:

```
dsh: MISSING_CREDENTIAL: llm-pi-ai: no credential for provider route "opencode-go-2";
its profile resolves OPENCODE_GO_2_API_KEY, which is not set — store
OPENCODE_GO_2_API_KEY through the credentials service (the web Models page writes it)
or export it, ...
```

That is why the overlay above includes `- id: credentials ... disabled: false`.
It only makes the local credential store readable by this child process; it
does not print or copy secrets.

### Exact command (raw, verified)

The launcher itself is always the same; the overlay file is generated in a temp
directory and deleted afterwards:

```bash
dsh --profile headless --patch /tmp/ojs-ai-test/overlay2.yml -
# stdin: Reply with exactly OK and nothing else.
# rc=0, stdout="OK\n", wall time ~3.7 s
```

`overlay2.yml` was the overlay shown above (including the credentials entry).
Without the credentials entry the same command exits 1 with the
`MISSING_CREDENTIAL` message quoted above.

### Verified invocation (2026-10-23, macOS)

Environment: `dsh 0.2.1-rc.2`, `OPENCODE_GO_2_API_KEY` **not** exported in the
shell, key available through the local DSH credentials store.

```bash
python - <<'PY'
from oj_sentinel.ai import AgentConfig, DshAgent
agent = DshAgent(AgentConfig(timeout=150))
result = agent.complete("Reply with exactly the token OJS_DSH_OK and nothing else.")
print(result.provider, result.model, result.error)
print(repr(result.text))
PY
```

Observed result:

```
provider: dsh
model: deepseek-v4.1-flash
elapsed_s: 5.0
error: None
text: 'OJS_DSH_OK'
```

A second call with the strict verdict JSON schema returned a valid parsed
object (`verdict=unclear`, `confidence=0.5`, `evidence` list, no error), also
in ~7 s. The generated overlay lived in a temp directory that was deleted
afterwards; no secret appeared in stdout/stderr or in `AgentResult`.

If DSH is unavailable or exits non-zero, `DshAgent` falls back to
`OpenAICompatAgent` **only when** `base_url` is configured and the key env var
is set; otherwise it returns `AgentResult(error=...)` (redacted). This keeps the
GUI usable without a local DSH installation.

## 2. OpenAI-compatible fallback

```python
from oj_sentinel.ai import AgentConfig, build_agent

agent = build_agent(AgentConfig(
    provider="openai",
    base_url="http://127.0.0.1:8000/v1",
    model="deepseek-v4.1-flash",
    api_key_env="OPENCODE_GO_2_API_KEY",
    timeout=180,
))
```

`POST {base_url}/chat/completions` with `Authorization: Bearer $ENV`. When a
JSON schema is requested the payload uses `response_format={"type":
"json_schema", ...}`; if the server rejects it, the client retries once with
`{"type": "json_object"}`. API keys are read from the environment only and are
never written to results, logs or the overlay.

`provider="mock"` is deterministic and used by the offline test-suite;
`provider="offline"` returns an explicit “AI disabled” error.

## 3. Task helpers

| Helper | Output schema |
| --- | --- |
| `review_pair(agent, problem, sub_a, sub_b, pair)` | verdict JSON |
| `diagnose_submission(agent, problem, submission, run_result)` | diagnosis JSON |
| `generate_tests(agent, problem, n=10)` | `{"tests": [...]}` |
| `analyze_authorship(agent, problem, user_submissions)` | verdict JSON |
| `evaluate_project(agent, assignment, files, rubric, run_evidence)` | project rubric JSON |
| `evaluate_game_project(..., screenshots=..., logs=...)` | project rubric JSON |

Verdict JSON: `verdict` ∈ {`plausible_plagiarism`, `likely_independent`,
`unclear`, `ai_generated`, `not_ai`}, `confidence` 0..1, `summary`,
`evidence[]`, `recommended_action`.

### Open-ended grading is advisory

`evaluate_project` / `evaluate_game_project` produce `{score 0-100,
rubric_items[], strengths[], weaknesses[], evidence[], confidence,
needs_human_review}`. **The AI score is advisory only** — the instructor must
confirm or edit it. `needs_human_review` is deliberately conservative (and
defaults to `true` for mock/uncertain responses). For games, screenshots are
passed as **name + caption metadata only**; the model cannot see pixels, so any
visual-quality claim always requires human review. Logs and local run evidence
are included as text.

## 4. Local judge

`oj_sentinel.judge` never contacts the OJ; it only runs the student code
against the local snapshot.

```python
from oj_sentinel.judge import (
    load_cases, run_python, run_program, evaluate_rid, evaluate_snapshot,
    generate_and_validate, diagnose_rid, crossvalidate,
)
```

* `load_cases(snapshot_root, problem_key)` reads `problems/<KEY>.json` (numeric
  id/limits), then `testdata/<id>/config.yaml` in subtask order, falling back to
  sorted `*.in`/`*.out`. It attaches `checker.py` when present.
* `run_python(code, cases, ...)` runs `python main.py` per case in a temp dir
  with stdin from the case, per-case and global timeouts, a 2 MB output cap and
  OJ-style line normalisation (CRLF, trailing spaces, trailing blank lines).
  Hydro `test.py` wrappers for function-definition problems are appended exactly
  like `compile.sh` does.
* `run_program(language, source, cases, compile_cmd=None, run_cmd=None, ...)` is
  the generic engine. Built-ins:
  * `python` — `python main.py`
  * `c` — `gcc/clang/cc -O2 -std=c17 -o main main.c` (compiler auto-detected)
  * `cpp` — `g++/clang++/c++ -O2 -std=c++17 -o main main.cpp`
  Custom `compile_cmd` / `run_cmd` accept argv lists or shell-less strings with
  `{source}`, `{binary}`, `{workdir}`, `{interpreter}` placeholders.
* Verdict mapping: all pass → `Accepted`; any timeout → `Time Exceeded`; any
  non-zero exit/crash → `Runtime Error`; compile failure → `Compile Error`;
  otherwise `Wrong Answer`. `score` is weighted by subtask/case weight,
  `passed`/`total` are returned alongside `cases`.
* Hydro checker: invoked as `python checker.py <input> <answer> <student-output>`
  (matching the checker docstring); exit 0 = accepted, non-zero = wrong answer,
  checker output becomes the case message.
* `evaluate_rid` / `evaluate_snapshot` / `crossvalidate` are the stable CLI
  hooks: `evaluate_snapshot(..., problem_key=, rids=, limit=, accepted_only=,
  language=, out=, on_event=, workers=)` returns `{schema_version, results,
  summary}`; `crossvalidate(..., oj_status=, rids=)` returns a matrix +
  disagreement list. `evaluate_and_crossvalidate` accepts the same `workers=`.
* `workers > 1` runs independent submissions in a `ThreadPoolExecutor` (each
  call spawns its own subprocesses and temp directory). Results keep the
  original `records.json` order, progress events are monotonic and emitted from
  the coordinating thread, and a single failure becomes an error row instead of
  aborting the batch. `memory_kb` becomes approximate under concurrency; verdicts
  and scores do not change.
  Measured on the 12-core dev Mac, `limit=20` (20 cases/submission, HW2001):
  `workers=1` 11.41 s vs `workers=6` 2.74 s (**4.17x**), identical verdicts and
  order. At that rate a full 4767-submission pass drops from hours to a few
  tens of minutes.
* `generate_and_validate` validates AI-proposed tests against accepted
  submissions; accepted-submission output wins over the model's expected
  output, and tests on which accepted submissions disagree are rejected.
* `diagnose_rid` evaluates a submission (unless a `RunResult` is supplied) and
  sends problem + code + failing case to the agent.

### Safety limits

Executed code runs in a throw-away directory with a scrubbed environment (no
`PYTHONPATH`, no proxy variables, no `*_API_KEY`/`*_TOKEN`/`*_SECRET`/
`*_PASSWORD` variables), a wall-clock timeout and an output cap. An
`RLIMIT_AS` memory cap is applied on Linux via `resource.prlimit` (thread-safe);
on macOS/Windows memory limiting is best-effort and the time/output caps remain
the real protection. This is a grading helper, not a hardened sandbox: use
container/nsjail isolation when grading hostile code.

## 5. Offline tests

All AI/judge tests are offline (MockAgent + synthetic testdata + fake HTTP
responses); no student code is ever sent to an external service from tests.

```bash
.venv/bin/python -m pytest tests/test_ai_agent.py tests/test_ai_prompts.py \
    tests/test_judge_runner.py tests/test_judge_evaluate.py -q
```

C/C++ tests run only when `gcc`/`g++` (or clang equivalents) are installed and
are skipped otherwise.
