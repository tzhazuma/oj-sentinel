# OJ Sentinel

> Hydro OJ 作业数据采集 · 代码查重 · 异常行为分析 · AI 自动评测 · 跨平台 GUI

OJ Sentinel（OJ 哨兵）是一个面向课程 OJ（默认支持 Hydro v4）的教学质控工具。教师输入 OJ 地址、域名与账号后，工具会自动发现作业、题目、测试数据和全部学生提交，随后在本地完成：

- **多算法查重**：winnowing 指纹 + token n-gram + Python AST/结构特征 + 可选 AI 语义复核；不把学生代码上传到第三方。
- **异常行为检测**：短时间批量提交、提交间突变、首次提交即通过、AI 生成/粘贴特征、跨用户同源代码。
- **AI 智能分析**：默认使用 [DeepSeek Harness (dsh)](https://github.com/tzhazuma/dsh-android) 作为 agent；模型可自定义（默认 `opencode-go-2 / deepseek-v4.1-flash`）。
- **自动评测**：读取 OJ 测试数据本地复评，与 OJ verdict 交叉验证；AI 生成补充测试；对失败提交给出可执行的纠错建议。
- **跨平台**：Web GUI、macOS/Windows/Linux 桌面应用、Android App。

> 隐私：学生姓名、账号、代码和测试数据只保存在本机 `data/`；该目录已被 `.gitignore` 排除。报告默认支持匿名化导出。

## 快速开始（开发者）

```bash
git clone https://github.com/tzhazuma/oj-sentinel.git
cd oj-sentinel
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q

# 采集（密码从环境变量读取，不落盘）
export OJ_PASSWORD='...'
ojs acquire --url http://YOUR_HYDRO_HOST --domain YOUR_DOMAIN \
  --username YOUR_USERNAME --homework YOUR_HOMEWORK_ID
ojs analyze --homework YOUR_HOMEWORK_ID
ojs report --homework YOUR_HOMEWORK_ID --open
ojs serve
```

打开 <http://127.0.0.1:8765> 使用 Web GUI。

> 本仓库不包含任何真实账号、密码、学生数据或测试数据。示例中的地址/账号仅用于说明。

## 命令

| 命令 | 作用 |
| --- | --- |
| `ojs login` | 验证登录并列出域名信息 |
| `ojs homeworks` | 列出可访问作业 |
| `ojs acquire` | 采集作业、题目、测试数据、成绩单与全部提交 |
| `ojs ingest` | 将快照写入 SQLite 索引 |
| `ojs analyze` | 查重 + 异常行为 + 聚类，输出 JSON |
| `ojs report` | 生成 HTML/CSV/JSON 报告 |
| `ojs judge` | 本地评测与 OJ 交叉验证 |
| `ojs ai` | AI 复核 / 诊断 / 测试生成 |
| `ojs serve` | 启动 Web GUI |
| `ojs desktop` | 启动桌面壳 |

## 架构

```
Hydro OJ ──HTTP──> hydro/ (login, parse, snapshot)
                         │
                    data/ snapshots + SQLite
                         │
        ┌────────────────┼─────────────────┐
   analysis/          judge/             ai/
 (winnowing/AST/   (local runner,     (dsh / OpenAI-
  behaviour)        cross-validate)    compatible)
        └────────────────┴─────────────────┘
                         │
              report/ + server/ (FastAPI + Web UI)
                         │
          desktop (pywebview) / Android (WebView + dsh)
```

## 开源查重工具对照

| 工具 | 算法 | 是否上传代码 | 本项目的取舍 |
| --- | --- | --- | --- |
| [MOSS](https://theory.stanford.edu/~aiken/moss/) | winnowing 指纹 | 是（Stanford 服务器） | 借鉴 winnowing；本地实现，不上传 |
| [JPlag](https://github.com/jplag/JPlag) | token/结构 + 贪心串匹配 | 否（可自建） | 借鉴 token 归一化与 greedy string tiling |
| [Dolos](https://github.com/dodona-edu/dolos) | 指纹 + 可视化 | 否（可自建） | 借鉴指纹与配对浏览体验 |
| [Codequiry](https://codequiry.com/) | 商业 MOSS 类 | 是 | 不采用 |

## License

MIT. 仅用于课程教学与学术诚信辅助；使用前请遵守所在学校与 OJ 的隐私政策。
