# Behaviour and anomaly signals for academic-integrity triage

- **Status:** deep-dive companion to [`landscape.md`](landscape.md)
- **Date:** 2026-10-23
- **Scope:** signals computable from Hydro OJ snapshots — submission timestamps, statuses, scores, and submitted code. No keystrokes, IDE telemetry, IP addresses, device fingerprints, or deleted drafts are assumed.
- **Bottom line:** behaviour signals are **triage features**, not findings. Their value is in prioritising which students/pairs a human should look at, and in providing context that code similarity alone cannot. Every signal below has a plausible innocent explanation; the design must make that explanation visible.

---

## 1. What OJ Sentinel can actually observe

`docs/INTERFACES.md` and `oj_sentinel.hydro.models` define the available data:

- per submission: `rid`, `uid`, `username`, `display_name`, `problem_key`, `status`, `score`, `language`, `submit_at`, `submit_ts`, `time_ms`, `memory`, and the submitted `code`;
- per homework: `open_at`, `deadline`, problem metadata, scoreboard;
- snapshots may contain **all** records for a homework (`records.json`, `submissions/<rid>.json`), which is enough to reconstruct a per-student submission sequence;
- **not** available: keystrokes, intermediate editor states, compile/run events before submission, IP/device, browser focus, pasted-text events, deleted submissions.

This is a real limitation. The behaviour literature often uses rich process data (keystrokes, commits, snapshots) that OJ Sentinel does not have. The features below are deliberately restricted to what a submission log can support.

---

## 2. Principles

1. **Base rates first.** A signal that fires for 30% of the class is not a signal. Estimate per-problem and per-cohort base rates; report a student's position relative to the cohort, not an absolute threshold.
2. **Per-student baseline.** Students differ in speed, style, and debugging habits. Compare a student with their own history where possible; use cohort percentiles only when history is sparse.
3. **Corroboration.** No single behaviour signal should rank a student for review. Require at least two independent signals (e.g. burst + first-attempt success + style distance), or one strong code-similarity edge.
4. **Prefer robust statistics.** Medians/MAD, percentiles, and shrinkage; avoid means/standard deviations on small, skewed samples.
5. **Explainability.** Every anomaly carries reason codes, the raw metric, the baseline, and a benign explanation. "Score 0.82" alone is not reviewable.
6. **Human review.** A high score is never evidence of misconduct. Argus, the closest published system, says exactly this: a high H-score "is never treated as evidence of misconduct"; it is a triage aid ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)).
7. **No covert surveillance.** OJ Sentinel should not add IP/device/keystroke collection to compensate for missing data. That is a policy and privacy escalation, not a modelling improvement.

---

## 3. Signal catalogue

### 3.1 Submission bursts and pacing

**What it captures.** A student submits many attempts in a short window, or introduces a large amount of new code per minute. A burst can indicate pasting externally generated code, but it also occurs during normal debugging, after an IDE crash, or when a student drafts offline and pastes a working version.

**Features (per student, per problem):**

- `n_submissions`: total attempts;
- `max_10min`: maximum submissions in any rolling 10-minute window;
- `min_gap_sec`: minimum inter-arrival time;
- `median_gap_sec`: median inter-arrival;
- `burst_ratio`: fraction of submissions that occur within 10 minutes of the previous one;
- `session_count`: number of sessions separated by a gap > 30 minutes;
- `lines_per_minute`: added lines between consecutive submissions divided by the gap (cap at a sane maximum, e.g. 200 lines/min);
- `max_new_lines_burst`: maximum new lines introduced in a window of ≤ 2 minutes.

**Evidence and precedent.**

- Argus defines a burst as a commit with **at least 30 new lines at a rate of ≥ 15 lines per minute**, empirically chosen from student/staff behaviour to reduce false positives; it treats these as consistent with pasting externally generated code rather than incremental typing ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)). Note that Argus has commit history; OJ Sentinel only has submissions, so the equivalent is "new lines between consecutive submissions / gap".
- Argus also compares the elapsed time between a student's earliest commit and final submission with a per-assignment threshold scaled by expected lines of code; a very short span relative to assignment scope is suspicious, especially when far outside the class average.
- CodeWorkout's anomaly study notes that MOSS-style similarity cannot detect "a student gaming a system with multiple attempts of similar solutions"; repeated similar attempts are a distinct behavioural pattern ([arXiv:2407.17475](https://arxiv.org/abs/2407.17475)).
- ChatGPT-assisted students completed programming tests about **twice as fast** with comparable performance in a controlled experiment ([Toba et al., arXiv:2309.16697](https://arxiv.org/abs/2309.16697)); this supports time/pace as a weak signal, not a detector.

**Confounders.** Debugging sprees, paste-from-own-editor, IDE/connection failures, pair programming, easy problems, deadline pressure, and students who draft offline. Argus explicitly lists prior experience and pasting from an external editor as benign causes of elevated burst/span scores ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)).

