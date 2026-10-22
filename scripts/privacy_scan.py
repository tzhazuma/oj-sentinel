#!/usr/bin/env python3
"""Fail a push if likely secrets or private student data are in the repo.

Run from the repository root:  python scripts/privacy_scan.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"\n]{6,}['\"]"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"\b(?:gho|ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]

# Private/internal addresses and identifiers from our own run must not ship.
PRIVATE_PATTERNS = [
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
]

FORBIDDEN_DIRS = ("data/", "reports/", "output/", ".venv/", "dist/", "node_modules/")
FORBIDDEN_SUFFIXES = (".apk", ".db", ".sqlite", ".pem", ".key", ".cookie")
ALLOWLIST = {
    # documentation placeholders / tests
    "docs/PRIVACY.md", "scripts/privacy_scan.py", "README.md",
}


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line.strip()]


def main() -> int:
    bad = 0
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if any(rel.startswith(d) for d in FORBIDDEN_DIRS):
            print(f"[forbidden path] {rel}")
            bad += 1
            continue
        if path.suffix in FORBIDDEN_SUFFIXES:
            print(f"[forbidden suffix] {rel}")
            bad += 1
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                if rel in ALLOWLIST:
                    continue
                lineno_text = text.splitlines()[line - 1] if line - 1 < len(text.splitlines()) else ""
                if any(marker in lineno_text for marker in (
                    "add_argument", "def ", "password=True", "os.environ",
                    "password: str", "password: Password", "read_password",
                )):
                    continue
                print(f"[secret?] {rel}:{line}: {m.group(0)[:80]}")
                bad += 1
        for pat in PRIVATE_PATTERNS:
            for m in pat.finditer(text):
                if rel in ALLOWLIST or rel.startswith("tests/"):
                    continue
                if m.group(0) == "10.0.2.2":  # standard Android emulator host alias
                    continue
                line = text.count("\n", 0, m.start()) + 1
                print(f"[private address?] {rel}:{line}: {m.group(0)}")
                bad += 1
    if bad:
        print(f"\n{bad} issue(s) found — refusing to ship.", file=sys.stderr)
        return 1
    print(f"privacy scan OK ({len(tracked_files())} tracked files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
