"""Local judge: test-case loading, sandboxed execution and OJ-style verdicts.

Interface section 5.  Python is the primary language; :func:`run_program` is
the generic engine and :func:`run_python` is the documented thin wrapper.
C and C++ are supported through the system compiler (``gcc``/``clang``,
``g++``/``clang++``) so the same verdict/score machinery applies to systems
courses without changing callers.

Safety notes
------------
* Executed code is untrusted.  It runs in a throw-away directory with stdin /
  stdout redirected to files, a wall-clock timeout, an output-size cap and a
  scrubbed environment (no ``PYTHONPATH``, no proxies, no ``*_API_KEY`` /
  ``*_TOKEN`` / ``*_SECRET`` / ``*_PASSWORD`` variables).
* Memory caps are best-effort: ``RLIMIT_AS`` is applied on Linux via
  ``resource.prlimit`` (thread-safe); other platforms rely on the time and
  output caps.  This is a *local grading helper*, not a hardened sandbox:
  teachers should still run it on their own machine and may wrap it in
  Docker/nsjail when grading hostile code.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # POSIX only; imported lazily elsewhere
    import resource  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Verdict constants
# ---------------------------------------------------------------------------
ACCEPTED = "Accepted"
WRONG_ANSWER = "Wrong Answer"
RUNTIME_ERROR = "Runtime Error"
TIME_EXCEEDED = "Time Exceeded"
COMPILE_ERROR = "Compile Error"

CASE_AC = "AC"
CASE_WA = "WA"
CASE_TLE = "TLE"
CASE_RE = "RE"
CASE_CE = "CE"
CASE_OLE = "OLE"
CASE_SKIP = "SKIPPED"

DEFAULT_TIME_LIMIT_MS = 2000
DEFAULT_MEMORY_LIMIT_MB = 256
DEFAULT_OUTPUT_LIMIT_BYTES = 2_000_000
DEFAULT_COMPILE_TIMEOUT_MS = 30_000


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class CaseResult:
    name: str
    status: str
    time_ms: int
    memory_kb: int
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "time_ms": self.time_ms,
            "memory_kb": self.memory_kb,
            "message": self.message,
        }


@dataclass
class RunResult:
    status: str
    score: float
    cases: list[CaseResult]
    stdout: str
    stderr: str
    compile_output: str = ""
    elapsed_ms: int = 0
    passed: int = 0
    total: int = 0

    @property
    def accepted(self) -> bool:
        return self.status == ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "cases": [case.to_dict() for case in self.cases],
            "stdout": self.stdout,
            "stderr": self.stderr,
            "compile_output": self.compile_output,
            "elapsed_ms": self.elapsed_ms,
            "passed": self.passed,
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# Output normalisation (Hydro/OJ style)
# ---------------------------------------------------------------------------
def normalize_output(text: Any) -> str:
    """Normalise text the way Hydro's default comparator does.

    Accepted differences: CRLF vs LF, trailing spaces/tabs on each line,
    trailing blank lines, and a missing final newline.  Internal spaces and
    internal empty lines still matter.
    """

    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    value = str(text)
    if value.startswith("\ufeff"):
        value = value[1:]
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in value.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def outputs_match(expected: Any, actual: Any) -> bool:
    return normalize_output(expected) == normalize_output(actual)


# ---------------------------------------------------------------------------
# Minimal YAML parser (no PyYAML dependency)
# ---------------------------------------------------------------------------
def _strip_yaml_comment(line: str) -> str:
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            out.append(char)
            if quote == '"' and char == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if char == quote:
                if quote == "'" and i + 1 < len(line) and line[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "#" and (i == 0 or line[i - 1].isspace()):
            break
        out.append(char)
        i += 1
    return "".join(out)


def _split_key(content: str) -> tuple[str | None, str | None]:
    quote: str | None = None
    i = 0
    while i < len(content):
        char = content[i]
        if quote:
            if quote == '"' and char == "\\":
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "\"'":
            quote = char
            i += 1
            continue
        if char == ":" and (i + 1 >= len(content) or content[i + 1] in " \t"):
            return content[:i].strip(), content[i + 1 :].strip()
        i += 1
    return None, None


def _split_inline(content: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    i = 0
    while i < len(content):
        char = content[i]
        if quote:
            current.append(char)
            if quote == '"' and char == "\\" and i + 1 < len(content):
                current.append(content[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif char in "[{(":
            depth += 1
            current.append(char)
        elif char in "]})":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_scalar(text: str) -> Any:
    value = (text or "").strip()
    if not value:
        return None
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except ValueError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_parse_scalar(part) for part in _split_inline(inner)] if inner else []
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        mapping: dict[str, Any] = {}
        for part in _split_inline(inner) if inner else []:
            key, rest = _split_key(part)
            if key is not None:
                mapping[key] = _parse_scalar(rest) if rest else None
        return mapping
    lowered = value.lower()
    if lowered in ("null", "~"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"[+-]?\d+", value):
        try:
            return int(value)
        except ValueError:
            pass
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", value):
        try:
            return float(value)
        except ValueError:
            pass
    return value


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in (text or "").splitlines():
        line = _strip_yaml_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if content.startswith("- ") and _split_key(content[2:])[0] is not None:
            lines.append((indent, "-"))
            lines.append((indent + 2, content[2:].strip()))
        else:
            lines.append((indent, content))
    return lines


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return None, index
    if lines[index][1] == "-" or lines[index][1].startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while index < len(lines):
        ind, content = lines[index]
        if ind < indent:
            break
        if ind > indent or content == "-" or content.startswith("- "):
            break
        key, rest = _split_key(content)
        if key is None:
            index += 1
            continue
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            index += 1
            block: list[str] = []
            block_indent: int | None = None
            while index < len(lines) and lines[index][0] > indent:
                if block_indent is None:
                    block_indent = lines[index][0]
                prefix = " " * max(0, lines[index][0] - block_indent)
                block.append(prefix + lines[index][1])
                index += 1
            out[key] = "\n".join(block) if rest.startswith("|") else " ".join(
                part.strip() for part in block
            )
            continue
        if rest:
            out[key] = _parse_scalar(rest)
            index += 1
            continue
        index += 1
        if index < len(lines) and lines[index][0] > indent:
            out[key], index = _parse_block(lines, index, lines[index][0])
        else:
            out[key] = None
    return out, index


def _parse_list(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[Any], int]:
    out: list[Any] = []
    while index < len(lines):
        ind, content = lines[index]
        if ind != indent or not (content == "-" or content.startswith("- ")):
            break
        if content == "-":
            index += 1
            if index < len(lines) and lines[index][0] > indent:
                value, index = _parse_block(lines, index, lines[index][0])
            else:
                value = None
            out.append(value)
        else:
            out.append(_parse_scalar(content[2:]))
            index += 1
    return out, index


def parse_simple_yaml(text: str) -> Any:
    """Parse the small YAML subset used by Hydro ``config.yaml`` files.

    Supports nested mappings/lists, inline lists/maps, quoted scalars,
    comments and simple block scalars.  It intentionally does not implement
    anchors, tags or multi-document YAML; PyYAML is not a dependency.
    """

    lines = _yaml_lines(text)
    if not lines:
        return {}
    value, _index = _parse_block(lines, 0, lines[0][0])
    return value


# ---------------------------------------------------------------------------
# Limits parsing helpers
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|s|sec|seconds?)?\s*$", re.I)


def parse_time_ms(value: Any) -> int | None:
    """Parse ``5000ms`` / ``5s`` / ``5000`` into milliseconds."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _TIME_RE.match(str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "ms").lower()
    if unit.startswith("s") and unit != "ms":
        number *= 1000
    return max(1, int(round(number)))


