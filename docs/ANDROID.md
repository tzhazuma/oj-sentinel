# Android 客户端构建与使用

`android/` 是一个 Kotlin + Gradle 的轻量 WebView 客户端。Python 不能直接运行在
Android 上，所以它**不是**本地计算端：OJ Sentinel 的 FastAPI 服务运行在电脑上
（`ojs serve` 或桌面应用），Android App 负责登录、会话 Cookie 同步，并把同一个
Web GUI 装进 WebView。查重、异常分析、评分、AI 全部在电脑端执行，学生数据不上传。

- 包名：`com.ojsentinel.app`
- 最低系统：Android 8.0（API 26），targetSdk 35
- 依赖：仅 Android Framework + Kotlin 标准库（无 AndroidX、无第三方 UI 库、无外部 CDN）
- 本地已构建产物：`dist/OJ-Sentinel-0.1.6-android-debug.apk`（约 840 KB，debug 签名）

## 1. 本机构建（macOS arm64，已验证）

工具链：

| 组件 | 版本 |
| --- | --- |
| JDK | Homebrew OpenJDK 17（`/opt/homebrew/opt/openjdk@17`） |
| Android SDK | `~/Library/Android/sdk`（platform 35、build-tools 36.0.0） |
| Gradle | wrapper 8.12（`android/gradle/wrapper/gradle-wrapper.properties`） |
| AGP / Kotlin | 8.9.1 / 2.1.0 |

```bash
cd android

# JDK 17（AGP 8.9 不支持 JDK 27；系统默认 java 是 27 时必须显式指定）
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home

# 指向本机 SDK（android/local.properties 已被 .gitignore 排除，不会入库）
printf 'sdk.dir=%s\n' "$HOME/Library/Android/sdk" > local.properties

./gradlew :app:assembleDebug --no-daemon

# 产物：android/app/build/outputs/apk/debug/app-debug.apk
mkdir -p ../dist
cp app/build/outputs/apk/debug/app-debug.apk \
   ../dist/OJ-Sentinel-0.1.6-android-debug.apk
```

首次运行 `./gradlew` 会下载 Gradle 8.12 与 AGP/Kotlin 依赖（需要网络；本机已有
`~/.gradle` 缓存）。不需要 Node.js，也不使用 Gradle 之外的构建系统。

### 验证 APK

```bash
AAPT="$HOME/Library/Android/sdk/build-tools/36.0.0/aapt"
"$AAPT" dump badging ../dist/OJ-Sentinel-0.1.6-android-debug.apk | head -20

# 期望：
# package: name='com.ojsentinel.app' versionCode='6' versionName='0.1.6'
# sdkVersion:'26'
# targetSdkVersion:'35'
# launchable-activity: name='com.ojsentinel.app.MainActivity'
# usesCleartextTraffic 为 true（见 AndroidManifest.xml / network_security_config.xml）

"$HOME/Library/Android/sdk/build-tools/36.0.0/apksigner" verify \
  --print-certs ../dist/OJ-Sentinel-0.1.6-android-debug.apk
# Signer #1 certificate DN: C=US, O=Android, CN=Android Debug
```

### 安装

```bash
adb install -r dist/OJ-Sentinel-0.1.6-android-debug.apk
adb shell am start -n com.ojsentinel.app/.MainActivity
```

本机已在 Android 模拟器上完成运行验证（2026-10-23，`emulator-5554`，
sdk_gphone64_arm64 / API 36）：

1. `adb install -r` → `Success`；
2. 启动后 uiautomator dump 可见完整设置页与 服务器/查重/异常/评分/AI 五个标签；
3. `adb reverse tcp:8765 tcp:59442` + 桌面服务 `--no-window --port 59442`，
   点击「测试连接」后状态栏显示
   `服务器在线 · {"status":"ok","app":"oj-sentinel","version":"0.1.6",...}`；
4. 点击「打开 Web GUI」后 UI 中出现 `android.webkit.WebView`，Activity 保持前台；
5. 返回键回到「连接设置」页；
6. AI 标签正确显示「未检测到 dsh-android」及安装说明（模拟器未安装 companion）。

> 登录路径（`POST /api/session`）代码已按 INTERFACES.md 实现，但因本任务不使用真实
> OJ 账号，未在模拟器上提交真实凭据测试。

