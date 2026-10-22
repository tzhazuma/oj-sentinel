# 构建与打包（macOS / Windows / Linux）

OJ Sentinel 的桌面端是一个 Python 进程：`oj_sentinel.desktop` 在后台线程启动
FastAPI/uvicorn（绑定一个空闲的 `127.0.0.1` 端口），等待 `/api/health` 通过后用
pywebview 打开原生窗口。PyInstaller 把 Python、依赖、静态 Web GUI 和报告模板打进
`.app` / `.exe` / AppImage / `.deb`。

```
packaging/
├── entry.py                 # PyInstaller 入口（调用 oj_sentinel.desktop.main）
├── oj_sentinel.spec         # onedir 打包；macOS 额外生成 BUNDLE (.app)
├── build_macos.sh           # .app + ad-hoc 签名 + .zip + .dmg
├── build_windows.ps1        # onefile .exe + .zip
├── build_linux.sh           # AppImage + .deb
└── smoke_desktop.py         # 打包产物冒烟：--no-window + /api/health
```

所有构建产物都写到仓库根的 `dist/`（已在 `.gitignore` 中排除，不入库）。

## 0. 产物一览（本机已实际构建）

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| macOS arm64 | `dist/OJ Sentinel.app` | 46 MB；ad-hoc 签名（无 Developer ID），可直接本机运行 |
| macOS arm64 | `dist/OJ-Sentinel-0.1.0-macos-arm64.dmg` | 27,214,951 B（约 26 MB）；UDZO，未 notarize |
| macOS arm64 | `dist/OJ-Sentinel-0.1.0-macos-arm64.zip` | 24,592,114 B（约 23 MB）；保留 bundle 结构 |
| Windows x64 | `dist\OJ Sentinel.exe` | 22,621,530 B（约 22 MB）；onefile，未签名（CI 原生构建；已用 Wine 构建并冒烟） |
| Linux x64 | `dist/OJ-Sentinel-0.1.0-linux-x86_64.AppImage` | 23,845,368 B；已在 OrbStack x86_64 VM 本地构建并冒烟 |
| Linux x64 | `dist/oj-sentinel_0.1.0_amd64.deb` | 20,411,558 B；同上 |
| Android | `dist/OJ-Sentinel-0.1.0-android-debug.apk` | 859,927 B（约 840 KB）；debug 签名，见 [ANDROID.md](ANDROID.md) |

> 版本号来自 `oj_sentinel.desktop.package_version()`（当前 `0.1.0`）。文件名中的
> 架构取自 `uname -m` / `PROCESSOR_ARCHITECTURE`。

## 1. 开发环境

```bash
cd oj-sentinel
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[desktop,dev]"      # fastapi/uvicorn/pywebview/pyinstaller/pytest
```

源码方式运行（不需要打包）：

```bash
# 桌面窗口（自动选空闲端口）
PYTHONPATH=src python -m oj_sentinel.desktop

# 无窗口冒烟 / 局域网访问（0.0.0.0 仅限可信网络！）
PYTHONPATH=src python -m oj_sentinel.desktop --no-window --host 0.0.0.0 --port 8765

# 打印/退出
PYTHONPATH=src python -m oj_sentinel.desktop --help
PYTHONPATH=src python -m oj_sentinel.desktop --version
```

`--no-window` 在 stdout 打印一行 JSON 就绪信息，便于 CI 或手机客户端发现端口：

```json
{"event": "ready", "url": "http://127.0.0.1:57645/", "health": "http://127.0.0.1:57645/api/health", "port": 57645, ...}
```

收到 SIGINT/SIGTERM 后会等待 uvicorn 优雅退出。窗口模式在窗口关闭后停止服务；
`.app`/`.exe` 内同样支持 `--no-window`（供打包冒烟、CI 和远程访问使用）。

### 数据目录

优先级：`--data-dir` > `OJS_DATA_DIR` > 每系统默认值。

