# 验证记录（v0.1.0）

本文件记录发布前的可复现检查。真实学生数据只在本机 `data/`，不入库。

## 1. 自动化测试

```bash
.venv/bin/python -m pytest -q
# 131 passed, 1 warning（Starlette/httpx 弃用警告，非失败）
python scripts/privacy_scan.py
# privacy scan OK (91 tracked files)
git ls-files data | wc -l
# 0
```

## 2. 真实 Hydro 作业端到端（2026-10-23）

只读采集，未提交/未改判：

| 指标 | 值 |
| --- | --- |
| 作业 | 4 道题，4,767 条提交，340 名学生 |
| 详情/代码 | 4,767 条详情全部落盘，4,752 份可分析代码 |
| 测例 | 4 套 testdata，含 checker/config/test.py |
| 查重 | 1,509 组跨学生可疑对；32 个近复制簇；Top pair 131 行完全一致，52 对提交互证 |
| 行为异常 | 80 名（6 类信号，默认上限 80） |
| 最终分 | 341 行（含未提交学生），口径 = 该题最后一次提交 |

复现（占位符）：

```bash
ojs acquire --url http://YOUR_HYDRO_HOST --domain YOUR_DOMAIN \
  --username YOUR_USERNAME --homework YOUR_HOMEWORK_ID
ojs analyze --homework YOUR_HOMEWORK_ID --domain YOUR_DOMAIN --threshold 0.6
ojs report --homework YOUR_HOMEWORK_ID --domain YOUR_DOMAIN --top-pairs 100
ojs final --homework YOUR_HOMEWORK_ID --domain YOUR_DOMAIN --csv final_scores.csv
```

产物（本机、gitignored）：`data/analysis/<domain>/<hid>/{analysis.json,report.html,pairs.csv,final_scores.csv,anomalies.csv}`。

## 3. 本地评测与 OJ 交叉验证

120 条分层抽样（覆盖 4 题、Accepted/WA/RE/CE）：

```json
{"evaluated": 120, "errors": 0,
 "status_counts": {"Accepted": 45, "Runtime Error": 37, "Wrong Answer": 24, "Compile Error": 14},
 "agreement": 120, "compared": 120, "agreement_rate": 1.0}
```

- 修复了 UTF-8 BOM 导致本地 CE 而 OJ Accepted 的唯一分歧。
- C/C++：`run_program` 编译运行路径由 `tests/test_judge_runner.py` 覆盖（有编译器时执行）。
- 批量命令：`ojs judge --all --workers 6`；GUI「评测」页提供本题全部 / 全部提交 / 前 N 条。

## 4. AI（DSH 默认）

| 功能 | 结果 |
| --- | --- |
| DSH 连通 | `dsh --profile headless --patch <overlay>`，opencode-go-2 + deepseek-v4.1-flash，约 5–7s 返回 JSON |
| 可疑对复核 | 真实 pair 返回 verdict/confidence/summary/evidence（约 50s） |
| 失败诊断 | 3.9 万 ms，准确定位 `sum_odd` 缩进导致的提前 return，confidence 0.97 |
| AI 测试生成 | 3 条测试全部通过 5 份 accepted 提交校验，0 拒绝（约 234s，timeout 300s） |
| 小游戏 rubric 评测 | 贪吃蛇示例 85/100，逐项评语、代码证据、`needs_human_review=true`（约 65–164s） |

## 5. 打包与设备

| 平台 | 验证 |
| --- | --- |
| macOS arm64 | `.app` `--no-window` → health 200 / `/` 200；dmg 挂载、codesign 校验 |
| Windows x64 | Wine 命令行构建 + health/`/` 200；CI windows-latest 重建成功 |
| Linux x64 | OrbStack Ubuntu x86_64 构建；AppImage/deb health/`/` 200 |
| Android | debug APK，API 36 模拟器与真机 PA2573（Android 16）安装启动，连 Mac 服务加载 Web GUI |
| GitHub | main 分支 + tag `v0.1.0` + Release 8 个资产（含 SHA256SUMS） |

## 6. 隐私

- 仓库 91 个跟踪文件，`data/` 0 个；
- `scripts/privacy_scan.py` 检查密码/API key/私网 IP/学生数据痕迹；
- 报告 `--redact` 移除课程标识、姓名、账号、代码与相似片段；
- 截图使用合成演示数据（Alice/Bob、DEMO_2026）；
- 无 OJ 写操作，AI 仅在显式操作时接收必要片段。

## 7. v0.1.1 修复验证（2026-10-23）

### 本地评测与 OJ 不一致

根因：PyInstaller 冻结后 `sys.executable` 是 `OJ Sentinel` 应用，不是 Python；学生代码和
`checker.py` 都被应用自身启动，导致打包版本地评测全部异常。

验证（从 v0.1.1 Release 下载 `macos-arm64.dmg` 挂载后运行 `.app`，最小 PATH `/usr/bin:/bin`，
`OJS_DATA_DIR` 指向真实快照）：

