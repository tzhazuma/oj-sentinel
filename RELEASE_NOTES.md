# OJ Sentinel v0.1.3

修复桌面版导出位置不可选，并加入 AI 实时输出。

## 1. 导出位置（桌面原生选择器）

上一版依赖 pywebview 的“保存文件”回调，实际运行时用户看不到位置选择。现在改为更可靠的方案：

- 设置 → 数据与报告新增 **默认导出位置**。
- **“选择导出文件夹…”** 调用系统原生文件夹选择器（pywebview `FOLDER_DIALOG`），选完直接填入路径并保存。
- 导出报告时，服务端直接把 ZIP 写入该目录，不再依赖浏览器下载或事后保存对话框；完成后显示完整路径。
- 未设置时仍使用数据目录 `exports/`；浏览器无法拿到本机绝对路径时按旧方式下载。
- 路径会做创建/可写性检查，失败时给出明确错误。

## 2. AI 实时输出

- DSH 改为 `dsh --profile headless --patch ... --json`，逐行读取 newline-delimited 事件：
  - `session` / `status`（turn/step）
  - `thinking`
  - `text`
  - `final`
- 新增 `/api/ai/live/{trace_id}`：AI 请求进行中即可轮询已收到的流式文本和事件。
- GUI 的 AI 复核 / 作品评测在等待期间显示 **实时输出面板**，不再是空白等待；完成后仍保留完整“AI 调用详情”（Prompt、命令、原始输出、解析 JSON、错误）。
- 每个 AI 调用仍保存到 `data/ai-traces/`，导出 ZIP 中带 `ai-trace.json`。

## 3. 其它

- 版本号 `0.1.3`（App、API、安装包一致）。
- 全量测试通过（version 0.1.3 构建）。
- 多架构安装包：macOS arm64/x86_64、Windows amd64/arm64、Linux x86_64/arm64、Android。

## 验证重点

- 设置导出位置 `/tmp/...` 后导出，ZIP 直接出现在该目录，界面显示路径。
- DSH 调用期间 `/api/ai/live/<trace_id>` 依次返回 session/status/thinking/text/final；`done` 从 false 变 true。
- macOS 打包 App 中运行导出与 AI 调用均使用同一套代码。

## 升级

```bash
git pull
pip install -e ".[dev,desktop]"
pytest -q
```