| 系统 | 默认数据目录 |
| --- | --- |
| macOS | `~/Library/Application Support/OJ Sentinel` |
| Windows | `%APPDATA%\OJ Sentinel` |
| Linux | `$XDG_DATA_HOME/oj-sentinel`，默认 `~/.local/share/oj-sentinel` |

数据目录存放 SQLite、snapshots、分析结果、报告与 `desktop.log`；**学生数据与凭据
不会写入仓库**。桌面壳不会把 OJ 密码落盘（密码只在 Web GUI 的进程内会话中使用）。

## 2. macOS：`.app` + `.dmg`

```bash
./packaging/build_macos.sh
```

脚本执行：

1. `.venv/bin/python -m PyInstaller --noconfirm --clean packaging/oj_sentinel.spec`
2. 生成 `dist/OJ Sentinel.app`（`Contents/Resources` 内包含
   `oj_sentinel/server/static/index.html`、`oj_sentinel/report/templates/*.j2` 等数据文件）
3. ad-hoc 重签名（`codesign --force --deep --sign -`）并校验
4. 生成 zip，`hdiutil` 可用时生成 UDZO 的 `.dmg`

输出：

```
dist/OJ Sentinel.app/Contents/MacOS/OJ Sentinel
dist/OJ-Sentinel-0.1.0-macos-arm64.zip
dist/OJ-Sentinel-0.1.0-macos-arm64.dmg
```

验证（不需要打开窗口）：

```bash
# 打包产物自检：启动 --no-window、请求 /api/health、再优雅关闭
.venv/bin/python packaging/smoke_desktop.py \
  "dist/OJ Sentinel.app/Contents/MacOS/OJ Sentinel"

# 或者手动：
"dist/OJ Sentinel.app/Contents/MacOS/OJ Sentinel" --no-window --port 0 &
curl -s http://127.0.0.1:<上一步日志中的端口>/api/health
```

已在本机验证结果（macOS 27.2 arm64，2026-10-23）：

```
==> health HTTP 200: {"status":"ok","app":"oj-sentinel","version":"0.1.0",
    "data_dir":"...", "snapshots":0, "jobs_running":0, "sessions":0, ...}
==> Web GUI index HTTP 200 (1978 bytes read)
==> shutdown complete (exit code 0)
```

`.zip` 解压后的 `.app` 与 `.dmg` 挂载后的 `.app` 均已用
`codesign --verify --deep --strict` 验证通过，并确认
`Contents/Resources/oj_sentinel/server/static/index.html` 存在。

> 注意：仓库位于 iCloud/文件提供程序同步目录时，`dist/OJ Sentinel.app` 目录可能被
> 异步重新加上 `com.apple.FinderInfo`，导致对**散装 .app** 的 `codesign --verify`
> 报 detritus；这不影响运行，且从干净暂存目录生成的 `.zip`/`.dmg` 不受影响。

### macOS 签名与 Gatekeeper 说明

- **未签名/未公证**：发行版只做 ad-hoc 签名，没有 Apple Developer ID。首次打开
  `.dmg` 里的 App 会被 Gatekeeper 拦截（右键「打开」或「仍要打开」）。
  用户可执行：
  ```bash
  xattr -dr com.apple.quarantine "/Applications/OJ Sentinel.app"
  ```
  正式发布需 `codesign --deep --sign "Developer ID Application: ..."` +
  `xcrun notarytool submit` + `xcrun stapler staple`。
- 某些同步目录（iCloud Drive / file provider）会给 bundle 根目录重新加上
  `com.apple.FinderInfo`，导致 `codesign` 报
  `resource fork, Finder information, or similar detritus not allowed`。
  `build_macos.sh` 已内置回退：把 bundle 用 `ditto --norsrc --noextattr` 复制到
  `$TMPDIR` 签名后再移回 `dist/`，并验证签名。
- 只构建当前机器架构（arm64 macOS 得到 arm64 App）；Intel 需在 Intel 机器或
  CI 的 `macos-13` 上构建。

## 3. Windows：`.exe`

