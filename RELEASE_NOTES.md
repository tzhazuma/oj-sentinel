# OJ Sentinel v0.1.1

修复本地评测与 OJ 不一致、DSH 后端发现、批量并发输入，并新增可选 CodeBERT 语义信号与更多 CPU 架构安装包。

## 重点修复

### 1. 本地评测在打包版中“完全不一致”（关键）

根因：PyInstaller 打包后 `sys.executable` 指向 `OJ Sentinel` 应用本身，而不是 Python。
评测器用 `sys.executable` 运行学生代码和 Hydro `checker.py`，实际启动的是第二个 GUI 进程，
导致 Python 提交全部 CE/RE、带 checker 的题目全部 WA。

修复：
- 新增 `resolve_python_interpreter()`：在冻结应用中自动查找系统 Python 3
  （`OJS_PYTHON`/`PYTHON` 覆盖、PATH、`/usr/bin/python3`、Homebrew、Windows Python 安装目录）。
- `run_program`、`run_python`、`checker.py` 调用全部使用真实解释器。
- 找不到 Python 时返回明确 CE 文案，而不是静默启动 GUI。
- 新增「测试数据 / 解释器自检」：显示每个题目的测例数、checker、wrapper、testdata 路径和实际 Python。
- 真机验证（macOS arm64 DMG 同款打包）：HW2001 accepted `20/20`、HW2004 accepted `25/25`、
  HW2004 WA `22/25`，`matches_oj=true`。

### 2. DSH 后端找不到

打包版从 Finder 启动时 PATH 很小，`dsh` 不在 PATH 中。现在：
- 自动发现：`OJS_DSH_COMMAND`/`DSH_COMMAND`、PATH、`/usr/local/bin`、`/opt/homebrew/bin`、
  `~/.local/bin`、DeepSeek Harness.app 内置 `runtime/cli/bin/dsh`、Windows 常见路径。
- 设置里可选「自动发现」或「自定义路径」，并提供「检测 DSH」按钮，显示路径、版本、候选与失败原因。
- CLI：`ojs dsh-check [--path ...]`；AI 命令支持 `--dsh-path`。
- 在最小 PATH（`/usr/bin:/bin`）下验证：自动找到 `/usr/local/bin/dsh` 0.2.1-rc.2。

### 3. 批量评测并发数

- GUI 从固定 1/4/8 下拉改为 **1–32 数字输入**，并真正传给评测引擎。
- 服务端同样限制 1–32；CLI `--workers` 不变。

## 算法改进（research 结论落地）

- **模板/基础代码排除**：同一题中出现频率 >6% 的指纹不计入 containment/Jaccard；
  `distinctive_ratio`、`template_only` 写入分析结果与报告。
- **最长连续匹配**：k-gram 索引近似 Greedy String Tiling，输出 `longest_fragment` 与
  `token_coverage`；两阶段计算（先便宜预筛，再只对前 400 对计算长片段），保证全班规模可运行。
- 综合分调整为 `0.40*containment + 0.20*Jaccard + 0.13*AST + 0.27*最长片段占比`；
  模板-only 重叠降权 45%，短片段降权。
- 报告/GUI/CSV 展示新指标与理由；仍保留双方完整源码、高亮相似行与人工确认。
- 真实数据结果：481 组跨学生可疑对（模板排除后）、13 个近复制簇、77 条异常线索；
  原始 v0.1.0 口径为 1,509 对/32 簇。

## 可选 CodeBERT（默认关闭，模型不打包）

- 新增 `[codebert]` 可选依赖（`transformers` + `torch`），**不进入桌面安装包**。
- `ojs analyze --codebert` 或 GUI 勾选后启用；只对最强候选对计算，权重默认 0.15，
  结果仍以 winnowing/AST 为主。
- GUI「检测模型 / 下载模型到本机」：模型保存在用户数据目录 `data/codebert/<model>`，
  不写入安装包；首次下载需要网络，下载后可离线使用。
- 发布包构建时排除 `torch`/`transformers`/`tokenizers`/`huggingface_hub` 与模型权重；
  CI 用 `packaging/check_no_model.py` 检查。
- 需要更快/更省资源时不要启用；普通查重无需任何模型。

## 多架构安装包

| 平台 | 产物 |
| --- | --- |
| macOS arm64 | `OJ-Sentinel-0.1.1-macos-arm64.dmg` / `.zip` |
| macOS x86_64 | `OJ-Sentinel-0.1.1-macos-x86_64.dmg` / `.zip` |
| Windows amd64 | `OJ-Sentinel-0.1.1-windows-amd64.exe` / `.zip` |
| Windows arm64 | `OJ-Sentinel-0.1.1-windows-arm64.exe` / `.zip` |
| Linux x86_64 | `OJ-Sentinel-0.1.1-linux-x86_64.AppImage` / `oj-sentinel_0.1.1_amd64.deb` |
| Linux arm64 | `OJ-Sentinel-0.1.1-linux-arm64.AppImage` / `oj-sentinel_0.1.1_arm64.deb` |
| Android | `OJ-Sentinel-0.1.1-android-debug.apk` |

未签名构建：macOS/Windows 首次启动会有 Gatekeeper/SmartScreen 提示；Android 为 debug 签名。
Linux AppImage 窗口模式需要 WebKitGTK（无窗口服务器模式不需要）。

## 升级

```bash
git pull
pip install -e ".[dev,desktop]"
pytest -q
```

已有 `data/` 快照可直接复用；重新运行 `ojs analyze` 以应用新算法，重新生成报告。