**How to use.** Treat burst as a *context* feature. Rank only when combined with a code-similarity edge or a first-attempt-success/style jump. Never accuse on timing alone.

### 3.2 Consecutive-submission differences (edit distance / rewrite magnitude)

**What it captures.** How much the code changed between consecutive submissions by the same student on the same problem, and how long the gap was. A large rewrite after a long pause, or a jump from a broken program to a complete solution with few intermediate steps, can indicate external help. But normal debugging also produces large rewrites, and a student may write offline and paste a finished version.

**Features (for submissions `s_i`, `s_{i+1}`):**

- `line_similarity = difflib.SequenceMatcher(None, lines_i, lines_{i+1}).ratio()`;
- `rewrite_ratio = 1 - line_similarity`;
- `token_similarity` on normalised tokens (identifier/literal-normalised);
- `ast_similarity` via AST node-type n-gram Jaccard or tree-edit ratio; `ast_rewrite = 1 - ast_similarity`;
- `added_lines`, `removed_lines`, `net_growth`;
- `gap_sec = submit_ts_{i+1} - submit_ts_i`;
- `status_before`, `status_after`, `score_before`, `score_after`;
- `lines_per_minute` (only when `gap_sec > 0`).

A simple, explainable definition:

```
rewrite_ratio = 1 - SequenceMatcher(prev_lines, cur_lines).ratio()
ast_rewrite   = 1 - jaccard(ast_ngrams(prev), ast_ngrams(cur))
```

**Evidence and precedent.**

