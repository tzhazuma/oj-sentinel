# OJ Sentinel v0.1.0

Hydro OJ 作业数据采集 · 跨学生代码查重 · 异常行为分析 · Python/C/C++ 本地评测 · DSH AI 复核 / 测试生成 / 小游戏 rubric 评测 · 跨平台 GUI。

## 主要功能

- **只读采集**：登录 Hydro 后自动发现域名下的作业、题目、测试数据、成绩单与**全部提交**；不提交、不改判、不修改任何 OJ 数据。
- **查重**：winnowing 指纹 + token 归一化 + Python AST / C/C++ 结构特征 + 稀有指纹索引；只在**不同学生**之间配对；输出可疑度、理由、两份完整原始代码与高亮相似行，人工最终确认。
- **成绩**：每个学生每道题的最终分 = 该题**最后一次提交**的得分；同时输出最终成绩表与 CSV。
- **异常行为**：短时间批量提交、提交间突变、首次提交即高分、风格漂移、AI 粘贴特征、跨用户近似重复；6 类信号、可解释理由，默认最多 80 人。
- **本地评测**：Python/C/C++ 编译运行，Hydro subtasks/checker/`test.py` 包装；单条、批量（本题全部 / 全部提交 / 前 N 条）、多 worker 并行，与 OJ verdict 交叉验证。
- **AI（默认 DSH）**：可疑对复核、失败诊断、根据题目描述生成补充测试并用 accepted 提交校验、开放式小游戏/项目 rubric 评分（AI 分数为建议，强制人工复核）。模型可配置；默认 `opencode-go-2 / deepseek-v4.1-flash`。
- **跨平台**：Web GUI、macOS `.app/.dmg`、Windows `.exe`、Linux AppImage/`.deb`、Android WebView APK（AI tab 可调起 dsh-android）。

## v0.1.0 实测（真实作业，已脱敏）

| 项目 | 结果 |
| --- | --- |
| 采集 | 4,767 条提交 / 340 名学生 / 4 道题 / 4,752 份可分析代码 |
| 查重 | 1,509 组跨学生可疑对（同一对学生保留最强证据）、32 个近复制簇；Top pair 131 行归一化后完全一致，52 对提交互相印证 |
| 异常 | 80 名高置信度线索（6 类信号，班级规模校准） |
| 本地评测 | 120 条分层抽样提交与 OJ verdict **120/120 一致**（含 Hydro checker） |
| AI | DSH 真实跑通：可疑对复核、失败诊断、AI 测试生成 3/3 通过 5 份 accepted 校验、贪吃蛇小游戏 rubric 评测 85/100（needs_human_review） |
| 桌面 | macOS app/dmg、Windows exe、Linux AppImage/deb 均通过 `/api/health` + `/` 冒烟；macOS app 可启动 |
| Android | APK 安装并启动于 API 36 模拟器与真机 PA2573（Android 16），连上 Mac 服务并加载 Web GUI |

## 安装包

| 平台 | 文件 |
| --- | --- |
| macOS arm64 | `OJ-Sentinel-0.1.0-macos-arm64.dmg` / `.zip`（ad-hoc 签名，未 notarize） |
| Windows x64 | `OJ-Sentinel-0.1.0-windows-amd64.zip`（内含 `OJ Sentinel.exe`，未签名） |
| Linux x64 | `OJ-Sentinel-0.1.0-linux-x86_64.AppImage` / `oj-sentinel_0.1.0_amd64.deb` |
| Android | `OJ-Sentinel-0.1.0-android-debug.apk`（debug 签名，minSdk 26 / target 35） |

桌面端启动后会自动打开本机 Web GUI；Android 是瘦客户端，需要先在同一局域网电脑上运行
`ojs serve` 或桌面应用，然后在 App 设置服务器地址。

## 安全与隐私

- 学生数据、代码、测试数据、报告与 SQLite 只保存在本机 `data/`，不进入仓库。
- OJ 密码只用于 `POST /login`；Web GUI 会话仅保存在进程内存（HttpOnly cookie），CLI 从 `OJ_PASSWORD` / stdin 读取。
- 查重完全本地；AI 仅在你显式点击复核/评测时发送必要片段，可切换到本地/自建 OpenAI-compatible 端点。
- 仓库经过 `scripts/privacy_scan.py` 检查；发布物不含真实账号、课程域名、学生姓名或代码。
- AI 评测分数是建议，不构成学术不端结论；教师必须人工复核。

## 快速开始

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[desktop,dev]"
pytest -q
ojs serve                # 浏览器打开 http://127.0.0.1:8765
```

详细说明见仓库 `README.md`、`docs/METHOD.md`、`docs/C_CPP.md`、`docs/BUILD.md`、`docs/ANDROID.md`、`docs/ai-setup.md`。
