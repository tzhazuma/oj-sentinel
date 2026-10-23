"""Native save-dialog bridge used by the desktop export button."""
from __future__ import annotations

import base64
import sys
import types
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from oj_sentinel.desktop import DesktopApi


class FakeWindow:
    def __init__(self, target: Path):
        self.target = target
        self.calls = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.calls.append((dialog_type, kwargs))
        return [str(self.target)]


def test_desktop_save_bytes(tmp_path, monkeypatch):
    target = tmp_path / "saved.zip"
    window = FakeWindow(target)
    fake_webview = types.SimpleNamespace(windows=[window], SAVE_DIALOG=30)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    api = DesktopApi()
    result = api.save_bytes("report.zip", base64.b64encode(b"PK-test").decode())
    assert result == str(target)
    assert target.read_bytes() == b"PK-test"
    assert window.calls and window.calls[0][1]["save_filename"] == "report.zip"


def test_desktop_save_cancel(tmp_path, monkeypatch):
    class CancelWindow:
        def create_file_dialog(self, *args, **kwargs):
            return None

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(windows=[CancelWindow()], SAVE_DIALOG=30))
    assert DesktopApi().save_bytes("x.zip", base64.b64encode(b"x").decode()) == ""


def test_desktop_choose_directory(tmp_path, monkeypatch):
    class FolderWindow:
        def create_file_dialog(self, dialog_type, **kwargs):
            assert kwargs.get("allow_multiple") is False
            return [str(tmp_path)]

    monkeypatch.setitem(
        sys.modules,
        "webview",
        types.SimpleNamespace(windows=[FolderWindow()], FOLDER_DIALOG=20),
    )
    assert DesktopApi().choose_directory() == str(tmp_path)
