# 查重与异常检测方法（可解释版）

本文说明 OJ Sentinel 如何得到「可疑代码对 / 相似位置 / 异常行为」以及这些数字的局限。
目标是给教师提供**排错线索**，不是自动定罪。

## 1. 数据与口径

- 采集：Hydro 作业页、题目、测试数据、成绩单、**全部提交**（分页 100 条/页）。
- 查重：**只在不同学生之间**配对（`uid_a != uid_b`）。同一学生的多次提交不参与查重，
  只进入异常行为时间线。
- 成绩：每个学生每道题的最终得分 = 按 `(submit_ts, rid)` 排序后的**最后一次提交**分数，
  不取历史最高分。报告中的 `final_scores.csv` 即此口径。
- 同一对学生/同一道题只保留证据最强的一对提交，`variant_count` 记录有多少对提交互相对应。

## 2. 归一化

Python：

1. `tokenize` 提取 token；去注释、去 docstring；
2. 标识符归一为 `V`，数字归一为 `0`，字符串归一为 `"S"`，保留关键字/内置函数/运算符；
3. 连续空行压缩为一个换行标记；`def`/循环/分支结构保留。

C / C++：

1. 去 `//`、`/* */` 注释，字符串/字符/数字归一；
2. 标识符归一为 `V`，保留 C/C++ 关键字与常见标准库名；
3. 统计 include、函数、class/struct、循环、指针、malloc/new、printf/cin 等结构特征。

不同语言家族（Python / C / C++）之间不互相配对。

## 3. 相似度

- **winnowing 指纹**（MOSS 思路）：token 5-gram → 每 4 个窗口取最小 hash，得到指纹集合。
- **containment** = 交集 / 较小集合；**Jaccard** = 交集 / 并集。
- **AST 余弦**：Python 用 `ast` 节点类型分布；C/C++ 用结构统计特征。
- **最长连续匹配（Greedy String Tiling 近似）**：在归一化 token 序列上用 k-gram 索引
  找最长连续公共片段，`token_coverage = 最长片段 / 较短代码 token 数`。这比单纯
  bag-of-fingerprints 更能说明「整段照抄」。
- **模板/基础代码排除**：同一题内出现频率 >6% 的指纹视为班级模板，不计入
  containment/Jaccard（仍保留原始指纹数供参考）；`distinctive_ratio` 表示区分性指纹占比。
- 综合分 = `0.40 * containment + 0.20 * Jaccard + 0.13 * AST + 0.27 * 最长片段占比`；
  归一化代码完全一致时记为 1.0；模板-only 重叠降权 45%。
- 两阶段计算：先对全部候选做便宜的 containment/Jaccard/AST 预筛，再只对最有希望的
  400 对计算最长连续片段，保证全班规模下可运行。
- 短代码/弱证据降权：少于 30 token 且非精确匹配的 pair 不会进入结果；少于 18 个指纹的 pair
  不进入聚类，并标记 `substantial=false`。
- 候选生成只用「稀有指纹」倒排索引，避免模板代码造成 O(n²) 噪声。

## 4. 排序与理由

- 默认阈值 0.60（CLI `--threshold` 可调），按 `rank_score = score × (指纹充实度 + 最长片段)` 排序；
  100 行的结构性复制会排在 5 行 one-liner 之前。
- 报告/GUI 额外展示 `longest_fragment`、`token_coverage`、`distinctive_ratio`、`template_only`；
  建议人工复核时优先看「最长连续 ≥30 token」且 `containment ≥0.7` 的对。
- 每个 pair 输出：
  - `reasons`：精确复制 / 共享指纹数 / containment / Jaccard / AST / 匹配行块数；
  - `evidence`：行号区间 + 代码片段；
  - 报告和 GUI 同时展示**两份完整原始代码**，匹配行高亮，供人工最终确认。
- 人工判定（确认抄袭 / 独立完成 / 待复核）只写入浏览器 localStorage，不上传服务器。

## 5. 异常行为

| code | 含义 | 典型阈值 |
| --- | --- | --- |
| `burst` | 短时间大量提交 | 5/10/30/60 分钟窗口，最大窗口计数 + 中位间隔 |
| `rapid_distinct` | 连续提交差异极大但分数跳变 | 相似度 <0.20、分数跳变 ≥20、30 分钟内 |
| `first_submission_high` | 首次提交即满分/高分 | 首次 ≥95 分，或快速满分 |
| `style_drift` | 连续提交风格突变 | 风格距离 ≥0.35 |
| `ai_paste` | AI 粘贴特征 | markdown 围栏、解释性注释、每个函数都有 docstring、初学者作业中的高级构造 |
| `cross_user_near_identical` | 与他人的近似重复 | pair ≥0.90；>8 人的模板簇会抑制，仅作为指标 |

异常分 = 最强原因权重 + 0.30 × 其余原因权重（上限 0.95），默认最多返回 80 人。
异常是**线索**，不是处分依据。

## 6. 已知局限

- 指纹/结构方法对「同一题目、同一模板、不同实现」会给出中等分，尤其初学者代码；
  必须结合完整代码、提交时间线、课堂情况人工判断。
- 变量重命名 + 运算符改写可能降低分数；可配合 AI 复核，但 AI 也会误判。
- 模板/教师参考代码可能造成群体相似；大簇会被降级为 common-template 指标。
- AI 生成检测本质是概率启发式，不能作为唯一证据。

## 7. 参考

见 `docs/research/landscape.md`（调研；若尚未生成则以 README 的对照表为准）。

## 可选 CodeBERT 语义信号（默认关闭）

Research 结论：CodeBERT 对“改名/重排/换写法”的结构性改写有帮助，但对强模板题会引入
假阳性，且 CPU 推理明显慢于 winnowing/AST。因此实现为**可选、低权重、只作用于最强候选对**：

- 依赖：`pip install -e ".[codebert]"`（`transformers` + `torch`，不进入发布包）。
- 开关：CLI `--codebert` / GUI 勾选；模型默认 `microsoft/codebert-base`。
- 只对按基础分排序的前 `--codebert-max-pairs`（默认 300）对计算；每份代码只嵌入一次。
- 融合：`score = 0.85 * base_score + 0.15 * codebert_cosine`；原算法仍主导。
- 模型文件由用户在 GUI 中点击“下载模型到本机”下载到 `data/codebert/<model>`，
  **绝不打包进安装包**；首次需要网络，之后可离线。
- 失败/缺依赖时只记录 `codebert.available=false`，普通查重照常完成。
