# Build a single-file Windows executable: dist\OJ Sentinel.exe
#
# Prerequisites (PowerShell, repo root):
#   py -3.12 -m venv .venv
#   .venv\Scripts\pip install -e ".[desktop]"
#
# Output (gitignored):
#   dist\OJ Sentinel.exe
#   dist\OJ-Sentinel-<version>-windows-<arch>.zip
#
# Windows 10/11 with the WebView2 Evergreen Runtime is required for the window
# (it ships with current Windows; servers may need the runtime installed).
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = if ($env:PYTHON) { $env:PYTHON } elseif (Test-Path (Join-Path $Root '.venv\Scripts\python.exe')) {
    Join-Path $Root '.venv\Scripts\python.exe'
} else {
    (Get-Command python -ErrorAction Stop).Source
}
$Version = if ($env:OJS_VERSION) { $env:OJS_VERSION } else {
    & $Python -c "import sys; sys.path.insert(0, r'$Root\src'); from oj_sentinel.desktop import package_version; print(package_version())"
}
$Arch = if ($env:PROCESSOR_ARCHITECTURE) { $env:PROCESSOR_ARCHITECTURE.ToLower() } else { 'amd64' }
$Exe = Join-Path $Root 'dist\OJ Sentinel.exe'
$Zip = Join-Path $Root "dist\OJ-Sentinel-$Version-windows-$Arch.zip"

Set-Location $Root
if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "python not found at $Python (set `$env:PYTHON or create .venv)"
}
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller missing; run: $Python -m pip install -e '.[desktop]'"
}

Write-Host "==> Building OJ Sentinel $Version for windows/$Arch (onefile)"
$env:OJS_ONEFILE = '1'
& $Python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot 'oj_sentinel.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
Remove-Item Env:OJS_ONEFILE -ErrorAction SilentlyContinue

if (-not (Test-Path $Exe)) {
    throw "expected $Exe was not produced"
}
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -LiteralPath $Exe -DestinationPath $Zip -CompressionLevel Optimal

Write-Host "==> $Exe"
Write-Host "==> $Zip"
Write-Host ''
Write-Host 'Smoke test (headless server, unsigned build):'
Write-Host "  & '$Exe' --no-window --port 0"
Write-Host 'Note: the executable is unsigned; SmartScreen will warn on first launch.'