- Process features in general improve authorship verification in educational data: mean Top-1 rises from `0.094` to `0.233`, and pairwise ROC-AUC from `0.556` to `0.752`, though the author concludes this is only instructor-mediated decision support, not independent proof ([Horváth, arXiv:2607.07400](https://arxiv.org/abs/2607.07400), preprint).
- Argus analyses the full commit history, not just the final submission, and uses span/burst components; this is the published precedent for treating the *trajectory* as evidence ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)).
- AST tracking through time is feasible at scale and captures high-level code changes better than line logs ([Rau, Brown & Edwards, arXiv:2509.03668](https://arxiv.org/abs/2509.03668)).
- A 2026 multi-attempt study found that for *success prediction*, the current-attempt-only regime was the most reliable, and pairwise/multi-step history provided no consistent gain ([Amin et al., arXiv:2608.26169](https://arxiv.org/abs/2608.26169)). This is a caution against assuming that "more trajectory features = better"; validate on the task at hand.

**Confounders.** Normal debugging, refactoring, switching from a brute-force to an optimised approach, copying one's own earlier code, and offline drafting. A student who writes a complete solution offline and pastes it once will look like a huge rewrite with a long gap; that is not necessarily misconduct.

**How to use.** Medium-weight reason code only when (a) the rewrite is large (`ast_rewrite ≥ 0.7`), (b) the gap is long (`≥ 30 min`), (c) the status improves (e.g. WA/RE → AC), and (d) there is another signal (similarity to a classmate, first-attempt success, burst). Do not use raw Levenshtein on source text: formatting/renaming dominates; normalise first.

### 3.3 First-submission success and attempt trajectories

**What it captures.** A student solves a problem on the first attempt, in a very short time, with a comparatively large/complete program, or with fewer attempts than the cohort. This can indicate prior experience, a copied solution, or AI assistance; it can also indicate a strong student or an easy problem.

**Features:**

- `first_attempt_ac = 1[status_1 == "Accepted"]`;
- `attempts_to_ac` (number of submissions before the first AC; `∞` if never);
- `time_to_ac = ts_first_ac - ts_first_submission` (or `- homework.open_at` if available);
- `time_to_ac_percentile` within the same problem/cohort;
- `code_len_at_first_ac` and its percentile;
- `prior_ac_rate` for the student in the same course (empirical Bayes);
- `status_sequence` (e.g. `CE, CE, WA, AC` vs `AC`);
- `n_distinct_problems_solved_first_try`.

**Evidence and precedent.**

- ChatGPT-assisted students completed tests about **2× faster** than unaided students, with comparable marks ([arXiv:2309.16697](https://arxiv.org/abs/2309.16697)).
- Plagiarised submissions in a web-programming experiment were similar to independent ones except trivial aspects (colour, identifiers), while AI-assisted submissions were more complex and less readable ([Karnalim et al., arXiv:2310.20104](https://arxiv.org/abs/2310.20104)). This suggests that "complexity" and "speed" are weak, context-dependent signals.
- Argus's span component explicitly flags work completed in a very short window relative to assignment scope, especially when outside the class average ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)).
- CodeWorkout's anomaly study highlights repeated similar attempts as a pattern that MOSS cannot see ([arXiv:2407.17475](https://arxiv.org/abs/2407.17475)); the inverse pattern (one-shot success) is a useful feature but not a validated detector.
- `first-submission success` is also a legitimate outcome variable in OJ research; models predict whether the current/next attempt is accepted ([Amin et al., arXiv:2608.26169](https://arxiv.org/abs/2608.26169)). Do not confuse prediction with misconduct detection.

**Confounders.** Strong students, prior exposure, easy problems, a generous problem statement, reused code from a previous term (if the student retakes the course), pair programming, and tutors. A first-attempt AC is common in introductory courses and should have a low weight.

**How to use.** Low weight; require an extreme cohort percentile (fastest 10% for time-to-AC) **and** at least one corroborating signal. Never flag a first-attempt AC alone.

### 3.4 Stylometric drift

**What it captures.** A change in a student's coding style across submissions: layout, comment density/language, identifier naming, use of type hints/docstrings, AST node mix, library/API choices, error-handling patterns. A sudden shift may indicate that someone/something else wrote the code, but style also evolves with learning, problem domain, and tooling.

**Features (per submission):**

- layout: average/max line length, blank-line ratio, indentation width, tabs vs spaces;
- comments: comment lines ratio, comment language/script, comment position (inline vs block), docstrings;
- identifiers: average length, snake_case vs camelCase, single-letter names, use of builtins vs custom names;
- AST: node-type distribution, function/class/loop/branch counts, max depth, comprehension/lambda usage;
- API/library: set of imported modules and called functions, use of `sys.stdin`/`input`, error handling;
- literals: f-strings, format calls, type hints, Unicode arrows, emoji.

**Distance.** For a student with prior submissions, compute a robust z-score per feature using the median and MAD of their own history; optionally aggregate with a shrinkage covariance (Ledoit–Wolf) or a simple mean absolute z. Use a change-point method (CUSUM) for a sustained shift rather than a single outlier.

**Evidence and precedent.**

- Code stylometry can identify programmers with high accuracy on contest data: 94%/98% for 1,600/250 authors on Google Code Jam, using AST-derived style features, with the authors arguing the features are robust and applicable to Python ([Caliskan-Islam et al., USENIX Security 2015](https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/caliskan-islam)).
- But the same class of method fails to generalize to classrooms: CodeBERT reached `92.6%` Top-1 on GCJ (10 authors) but `0.2%` / `0.06%` Top-1 on coursework with 690/812 authors ([Yemets & Horváth, arXiv:2608.00227](https://arxiv.org/abs/2608.00227)).
- Process evidence improves educational attribution but remains weak (`0.233` mean Top-1) and is explicitly decision support only ([Horváth, arXiv:2607.07400](https://arxiv.org/abs/2607.07400)).
- Style is manipulable: RoPGen shows adversarial style transformation can reduce deep attribution accuracy, and robust training only partially helps ([arXiv:2202.06043](https://arxiv.org/abs/2202.06043)).
- A transformer-based multilingual stylometry classifier reports `84.1% ± 3.8%` average accuracy on its own 10-language dataset ([Gurioli et al., arXiv:2412.14611](https://arxiv.org/abs/2412.14611)); this is in-distribution performance, not a classroom guarantee.

**Confounders.** Learning over the term, new problem domains (recursion vs file I/O), new libraries, IDE auto-formatting, pair programming, and code copied from lecture notes. A student's style can legitimately change after feedback or a new course module.

**How to use.** Weak reason code only; require `≥ 3` prior submissions and corroboration. Never use a global "human vs AI" classifier as the drift detector; compare the student with themselves. Report the specific features that moved, not a single "style score".

### 3.5 Collusion graphs

**What it captures.** Groups of students whose submissions are unusually similar across problems, ideally with temporal direction (who submitted first) and cross-problem evidence. A graph makes collusion clusters visible and avoids treating each pair in isolation.

**Construction.**

- **Nodes:** submissions or students (prefer students for review, submissions for evidence).
- **Edges:** pairs above a calibrated similarity threshold; weight = Dice/overlap (`2|A∩B|/(|A|+|B|)`) or containment (`|A∩B|/min(|A|,|B|)`); store the metric and value.
- **Base-code filter:** remove edges explained by instructor/starter code; JPlag's `--bc`, CodeGrade's base code, and MOSS's shared-code elimination are precedents ([JPlag wiki](https://github.com/jplag/JPlag/wiki); [CodeGrade docs](https://help.codegrade.com/setup-assignment/analyze-your-assignment/checking-for-plagiarism.md)).
- **Temporal direction:** record which submission came first, and the gap. A cluster where one submission precedes many near-identical ones is more consistent with sharing than independent convergence.
- **Cross-problem edges:** same pair of students similar on ≥ 2 problems is stronger evidence than a single problem.
- **Clustering:** connected components/union-find for small graphs (current OJ Sentinel uses union-find at `score ≥ 0.55`); for larger graphs use a weighted community method (Louvain/Leiden) or JPlag's agglomerative/spectral clustering. JPlag exposes cluster strength and supports AVG (Dice) / MAX (overlap) metrics, agglomerative/spectral algorithms, and CDF/percentile preprocessing ([JPlag clustering wiki](https://github.com/jplag/JPlag/wiki/8.-Clustering-of-Submissions)). Dolos provides graph visualisation for exploration ([Dolos](https://github.com/dodona-edu/dolos)).
- **Cluster features:** size, edge density, minimum edge weight, number of problems, temporal span, number of submissions per member, whether a member is an outlier in code style/behaviour.

**Evidence and precedent.**

- JPlag's clustering was designed for "potentially colluding groups" and reports a cluster strength score; this is the most directly reusable published design ([wiki](https://github.com/jplag/JPlag/wiki/8.-Clustering-of-Submissions)).
- Dolos uses interactive visualisations, including a plagiarism graph, to help teachers explore relationships ([Dolos README](https://github.com/dodona-edu/dolos)).
- A study of collusion in programming assignments quantified similarity with MOSS and examined personality correlates; it is a reminder that collusion can persist even when assignments are novel and students are educated about integrity ([Parthasarathy et al., arXiv:2407.15014](https://arxiv.org/abs/2407.15014)).
- Plagiarism deterrence work argues for class-wide statistical characterisation and a class-wide independence measure, rather than only pairwise punitive comparisons ([Cohen et al., arXiv:2206.02848](https://arxiv.org/abs/2206.02848)); this supports cohort-relative cluster scoring.
- Robustness work shows that structural refactoring can evade similarity detectors ([Sağlam & Schmid, arXiv:2505.20158](https://arxiv.org/abs/2505.20158); [Maisch et al., arXiv:2510.25057](https://arxiv.org/abs/2510.25057); [Cheers et al., arXiv:2102.03997](https://arxiv.org/abs/2102.03997)). A graph built only on one token metric will miss refactored collusion; combine metrics and inspect code.

**Confounders.** Shared templates/starter code, pair programming that the course allows, students working together on the same problem, common solutions copied from a textbook, and small classes where everyone's code looks similar.

**How to use.** Present the subgraph, not a list of accused students. Require ≥ 3 members with ≥ 2 edges each, or a pair appearing on ≥ 2 problems, and a minimum edge weight around `0.60–0.75` (calibrate). Exclude base code and same-user pairs. A human must read the code.

### 3.6 Additional signals worth logging (all weak)

| Signal | Definition | Why it might matter | Why it can be innocent |
| --- | --- | --- | --- |
| Paste/AI surface markers | markdown fences, "Here is/Here's", Unicode arrows, inconsistent indentation, comments explaining obvious code, docstrings on trivial functions, mixed comment languages | may indicate pasted model output | lecture notes/IDE snippets; bilingual students; style habits |
| Advanced constructs not taught | use of libraries/features beyond the syllabus | possible external help | prior experience; self-study; retaking the course |
| Identical wrong answers | same non-obvious bug and same code structure | strong copying signal | common misconception; shared test data/starter code |
| Cross-problem reuse | same code/file submitted to different problems | possible reuse of a template or a friend's solution | legitimate boilerplate; problem statements that share setup |
| Status trajectory | many CE/RE then sudden AC; long pause then AC | possible external help | normal debugging; learning; compiler issues |
| Language/tool switch | sudden change of language, formatter, or import style | possible new author/tool | course switch; new IDE; pair work |
| Account/identity anomaly | multiple accounts, IP/device changes | possible account sharing | shared labs, VPNs, family devices; **not available in OJ snapshots — do not infer** |

**Rule:** these are context, not score drivers. If they are shown at all, show them as "notes" next to the evidence, with the benign explanation.

---

## 4. Feature computation and scoring

### 4.1 Normalisation

- Use **per-student** baselines where `n ≥ 3`; otherwise use **per-problem cohort** percentiles with shrinkage.
- For count features, use `log1p` to tame skew.
- For time features, use percentiles within the problem, not raw seconds (problem difficulty dominates).
- For code-length features, use percentiles within the problem and language.

### 4.2 Robust z-score

```
median = median(x)
mad = median(|x - median|)
robust_z = 0.6745 * (x - median) / (mad + eps)
```

Clip at ±5 to avoid one outlier dominating. For a student with few observations, shrink toward the cohort:

```
z_shrunk = (n / (n + k)) * z_student + (k / (n + k)) * z_cohort
```

with `k ≈ 3–5`.

### 4.3 Combining signals

Do **not** sum arbitrary weights and call it a probability. Two defensible options:

1. **Rank-based:** convert each signal to a within-cohort percentile, take a weighted sum or a max of corroborated pairs, and rank. The output is an ordering, not a probability.
2. **Calibrated model:** if labelled data exist, fit a logistic regression / gradient-boosted model on a small set of interpretable features, calibrate with isotonic regression, and report **precision at the review budget**. Keep the model per-term and freeze it; document features.

In both cases:

- output components separately (`code_similarity`, `behaviour`, `style`, `collusion`);
- output reason codes with raw values and baselines;
- cap the contribution of any single signal (e.g. 40%);
- require at least two independent components for a "high priority" band.

### 4.4 Review bands (starting points)

| Band | Suggested rule | Human action |
| --- | --- | --- |
| Candidate | code similarity ≥ candidate threshold, or behaviour percentile ≥ 95th | optional skim |
| Review | similarity ≥ review threshold **or** two behaviour signals ≥ 95th percentile | read the pair/student timeline |
| High priority | exact/near-exact code match, or similarity ≥ high threshold + corroborating behaviour, or a collusion cluster meeting size/density rules | second reviewer, student conversation |

These are **not** misconduct bands. They are workload bands.

---

## 5. Threshold candidates to test

Calibrate on local data; the values below are starting points from the literature/current code, not validated policy.

| Signal | Candidate threshold | Notes |
| --- | --- | --- |
| Burst (submissions) | `max_10min ≥ 4` or `≥ 99th` percentile of the problem cohort; `p < 0.01` under a per-student baseline | OJ Sentinel already has `max_10min`; add cohort percentile |
| Burst (code volume) | `≥ 30` new lines in `≤ 2 min`, or `≥ 15` lines/min | Argus's empirical rule; adapt to snapshot gaps |
| Span | time-to-final in the fastest 10% of the cohort for the problem, scaled by LOC | Argus uses span relative to assignment scope |
| Consecutive rewrite | `ast_rewrite ≥ 0.7` and `gap ≥ 30 min` and status improves | normal debugging can also do this |
| First-attempt AC | `first_attempt_ac` and time-to-AC in the fastest 10% of the cohort and `code_len ≥ median` | low weight; strong-student confound |
| Stylometric drift | robust distance `> 3` from own baseline over `≥ 3` submissions | weak; require corroboration |
| Collusion edge | Dice `≥ 0.60` (review) / `≥ 0.75` (high) | depends on metric; calibrate per problem |
| Collusion cluster | `≥ 3` members, `≥ 2` edges each, or pair on `≥ 2` problems; density `≥ 0.5` | avoid single-link chains |
| Overall anomaly | top 5% review, top 1% high priority | workload-based, not probability |

**Important:** these thresholds interact. A first-attempt AC by a student with no other signals is normal; a first-attempt AC plus a 95th-percentile burst plus an AST similarity edge to a classmate is a different story. Report the combination.

---

## 6. False positives and benign explanations

Every signal in this document has at least one benign explanation. The review UI should display the likely ones:

- **Prior programming experience** → advanced constructs, fast first attempts, style unlike the class. Argus explicitly lists prior experience as a benign cause ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)).
- **Drafting offline / external editor** → large paste-like rewrite, burst. Argus explicitly lists pasting from an external editor ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)).
- **Pair programming / allowed collaboration** → near-identical code, cross-problem similarity. Check the course policy before flagging.
- **Tutors / writing centres / allowed AI** → polished code, unusual comments, fast progress.
- **Lecture notes / starter code / textbooks** → shared structure, advanced idioms, identical boilerplate. Base-code exclusion is essential.
- **Non-native language / bilingual comments** → comment-language mismatch, phrasing that detectors flag; Liang et al. show text detectors are biased against non-native English writers ([arXiv:2304.02819](https://arxiv.org/abs/2304.02819)).
- **Learning over the term** → stylometric drift; new problem domain → different AST mix.
- **Late submission / deadline pressure** → bursts, rewrites.
- **Accessibility / assistive tools** → unusual formatting, editor-generated code.
- **Retaking the course** → prior code, advanced style.

A false accusation can have serious academic and personal consequences; Argus's authors make this the central design concern ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)). The system should make it easy for a reviewer to see and record the benign explanation.

---

## 7. Ethics and operations

1. **Disclose.** Students should know that submission logs and code similarity are used for academic-integrity review. Covert behavioural profiling is not acceptable.
2. **Minimise.** Only compute what the review needs; do not retain derived behavioural profiles longer than necessary; do not export them into general reports without context.
3. **Human decision.** The output is a review queue. A human inspects code, talks to the student, and applies the course policy.
4. **Consistency.** Use the same thresholds and review process for all students in a cohort; document exceptions.
5. **Bias audit.** Compare flag rates/false positives across sections, language backgrounds, and prior-experience proxies where lawful; investigate disparities.
6. **No automation of sanctions.** Do not auto-report to an integrity office, auto-deduct marks, or auto-annotate transcripts.
7. **Appeal and correction.** Provide a path to contest a flag; correct records if the flag was wrong.
8. **Security.** Behaviour data is sensitive; keep it under `data/`, gitignored; redact names in reports; no credentials in logs.

---

## 8. Validation plan

1. **Build a labelled evaluation set** from historical cases (with permission/anonymisation) and synthetic scenarios:
   - known collusion pairs/clusters;
   - known AI-assisted cases (if adjudicated);
   - hard negatives: strong students, prior experience, pair programming, template-heavy assignments, offline drafters, non-native comments;
   - synthetic transforms: rename, reorder, dead code, extract function, reformat.
2. **Evaluate per signal and combined:** precision/recall at the review budget (top 20/50/100 students or pairs), false positives per 100 students, and calibration.
3. **Evaluate per problem:** short/easy problems will have different baselines; do not pool blindly.
4. **Evaluate over time:** one term train/validate, next term test; track drift after assignment/policy changes.
5. **Red-team:** try to evade with renaming/refactoring/AI paraphrasing; if a signal is trivially evaded, do not present it as strong.
6. **Document:** feature definitions, thresholds, version, date, known limitations, and the human-review protocol.

If a signal cannot be validated, keep it out of the default ranking and show it only as context, if at all.

---

## 9. What NOT to do

1. **Do not treat an anomaly score as a probability of cheating.** It is a ranking heuristic.
2. **Do not flag on timing alone** (burst, speed, first-attempt success). Confounders are too strong.
3. **Do not use stylometry as a standalone accusation.** Classroom attribution is near chance; style changes with learning.
4. **Do not use absolute thresholds across problems/cohorts.** Normalise by problem and student history.
5. **Do not build single-linkage clusters without review.** One shared template can chain hundreds of students; require density/multiple problems.
6. **Do not compare against the open internet or upload code to a service.** Use local corpora the institution owns.
7. **Do not add covert IP/device/keystroke surveillance** to fill the data gap; that is a policy decision with legal implications, not a modelling fix.
8. **Do not auto-report or auto-penalise.** Human review, student response, consistent policy.
9. **Do not publish or log student-level anomaly data** outside the protected `data/` area; redact reports.
10. **Do not overfit to one term's thresholds.** Recalibrate when the assignment, cohort, or tooling changes.

---

## 10. Sources

1. Racovan et al., *Argus: Academic Integrity in the Era of Generative AI* — <https://arxiv.org/abs/2609.36073>.
2. Hicks et al., *An Approach to Detect Abnormal Submissions for CodeWorkout Dataset* — <https://arxiv.org/abs/2407.17475>.
3. Toba et al., *Inappropriate Benefits and Identification of ChatGPT Misuse in Programming Tests: A Controlled Experiment* — <https://arxiv.org/abs/2309.16697>.
4. Karnalim et al., *Plagiarism and AI Assistance Misuse in Web Programming: Unfair Benefits and Characteristics* — <https://arxiv.org/abs/2310.20104>.
5. Amin et al., *Revision-Aware Success Prediction from Multi-Attempt Programming Trajectories* — <https://arxiv.org/abs/2608.26169>.
6. Cohen et al., *Plagiarism deterrence for introductory programming* — <https://arxiv.org/abs/2206.02848>.
7. Parthasarathy et al., *Influence of Personality Traits on Plagiarism Through Collusion in Programming Assignments* — <https://arxiv.org/abs/2407.15014>.
8. JPlag clustering documentation — <https://github.com/jplag/JPlag/wiki/8.-Clustering-of-Submissions>.
9. JPlag repository — <https://github.com/jplag/JPlag>.
10. Dolos repository — <https://github.com/dodona-edu/dolos>.
11. CodeGrade plagiarism documentation — <https://help.codegrade.com/setup-assignment/analyze-your-assignment/checking-for-plagiarism.md>.
12. Caliskan-Islam et al., *De-anonymizing Programmers via Code Stylometry*, USENIX Security 2015 — <https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/caliskan-islam>.
13. Yemets & Horváth, *Source Code Authorship Attribution Does Not Generalize from Competitions to Classrooms* — <https://arxiv.org/abs/2608.00227>.
14. Horváth, *Evaluating Static and Process Evidence for Code Authorship in Programming Education* — <https://arxiv.org/abs/2607.07400>.
15. Li et al., *RoPGen: Towards Robust Code Authorship Attribution via Automatic Coding Style Transformation* — <https://arxiv.org/abs/2202.06043>.
16. Gurioli, Gabbrielli & Zacchiroli, *Is This You, LLM? Recognizing AI-written Programs with Multilingual Code Stylometry* — <https://arxiv.org/abs/2412.14611>.
17. Rau, Brown & Edwards, *Parse Tree Tracking Through Time for Programming Process Analysis at Scale* — <https://arxiv.org/abs/2509.03668>.
18. Gao, Marwan & Price, *Early Performance Prediction using Interpretable Patterns in Programming Process Data* — <https://arxiv.org/abs/2102.05765>.
19. Sağlam & Schmid, *Evaluating Software Plagiarism Detection in the Age of AI: Automated Obfuscation and Lessons for Academic Integrity* — <https://arxiv.org/abs/2505.20158>.
20. Maisch et al., *Same Same But Different: Preventing Refactoring Attacks on Software Plagiarism Detection* — <https://arxiv.org/abs/2510.25057>.
21. Cheers, Lin & Smith, *Evaluating the robustness of source code plagiarism detection tools to pervasive plagiarism-hiding modifications* — <https://arxiv.org/abs/2102.03997>.
22. Liang et al., *GPT detectors are biased against non-native English writers* — <https://arxiv.org/abs/2304.02819>.
23. MOSS — <https://theory.stanford.edu/~aiken/moss/> (archived: <https://web.archive.org/web/2024/https://theory.stanford.edu/~aiken/moss/>).
24. Dickey, *The Failure of Plagiarism Detection in Competitive Programming* — <https://arxiv.org/abs/2505.08244>.
25. Kannam et al., *Code Interviews: Design and Evaluation of a More Authentic Assessment for Introductory Programming Assignments* — <https://arxiv.org/abs/2410.01010>.
