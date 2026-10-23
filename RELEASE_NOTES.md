# OJ Sentinel v0.1.5

修复导出报告中的 **AI 解释内容为空**（只有标题、没有结论/置信度/摘要）。

## 根因

`review_pair` 返回的 `AgentResult` 里，结构化 verdict 在 `data` 字段，而导出 Markdown 从顶层读取 `verdict/confidence/summary`，所以生成的是空条目；原始 JSON 里明明有完整 AI 结论。

## 修复

- 导出时把 `ai.data` 合并到顶层后再生成 `ai-explanations.md` 与 PDF 的 AI 章节。
- 现在会显示：
  - 结论（例如 `plausible_plagiarism`）
  - 置信度
  - 摘要
  - 证据列表
- `ai-explanations.json` / `ai-trace.json` 保留完整原始输出与解析数据。

## 验证

- 使用已有 AI 结果直接渲染：Markdown 出现 `结论：plausible_plagiarism；置信度：0.99` 和摘要。
- 重新导出完整 ZIP：AI 解释、PDF、HTML、CSV、analysis.json、trace 均齐全。
- 全量测试通过。

## 版本

- `0.1.5`
- 多架构包与 v0.1.4 相同。
