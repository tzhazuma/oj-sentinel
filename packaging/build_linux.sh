#!/usr/bin/env bash
# Build Linux artifacts from the PyInstaller onedir bundle:
#   dist/OJ-Sentinel-<version>-linux-<arch>.AppImage   (needs appimagetool)
#   dist/oj-sentinel_<version>_<arch>.deb              (needs dpkg-deb)
#
# Prerequisites:
#   sudo apt install libwebkit2gtk-4.1-0 gir1.2-webkit2-4.1 python3-gi \
#                    libgtk-3-0 xvfb desktop-file-utils
#   python3.12 -m venv .venv && .venv/bin/pip install -e ".[desktop]"
#
# The window mode needs PyGObject + WebKitGTK at runtime.  The headless server
# (--no-window) has no such requirement and is what CI smoke-tests.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
  :
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi
DIST="$ROOT/dist"
BUNDLE="$DIST/OJ-Sentinel"
APP_NAME="OJ Sentinel"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) APPIMAGE_ARCH="x86_64"; DEB_ARCH="amd64" ;;
  aarch64|arm64) APPIMAGE_ARCH="aarch64"; DEB_ARCH="arm64" ;;
  *) APPIMAGE_ARCH="$ARCH"; DEB_ARCH="$ARCH" ;;
esac

cd "$ROOT"

if [[ ! -x "$PYTHON" ]]; then
  echo "error: python not found at $PYTHON (set PYTHON=... or create .venv)" >&2
  exit 1
fi
if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "error: PyInstaller missing; run: $PYTHON -m pip install -e '.[desktop]'" >&2
  exit 1
fi

VERSION="$(PYTHONPATH="$ROOT/src" "$PYTHON" -c 'from oj_sentinel.desktop import package_version; print(package_version())')"
echo "==> Building $APP_NAME $VERSION for linux/$ARCH"
"$PYTHON" -m PyInstaller --noconfirm --clean "$ROOT/packaging/oj_sentinel.spec"
[[ -d "$BUNDLE" ]] || { echo "error: expected $BUNDLE" >&2; exit 1; }

# ---------------------------------------------------------------- AppImage
APPDIR="$DIST/OJ-Sentinel.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -R "$BUNDLE/." "$APPDIR/usr/bin/"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/OJ Sentinel" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/oj-sentinel.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=OJ Sentinel
Comment=Hydro OJ plagiarism, anomaly analysis and AI judging (local only)
Exec=OJ Sentinel
Icon=oj-sentinel
Terminal=false
Categories=Education;
DESKTOP
cp "$APPDIR/oj-sentinel.desktop" "$APPDIR/usr/share/applications/oj-sentinel.desktop"

if [[ -f "$ROOT/packaging/icon.png" ]]; then
  cp "$ROOT/packaging/icon.png" "$APPDIR/oj-sentinel.png"
  cp "$ROOT/packaging/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/oj-sentinel.png"
fi

APPIMAGE="$DIST/OJ-Sentinel-${VERSION}-linux-${APPIMAGE_ARCH}.AppImage"
APPIMAGETOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
TOOL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ojs-appimage.XXXXXX")"
trap 'rm -rf "$TOOL_DIR"' EXIT
if [[ -z "$APPIMAGETOOL" ]]; then
  TOOL="$TOOL_DIR/appimagetool-${APPIMAGE_ARCH}.AppImage"
  if [[ ! -x "$TOOL" ]]; then
    echo "==> Downloading appimagetool"
    curl -fsSL -o "$TOOL" \
      "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${APPIMAGE_ARCH}.AppImage" || true
    chmod +x "$TOOL" 2>/dev/null || true
  fi
  [[ -x "$TOOL" ]] && APPIMAGETOOL="$TOOL"
fi
if [[ -n "$APPIMAGETOOL" && -x "$APPIMAGETOOL" ]]; then
  rm -f "$APPIMAGE"
  ARCH="$APPIMAGE_ARCH" "$APPIMAGETOOL" --no-appstream "$APPDIR" "$APPIMAGE" || \
    echo "warning: appimagetool failed; on CI export APPIMAGE_EXTRACT_AND_RUN=1 and install FUSE" >&2
  [[ -f "$APPIMAGE" ]] && echo "==> $APPIMAGE ($(du -sh "$APPIMAGE" | cut -f1))"
else
  echo "warning: appimagetool unavailable; skipped AppImage" >&2
fi

# ---------------------------------------------------------------- .deb
if command -v dpkg-deb >/dev/null 2>&1; then
  DEBROOT="$DIST/debroot"
  rm -rf "$DEBROOT"
  mkdir -p "$DEBROOT/DEBIAN" "$DEBROOT/opt/oj-sentinel" "$DEBROOT/usr/bin" \
           "$DEBROOT/usr/share/applications"
  cp -R "$BUNDLE/." "$DEBROOT/opt/oj-sentinel/"
  ln -sf "/opt/oj-sentinel/OJ Sentinel" "$DEBROOT/usr/bin/oj-sentinel"
  cp "$APPDIR/oj-sentinel.desktop" "$DEBROOT/usr/share/applications/oj-sentinel.desktop"
  cat > "$DEBROOT/DEBIAN/control" <<CONTROL
Package: oj-sentinel
Version: ${VERSION}
Section: education
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: OJ Sentinel contributors <noreply@example.invalid>
Depends: libwebkit2gtk-4.1-0, libgtk-3-0
Description: Hydro OJ plagiarism and AI judging toolkit (local-only)
 Desktop + Web GUI for course staff: local plagiarism detection, behaviour
 anomaly analysis, AI review and local judging.  Student data stays local.
CONTROL
  DEB="$DIST/oj-sentinel_${VERSION}_${DEB_ARCH}.deb"
  rm -f "$DEB"
  dpkg-deb --build --root-owner-group "$DEBROOT" "$DEB" >/dev/null
  echo "==> $DEB ($(du -sh "$DEB" | cut -f1))"
else
  echo "warning: dpkg-deb unavailable; skipped .deb" >&2
fi

echo
echo "Smoke test (no window, no WebKit needed):"
echo "  \"$BUNDLE/OJ Sentinel\" --no-window --port 0"
