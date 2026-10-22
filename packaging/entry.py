"""PyInstaller entry point for the OJ Sentinel desktop application.

PyInstaller runs this file as ``__main__``; it must stay importable without the
rest of the package on ``sys.path`` because the spec adds ``src/`` explicitly.
"""
from __future__ import annotations

import multiprocessing
import sys


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()  # harmless on POSIX, required with Windows spawn
    from oj_sentinel.desktop import main as desktop_main

    return desktop_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