### 真机验证（2026-10-23）

设备：`192.0.2.10:41129`（adb over Wi-Fi），型号 PA2573，Android 16，
arm64-v8a。

1. `adb install -r dist/OJ-Sentinel-0.1.6-android-debug.apk` → `Success`；
2. 电脑端启动 `python -m oj_sentinel.desktop --no-window --host 0.0.0.0 --port 18876`，
   手机与 Mac 同一 Wi-Fi；
3. App 服务器地址填 `http://192.0.2.11:18876`（Mac 局域网 IP），
   点「测试连接」→ 状态栏显示
   `服务器在线 · {"status":"ok","app":"oj-sentinel","version":"0.1.6","snapshots":1,...}`；
4. 点「打开 Web GUI（无需重新登录）」→ `android.webkit.WebView` 占满内容区，
   Activity 保持前台，成功加载本地已有快照/报告（无需登录 OJ）。

> 说明：`POST /api/session` 的 Hydro 登录由**电脑端**发起；手机只需要能访问电脑的
> OJ Sentinel 服务。因此校园网/OJ 访问只需在 Mac 上可用（例如 atrust 已连接），
> 不需要把代理暴露给手机。若手机无法访问校园网，可先在 Mac 上采集/分析，再用手机
> 浏览本地报告（如上第 4 步）。本次未使用真实 OJ 凭据，登录接口仅按文档实现。

模拟器访问电脑本机服务请用 `http://10.0.2.2:8765`；真机用电脑的局域网 IP
（例如 `http://192.0.2.10:8765`），并确保防火墙放行端口。

## 2. 使用流程

1. 在电脑上启动服务：
   ```bash
   # 源码运行
   PYTHONPATH=src .venv/bin/python -m oj_sentinel.desktop --no-window --host 0.0.0.0 --port 8765
   # 或 macOS .app / Windows .exe 正常打开窗口（默认只绑定 127.0.0.1，
   # 手机访问时需要 --host 0.0.0.0 或端口转发，见下文）
   ```
2. App 打开「服务器」页：
   - **服务器地址**：OJ Sentinel 服务地址，默认 `http://127.0.0.1:8765`
   - **Hydro OJ 地址 / 域名 / 用户名 / 密码**：用于 `POST /api/session`
3. 点「测试连接」→ `GET /api/health`；点「登录并打开」：
   - 原生代码 `POST {server}/api/session`，body:
     `{"base_url": "...", "domain": "...", "username": "...", "password": "..."}`
   - 登录成功后把 `Set-Cookie`（`ojs_session`）写入 App 自己的 `java.net.CookieManager`，
     再逐条同步到 WebView 的 CookieManager，然后加载 `{server}/`。
   - 密码只在本次请求内存中使用，**不写入 SharedPreferences**；只保存服务器地址、
     OJ 地址、域名和用户名。
4. 顶部标签：**服务器 / 查重 / 异常 / 评分 / AI**。查重/异常/评分/AI 通过
   JavaScript 切换 Web GUI 页签：
   - 优先 `document.querySelector('[data-tab="pairs|anomalies|final|project"]').click()`
     （与当前 `server/static/index.html` 的 `data-tab` 一致；评分对应 `final`，
     AI 对应 `project` 即「作品评测/AI 评测」）；
   - 失败时按可见文字（查重/异常/评分/作品评测）查找可点击元素；
   - 最后回退到 `#/pairs`、`#/anomalies`、`#/scores` hash。
   Web GUI 由 web teammate 维护，若以后改 tab 名称，同步修改
   `MainActivity.navScript()` 的 `tab`/`labels` 即可。
5. 返回键：WebView 可后退时后退；否则回到「服务器」设置页；再按返回退出 App。

### 手机访问桌面服务的网络说明

桌面壳默认绑定 `127.0.0.1`（安全默认值），手机无法直接访问。两种做法：

```bash
# A. 显式监听局域网（仅可信网络）
PYTHONPATH=src .venv/bin/python -m oj_sentinel.desktop --no-window --host 0.0.0.0 --port 8765

# B. USB 端口转发（无需暴露端口）
adb reverse tcp:8765 tcp:8765
# 然后 App 里服务器地址保持 http://127.0.0.1:8765
```

> 服务端默认没有登录鉴权（仅本机使用），把它暴露到局域网前请确认网络可信；
> 不要把端口映射到公网。

