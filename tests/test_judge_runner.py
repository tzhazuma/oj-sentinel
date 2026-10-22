"""Offline judge-runner tests: YAML, cases, verdicts, checker, C/C++.

Synthetic testdata only; no snapshot, OJ or network access.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from oj_sentinel.judge.runner import (  # noqa: E402
    ACCEPTED,
    COMPILE_ERROR,
    RUNTIME_ERROR,
    TIME_EXCEEDED,
    WRONG_ANSWER,
    load_cases,
    normalize_language,
    normalize_output,
    parse_memory_mb,
    parse_simple_yaml,
    parse_time_ms,
    run_program,
    run_python,
    run_stdin,
)

SUM_CASES = [
    {"name": "c1", "input": "1 2\n", "output": "3\n"},
    {"name": "c2", "input": "5 7\n", "output": "12\n"},
]

SUM_CODE = "a, b = map(int, input().split())\nprint(a + b)\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_normalize_output_oj_style():
    actual = "a  \r\nb\t\r\n\r\n\r\n"
    expected = "a\nb"
    assert normalize_output(actual) == expected
    assert normalize_output("a\n\nb\n") == "a\n\nb"  # internal blank line kept
    assert normalize_output("") == ""
    assert normalize_output("\ufeffx\r\n") == "x"


def test_parse_time_and_memory():
    assert parse_time_ms("5000ms") == 5000
    assert parse_time_ms("1.5s") == 1500
    assert parse_time_ms(2000) == 2000
    assert parse_time_ms("nope") is None
    assert parse_memory_mb("64MB") == 64
    assert parse_memory_mb("256m") == 256
    assert parse_memory_mb("1GB") == 1024
    assert parse_memory_mb("512") == 512
    assert parse_memory_mb("nope") is None


def test_normalize_language_labels():
    assert normalize_language("Python 3") == "python"
    assert normalize_language("py.py3") == "python"
    assert normalize_language("C") == "c"
    assert normalize_language("C++") == "cpp"
    assert normalize_language("g++") == "cpp"
    assert normalize_language(None) == "python"


def test_parse_simple_yaml_real_hydro_config():
    text = """\
    # Hydro testdata config
    type: default
    checker_type: custom
    checker: checker.py
    subtasks:
      - time: 1500ms
        memory: 64MB
        score: 2
        if: []
        type: sum
        cases:
          - input: a1.in   # first
            output: a1.out
          - input: "a2.in"
            output: 'a2.out'
      - score: 3
        cases:
          - input: a3.in
            output: a3.out
    langs:
      - py.py3
      - py
    """
    config = parse_simple_yaml(textwrap.dedent(text))
    assert config["type"] == "default"
    assert config["checker_type"] == "custom"
    assert config["checker"] == "checker.py"
    assert config["langs"] == ["py.py3", "py"]
    assert config["subtasks"][0]["time"] == "1500ms"
    assert config["subtasks"][0]["memory"] == "64MB"
    assert config["subtasks"][0]["score"] == 2
    assert config["subtasks"][0]["if"] == []
    assert config["subtasks"][0]["cases"][0] == {"input": "a1.in", "output": "a1.out"}
    assert config["subtasks"][1]["cases"][0]["input"] == "a3.in"
    assert len(config["subtasks"]) == 2


def test_parse_simple_yaml_block_scalar_and_inline_map():
    config = parse_simple_yaml(
        "checks: {mode: strict, count: 2}\n"
        "notes: |\n"
        "  line one\n"
        "  line two\n"
    )
    assert config["checks"] == {"mode": "strict", "count": 2}
    assert config["notes"] == "line one\nline two"


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------
def _make_snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    _write(root / "problems" / "HW1.json",
           json.dumps({"key": "HW1", "title": "Add", "numeric_id": 7,
                       "time_limit": "2s", "memory_limit": "128MB"}))
    testdata = root / "testdata" / "7"
    _write(testdata / "config.yaml", textwrap.dedent("""\
        type: default
        checker_type: custom
        checker: checker.py
        subtasks:
          - time: 1500ms
            memory: 64MB
            score: 2
            cases:
              - input: a1.in
                output: a1.out
              - input: a2.in
                output: a2.out
          - score: 3
            cases:
              - input: a3.in
                output: a3.out
        langs:
          - py.py3
        """))
    _write(testdata / "a1.in", "1 2\n")
    _write(testdata / "a1.out", "3\n")
    _write(testdata / "a2.in", "2 3\n")
    _write(testdata / "a2.out", "5\n")
    _write(testdata / "a3.in", "10 20\n")
    _write(testdata / "a3.out", "30\n")
    _write(testdata / "checker.py", "print('checker')\n")
    return root


def test_load_cases_reads_subtasks_in_order(tmp_path):
    root = _make_snapshot(tmp_path)
    cases, meta = load_cases(root, "HW1")
    assert [case["name"] for case in cases] == ["1-1", "1-2", "2-1"]
    assert cases[0]["input"] == "1 2\n"
    assert cases[0]["output"] == "3\n"
    assert [case["weight"] for case in cases] == [1.0, 1.0, 3.0]
    assert [case["subtask"] for case in cases] == [1, 1, 2]
    assert meta["numeric_id"] == 7
    assert meta["time_limit_ms"] == 1500  # subtask/global config beats problem fallback
    assert meta["memory_limit_mb"] == 64
    assert meta["case_count"] == 3
    assert meta["checker"]["path"].endswith("checker.py")
    assert meta["checker"]["type"] == "custom"
    assert [subtask["cases"] for subtask in meta["subtasks"]] == [2, 1]


def test_load_cases_falls_back_to_sorted_pairs(tmp_path):
    root = tmp_path / "snapshot"
    _write(root / "problems" / "HW9.json",
           json.dumps({"key": "HW9", "numeric_id": 9}))
    testdata = root / "testdata" / "9"
    for name in ("a1", "a2", "a10"):
        _write(testdata / f"{name}.in", f"{name}\n")
        _write(testdata / f"{name}.out", f"{name}-out\n")
    cases, meta = load_cases(root, "HW9")
    assert [case["name"] for case in cases] == ["a1", "a2", "a10"]
    assert cases[0]["output"] == "a1-out\n"
    assert meta["checker"] is None
    assert meta["case_count"] == 3


def test_load_cases_missing_testdata_is_empty(tmp_path):
    root = tmp_path / "snapshot"
    _write(root / "problems" / "HW1.json", json.dumps({"key": "HW1", "numeric_id": 99}))
    cases, meta = load_cases(root, "HW1")
    assert cases == []
    assert meta["case_count"] == 0
    assert meta["time_limit_ms"] == 2000
    assert meta["memory_limit_mb"] == 256


# ---------------------------------------------------------------------------
# run_python verdicts
# ---------------------------------------------------------------------------
def test_run_python_accepted_and_wrong_answer():
    accepted = run_python(SUM_CODE, SUM_CASES)
    assert accepted.status == ACCEPTED
    assert accepted.score == 100.0
    assert accepted.passed == accepted.total == 2
    assert all(case.status == "AC" for case in accepted.cases)

    wrong = run_python("print(0)\n", SUM_CASES)
    assert wrong.status == WRONG_ANSWER
    assert wrong.score == 0.0
    assert wrong.passed == 0
    assert "first mismatch" in wrong.cases[0].message


def test_run_python_partial_score_uses_weights():
    result = run_python(
        SUM_CODE,
        [
            {"name": "ok", "input": "1 2\n", "output": "3\n", "weight": 1.0},
            {"name": "bad", "input": "2 2\n", "output": "5\n", "weight": 3.0},
        ],
    )
    assert result.status == WRONG_ANSWER
    assert result.passed == 1
    assert result.score == 25.0


def test_run_python_time_limit_exceeded():
    result = run_python("while True:\n    pass\n", SUM_CASES, time_limit_ms=150)
    assert result.status == TIME_EXCEEDED
    assert all(case.status == "TLE" for case in result.cases)


def test_run_python_runtime_error():
    result = run_python("raise ValueError('boom')\n", SUM_CASES)
    assert result.status == RUNTIME_ERROR
    assert "ValueError" in result.cases[0].message


def test_run_python_compile_error():
    result = run_python("def broken(:\n    pass\n", SUM_CASES)
    assert result.status == COMPILE_ERROR
    assert result.compile_output
    assert result.passed == 0
    assert all(case.status == "CE" for case in result.cases)


def test_run_python_output_limit():
    result = run_python("print('x' * 5000)\n", SUM_CASES, output_limit_bytes=1000)
    assert result.status == WRONG_ANSWER
    assert result.cases[0].status == "OLE"
    assert "output limit" in result.cases[0].message


def test_run_python_global_timeout_skips_remaining():
    result = run_python(SUM_CODE, SUM_CASES, global_timeout_ms=1)
    assert result.status == TIME_EXCEEDED
    assert any(case.status == "SKIPPED" for case in result.cases)


def test_run_python_env_isolation(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/tmp/evil")
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    code = (
        "import os\n"
        "a, b = map(int, input().split())\n"
        "print('PYTHONPATH' in os.environ, 'SECRET_TOKEN' in os.environ, a + b)\n"
    )
    result = run_python(
        code,
        [{"name": "env", "input": "1 2\n", "output": "False False 3\n"}],
    )
    assert result.status == ACCEPTED, result.cases[0].message


def test_run_python_extra_source_wrapper():
    code = "def add(a, b):\n    return a + b\n"
    extra = "print(add(2, 3))\n"
    result = run_python(code, [{"name": "w", "input": "", "output": "5\n"}],
                        extra_source=extra)
    assert result.status == ACCEPTED


# ---------------------------------------------------------------------------
# Hydro checker
# ---------------------------------------------------------------------------
def test_run_python_checker_argv_order_and_override(tmp_path):
    checker = tmp_path / "checker.py"
    checker.write_text(textwrap.dedent("""\
        import json
        import pathlib
        import sys

        here = pathlib.Path(__file__).resolve().parent
        contents = [pathlib.Path(arg).read_text() for arg in sys.argv[1:]]
        (here / "checker_argv.json").write_text(json.dumps(contents))
        sys.exit(0)  # accept regardless of the stored expected output
        """), encoding="utf-8")
    cases = [{"name": "c", "input": "1 2\n", "output": "999\n"}]
    result = run_python(
        "a, b = map(int, input().split())\nprint(a + b)\n",
        cases,
        checker={"path": str(checker), "argv_order": ["input", "answer", "output"]},
    )
    assert result.status == ACCEPTED
    contents = json.loads((tmp_path / "checker_argv.json").read_text(encoding="utf-8"))
    # Hydro checker docstring: python checker.py input answer output
    assert [part.strip() for part in contents] == ["1 2", "999", "3"]


def test_run_python_checker_rejection_becomes_wrong_answer(tmp_path):
    checker = tmp_path / "checker.py"
    checker.write_text(
        "import sys\nprint('checker says no')\nsys.exit(1)\n", encoding="utf-8"
    )
    result = run_python(
        "print(3)\n",
        [{"name": "c", "input": "1 2\n", "output": "3\n"}],
        checker={"path": str(checker)},
    )
    assert result.status == WRONG_ANSWER
    assert "checker says no" in result.cases[0].message


# ---------------------------------------------------------------------------
# run_stdin
# ---------------------------------------------------------------------------
def test_run_stdin_returns_raw_output():
    info = run_stdin("print('hello')\n", "")
    assert info["status"] == ACCEPTED
    assert info["stdout"] == "hello\n"
    assert info["returncode"] == 0

    failed = run_stdin("import sys\nsys.exit(3)\n", "")
    assert failed["status"] == "RE"
    assert failed["returncode"] == 3

    broken = run_stdin("def nope(:\n", "")
    assert broken["status"] == COMPILE_ERROR


# ---------------------------------------------------------------------------
# C / C++ support
# ---------------------------------------------------------------------------
C_COMPILER = shutil.which("gcc") or shutil.which("clang") or shutil.which("cc")
CXX_COMPILER = shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")


@pytest.mark.skipif(C_COMPILER is None, reason="no C compiler installed")
def test_run_program_c_accepted_and_compile_error():
    source = textwrap.dedent("""\
        #include <stdio.h>
        int main(void) {
            int a, b;
            if (scanf("%d %d", &a, &b) != 2) return 1;
            printf("%d\\n", a + b);
            return 0;
        }
        """)
    result = run_program("c", source, SUM_CASES)
    assert result.status == ACCEPTED, result.compile_output or result.cases[0].message
    assert result.passed == 2

    broken = run_program("c", "int main(void) { this is not C }\n", SUM_CASES)
    assert broken.status == COMPILE_ERROR
    assert broken.compile_output


@pytest.mark.skipif(CXX_COMPILER is None, reason="no C++ compiler installed")
def test_run_program_cpp_accepted_and_runtime_error():
    source = textwrap.dedent("""\
        #include <iostream>
        int main() {
            long long a, b;
            std::cin >> a >> b;
            std::cout << (a + b) << std::endl;
            return 0;
        }
        """)
    result = run_program("cpp", source, SUM_CASES)
    assert result.status == ACCEPTED, result.compile_output or result.cases[0].message

    crashing = textwrap.dedent("""\
        #include <cstdlib>
        int main() { std::abort(); }
        """)
    crashed = run_program("cpp", crashing, SUM_CASES[:1])
    assert crashed.status == RUNTIME_ERROR


def test_run_program_custom_commands(tmp_path):
    script = tmp_path / "custom_runner.py"
    script.write_text("import sys\nprint('custom')\n", encoding="utf-8")
    result = run_program(
        "custom",
        "ignored",
        [{"name": "c", "input": "", "output": "custom\n"}],
        compile_cmd=[],
        run_cmd=[sys.executable, str(script)],
    )
    assert result.status == ACCEPTED


def test_run_program_run_cmd_placeholders(tmp_path):
    script = tmp_path / "echo_stdin.py"
    script.write_text("import sys\nprint(sys.stdin.read().strip().upper())\n", encoding="utf-8")
    result = run_program(
        "custom",
        "ignored",
        [{"name": "c", "input": "hello\n", "output": "HELLO\n"}],
        compile_cmd=[],
        run_cmd=[sys.executable, str(script), "{source}"],
        source_name="input.txt",
    )
    assert result.status == ACCEPTED


def test_run_program_missing_compiler_is_compile_error():
    result = run_program(
        "c",
        "int main(void) { return 0; }",
        SUM_CASES,
        compile_cmd=["definitely-not-a-compiler-xyz", "{source}"],
    )
    assert result.status == COMPILE_ERROR
    assert "compiler not found" in result.compile_output
