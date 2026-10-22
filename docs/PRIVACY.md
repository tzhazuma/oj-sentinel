# Privacy & data handling

OJ Sentinel is built for teachers who must handle student code and identity
data responsibly.

## What stays local

- Hydro password: sent only to the Hydro instance from the login form/CLI. The
  Web GUI keeps the authenticated session in process memory (HttpOnly cookie),
  never in a file or the browser's localStorage. The CLI reads the password from
  `OJ_PASSWORD` / `--password-stdin`.
- Snapshot data (names, accounts, submissions, judge data, reports): written to
  `data/` only. `data/` is gitignored and must never be committed.
- SQLite indexes and `analysis.json` are derived files under `data/`.

## Reports

Reports contain student names, accounts, code and similarity evidence by
default because teachers need them for review. Before sharing a report:

```bash
ojs report ... --redact
```

`--redact` replaces names/accounts with `Student-001…` and removes code
snippets. Still review the HTML/CSV manually. Do not upload student code to a
third-party plagiarism service; OJ Sentinel never does.

## AI layer

- Default provider is DeepSeek Harness (`dsh`) running on the teacher's machine.
- AI review is opt-in per pair/assignment. Only the selected snippet, problem
  statement and metadata are sent to the configured model provider.
- Configure a local/self-hosted OpenAI-compatible endpoint to keep everything
  on-premises.
- AI verdicts are advisory. Never use an AI score as the sole basis for an
  academic-integrity decision.

## Repository hygiene

Before every push:

1. `git status --short` must not show `data/`, credentials, `.env`, cookies,
   reports or logs.
2. Search for secrets: `grep -rn -E 'password|token|api[_-]?key|secret'` and
   inspect matches.
3. Run `python scripts/privacy_scan.py` (checks common secret/student-data
   patterns). CI runs the same check.

If a secret was ever committed, rotate it immediately; deleting the commit is
not sufficient.