## 3. 明文 HTTP 与安全边界

`AndroidManifest.xml` 设置 `android:usesCleartextTraffic="true"`，同时
`res/xml/network_security_config.xml` 的 `base-config` 允许 cleartext：OJ Sentinel
服务默认是局域网明文 HTTP，Android 9+ 默认禁止明文，必须显式放行。HTTPS 服务仍使用
系统信任锚点。App 只与用户填写的服务器通信，不包含统计、广告、CDN 或任何第三方上传。

## 4. AI：dsh-android companion

Android 端不内置模型，也不直接调用 AI API。AI 标签页会检测并启动 dsh-android：

- 已核实包名（2026-10-23，`aapt dump badging`）：
  ```bash
  ~/Library/Android/sdk/build-tools/36.0.0/aapt dump badging \
    ~/dsh-android/dist/dsh-android-0.3.0-arm64-debug.apk | grep -E "package:|launchable-activity|native-code"
  # package: name='com.dsh.android' versionCode='3' versionName='0.3.0'
  # launchable-activity: name='com.dsh.android.MainActivity'
  # native-code: 'arm64-v8a'
  ```
  因此 App 优先检测 `com.dsh.android`（兼容别名 `com.deepseek.dsh`，该包名在当前
  APK 中不存在）。
- 检测到：展示「打开 dsh-android」，用 `getLaunchIntentForPackage()` 启动。
- 未检测到：显示安装说明 + 重试按钮，不影响查重/异常/评分功能。
- 安装方式：
  ```bash
  adb install -r ~/dsh-android/dist/dsh-android-0.3.0-arm64-debug.apk
  ```
  或从 <https://github.com/tzhazuma/dsh-android> 的 Release 下载。注意 0.3.0 仅
  提供 arm64-v8a，x86_64 模拟器无法安装。

AI 分析的实际执行路径：桌面/服务器进程调用电脑上的 `dsh` CLI（默认
`opencode-go-2 / deepseek-v4.1-flash`），Android 只是触发与查看入口。这样学生代码
不会离开本机。

## 5. CI

`.github/workflows/build.yml` 的 `android` job 在 `ubuntu-latest` 上：

```bash
actions/setup-java@v4 (temurin 17)
android-actions/setup-android@v3
gradle/actions/setup-gradle@v4 (8.12)
cd android && ./gradlew :app:assembleDebug --no-daemon
```

产物上传为 `OJ-Sentinel-android` artifact；tag `v*` 时由 `release` job 汇总上传到
GitHub Release。

## 6. 已知限制

- **debug 签名**：使用 Android 自动生成的 debug keystore，只能本机/内测安装，不能上架
  Play；发布需自建 keystore 并配置 `signingConfigs`，本文不提供任何私钥。
- **无离线报告浏览**：所有内容来自服务器；服务器不可达时只能看设置页。
- **SPA 路由假设**：「查重/异常/评分」标签是 best-effort 注入（见 §2.4），Web GUI
  若改名或改路由，需要同步 `navScript()`。
- **dsh-android 仅 arm64**；x86 模拟器只能使用 Web GUI 的 AI 功能。
- **服务端无鉴权**：不要把服务暴露到公网。
- 未做平板/横屏专门布局，`configChanges` 保持 WebView 不被重建。

## 7. 故障排查

| 症状 | 处理 |
| --- | --- |
| App 显示「连接失败」 | 确认电脑服务已启动；`curl http://<电脑IP>:8765/api/health` 应返回 200；检查防火墙与同一 Wi-Fi |
| 登录 401 | Hydro 账号/密码/域名错误；先在浏览器用同一组凭据登录 OJ 验证 |
| 登录成功但 Web GUI 未登录 | 服务器可能改了 Cookie 名；检查 `Set-Cookie`，或在 Web GUI 重新登录一次 |
| 模拟器连不上 | 用 `10.0.2.2` 而非 `127.0.0.1`，或 `adb reverse` |
| AI 标签未检测到 dsh | `adb shell pm list packages | grep dsh`；安装 arm64 APK 后点「重新检测」 |
| Gradle 报 JDK 版本错误 | 显式 `export JAVA_HOME=/opt/homebrew/opt/openjdk@17/...`（AGP 8.9 需 JDK 17） |
