import os
import stat
import sys
from pathlib import Path

from oj_sentinel.ai.dsh_discovery import discover_dsh, probe_dsh, resolve_dsh


def _fake_dsh(tmp_path: Path, body: str = "echo 'dsh 9.9.9-test'") -> Path:
    path = tmp_path / "dsh"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_probe_and_custom_discovery(tmp_path):
    exe = _fake_dsh(tmp_path)
    cand = probe_dsh(str(exe))
    assert cand.ok and cand.version == "9.9.9-test"
    info = discover_dsh(str(exe))
    assert info.found and info.selected == str(exe)
    assert info.mode == "custom"
    assert info.source == "custom"


def test_invalid_custom_path_reports_error(tmp_path):
    missing = tmp_path / "does-not-exist"
    info = discover_dsh(str(missing))
    # auto discovery may still find a real dsh on this machine; if so, the
    # custom path must not be silently selected.
    assert info.selected != str(missing)
    if not info.found:
        assert "自定义路径不可用" in info.error


def test_resolve_dsh_accepts_fake(tmp_path):
    exe = _fake_dsh(tmp_path, "echo 'dsh 1.2.3'")
    assert resolve_dsh(str(exe)) == str(exe)


def test_probe_rejects_non_executable(tmp_path):
    path = tmp_path / "dsh"
    path.write_text("not executable", encoding="utf-8")
    cand = probe_dsh(str(path))
    assert not cand.ok
    assert "不可执行" in cand.error


def test_auto_discovery_finds_dsh_or_reports_candidates():
    info = discover_dsh()
    if info.found:
        assert info.selected
        assert info.version
    else:
        assert info.error
        assert info.searched