| 提交 | OJ | 本地 | 结果 |
| --- | --- | --- | --- |
| HW2001 `6ad757ff…` | Accepted | Accepted 20/20 | matches_oj=true |
| HW2004 `6ad755a3…` | Accepted | Accepted 25/25 | matches_oj=true |
| HW2004 `6ad753f4…` | Wrong Answer | Wrong Answer 22/25 | matches_oj=true |

`/api/judge/doctor` 返回真实解释器 `/usr/bin/python3`（而不是 app 二进制），并列出每题测例数：
HW2004=25、HW2003=25、HW2002=20、HW2001=20。

### DSH 自动发现

最小 PATH 下 `/api/ai/dsh/check` 自动找到 `/usr/local/bin/dsh`，版本 `0.2.1-rc.2`；
自定义无效路径返回 `found=false` 和具体错误；Web「检测 DSH」显示状态。

### 算法与可选 CodeBERT

- 真实数据重跑：481 组跨学生可疑对、13 个近复制簇、77 条异常；模板排除生效。
- CodeBERT 默认关闭；发布包不包含 `torch`/`transformers`/模型权重，
  `packaging/check_no_model.py` 检查通过；GUI 可检测/下载模型到用户数据目录。

### 多架构

CI 矩阵新增 macOS x86_64、Linux arm64、Windows arm64；构建后运行无模型检查与 headless 冒烟。

## 8. v0.1.2 验证（2026-10-23）

- **模型下载**：`POST /api/ai/codebert/download` 返回 202 + job；直连 Hugging Face 下载 476.8 MB 模型，job 进度 5→100，文件落在 `data/codebert/codebert-base`；模型不进安装包。
- **环境发现**：`POST /api/ai/environment` 返回 9 个 Python 候选、1 个本地 CodeBERT 模型、DSH；发现 `anaconda3/bin/python3.12`（torch 2.11 / transformers 5.3）。
- **外部 CodeBERT**：用该 Python 加载本地模型，两条代码嵌入成功（768 维），`enrich_pairs` 自动选用；打包版无 torch 时也可用现有环境。
- **AI trace**：Mock/DSH 复核与作品评测均返回 `ai_trace`，含完整 Prompt、命令、原始输出、解析 JSON、耗时；GUI 显示“AI 调用详情”，文件写入 `data/ai-traces/`。
- **导出**：`POST /api/report/export` 生成 ZIP，含 `report.html`、`analysis.json`、`final_scores.csv`、`pairs.csv`、`anomalies.csv`、`report.pdf`、`ai-explanations.*`、`ai-trace.json`；PDF 中文渲染 5 页；`GET .../download` 返回 200。
- **确定性**：winnowing 改用 BLAKE2b；连续两次独立进程分析结果一致（730 对 / 13 簇 / 76 异常）。
- 全量测试 153 passed。

## 9. v0.1.3 验证（2026-10-23）

### 桌面导出位置

- 设置新增 `settings-export-dir` 与「选择导出文件夹…」；桌面通过 pywebview `FOLDER_DIALOG` 原生选择器，浏览器不可用时提示手动输入。
- `/api/report/export` 接收 `save_dir`，检查目录可写后直接把 ZIP 写入；实测指定 `/tmp/ojs-ui-export`，返回路径 `.../OJ-Sentinel-report-...zip`，界面显示“已保存”。
- 无法使用原生选择器时仍保留浏览器下载回退。

### AI 实时输出

- DSH 命令加 `--json`，逐行读取事件：`session/status/thinking/text/final`。
- `/api/ai/live/{trace_id}` 在调用完成前返回 `done=false`、当前调用及 `stream_text`/`stream_events`；完成后 `done=true`。
- 实测一次真实 DSH 复核：先返回 `turn_start/step_start`，随后 `thinking`、`text`，最终 `done=true`；GUI 显示实时面板与最终“AI 调用详情”。
- 全量测试 155 passed。

## 10. v0.1.4 导出完整性修复（2026-10-23）

- 现象：下载的 ZIP 只有 `analysis.json` + PDF，缺 `report.html`、`final_scores.csv`、`pairs.csv`、`anomalies.csv`，manifest 有 4 条“缺少…”warning。
- 根因：分析后尚未生成 HTML/CSV，导出只复制已有文件。
- 修复：导出时检测缺失，调用 `build_reports(..., include_all_pairs=True)` 自动生成完整 HTML/CSV，再打包；模板兼容旧 analysis 缺少新字段；附带 `judge_batch.*`。
- 验证：
  - 桌面 App 数据目录（1000 对）重新导出：ZIP 2.5 MB，`report.html` 存在，warning 0。
  - 完整快照导出：730 对 / 13 簇 / 76 异常，HTML 4.6 MB，`final_scores.csv` 62 KB，`pairs.csv` 223 KB，`anomalies.csv` 27 KB，PDF 34 KB，AI 解释与 ai-trace 各一份。
  - 最终完整包：`~/Downloads/OJ-Sentinel-report-20261023-184508.zip`（11 个文件）。
