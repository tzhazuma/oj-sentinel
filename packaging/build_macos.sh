#!/usr/bin/env bash
# Build the macOS .app bundle and (when hdiutil is available) a .dmg.
#
# Prerequisites:
#   python3.12 -m venv .venv
#   .venv/bin/pip install -e ".[desktop]"
#
# Outputs (gitignored):
#   dist/OJ Sentinel.app
#   dist/OJ-Sentinel-<version>-macos-<arch>.zip
#   dist/OJ-Sentinel-<version>-macos-<arch>.dmg   (best effort)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
  :
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi
APP_NAME="OJ Sentinel"
APP="$ROOT/dist/$APP_NAME.app"
DIST="$ROOT/dist"

cd "$ROOT"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: python not found at $PYTHON (set PYTHON=... or create .venv)" >&2
  exit 1
fi
if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "error: PyInstaller missing; run: $PYTHON -m pip install -e '.[desktop]'" >&2
  exit 1
fi

VERSION="$(PYTHONPATH="$ROOT/src" "$PYTHON" -c 'from oj_sentinel.desktop import package_version; print(package_version())')"
ARCH="$(uname -m)"
echo "==> Building $APP_NAME $VERSION for macOS/$ARCH"

# Note: the Web GUI static assets are collected from src/oj_sentinel/server/static
# at build time; rebuild after the web workstream lands new assets.
"$PYTHON" -m PyInstaller --noconfirm --clean "$ROOT/packaging/oj_sentinel.spec"

if [[ ! -d "$APP" ]]; then
  echo "error: expected bundle at $APP was not produced" >&2
  ls -la "$ROOT/dist" >&2 || true
  exit 1
fi

# Ad-hoc sign so Apple Silicon can launch the bundle (no Developer ID needed).
# ``SIGNED_APP`` is the clean bundle the .zip/.dmg are made from: normally the
# bundle in dist/, or a staged copy outside the synced tree when a file provider
# keeps re-adding com.apple.FinderInfo (codesign treats it as detritus).
SIGNED_APP="$APP"
STAGE=""
if command -v codesign >/dev/null 2>&1; then
  echo "==> Ad-hoc signing (unsigned distribution; Gatekeeper will warn)"
  xattr -cr "$APP" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$APP" 2>/dev/null || true
  if codesign --force --deep --sign - --timestamp=none "$APP" >/dev/null 2>&1 \
      && codesign --verify --deep --strict "$APP" >/dev/null 2>&1; then
    echo "==> Signed in place"
  else
    echo "==> In-place signing blocked (file-provider detritus); staging in ${TMPDIR:-/tmp}"
    STAGE="$(mktemp -d "${TMPDIR:-/tmp}/ojs-sign.XXXXXX")"
    STAGED="$STAGE/$APP_NAME.app"
    rm -rf "$STAGED"
    ditto --norsrc --noextattr "$APP" "$STAGED"
    xattr -cr "$STAGED" 2>/dev/null || true
    if codesign --force --deep --sign - --timestamp=none "$STAGED" >/dev/null 2>&1 \
        && codesign --verify --deep --strict "$STAGED" >/dev/null 2>&1; then
      SIGNED_APP="$STAGED"
      echo "==> Signed via temp staging"
    else
      echo "warning: ad-hoc codesign failed; the .app may not launch on arm64" >&2
    fi
  fi
fi

# Preserve the bundle structure/metadata in the zip; build artifacts from the
# clean signed bundle so they do not inherit file-provider xattrs.
ZIP="$DIST/OJ-Sentinel-${VERSION}-macos-${ARCH}.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$SIGNED_APP" "$ZIP"
echo "==> $ZIP ($(du -sh "$ZIP" | cut -f1))"

if command -v hdiutil >/dev/null 2>&1; then
  DMG="$DIST/OJ-Sentinel-${VERSION}-macos-${ARCH}.dmg"
  rm -f "$DMG"
  echo "==> Creating DMG (may take a minute)"
  if hdiutil create -volname "$APP_NAME" -srcfolder "$SIGNED_APP" -ov -format UDZO "$DMG" >/dev/null; then
    echo "==> $DMG ($(du -sh "$DMG" | cut -f1))"
  else
    echo "warning: hdiutil failed; the .app and .zip are still usable" >&2
  fi
else
  echo "note: hdiutil not available, skipped .dmg" >&2
fi

# Install the staged signed bundle back into dist/ (best effort: a synced folder
# may re-add FinderInfo, which only affects codesign --verify, not execution).
if [[ -n "$STAGE" ]]; then
  rm -rf "$APP"
  mv "$SIGNED_APP" "$APP"
  xattr -dr com.apple.FinderInfo "$APP" 2>/dev/null || true
  if codesign --verify --deep --strict "$APP" >/dev/null 2>&1; then
    echo "==> Signed bundle installed at $APP"
  else
    echo "warning: codesign --verify reports synced-folder detritus on $APP;" >&2
    echo "         the .zip/.dmg built from the clean staged bundle are unaffected." >&2
  fi
  rm -rf "$STAGE"
fi

echo "==> $APP"
echo
echo "Smoke test (headless server inside the bundle):"
echo "  \"$APP/Contents/MacOS/$APP_NAME\" --no-window --port 0"
echo "Unsigned build: distribute with 'xattr -dr com.apple.quarantine' or notarize for public release."
