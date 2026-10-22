# C / C++ 课程支持

OJ Sentinel 从 v0.1 起把「语言」作为一等字段，Python / C / C++ 共用同一条采集、
查重、评测和报告管线。当前真实课程数据是 Python；下面的路径均已在本地用合成
C/C++ 代码和编译器验证。

## 1. 采集

Hydro 提交记录里的语言（`C`、`C++`、`GCC`、`Clang` 等）会写入
`records.json` / `submissions/*.json`。采集命令不变：

```bash
ojs acquire --url ... --domain ... --username ... --homework ...
```

## 2. 查重

- `analysis/normalize.language_family()` 把语言映射为 `python | c | cpp`；
- 仅在同一语言家族内配对，避免 Python 与 C++ 互相比较产生噪声；
- C/C++ 归一化：去注释、字符串/字符/数字归一、标识符归一为 `V`、保留关键字与
  常用库名；结构特征包括 include、函数、class/struct、循环、指针、`malloc/new`、
  `printf/cin`、模板与 STL；
- 报告和 GUI 同样展示两份完整代码与高亮相似行，人工最终确认。

## 3. 本地评测

`judge/runner.run_program()` 自动选择：

| 语言 | 编译 | 运行 |
| --- | --- | --- |
| Python | 语法预检 | `python main.py` |
| C | `gcc -O2 -std=c17` | 生成的可执行文件 |
| C++ | `g++ -O2 -std=c++17` / `clang++` | 生成的可执行文件 |

- 支持 Hydro `config.yaml` 的 subtasks、时间/内存限制、`checker.py`
  （argv：input / answer / output）与 `test.py` 函数题包装；
- 编译错误会原样返回；批量评测支持 `--workers`；
- 与 OJ verdict 交叉验证：`ojs judge --all --output ...`，或 Web GUI 的
  「评测本题全部提交 / 评测全部已采集提交」。

```bash
# 单条
ojs judge --snapshot data/snapshots/<domain>/<hid> --rid <rid>
# 批量（本题 / 全部），4 路并发
ojs judge --snapshot data/snapshots/<domain>/<hid> --problem HW2001 --limit 200 --workers 4
ojs judge --snapshot data/snapshots/<domain>/<hid> --all --workers 6 --output data/judge_all.json
```

> 全部提交评测是计算密集型任务：本机实测 120 条 Python 提交（约 3,000 个测试点）
> 在 12 核 Mac 上约 2 分钟；C/C++ 受编译时间影响会略慢。建议先用 `--limit` 抽样。

## 4. 开放式作业 / 小游戏 AI 评测

OJ 无法自动判分的作品（小游戏、GUI、项目）使用 rubric 评测：

```bash
ojs ai evaluate-game --provider dsh \
  --assignment examples/game_project/README.md \
  --rubric examples/game_project/rubric.md \
  --files examples/game_project/snake_game.py
```

返回 JSON：总分 0–100、逐项 rubric 得分与评语、优点/缺点、代码证据、置信度、
`needs_human_review`。规则：

- AI 分数只是**建议**，教师必须复核并最终确认；
- 截图只作为元数据传入（模型看不到像素），因此视觉/可玩性结论一律要求人工复核；
- 可选传入 `--logs`、`--run-evidence` 让模型引用真实运行记录；
- 默认使用本地 DSH agent；可切换任意 OpenAI-compatible 端点。

## 5. 语言扩展清单（未来）

新增语言时只改两处：`normalize.language_family()`（查重家族）与
`judge/runner.LANGUAGE_SPECS`（编译/运行命令），然后在
`tests/` 增加一个合成用例即可。
