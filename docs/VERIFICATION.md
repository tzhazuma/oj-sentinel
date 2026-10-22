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
