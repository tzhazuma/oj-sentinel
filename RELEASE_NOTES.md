# OJ Sentinel v0.1.4

修复导出 ZIP **缺少 report.html / CSV** 的关键问题，并让导出自动补全完整报告。

## 根因

桌面版刚刚运行完分析时，分析目录里可能只有 `analysis.json`，还没有 `report.html`、`final_scores.csv`、`pairs.csv`、`anomalies.csv`。旧导出逻辑只做“复制已有文件”，缺失项直接跳过，于是 ZIP 里只剩 `analysis.json` 和一个 PDF，用户看到内容不全。

## 修复

- 导出时先检查四个核心文件；只要缺一个，就用当前快照/分析**自动生成完整 HTML 与 CSV**，再打包。
- 生成失败会写入明确的 warning，而不是静默跳过。
- `report.html` / CSV 生成支持 `include_all_pairs=True`：导出全部可疑对，而不是只导出“substantial”子集。
- 报告模板兼容旧版 analysis.json（缺少 `codebert_cosine`、`longest_fragment`、`token_coverage` 字段时不再报 Jinja UndefinedError）。
- ZIP 同时带上已有的 `judge_batch.json/csv`（若存在）。
- PDF 最多展示 80 对（避免页面过大），完整内容在 HTML/CSV/JSON 中。
- 导出位置仍由设置中的原生文件夹选择器决定；也可直接调用接口传入 `save_dir`。

## 验证

- 用桌面 App 数据目录（此前 ZIP 只有 4 个文件）重新导出：自动生成 `report.html`（全部 1000 对）、`final_scores.csv`、`pairs.csv`、`anomalies.csv`，ZIP 从 224 KB 变为约 2.5 MB，warning 为 0。
- 用完整仓库快照生成完整报告：730 对 / 13 簇 / 76 异常，HTML 约 4.6 MB，输出到 `~/Downloads`。
- 全量测试通过。

## 版本

- `0.1.4`
- 多架构包：macOS arm64/x86_64、Windows amd64/arm64、Linux x86_64/arm64、Android。
