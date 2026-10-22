# OJ Sentinel — 实现计划（Hydro OJ 查重 / 异常行为 / AI 评测 / 跨平台 GUI）

- 日期：2026-10-23
- 目标来源：用户 2026-10-23 目标（goal-af63cb9c-966f-4b53-a0d2-181d7e70677a），并给出真实 OJ 地址与账号
- 状态：执行中（Round 1）
- Aegis Visibility：本计划约束一个新的独立仓库（oj-sentinel）的 owner、数据契约、分发面与验证边界；后续任务/子智能体以本文件为基线。

## 1. Goal（可验收）

1. 用户输入 Hydro OJ 地址 / 域名 / 用户名 / 密码，登录后自动发现域名下作业、题目、测试数据、全部提交与成绩单。
2. 对提交做多算法查重（指纹 + token/结构 + AST + AI 语义复核），输出排行、配对详情与 HTML/JSON/CSV 报告文件。
3. 异常行为检测：短时间大量提交、提交间差异极大、代码风格/结构陡变、AI 生成粘贴特征、跨用户相同代码等。
4. 以 DSH 为默认 AI Agent（可配置模型；macOS 测试使用 opencode-go-2 + deepseek-v4.1-flash），提供：AI 复核可疑对、题目测试数据生成、本地评测、OJ 交叉验证、失败原因诊断。
5. 跨平台 GUI：Web（浏览器）、Windows/macOS/Linux 桌面壳、Android App；Android AI 采用 session-2072dc2d 中的 dsh-android 方案。
6. 创建 GitHub 仓库并推送**去除隐私信息**的源码；构建多平台安装包（macOS 本地，Linux/Windows 通过 CI 或交叉构建，Android APK）。
7. 用真实作业（YOUR_DOMAIN / Assignment 2 / YOUR_HOMEWORK_ID）跑通端到端并产出真实报告。

## 2. Architecture

```
OJ Sentinel
├─ core (Python 3.12, stdlib + requests/lxml/fastapi)
│  ├─ hydro/     登录、域名、作业、题目、测试数据、提交、成绩单抓取与 HTML 解析
│  ├─ store/     SQLite 索引 + 原始快照（data/，gitignored）
│  ├─ analysis/  归一化、winnowing、AST/结构特征、相似度、聚类、行为异常、AI 检测
│  ├─ ai/        AgentProvider 抽象：DSH CLI（默认） / OpenAI-compatible / offline
│  ├─ judge/     本地沙箱运行、资源限制、AI 测试生成、交叉验证、诊断
│  └─ report/    HTML/JSON/CSV，可 redact 姓名/代码
├─ server/       FastAPI + 单页 Web GUI（同源静态资源，无 node 依赖）
├─ desktop/      pywebview 壳（Win/mac/Linux），打包 PyInstaller
└─ android/      Kotlin WebView 壳 + 可选连接 dsh-android（Termux bridge）
```

- 单一事实源：Hydro 原始快照 + SQLite（`submissions`, `problems`, `users`, `pairs`, `anomalies`, `runs`）。
- 报告只从 SQLite 生成，保证可复现；AI 结论以 `ai_analysis` 表缓存。
- 凭据不落盘（默认）；可选钥匙串/环境变量。data/ 与凭据永不入库。

## 3. Tech Stack

- Python 3.12（`/opt/homebrew/bin/python3.12`），venv `.venv`
- requests、lxml、FastAPI、uvicorn、Jinja2、pytest
- pywebview（桌面壳）、PyInstaller（安装包）、GitHub Actions（三平台）
- Android：Kotlin + Gradle + WebView（复用 Web GUI），Android SDK 已装；dsh-android 0.3.0 APK 作为 AI companion
- AI：`dsh exec` / ACP 适配器；OpenCode Go 2（OpenAI-compatible）与 deepseek-v4.1-flash

## 4. Baseline / Authority

- Hydro v4.15.0（`http://YOUR_HYDRO_HOST`，域名 `YOUR_DOMAIN`）
- 真实作业：Assignment 2，hid `YOUR_HOMEWORK_ID`，题目 HW2001–HW2004（numeric id 15–18），~4767 条提交、341 名用户、813 条提交/题
- 参考实现：MOSS(winnowing)、JPlag、Dolos、Codequiry 思路；自研离线实现避免上传学生代码
- dsh 0.2.1-rc.2；dsh-android 0.3.0（`~/dsh-android`，github.com/tzhazuma/dsh-android）
- 数据只保存在本机 `data/`，推 GitHub 前运行脱敏检查

## 5. Compatibility Boundary

- 不修改 Hydro 服务端；只读抓取 + 教师权限操作（不自动 rejudge）。
- 报告格式版本化（`report_version: 1`），GUI 与 CLI 共用。
- 桌面/Android 均通过同一 FastAPI 契约，不允许分叉业务逻辑。

## 6. TDD Route

- Mode: off；Decision: skipped（用户未要求 strict TDD）。
- Test posture：解析器、相似度、异常检测、报告 schema 使用 pytest 回归测试；抓取器以真实快照 fixture 验证。UI 用手工/Playwright 冒烟。

