"""Local configuration. Credentials are intentionally NOT persisted here."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("OJS_CONFIG_DIR")
    if base:
        return Path(base)
    return Path.home() / ".config" / "oj-sentinel"


def default_data_dir() -> Path:
    env = os.environ.get("OJS_DATA_DIR")
    if env:
        return Path(env)
    return Path.cwd() / "data"


@dataclass
class Settings:
    base_url: str = ""
    domain: str = ""
    username: str = ""
    data_dir: Path = default_data_dir()

    def save(self) -> Path:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "config.json"
        path.write_text(
            json.dumps(
                {
                    "base_url": self.base_url,
                    "domain": self.domain,
                    "username": self.username,
                    "data_dir": str(self.data_dir),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "config.json"
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        return cls(
            base_url=data.get("base_url", ""),
            domain=data.get("domain", ""),
            username=data.get("username", ""),
            data_dir=Path(data.get("data_dir", default_data_dir())),
        )


def read_password(argv_password: str | None, stdin: bool = False) -> str:
    if argv_password:
        return argv_password
    if stdin:
        import sys

        return sys.stdin.readline().rstrip("\n")
    pw = os.environ.get("OJ_PASSWORD") or os.environ.get("HYDRO_PASSWORD")
    if pw:
        return pw
    raise SystemExit(
        "No password supplied. Use --password-stdin, OJ_PASSWORD env var, or the Web GUI."
    )
