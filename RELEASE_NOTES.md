# OJ Sentinel v0.1.2

集中设置界面、模型下载进度、自动环境发现、AI 调用透明化，以及一键导出完整报告（ZIP/PDF/AI 解释 + 原生保存对话框）。

## 新功能

### 1. 统一「设置」界面

侧边导航新增 **设置**，集中管理：

- **AI / DSH**：Provider、模型、Base URL、API key 环境变量、推理强度、DSH 自动发现/自定义路径、检测按钮与状态。
- **本地评测 / Python**：Python 解释器路径、默认并发数、解释器检测。
- **CodeBERT**：模型名/本地路径、缓存目录、检测模型、下载模型。
- **本机环境检测**：扫描 Python（含 torch/transformers 版本）、已有 CodeBERT 模型、DSH，可一键选用。
- **数据与报告**：导出入口。

以前 DSH 设置藏在作品评测的折叠项里，现在有独立入口。

### 2. 模型下载按钮修复与进度

根因：`/api/ai/codebert/download` 返回 job 时调用了不存在的 `JobManager.snapshot()`，接口 500，前端只看到“没反应”。

修复：

- 返回正确的 job 快照；按钮创建后台任务。
- GUI 每 1.2 秒轮询 `/api/jobs/{id}`，显示百分比、已下载文件/字节、错误和完成状态。
- 直连 Hugging Face 下载按 1 MB 流式写入，实时上报进度；无 `huggingface_hub` 的打包版也能下载。
- 模型始终写入用户数据目录（`data/codebert/<model>`），**不写入安装包**。

### 3. 自动发现本机环境并使用

- 扫描 `OJS_PYTHON`/`PYTHON`、PATH、`/usr/bin/python3`、Homebrew、conda、Windows Python 等。
- 对每个解释器检测 `torch`/`transformers`/`huggingface_hub` 版本。
- 扫描 `data/codebert`、HF cache、`HF_HOME`/`TRANSFORMERS_CACHE` 中的已有模型。
- 检测 DSH 路径与版本。
- CodeBERT 推理优先使用当前进程；若打包版没有 torch/transformers，会自动选择已发现的、带 torch/transformers 的 Python（也可在设置中手动指定），通过子进程加载本地模型。**模型和运行时都不打包。**

### 4. AI 调用过程与输出可见

- 新增 `TraceRecorder` / `TracedAgent`，记录每次 AI 调用：
  - provider/model、实际 dsh 命令、耗时
  - **完整 Prompt / System**
  - 模型原始输出、解析后的 JSON、错误
  - 调用时间、usage
- GUI 的 AI 复核 / 作品评测结果里有 **“AI 调用详情”** 折叠面板，可查看上述全部内容。
- 每次调用同时保存到 `data/ai-traces/<时间>-<任务>.json`；导出 ZIP 里包含 `ai-trace.json`。
- 这解决了“AI 调用失败但看不到过程”的排查问题；敏感代码仅保存在本机。

### 5. 一键导出完整报告

概览「报告与导出」和设置页都有 **“一键导出完整报告…”**：

- ZIP 内容：
  - `report.html`：完整可视化报告（双方源码、高亮、理由）
  - `analysis.json`：结构化数据
  - `final_scores.csv` / `pairs.csv` / `anomalies.csv`
  - `report.pdf`：可选中文 PDF（理由、证据片段、异常表、AI 解释）
  - `ai-explanations.md/.json` + `ai-trace.json`：可选 AI 详细解释与调用过程
  - `README.md` / `manifest.json`
- **PDF**：reportlab 生成，中文字体 STSong-Light；包含统计、可疑对、异常行为和 AI 解释。
- **AI 解释**：可选，调用 DSH 对前 N 对可疑代码做复核；结果强制标注“需人工确认”。
- **保存位置**：
  - 桌面应用（pywebview）通过原生「另存为」对话框选择路径；
  - Chromium 浏览器使用 File System Access API；
  - 其他浏览器回退到普通下载。
- 全程本地生成，不上传数据。

### 6. 算法确定性修复

发现 winnowing 使用 Python 内置 `hash(tuple)`，而该哈希每个进程随机化；由于 winnowing 取窗口最小值，同一份数据在不同进程会得到不同的指纹和可疑对数量（实测 481/565/607 波动）。

已改为稳定的 BLAKE2b 64 位 token 哈希，重复分析结果一致（当前真实数据：730 对、13 簇、76 条异常，具体数字随阈值/数据而变）。同时保留模板排除与最长连续片段优化。

## 验证

- 全量测试：**153 passed**
- 真实作业（4,767 提交 / 340 学生）重新分析；PDF 中文渲染、ZIP 下载、AI trace、外部 CodeBERT 推理均已实测。
- 多架构 Release 资产与 v0.1.1 相同：macOS arm64/x86_64、Windows amd64/arm64、Linux x86_64/arm64、Android。

## 升级

```bash
git pull
pip install -e ".[dev,desktop]"
pytest -q
```

已有 `data/` 可直接复用；重新运行一次分析以应用确定性指纹。