## 7. Change Necessity

需要新建独立代码库：现有 workspace 无任何 OJ 相关实现；仅靠配置/文档无法完成抓取、查重、评测与分发。最小代码边界为上述 core + server + desktop + android。

## 8. Workstreams / Tasks

### W1 数据获取（Lead，关键路径）
- W1.1 HydroClient：登录、cookie、重试、限速；域名/作业/题目/文件/记录/成绩单。
- W1.2 解析器 + fixture 测试。
- W1.3 全量抓取 Assignment 2：homework、problems、testdata、scoreboard、4767 条 record + code + judge detail。
- W1.4 SQLite 入库与快照完整性校验（计数、缺失重试）。
- 验收：`ojs acquire` 可重跑；`ojs stats` 输出作业/题目/提交/用户计数；抽样代码与 OJ 页面一致。

### W2 查重引擎（Lead + 子智能体）
- W2.1 归一化（去注释/字符串/空白/重命名不敏感可选）。
- W2.2 winnowing 指纹 + Jaccard/containment；同题跨用户配对；阈值与 Top-K。
- W2.3 AST/结构特征（Python ast 节点序列、函数/循环/分支统计）；token n-gram。
- W2.4 聚类（并查集/社区），可疑团伙；排除模板/教师参考代码。
- W2.5 报告：HTML 排名 + 每对 diff 摘要 + JSON/CSV。
- 验收：注入已知抄袭样本召回；真实数据 Top 对人工抽查；性能 < 10 min/5000 提交。

### W3 异常行为（子智能体）
- 时间窗内提交数、平均间隔、状态跳变；提交间相似度序列；首次提交即高分；代码风格（行长度、命名、注释语言）突变；AI 指纹（markdown 围栏、解释性注释、异常规整命名、库用法）。
- 输出 score + reasons，可解释；阈值可配置。

### W4 AI 智能分析（子智能体）
- DSH adapter（`dsh exec --json`）默认；OpenAI-compatible adapter（opencode-go2，deepseek-v4.1-flash）备用；离线启发式兜底。
- 功能：可疑对复核、提交 authorship 分析、题目测试生成、失败诊断（给定题目/代码/失败用例）。
- JSON schema 校验 + 缓存 + 成本/超时控制。
- 验收：用真实 code pair 得到结构化 verdict；失败诊断能指出行列/逻辑。

### W5 本地评测与交叉验证（子智能体）
- 沙箱：subprocess + resource limits + 临时目录；Python 专用；超时/内存/输出上限；禁用网络（平台能力允许时）。
- 用 OJ 测试数据跑本地评测 → 与 OJ verdict 对比（交叉验证矩阵）。
- AI 生成补充测试（边界/随机），运行并报告。
- 对 WA/RE 生成 AI 诊断与修复建议。
- 验收：本地 verdict 与 OJ 在 >=95% 用例一致（抽样）；生成测试可复现。

### W6 GUI / Web / 跨平台（子智能体 + Lead）
- Web：登录页、作业列表、数据概览、查重排行、配对 diff、异常列表、评测/诊断页、任务进度（SSE/轮询）、导出。
- 桌面：pywebview 启动内置 server；macOS .app/.dmg，Windows .exe，Linux AppImage/deb（CI）。
- Android：WebView 壳 + 服务器地址配置 + 离线报告浏览；AI 通过 dsh-android companion deep link。
- 验收：浏览器全流程；macOS app 可启动；APK 可安装启动。

### W7 发布（Lead）
- GitHub 仓库 `oj-sentinel`（public），MIT license，README（中英）、架构、隐私与安全、使用截图。
- 隐私清理：删除 data/、账号、token、真实姓名、学生代码；gitleaks 式扫描。
- 安装包：dist/ 产物 + GitHub Release。
- 验收：`git remote -v`、release assets、clone 后 quickstart 通过。

## 9. Verification

- `pytest -q` 全绿；`ruff`/`python -m compileall` 通过。
- `ojs acquire --homework ...` 重跑一致；`ojs analyze` 生成报告。
- 真实数据端到端：报告含 Top pairs、异常用户、AI 复核样例、本地评测交叉验证表。
- 打包冒烟：macOS app 启动并登录；APK `aapt dump badging` + 模拟器安装启动。
- 安全：`git grep` 无密码/token/cookie；data/ 未跟踪。

## 10. Risks / Stop Conditions

- OJ 限速/封禁：并发 <=6，指数退避，失败可续传。
- 学生代码隐私：默认本地、报告 redact；不上传第三方。
- 平台签名：Windows/Linux 安装包可能未签名，文档标注；Android debug 签名。
- DSH on Android 依赖 Termux/设备环境：提供 companion 集成与降级（HTTP provider）。
- 若真实抓取被权限阻断：退回只读页面数据并明确说明。

## 11. Retirement

- 抓取器旧 HTML 选择器变更时，以 fixture 测试失败为触发，更新 `hydro/parser.py`；不保留双解析路径。
- AI provider 的 offline 启发式仅作降级，不参与最终 verdict 的默认展示（标注 fallback）。