_MEMORY_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(kb|kib|mb|mib|gb|gib|k|m|g|b)?\s*$", re.I)


def parse_memory_mb(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(1, int(value))
    match = _MEMORY_RE.match(str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "mb").lower()
    if unit in ("kb", "kib", "k"):
        number /= 1024
    elif unit in ("gb", "gib", "g"):
        number *= 1024
    elif unit == "b":
        number /= (1024 * 1024)
    return max(1, int(round(number)))


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------
def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _find_problem(snapshot_root: Path, problem_key: str) -> dict[str, Any]:
    path = snapshot_root / "problems" / f"{problem_key}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {"key": problem_key}


def _subtask_cases(
    subtask: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    cases = subtask.get("cases")
    if isinstance(cases, Mapping):
        rows: list[Mapping[str, Any]] = []
        for input_name, output_name in cases.items():
            rows.append({"input": input_name, "output": output_name})
        return rows
    if isinstance(cases, list):
        return [case for case in cases if isinstance(case, Mapping)]
    return []


def load_cases(
    snapshot_root: Path, problem_key: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load judge cases and metadata for one Hydro problem.

    Reads ``problems/<KEY>.json`` for the numeric id / limits, then
    ``testdata/<numeric_id>/config.yaml`` in subtask order.  Falls back to
    sorted ``*.in`` / ``*.out`` pairs when the config is absent or empty.
    ``checker.py`` is attached when present.
    """

    root = Path(snapshot_root)
    problem = _find_problem(root, problem_key)
    numeric_id = problem.get("numeric_id") or 0
    candidates = []
    if numeric_id:
        candidates.append(root / "testdata" / str(numeric_id))
    candidates.append(root / "testdata" / str(problem_key))
    testdata_dir = next((path for path in candidates if path.is_dir()), candidates[0])

    config: dict[str, Any] = {}
    config_path = testdata_dir / "config.yaml"
    if not config_path.exists():
        config_path = testdata_dir / "config.yml"
    if config_path.exists():
        parsed = parse_simple_yaml(_read_text(config_path))
        if isinstance(parsed, Mapping):
            config = dict(parsed)

    time_limit_ms = (
        parse_time_ms(config.get("time"))
        or parse_time_ms(problem.get("time_limit"))
        or DEFAULT_TIME_LIMIT_MS
    )
    memory_limit_mb = (
        parse_memory_mb(config.get("memory"))
        or parse_memory_mb(problem.get("memory_limit"))
        or DEFAULT_MEMORY_LIMIT_MB
    )

    cases: list[dict[str, Any]] = []
    subtasks_meta: list[dict[str, Any]] = []
    subtask_time_default: int | None = None
    subtask_memory_default: int | None = None
    subtasks = config.get("subtasks")
    if not isinstance(subtasks, list):
        subtasks = []
    if not subtasks and isinstance(config.get("cases"), list):
        subtasks = [{"score": config.get("score", 100), "cases": config.get("cases")}]

    for subtask_index, subtask in enumerate(subtasks):
        if not isinstance(subtask, Mapping):
            continue
        raw_cases = _subtask_cases(subtask)
        subtask_score = subtask.get("score", 0)
        subtask_time = parse_time_ms(subtask.get("time"))
        subtask_memory = parse_memory_mb(subtask.get("memory"))
        if subtask_time_default is None and subtask_time is not None:
            subtask_time_default = subtask_time
        if subtask_memory_default is None and subtask_memory is not None:
            subtask_memory_default = subtask_memory
        weight_each = (
            (float(subtask_score) / len(raw_cases)) if subtask_score and raw_cases else 1.0
        )
        rows: list[dict[str, Any]] = []
        for case_index, case in enumerate(raw_cases):
            input_name = case.get("input") or case.get("in")
            output_name = case.get("output") or case.get("out") or case.get("answer")
            if not input_name:
                continue
            input_path = testdata_dir / str(input_name)
            if not input_path.exists():
                continue
            output_path = (
                testdata_dir / str(output_name)
                if output_name
                else input_path.with_suffix(".out")
            )
            rows.append(
                {
                    "name": f"{subtask_index + 1}-{case_index + 1}",
                    "input": _read_text(input_path),
                    "output": _read_text(output_path) if output_path.exists() else "",
                    "input_file": str(input_name),
                    "output_file": str(output_name or output_path.name),
                    "subtask": subtask_index + 1,
                    "subtask_score": subtask_score,
                    "time_limit_ms": subtask_time,
                    "memory_limit_mb": subtask_memory,
                    "weight": weight_each,
                }
            )
        cases.extend(rows)
        subtasks_meta.append(
            {
                "id": subtask.get("id", subtask_index + 1),
                "score": subtask_score,
                "type": subtask.get("type", "sum"),
                "cases": len(rows),
                "time_limit_ms": subtask_time,
                "memory_limit_mb": subtask_memory,
            }
        )

    # Hydro keeps authoritative limits on subtasks; prefer them over the
    # problem page's display limits when the config has no global time/memory.
    if parse_time_ms(config.get("time")) is None and subtask_time_default is not None:
        time_limit_ms = subtask_time_default
    if parse_memory_mb(config.get("memory")) is None and subtask_memory_default is not None:
        memory_limit_mb = subtask_memory_default

    if not cases and testdata_dir.is_dir():
        inputs = sorted(testdata_dir.glob("*.in"), key=lambda path: _natural_key(path.name))
        for index, input_path in enumerate(inputs):
            output_path = input_path.with_suffix(".out")
            if not output_path.exists():
                continue
            cases.append(
                {
                    "name": input_path.stem,
                    "input": _read_text(input_path),
                    "output": _read_text(output_path),
                    "input_file": input_path.name,
                    "output_file": output_path.name,
                    "subtask": 0,
                    "subtask_score": 0,
                    "time_limit_ms": None,
                    "memory_limit_mb": None,
                    "weight": 1.0,
                }
            )

    checker: dict[str, Any] | None = None
    checker_value = config.get("checker")
    checker_path: Path | None = None
    if checker_value:
        candidate = testdata_dir / str(checker_value)
        if candidate.exists():
            checker_path = candidate
    if checker_path is None:
        for name in ("checker.py", "checker"):
            candidate = testdata_dir / name
            if candidate.exists():
                checker_path = candidate
                break
    if checker_path is not None:
        checker = {
            "path": str(checker_path),
            "type": str(config.get("checker_type") or config.get("checkerType") or "custom"),
            "argv_order": ["input", "answer", "output"],
        }

    extra_source = ""
    for name in ("test.py", "main.py"):
        candidate = testdata_dir / name
        if candidate.exists():
            extra_source = _read_text(candidate)
            break

    meta: dict[str, Any] = {
        "problem_key": problem_key,
        "title": problem.get("title", ""),
        "numeric_id": numeric_id,
        "testdata_dir": str(testdata_dir),
        "time_limit_ms": time_limit_ms,
        "memory_limit_mb": memory_limit_mb,
        "checker": checker,
        "subtasks": subtasks_meta,
        "case_count": len(cases),
        "extra_source": extra_source,
        "config": config,
    }
    return cases, meta


# ---------------------------------------------------------------------------
# Generic program execution
# ---------------------------------------------------------------------------
LANGUAGE_SPECS: dict[str, dict[str, Any]] = {
    "python": {"extension": ".py", "compiled": False},
    "c": {"extension": ".c", "compiled": True, "std": "c17"},
    "cpp": {"extension": ".cpp", "compiled": True, "std": "c++17"},
}


def normalize_language(language: Any) -> str:
    """Map UI/OJ language labels to ``python``/``c``/``cpp``."""

    text = str(language or "python").strip().lower()
    if text in {"py", "python", "python3", "python 3", "py.py3", "py3"}:
        return "python"
    if text in {"c", "c11", "c17", "gcc", "ansi c"}:
        return "c"
    if text in {"cpp", "c++", "cxx", "g++", "c++11", "c++14", "c++17", "c++20", "clang++"}:
        return "cpp"
    if "python" in text:
        return "python"
    if "c++" in text or "cpp" in text or "cxx" in text:
        return "cpp"
    if text == "c" or text.startswith("c-"):
        return "c"
    return text or "python"


def detect_language_from_text(language: Any) -> str:
    """Best-effort mapping of Hydro labels such as ``Python 3`` / ``C++``."""

    return normalize_language(language)


def _find_compiler(kind: str) -> str:
    names = ("gcc", "clang", "cc") if kind == "c" else ("g++", "clang++", "c++")
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return names[0]


def _render_command(command: Any, mapping: Mapping[str, str]) -> list[str]:
    if command is None:
        return []
    if isinstance(command, str):
        return [part.format(**mapping) for part in shlex.split(command)]
    return [str(part).format(**mapping) for part in command]


def _python_compile_error(source: str, filename: str = "main.py") -> str:
    try:
        compile(source, filename, "exec")
    except (SyntaxError, ValueError) as exc:
        if isinstance(exc, SyntaxError):
            location = f" (line {exc.lineno})" if exc.lineno else ""
            return f"{type(exc).__name__}: {exc.msg}{location}"
        return f"{type(exc).__name__}: {exc}"
    return ""


_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
_EXPLICIT_SECRETS = {
    "OJ_PASSWORD",
    "HYDRO_PASSWORD",
    "OPENCODE_GO_API_KEY",
    "OPENCODE_GO_2_API_KEY",
    "DEEPSEEK_API_KEY",
}


def sandbox_env(
    workdir: Path, overrides: Mapping[str, Any] | None = None
) -> dict[str, str]:
    """Environment for untrusted program execution.

    Removes Python path injection, network proxy hints and credential-like
    variables so a student program cannot read the teacher's API keys.
    """

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            continue
        if upper in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            continue
        if upper in _EXPLICIT_SECRETS or upper.endswith(_SECRET_SUFFIXES):
            continue
        env[key] = value
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HOME"] = str(workdir)
    env["TMPDIR"] = str(workdir)
    env["TMP"] = str(workdir)
    env["TEMP"] = str(workdir)
    if overrides:
        env.update({str(key): str(value) for key, value in overrides.items()})
    return env


def _maxrss_kb() -> int:
    if resource is None:
        return 0
    try:
        value = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (OSError, ValueError):  # pragma: no cover
        return 0
    if sys.platform == "darwin":
        value //= 1024
    return int(value)


def _apply_memory_limit(pid: int, memory_limit_mb: int) -> None:
    """Best-effort Linux ``RLIMIT_AS`` cap without ``preexec_fn`` races."""

    if resource is None or memory_limit_mb <= 0:
        return
    prlimit = getattr(resource, "prlimit", None)
    if prlimit is None or not sys.platform.startswith("linux"):
        return
    limit = max(16, int(memory_limit_mb)) * 1024 * 1024
    try:
        prlimit(pid, resource.RLIMIT_AS, (limit, limit))  # type: ignore[arg-type]
    except (OSError, ValueError):  # pragma: no cover - process may have exited
        pass


def _kill_process(proc: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _execute_case(
    command: Sequence[str],
    workdir: Path,
    stdin_data: Any,
    *,
    time_limit_ms: int,
    memory_limit_mb: int,
    output_limit_bytes: int,
    env: Mapping[str, str],
) -> dict[str, Any]:
    input_path = workdir / "_ojs_case.in"
    output_path = workdir / "_ojs_case.out"
    stderr_path = workdir / "_ojs_case.err"
    if isinstance(stdin_data, bytes):
        input_path.write_bytes(stdin_data)
    else:
        input_path.write_bytes(str(stdin_data if stdin_data is not None else "").encode("utf-8", "replace"))
    output_path.write_bytes(b"")
    stderr_path.write_bytes(b"")

    timeout_s = max(0.05, float(time_limit_ms) / 1000.0)
    before_rss = _maxrss_kb()
    started = time.perf_counter()
    timed_out = False
    returncode: int | None = None
    with input_path.open("rb") as fin, output_path.open("wb") as fout, stderr_path.open("wb") as ferr:
        try:
            proc = subprocess.Popen(
                list(command),
                cwd=str(workdir),
                stdin=fin,
                stdout=fout,
                stderr=ferr,
                env=dict(env),
                start_new_session=(os.name != "nt"),
            )
        except FileNotFoundError as exc:
            return {
                "returncode": None,
                "stdout": "",
                "stderr": f"executable not found: {exc}",
                "timed_out": False,
                "output_limit_exceeded": False,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "memory_kb": 0,
                "input_path": input_path,
                "output_path": output_path,
                "spawn_error": True,
            }
        _apply_memory_limit(proc.pid, memory_limit_mb)
        try:
            returncode = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
            returncode = proc.returncode
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    after_rss = _maxrss_kb()
    memory_kb = after_rss - before_rss if after_rss > before_rss else 0
    try:
        output_size = output_path.stat().st_size
    except OSError:
        output_size = 0
    output_limit_exceeded = output_size > output_limit_bytes
    read_bytes = output_limit_bytes + 1 if output_limit_exceeded else output_size
    try:
        with output_path.open("rb") as handle:
            stdout_bytes = handle.read(max(0, read_bytes))
    except OSError:
        stdout_bytes = b""
    try:
        with stderr_path.open("rb") as handle:
            stderr_bytes = handle.read(65_536)
    except OSError:
        stderr_bytes = b""
    return {
        "returncode": returncode,
        "stdout": stdout_bytes.decode("utf-8", "replace"),
        "stderr": stderr_bytes.decode("utf-8", "replace"),
        "timed_out": timed_out,
        "output_limit_exceeded": output_limit_exceeded,
        "output_size": output_size,
        "elapsed_ms": elapsed_ms,
        "memory_kb": memory_kb,
        "input_path": input_path,
        "output_path": output_path,
        "spawn_error": False,
    }


def _checker_command(checker: Any) -> tuple[list[str] | None, str]:
    if not checker:
        return None, ""
    if isinstance(checker, Mapping):
        path = checker.get("path") or checker.get("file") or ""
        order = checker.get("argv_order")
    else:
        path, order = str(checker), None
    if not path:
        return None, ""
    return [sys.executable, str(path)], str(path)


def _run_checker(
    checker: Any,
    input_path: Path,
    answer_path: Path,
    output_path: Path,
    *,
    timeout_ms: int,
    env: Mapping[str, str],
) -> tuple[bool, str]:
    base_command, path = _checker_command(checker)
    if not base_command:
        return True, ""
    if not Path(path).exists():
        return False, f"checker not found: {path}"
    order = checker.get("argv_order") if isinstance(checker, Mapping) else None
    mapping = {
        "input": str(input_path),
        "answer": str(answer_path),
        "output": str(output_path),
    }
    if isinstance(order, (list, tuple)) and len(order) == 3:
        files = [mapping.get(str(item), str(item)) for item in order]
    else:
        files = [mapping["input"], mapping["answer"], mapping["output"]]
    command = base_command + files
    try:
        proc = subprocess.run(
            command,
            cwd=str(Path(path).parent),
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout_ms / 1000.0),
            env=dict(env),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"checker failed: {type(exc).__name__}"
    message = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode == 0, message[:1500]


def _case_weight(case: Mapping[str, Any]) -> float:
    try:
        weight = float(case.get("weight", 1.0))
    except (TypeError, ValueError):
        weight = 1.0
    return weight if weight > 0 else 1.0


def _final_status(case_statuses: Iterable[str], *, global_timeout: bool = False) -> str:
    statuses = list(case_statuses)
    if global_timeout or any(status == CASE_TLE for status in statuses):
        return TIME_EXCEEDED
    if any(status == CASE_RE for status in statuses):
        return RUNTIME_ERROR
    if any(status in (CASE_WA, CASE_OLE) for status in statuses):
        return WRONG_ANSWER
    return ACCEPTED


def run_program(
    language: str,
    source: str,
    cases: Sequence[Mapping[str, Any]] | None,
    compile_cmd: Any = None,
    run_cmd: Any = None,
    *,
    time_limit_ms: int = 5000,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
    checker: Any = None,
    global_timeout_ms: int | None = None,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    compile_timeout_ms: int = DEFAULT_COMPILE_TIMEOUT_MS,
    interpreter: str | None = None,
    env_overrides: Mapping[str, Any] | None = None,
    source_name: str | None = None,
) -> RunResult:
    """Compile (if needed) and run *source* against *cases*.

    ``compile_cmd`` / ``run_cmd`` accept an argv list or a shell-less string
    with ``{source}``, ``{binary}``, ``{workdir}`` and ``{interpreter}``
    placeholders.  Omit them to use the built-in defaults for ``python``,
    ``c`` and ``cpp``.

    Verdict mapping: all pass -> ``Accepted``; any timeout -> ``Time
    Exceeded``; any non-zero exit / crash -> ``Runtime Error``; compile
    failure -> ``Compile Error``; otherwise ``Wrong Answer``.  ``score`` is
    weighted by each case's ``weight`` (subtask score divided across its
    cases), giving partial credit the way Hydro subtasks do.
    """

    started = time.perf_counter()
    # Some editors/OJ exports prepend a UTF-8 BOM; Python's tokenizer rejects
    # it as a non-printable character, while Hydro accepts it.
    source = str(source or "").lstrip("\ufeff")
    case_list = list(cases or [])
    language_key = normalize_language(language)
    spec = LANGUAGE_SPECS.get(language_key, {"extension": ".txt", "compiled": False})
    source_file = source_name or f"main{spec.get('extension', '.txt')}"
    interpreter = interpreter or sys.executable

    compile_error = ""
    if language_key == "python":
        compile_error = _python_compile_error(source or "", source_file)

    def _compile_failure(message: str) -> RunResult:
        case_results = [
            CaseResult(
                name=str(case.get("name") or f"case-{index + 1}"),
                status=CASE_CE,
                time_ms=0,
                memory_kb=0,
                message=message[:600],
            )
            for index, case in enumerate(case_list)
        ]
        return RunResult(
            status=COMPILE_ERROR,
            score=0.0,
            cases=case_results,
            stdout="",
            stderr=message[:4000],
            compile_output=message[:4000],
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            passed=0,
            total=len(case_results),
        )

    if compile_error:
        return _compile_failure(compile_error)

    with tempfile.TemporaryDirectory(prefix="ojs-judge-") as tmp:
        workdir = Path(tmp)
        binary_name = "main.exe" if os.name == "nt" else "main"
        mapping = {
            "source": source_file,
            "binary": binary_name,
            "workdir": str(workdir),
            "interpreter": interpreter,
        }
        (workdir / source_file).write_text(source or "", encoding="utf-8", errors="replace")
        env = sandbox_env(workdir, env_overrides)

        if compile_cmd is not None:
            compile_command = _render_command(compile_cmd, mapping)
        elif language_key == "c":
            compiler = _find_compiler("c")
            compile_command = [
                compiler, "-O2", "-std=c17", "-pipe", "-o", binary_name, source_file
            ]
        elif language_key == "cpp":
            compiler = _find_compiler("cpp")
            compile_command = [
                compiler, "-O2", "-std=c++17", "-pipe", "-o", binary_name, source_file
            ]
        else:
            compile_command = []

        if compile_command:
            try:
                proc = subprocess.run(
                    compile_command,
                    cwd=str(workdir),
                    capture_output=True,
                    text=True,
                    timeout=max(1.0, compile_timeout_ms / 1000.0),
                    env=env,
                )
                output = ((proc.stdout or "") + (proc.stderr or "")).strip()
                if proc.returncode != 0:
                    return _compile_failure(output or f"compiler exited with {proc.returncode}")
            except FileNotFoundError as exc:
                return _compile_failure(f"compiler not found: {exc}")
            except subprocess.TimeoutExpired:
                return _compile_failure(
                    f"compilation timed out after {compile_timeout_ms}ms"
                )
            except OSError as exc:
                return _compile_failure(f"compiler failed: {type(exc).__name__}: {exc}")

        if run_cmd is not None:
            run_command = _render_command(run_cmd, mapping)
        elif language_key == "python":
            run_command = [interpreter, source_file]
        else:
            run_command = [f"./{binary_name}" if os.name != "nt" else binary_name]
        if not run_command:
            return _compile_failure("no run command configured")

        if not case_list:
            return RunResult(
                status=ACCEPTED,
                score=0.0,
                cases=[],
                stdout="",
                stderr="",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                passed=0,
                total=0,
            )

        deadline = (
            started + global_timeout_ms / 1000.0 if global_timeout_ms else None
        )
        case_results: list[CaseResult] = []
        last_stdout = ""
        last_stderr = ""
        global_timeout = False
        total_weight = 0.0
        passed_weight = 0.0
        executed = 0
        for index, case in enumerate(case_list):
            name = str(case.get("name") or f"case-{index + 1}")
            weight = _case_weight(case)
            total_weight += weight
            if deadline is not None and time.perf_counter() > deadline:
                global_timeout = True
                break
            case_time_limit = parse_time_ms(case.get("time_limit_ms")) or time_limit_ms
            case_memory = parse_memory_mb(case.get("memory_limit_mb")) or memory_limit_mb
            info = _execute_case(
                run_command,
                workdir,
                case.get("input", ""),
                time_limit_ms=case_time_limit,
                memory_limit_mb=case_memory,
                output_limit_bytes=output_limit_bytes,
                env=env,
            )
            executed += 1
            actual = info["stdout"]
            last_stdout = actual[:8000]
            if info["stderr"]:
                last_stderr = info["stderr"][:8000]
            timed_out = bool(info["timed_out"])
            output_limit_exceeded = bool(info["output_limit_exceeded"])
            returncode = info["returncode"]
            message = ""
            if timed_out:
                status = CASE_TLE
                message = f"time limit exceeded ({case_time_limit} ms)"
            elif output_limit_exceeded:
                status = CASE_OLE
                message = (
                    f"output limit exceeded (> {output_limit_bytes} bytes)"
                )
            elif returncode != 0:
                status = CASE_RE
                reason = (info["stderr"] or "").strip().splitlines()
                message = f"exit code {returncode}" + (
                    f": {reason[-1][:400]}" if reason else ""
                )
            elif checker:
                expected = case.get("output", "")
                # Hydro checkers receive: input, standard answer, student output.
                answer_path = workdir / "_ojs_answer.out"
                if isinstance(expected, bytes):
                    answer_path.write_bytes(expected)
                else:
                    answer_path.write_bytes(
                        str(expected if expected is not None else "").encode(
                            "utf-8", "replace"
                        )
                    )
                ok, checker_message = _run_checker(
                    checker,
                    info["input_path"],
                    answer_path,
                    info["output_path"],
                    timeout_ms=case_time_limit + 2000,
                    env=env,
                )
                if ok:
                    status = CASE_AC
                else:
                    status = CASE_WA
                    message = checker_message or "checker rejected the output"
            elif outputs_match(case.get("output", ""), actual):
                status = CASE_AC
            else:
                status = CASE_WA
                message = _diff_message(case.get("output", ""), actual)

            case_results.append(
                CaseResult(
                    name=name,
                    status=status,
                    time_ms=int(info["elapsed_ms"]),
                    memory_kb=int(info["memory_kb"]),
                    message=message[:1500],
                )
            )
            if status == CASE_AC:
                passed_weight += weight
            if deadline is not None and time.perf_counter() > deadline:
                global_timeout = True
                break

        for index in range(executed, len(case_list)):
            case = case_list[index]
            case_results.append(
                CaseResult(
                    name=str(case.get("name") or f"case-{index + 1}"),
                    status=CASE_SKIP,
                    time_ms=0,
                    memory_kb=0,
                    message="not run: global time limit reached",
                )
            )

        status = _final_status(
            (case.status for case in case_results), global_timeout=global_timeout
        )
        passed = sum(1 for case in case_results if case.status == CASE_AC)
        score = (
            round(100.0 * passed_weight / total_weight, 2) if total_weight > 0 else 0.0
        )
        if status == ACCEPTED:
            score = 100.0
        return RunResult(
            status=status,
            score=score,
            cases=case_results,
            stdout=last_stdout,
            stderr=last_stderr,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            passed=passed,
            total=len(case_results),
        )


def _diff_message(expected: Any, actual: Any) -> str:
    expected_text = normalize_output(expected)
    actual_text = normalize_output(actual)
    expected_lines = expected_text.split("\n") if expected_text else []
    actual_lines = actual_text.split("\n") if actual_text else []
    mismatch = 0
    while (
        mismatch < len(expected_lines)
        and mismatch < len(actual_lines)
        and expected_lines[mismatch] == actual_lines[mismatch]
    ):
        mismatch += 1
    if mismatch < len(expected_lines):
        expected_line = expected_lines[mismatch]
    else:
        expected_line = "<end of expected output>"
    if mismatch < len(actual_lines):
        actual_line = actual_lines[mismatch]
    else:
        actual_line = "<end of output>"
    return (
        f"first mismatch at output line {mismatch + 1}: "
        f"expected {expected_line[:200]!r}, got {actual_line[:200]!r}"
    )


def run_python(
    code: str,
    cases: Sequence[Mapping[str, Any]] | None,
    *,
    time_limit_ms: int = 5000,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
    checker: Any = None,
    global_timeout_ms: int | None = None,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    interpreter: str | None = None,
    extra_source: str = "",
    env: Mapping[str, Any] | None = None,
) -> RunResult:
    """Thin Python wrapper around :func:`run_program` (interface section 5).

    ``extra_source`` is appended after the student's code, which mirrors
    Hydro's ``compile.sh`` for function-definition problems (``test.py`` is
    appended to ``foo.py``).  The file is still named ``main.py`` and run as
    ``python main.py``.
    """

    source = code or ""
    if extra_source:
        source = source.rstrip("\n") + "\n\n" + extra_source.lstrip("\n")
    return run_program(
        "python",
        source,
        cases,
        time_limit_ms=time_limit_ms,
        memory_limit_mb=memory_limit_mb,
        checker=checker,
        global_timeout_ms=global_timeout_ms,
        output_limit_bytes=output_limit_bytes,
        interpreter=interpreter,
        env_overrides=env,
        source_name="main.py",
    )


def run_stdin(
    code: str,
    stdin_data: Any,
    *,
    time_limit_ms: int = 5000,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    interpreter: str | None = None,
    extra_source: str = "",
    language: str = "python",
) -> dict[str, Any]:
    """Run one program once and return its raw output (used for validation)."""

    language_key = normalize_language(language)
    interpreter = interpreter or sys.executable
    source = code or ""
    if extra_source and language_key == "python":
        source = source.rstrip("\n") + "\n\n" + extra_source.lstrip("\n")
    result = {
        "status": "Accepted",
        "stdout": "",
        "stderr": "",
        "compile_output": "",
        "returncode": None,
        "timed_out": False,
        "output_limit_exceeded": False,
        "elapsed_ms": 0,
        "memory_kb": 0,
        "language": language_key,
    }
    if language_key == "python":
        compile_error = _python_compile_error(source, "main.py")
        if compile_error:
            result.update(status=COMPILE_ERROR, compile_output=compile_error, stderr=compile_error)
            return result

    with tempfile.TemporaryDirectory(prefix="ojs-run-") as tmp:
        workdir = Path(tmp)
        spec = LANGUAGE_SPECS.get(language_key, {"extension": ".txt", "compiled": False})
        source_file = f"main{spec.get('extension', '.txt')}"
        (workdir / source_file).write_text(source, encoding="utf-8", errors="replace")
        binary_name = "main.exe" if os.name == "nt" else "main"
        mapping = {
            "source": source_file,
            "binary": binary_name,
            "workdir": str(workdir),
            "interpreter": interpreter,
        }
        env = sandbox_env(workdir)
        compile_command: list[str] = []
        if language_key == "c":
            compile_command = [
                _find_compiler("c"), "-O2", "-std=c17", "-pipe", "-o", binary_name, source_file
            ]
        elif language_key == "cpp":
            compile_command = [
                _find_compiler("cpp"), "-O2", "-std=c++17", "-pipe", "-o", binary_name, source_file
            ]
        if compile_command:
            try:
                proc = subprocess.run(
                    compile_command,
                    cwd=str(workdir),
                    capture_output=True,
                    text=True,
                    timeout=max(1.0, DEFAULT_COMPILE_TIMEOUT_MS / 1000.0),
                    env=env,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                message = f"compiler failed: {type(exc).__name__}"
                result.update(status=COMPILE_ERROR, compile_output=message)
                return result
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            if proc.returncode != 0:
                result.update(
                    status=COMPILE_ERROR,
                    compile_output=output or "compile failed",
                    stderr=output,
                )
                return result
        run_command = (
            [interpreter, source_file]
            if language_key == "python"
            else [f"./{binary_name}" if os.name != "nt" else binary_name]
        )
        info = _execute_case(
            run_command,
            workdir,
            stdin_data,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            output_limit_bytes=output_limit_bytes,
            env=env,
        )
    status = "Accepted"
    if info["timed_out"]:
        status = CASE_TLE
    elif info["output_limit_exceeded"]:
        status = CASE_OLE
    elif info["returncode"] != 0:
        status = CASE_RE
    result.update(
        status=status,
        stdout=info["stdout"][:output_limit_bytes],
        stderr=info["stderr"],
        returncode=info["returncode"],
        timed_out=info["timed_out"],
        output_limit_exceeded=info["output_limit_exceeded"],
        elapsed_ms=info["elapsed_ms"],
        memory_kb=info["memory_kb"],
    )
    return result


__all__ = [
    "CaseResult",
    "RunResult",
    "load_cases",
    "run_program",
    "run_python",
    "run_stdin",
    "normalize_output",
    "outputs_match",
    "parse_simple_yaml",
    "parse_time_ms",
    "parse_memory_mb",
    "normalize_language",
    "detect_language_from_text",
    "sandbox_env",
    "ACCEPTED",
    "WRONG_ANSWER",
    "RUNTIME_ERROR",
    "TIME_EXCEEDED",
    "COMPILE_ERROR",
    "CASE_AC",
    "CASE_WA",
    "CASE_TLE",
    "CASE_RE",
    "CASE_CE",
    "CASE_OLE",
    "CASE_SKIP",
    "LANGUAGE_SPECS",
]
