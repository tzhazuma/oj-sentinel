# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OJ Sentinel (onedir; .app on macOS).

Usage (from the repository root, desktop extra installed)::

    .venv/bin/python -m PyInstaller --noconfirm --clean packaging/oj_sentinel.spec

Environment variables:

OJS_ONEFILE=1
    Build a single-file executable instead of an onedir bundle.  Supported on
    Windows/Linux only (macOS always gets ``OJ Sentinel.app``; onefile cannot be
    wrapped in a BUNDLE).  Used by ``packaging/build_windows.ps1``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPECPATH = Path(SPECPATH).resolve()  # noqa: F821 - injected by PyInstaller
ROOT = SPECPATH.parent
SRC = ROOT / "src"

APP_NAME = "OJ Sentinel"
APP_ID = "com.ojsentinel.desktop"
ONEFILE = os.environ.get("OJS_ONEFILE", "").strip().lower() in {"1", "true", "yes"}
IS_MAC = sys.platform == "darwin"

# Make the source tree importable so PyInstaller's collection helpers see the
# package the same way ``PYTHONPATH=src`` does.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from oj_sentinel.desktop import package_version

    VERSION = package_version()
except Exception:  # pragma: no cover - collection must never fail the build
    VERSION = "0.1.0"


def _package_data() -> list[tuple[str, str]]:
    """Static assets, templates and any other non-Python package files."""
    datas: list[tuple[str, str]] = []
    try:
        datas += collect_data_files("oj_sentinel", include_py_files=False)
    except Exception as exc:  # pragma: no cover - tolerated, explicit dirs below
        print(f"[oj-sentinel spec] collect_data_files(oj_sentinel) failed: {exc}")
    # collect_data_files maps every file in a directory to the same destination
    # directory.  Do NOT de-duplicate by destination here: that would keep only
    # one sibling (e.g. app.js) and silently drop index.html/styles.css.
    # Add the documented directories explicitly only when collection missed them.
    covered = {dest.replace("\\", "/") for _, dest in datas}
    for rel in ("server/static", "server/templates", "report/templates"):
        source = SRC / "oj_sentinel" / rel
        dest = f"oj_sentinel/{rel}"
        if source.is_dir() and dest not in covered:
            datas.append((str(source), dest))
    return datas


def _hidden_imports() -> list[str]:
    """Uvicorn loads loggers/loops/protocols dynamically; pywebview likewise."""
    hidden = [
        "oj_sentinel.desktop",
        "oj_sentinel.server.app",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "email.mime.multipart",
        "email.mime.text",
        "jinja2.ext",
    ]
    for package, extra in (
        ("oj_sentinel", None),
        ("uvicorn", None),
        ("webview", None),
        ("fastapi", None),
        ("starlette", None),
    ):
        try:
            hidden += collect_submodules(package, on_error="ignore")
        except Exception as exc:  # pragma: no cover - optional package
            print(f"[oj-sentinel spec] collect_submodules({package}) failed: {exc}")
        if extra:
            hidden += extra
    if IS_MAC:
        hidden += ["webview.platforms.cocoa", "objc", "AppKit", "Foundation", "WebKit", "Quartz"]
    elif os.name == "nt":
        hidden += [
            "webview.platforms.winforms",
            "webview.platforms.edgechromium",
            "clr",
            "pythonnet",
        ]
    else:
        # GTK is the preferred backend on Linux; Qt is optional and not bundled.
        hidden += ["webview.platforms.gtk"]
    return sorted(set(hidden))


datas = _package_data()
hiddenimports = _hidden_imports()

excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "IPython",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "pytest",
]

a = Analysis(
    [str(SPECPATH / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if ONEFILE:
    if IS_MAC:
        raise SystemExit(
            "OJS_ONEFILE=1 is not supported on macOS: build the default onedir "
            "spec to get 'OJ Sentinel.app'."
        )
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="OJ-Sentinel",
    )
    if IS_MAC:
        app = BUNDLE(
            coll,
            name=f"{APP_NAME}.app",
            icon=None,
            bundle_identifier=APP_ID,
            version=VERSION,
            info_plist={
                "CFBundleDisplayName": APP_NAME,
                "CFBundleName": APP_NAME,
                "CFBundleShortVersionString": VERSION,
                "CFBundleVersion": VERSION,
                "LSApplicationCategoryType": "public.app-category.education",
                "LSMinimumSystemVersion": "11.0",
                "NSHighResolutionCapable": True,
                "NSRequiresAquaSystemAppearance": False,
                "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
                "NSHumanReadableCopyright": "MIT License. Local-only student data.",
            },
        )