在 Windows 上（PowerShell）：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[desktop]"
pwsh -File packaging\build_windows.ps1
```

输出：

```
dist\OJ Sentinel.exe
dist\OJ-Sentinel-0.1.0-windows-amd64.zip
```

`.ps1` 设置 `OJS_ONEFILE=1` 让 PyInstaller 生成单文件 exe（macOS 不支持 onefile
BUNDLE，脚本会拒绝）。冒烟：

```powershell
python packaging\smoke_desktop.py "dist\OJ Sentinel.exe"
```

说明：

- **未签名**：没有 Authenticode 证书，SmartScreen 首次运行会提示；发布需购买
  代码签名证书并 `signtool sign /fd SHA256 ...`。
- **本地 Wine/CrossOver 验证**（2026-10-23）：本机没有 Windows 机器，使用
  CrossOver 的 Wine（`~/.ojs-wine`）+ Windows Python 3.12.10 成功执行
  `OJS_ONEFILE=1 python -m PyInstaller .../oj_sentinel.spec`，产物 22 MB。
  运行 `wine "dist/OJ Sentinel.exe" --no-window --port 18877` 后：
  `GET /api/health` → 200，data_dir 为 `C:\users\<user>\AppData\Roaming\OJ Sentinel`
  （Windows 路径逻辑正确），`GET /` → 200（静态 GUI 已打包）。这只是本机近似验证，
  **正式 Windows 验证以 CI 的 windows-latest 原生构建/冒烟为准**。
- 窗口模式依赖 **Microsoft Edge WebView2 Runtime**（Windows 10/11 通常自带；
  Server 或精简系统需安装 Evergreen Runtime）。`--no-window` 不需要 WebView2。
- Windows 的 `--no-window` 收到 `TerminateProcess` 时不保证优雅关闭（由 CI 冒烟
  脚本终止进程）；正常窗口关闭会优雅停止 uvicorn。

## 4. Linux：AppImage + `.deb`

依赖（Debian/Ubuntu）：

```bash
sudo apt-get update
sudo apt-get install -y libwebkit2gtk-4.1-0 gir1.2-webkit2-4.1 python3-gi \
  libgtk-3-0 libfuse2 file desktop-file-utils dpkg
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[desktop]"
bash packaging/build_linux.sh
```

输出：

```
dist/OJ-Sentinel/OJ Sentinel                      # PyInstaller onedir
dist/OJ-Sentinel-0.1.0-linux-x86_64.AppImage      # 需要 appimagetool（脚本自动下载）
dist/oj-sentinel_0.1.0_amd64.deb                  # dpkg-deb
```

- AppImage 需要 `file` 命令（appimagetool 依赖）；在没有 FUSE 的容器/VM 里用
  `APPIMAGE_EXTRACT_AND_RUN=1`（CI 中已设置）。
- 已在本机 OrbStack 的 Ubuntu 25.10 x86_64 VM 中实际执行 `build_linux.sh`：
  PyInstaller onedir、AppImage（24 MB）与 `.deb`（20 MB）均成功生成，并用
  `packaging/smoke_desktop.py` 验证 `health HTTP 200`、Web GUI index 200、进程优雅退出。
  该 VM 使用 Python 3.13（CI 使用 3.12）。
- 窗口模式需要目标机安装 WebKitGTK 与 PyGObject；`--no-window` 只需要 Python 运行时，
  因此服务器/远程访问场景不依赖 GTK。
- `.deb` 安装到 `/opt/oj-sentinel`，并创建 `/usr/bin/oj-sentinel` 软链接。
- 未签名：`.deb` 不含 GPG 签名，AppImage 不含更新签名；发行前请自行签名。

## 5. PyInstaller spec 细节

`packaging/oj_sentinel.spec`：

- 入口：`packaging/entry.py` → `oj_sentinel.desktop:main`。
- `pathex=["src"]`，`collect_data_files("oj_sentinel")` 收集所有非 `.py` 资源
  （`server/static/**`、`report/templates/**` 等）；若资源目录还没生成也允许构建。
- 隐藏导入：`collect_submodules("oj_sentinel"/"uvicorn"/"webview"/"fastapi"/"starlette")`，
  加上 uvicorn 动态加载的 logger/loop/protocol/lifespan 模块与各平台 pywebview 后端。
- `console=False`（窗口程序）；macOS 通过 `BUNDLE` 生成
  `OJ Sentinel.app`，Info.plist 设置 `NSHighResolutionCapable`、
  `NSAllowsLocalNetworking`、最小系统 macOS 11。
- 环境变量 `OJS_ONEFILE=1` 生成单文件可执行（仅 Windows/Linux）；默认 onedir
  （启动快、便于调试）。

重建时如果 Web GUI 静态资源有更新，直接重新运行构建脚本即可（spec 每次收集最新文件）。

## 6. GitHub Actions

`.github/workflows/build.yml`：

| Job | Runner | 内容 |
| --- | --- | --- |
| `tests` | ubuntu-latest | 安装 `.[dev,desktop]`，`pytest -q` |
| `desktop` | macos-latest / windows-latest / ubuntu-latest | 安装桌面依赖 → 对应构建脚本 → `smoke_desktop.py` 冒烟 → 上传 artifact |
| `android` | ubuntu-latest | JDK 17 + Android SDK + Gradle 8.12 → `assembleDebug` → `aapt dump badging` → 上传 APK |
| `release` | ubuntu-latest，仅 `v*` tag | 汇总全部 artifact，用 `softprops/action-gh-release` 上传 Release |

Artifact 名称：`OJ-Sentinel-macos`、`OJ-Sentinel-windows`、`OJ-Sentinel-linux`、
`OJ-Sentinel-android`。把所有文件下载到同一目录后即可得到完整发行集。

## 7. 测试

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m pytest tests/test_desktop_smoke.py -q
```

`tests/test_desktop_smoke.py`：

- 未安装 pywebview 时整体 skip（`importlib.util.find_spec("webview")`）；
- 未实现 `oj_sentinel.server.app` 时服务器启动用例 skip；
- 其余用例验证空闲端口分配、`OJS_DATA_DIR` 覆盖、健康 URL 拼接、`--help`、
  以及真实启动/停止 uvicorn 线程并请求 `/api/health`。

## 8. 已知限制

- 所有桌面包**未做代码签名/公证**（无证书），生产分发需自行签名。
- 跨平台不能交叉打包：macOS `.app` 必须在 macOS 构建，Windows `.exe` 在 Windows，
  Linux 在 Linux；CI matrix 已覆盖。
- macOS 产物是当前架构（本机 arm64）；Intel Mac 需另构建。
- Linux 窗口模式依赖系统 WebKitGTK/PyGObject（pip 里的 pywebview 不自带 GTK）。
- Windows onefile 首次启动会解压到临时目录，启动比 onedir 慢。
- 桌面端数据目录与 Web/CLI 共享；删除 `data/` 会丢失本地快照与报告（原始 OJ 数据
  可重新采集）。
- 服务没有远程鉴权，仅绑定 `127.0.0.1`；用 `--host 0.0.0.0` 暴露前请确认网络可信。

## 9. 故障排查

| 现象 | 处理 |
| --- | --- |
| `ModuleNotFoundError: oj_sentinel` | 源码运行加 `PYTHONPATH=src`，或 `pip install -e .` |
| `.app` 打开提示「已损坏」/无法验证 | `xattr -dr com.apple.quarantine ...`；本机构建可先 `codesign --verify --deep --strict` |
| codesign 报 resource fork / FinderInfo | 用 `build_macos.sh`（内置临时目录签名回退）；或把仓库移到非同步目录 |
| Linux 窗口打不开 | 安装 `gir1.2-webkit2-4.1 python3-gi libgtk-3-0`；或改用 `--no-window` |
| Windows 窗口空白 | 安装/修复 WebView2 Runtime；先试 `--no-window` 验证服务器 |
| 端口被占用 | 默认 `--port 0` 自动选择；固定端口时换一个 |
| 打包后 `/` 404 | 重新运行构建脚本，确保 `server/static/index.html` 已存在并被收集 |
