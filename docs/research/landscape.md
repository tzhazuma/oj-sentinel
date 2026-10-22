# OJ Sentinel — source-code similarity, AI-code detection, and behaviour-signal landscape

- **Status:** research note supporting OJ Sentinel v0.1
- **Date:** 2026-10-23
- **Author:** research teammate
- **Scope:** open-source and hosted code-plagiarism tools; AI-generated-code detection research and practice; behaviour/anomaly signals used in academic-integrity work; actionable recommendations for OJ Sentinel.
- **Constraints observed:** no OJ access, no student data, no credentials, no uploads of code; only public documentation, papers, and repositories were read. No source code outside `docs/research/` was modified.

This note is deliberately conservative. Similarity scores and "AI detectors" are **evidence generators, not findings**. MOSS says explicitly that a human must inspect the code; JPlag is designed as an instructor tool for investigating matches; Dolos is built around teacher exploration of pairwise evidence. The research literature on AI-code detection is much weaker than vendor marketing suggests, and the behaviour literature warns that timing and style signals have plausible innocent explanations. The recommendations below separate *candidate generation* (cheap, high recall) from *review evidence* (expensive, human-facing) and prohibit automated punishment.

Companion documents:

- [`ai-code-detection.md`](ai-code-detection.md) — detailed review of AI-code/AI-assistance detection methods, reliability limits, and ethics.
- [`behavior-signals.md`](behavior-signals.md) — detailed treatment of submission bursts, consecutive-submission diffs, first-submission success, stylometric drift, and collusion graphs.

---

## 1. Executive summary

1. **Keep the local winnowing core, but add structure and evidence.** OJ Sentinel already implements MOSS-style winnowing (`k=5`, `w=4`), containment/Jaccard, a weak AST cosine, and a rare-fingerprint candidate index. The highest-value additions are (a) JPlag-style **structural token sequences + Greedy String Tiling** for long matching runs, (b) Dolos-style **longest-fragment / total-overlap** metrics, (c) explicit **base-code/template exclusion**, and (d) function-level **AST comparison** for Python. These improve both recall against renaming/reordering and the explainability of a pair.
2. **Do not adopt hosted plagiarism services.** MOSS, Codequiry and CodeGrade all require code to leave the machine (or are SaaS by design). That conflicts with the project's hard rule: plagiarism computation is local; student code is never uploaded. Their *ideas* (shared-code elimination, report layout, base-code handling, clustering) are useful; their deployment model is not.
3. **Do not ship a binary "AI-generated code" detector.** Peer-reviewed and preprint evidence shows off-the-shelf text detectors are near chance on code (GPTZero accuracy ≈ 0.497 in one 5,069-sample study), code-specific classifiers are fragile across generators and prompts, stylometry does not generalize from contests to classrooms, and watermarks are not present in arbitrary student submissions. AI-assistance signals can be surfaced as *weak, explainable indicators* for human triage; they cannot be a verdict.
4. **Treat behaviour signals as triage features, not proof.** Submission bursts, large rewrites between consecutive submissions, first-attempt acceptance and stylometric drift have innocent explanations (prior experience, pasting from an external editor, pair programming, strong students). The defensible design is per-student baselines, cohort-relative percentiles, multiple corroborating signals, reason codes, and human review. Argus (2026) is the closest published system: it combines span/burst behaviour with stylistic indicators, reports an H-score, and explicitly says a high score is never evidence of misconduct ([Argus, arXiv:2609.36073](https://arxiv.org/abs/2609.36073)).
5. **Use a two-level output.** Candidate threshold (for the review queue) should be low; accusation-adjacent language should require a much higher, calibrated bar plus human inspection of evidence. The current composite threshold of `0.35` is reasonable for *candidate generation* but too low for *review priority*; test `0.60` (review) and `0.75` (high priority), calibrated per problem.
6. **Respect licences.** OJ Sentinel is MIT. JPlag, SourcererCC and AC2 are GPL-3.0; Pylint is GPL-2.0. Algorithms and published ideas can be reimplemented, but GPL source cannot be copied into an MIT codebase. Dolos (MIT), pycode_similar (MIT), Sherlock (Unlicense), SIM (BSD-3-Clause) and PMD CPD (BSD-style) are licence-compatible sources of inspiration/code, subject to attribution.
7. **Invest in evaluation, not more signals.** Build a small labelled benchmark from synthetic plagiarism transforms + hard benign negatives; report precision at a fixed human-review budget (e.g. top 50 pairs), recall, and false-positive rate per problem. A signal that cannot be calibrated on local data should stay out of the default UI.

---

## 2. What "code similarity" can mean

It helps to separate five families, because tools with the same "similarity score" are measuring different things:

| Family | What is compared | Examples | Strength | Weakness |
| --- | --- | --- | --- | --- |
| Lexical / fingerprint | k-grams of tokens or words, hashed and winnowed | MOSS, Dolos, Sherlock | Fast, scalable, robust to whitespace/comments if normalised | Can be evaded by renaming/reordering; shared boilerplate creates noise |
| Structural token | parser-derived token sequence representing syntax | JPlag | Robust to identifier renaming; long matches are explainable | Parser per language; can miss semantic clones; GPL runtime |
| AST / tree | AST node types or tree edit distance | pycode_similar, Clone Digger, OSLL | Captures structure; function-level evidence | Costly; brittle on parse errors; node-frequency cosine is weak |
| Compression | Normalised compression distance (NCD) | AC2 | Language-agnostic, cheap, captures repetition | Hard to localise evidence; sensitive to length |
| Semantic / ML | embeddings, classifiers, LLM judges | research prototypes; commercial "AI detectors" | Can catch paraphrases | Opaque, unstable, biased, hard to audit; needs labelled data |

OJ Sentinel's current mix is mostly family 1 with a small family-3 signal. The recommendation is to strengthen families 2 and 3 for *candidate pairs*, use family 4 only as a cheap fallback/secondary signal, and keep family 5 out of default scoring.

---

## 3. Tool-by-tool survey

### 3.1 MOSS (Measure Of Software Similarity)

- **Algorithm.** MOSS is the canonical deployment of winnowing: hash k-grams of language tokens, select a subset of hashes per window, compare document fingerprints, and highlight matching passages. The underlying algorithm is described in Schleimer, Wilkerson & Aiken, *Winnowing: Local Algorithms for Document Fingerprinting* ([DOI 10.1145/872757.872770](https://doi.org/10.1145/872757.872770); [paper PDF](http://theory.stanford.edu/~aiken/publications/papers/sigmod03.pdf)). Winnowing guarantees, roughly, that sufficiently long matches are selected while keeping fingerprint counts low; the window parameter trades sensitivity against size.
- **Languages / Python.** C, C++, Java, C#, **Python**, Visual Basic, JavaScript, FORTRAN, ML, Haskell, Lisp, Scheme, Pascal, Modula-2, Ada, Perl, TCL, Matlab, VHDL, Verilog, Spice, MIPS/a8086 assembly, HCL2 ([MOSS page](https://theory.stanford.edu/~aiken/moss/)).
- **Self-host / privacy.** **No.** MOSS is an Internet service: "you supply a list of files to compare and Moss does the rest"; results are HTML pages; "the results will contain copies of the code you submitted and these will be accessible to anyone with the result URL"; Stanford assumes no liability ([MOSS page, archived](https://web.archive.org/web/2024/https://theory.stanford.edu/~aiken/moss/)). Free for non-commercial use; 100 submissions/day/user cap (2022 update).
- **Licence.** Hosted service; the MOSS page offers client submission scripts but does not publish the detector as open source. Non-commercial terms.
- **Output.** HTML report listing pairs and highlighted matching passages; can exclude code expected to be shared (libraries/instructor code).
- **Strengths.** Very strong practical track record, local fingerprint robustness, shared-code elimination, readable evidence, scales on Stanford's hardware.
- **Limits.** Code leaves the machine; no self-host; HTML-only legacy interface; similarity scores are explicitly *not* proof; no structural/AST semantics beyond language tokenisation; no behaviour or AI analysis.
- **What OJ Sentinel should borrow.** The winnowing core (already present), **shared-code/base-code subtraction**, side-by-side highlighted evidence, and the UX rule that a score is a pointer to code worth reading, not a verdict. The MOSS page's own warning is worth reproducing in the UI: "It is a misuse of Moss to rely solely on the similarity scores."

### 3.2 JPlag

- **Algorithm.** Language-specific parsers turn each submission into a sequence of **structural tokens** (syntactic entities such as class/statement/control-structure tokens, not raw identifiers). Pairs are compared with **Greedy String Tiling** (Wise) using running Karp–Rabin hashing; the implementation is explicit about this lineage in [`GreedyStringTiling.java`](https://github.com/jplag/JPlag/blob/main/core/src/main/java/de/jplag/comparison/GreedyStringTiling.java). Minimal match length is configurable (`-t`); optional match merging and token normalisation counteract obfuscation. Base code can be supplied with `--bc`, and matches present in the base code are excluded ([JPlag wiki FAQ](https://github.com/jplag/JPlag/wiki)).
- **Languages / Python.** Java 25, C, C++, C#, **Python 3.6 (mature)**, JavaScript/TypeScript, Go, Kotlin, R, Rust, Swift, Scala, LLVM IR, Scheme, EMF, SCXML, text, multi-language ([supported languages](https://github.com/jplag/JPlag/wiki/2.-Supported-Languages)).
- **Self-host / privacy.** **Yes.** "All similarities are calculated locally; no source code or plagiarism results are ever uploaded online" ([JPlag README](https://github.com/jplag/JPlag)). CLI, Java API, Docker; report viewer is local.
- **Licence.** **GPL-3.0**.
- **Output.** A `.jplag` archive containing JSON: `topComparisons.json`, per-pair `comparisons/*.json`, `cluster.json`, `distribution.json`, `options.json`, `runInformation.json`, `submissionFileIndex.json`, `submissionMappings.json`; plus a Vue report viewer ([report format](https://github.com/jplag/JPlag/wiki/6.-Report-File-Generation)). By default only the top 2,500 pairs are in the top list; configurable with `-n`.
- **Strengths.** Mature, fast, explainable long token matches, base-code exclusion, built-in clustering, local, well-documented report format. The wiki reports ~3,600 large submissions / 5M LoC / 100K files / 6.5M comparisons in 15 minutes on an M1 MacBook.
- **Limits.** GPL-3.0; Java 25 runtime is heavy; token abstraction can miss semantically equivalent rewrites; thresholds are language-dependent; no behaviour/AI analysis; the report viewer historically needs Node for building.
- **What OJ Sentinel should borrow.** Structural tokenisation, **Greedy String Tiling / longest common token runs**, base-code exclusion, the distinction between average/Dice and maximum/overlap similarity, the **distribution histogram + top-pairs + clusters** report structure, and the "only top N pairs" performance discipline.

### 3.3 Dolos

- **Algorithm.** Tree-sitter parses source into syntax trees; tokens are k-grams hashed with a Rabin–Karp rolling hash; **winnowing** selects a subset of hashes; an index records which files/lines contain each fingerprint; pairs with shared fingerprints are reported with **similarity** (fraction of shared fingerprints), **total overlap** (absolute shared count) and **longest fragment** (longest matching subsequence) ([Dolos algorithm docs](https://github.com/dodona-edu/dolos/blob/main/docs/about/algorithm.md)).
- **Languages / Python.** Tree-sitter languages; out-of-the-box Bash, C, C++, C#, Elm, JavaScript, Java, PHP, **Python**, TypeScript, and more ([Dolos language docs](https://github.com/dodona-edu/dolos/blob/main/docs/about/languages.md)).
- **Self-host / privacy.** **Yes.** Open-source CLI, JavaScript library, Docker image, self-hostable web app; local analysis by default ([Dolos README](https://github.com/dodona-edu/dolos)).
- **Licence.** **MIT**.
- **Output.** Interactive web UI with pair/graph visualisations, machine-readable **CSV**, JS library API. The published tool paper is Maertens et al., *Discovering and exploring cases of educational source code plagiarism with Dolos*, SoftwareX (2024), [DOI 10.1016/j.softx.2024.101755](https://doi.org/10.1016/j.softx.2024.101755).
- **Strengths.** Language-agnostic via tree-sitter, scalable indexing, excellent visualisation, MIT licence, CSV export, longest-fragment metric that is more informative than a single percentage.
- **Limits.** Node/tree-sitter runtime and parser quality vary by language; token-level matching can be defeated by structural refactoring; no behaviour/AI analysis; a web UI is not the same as a batch/report pipeline.
- **What OJ Sentinel should borrow.** The **longest-fragment + total-overlap** pair metrics, the fingerprint-index design, the pair/graph visualisation vocabulary, and the MIT-compatible implementation style.

### 3.4 Sherlock

- **Algorithm.** Rob Pike/Loki's classic *digital signatures*: split text into n-word chunks (default 3), turn each chunk into a number, discard some bits (default 4 zero bits), compare all pairs, report a percentage similarity. It is explicitly a text-similarity tool that also works on source files ([Sherlock README](https://github.com/diogocabral/sherlock)).
- **Languages / Python.** Any text; examples include `.java` and `.py`. No language parser.
- **Self-host / privacy.** **Yes.** Single C program, local.
- **Licence.** **Unlicense** (public domain).
- **Output.** CSV-like lines `file1;file2;similarity%`; default threshold 20%; options for threshold, zero bits, word count, output file.
- **Strengths.** Trivial to deploy, fast enough for small cohorts, language-agnostic, public domain, useful baseline for unsupported languages.
- **Limits.** Lexical only; comments/strings/whitespace can dominate; random bit discarding makes scores approximate; N² comparisons; no structural or cluster analysis; no evidence snippets.
- **What OJ Sentinel should borrow.** A simple fallback signature for non-Python/parse-failure cases, the threshold/CSV conventions, and the reminder that a percentage is not a probability.

### 3.5 SIM (Dick Grune)

- **Algorithm.** Language-specific lexers turn source into a token stream; runs of at least `MinRunSize` tokens are hashed and compared via a forward-reference table; matching runs are reported with a similarity percentage that is computed carefully to avoid the usual "percentage of what?" errors ([SIM page](https://dickgrune.com/Programs/similarity_tester/); [concise algorithm report in the source archive](https://dickgrune.com/Programs/similarity_tester/sim.zip)).
- **Languages / Python.** C, C++, Java, Pascal, Modula-2, Lisp, Miranda, 8086 assembly, and natural-language text. **No Python** in the current checkers.
- **Self-host / privacy.** **Yes.** C sources, local.
- **Licence.** **BSD-3-Clause** (copyright Dick Grune, VU Amsterdam; see `LICENSE.txt` in the archive).
- **Output.** Side-by-side chunks, diff format, or percentages.
- **Strengths.** Efficient token-run matching; language-specific lexers; well-thought-out similarity percentages; local and simple.
- **Limits.** No Python; old codebase; no clustering/report UI; lexical rather than AST semantics; manual thresholds.
- **What OJ Sentinel should borrow.** The **run-based matching** idea and the percentage-normalisation discussion; not the implementation (no Python).
- **Name collision.** A separate 1999 SIGCSE tool also called *sim* measures similarity between C programs (Gitchell & Tran, [DOI 10.1145/384266.299783](https://doi.org/10.1145/384266.299783)). It is not the same as Grune's SIM; do not conflate the two in citations.

### 3.6 SourcererCC

- **Algorithm.** Token-based clone detection at file/method/statement/block granularity, designed for very large code bases. It tokenises source, builds token-frequency statistics, and uses an inverted-index/filter to avoid all-pairs comparison; default clone threshold in the tutorial is 80% ([SourcererCC README](https://github.com/Mondego/SourcererCC)). The underlying paper is Sajnani et al., *SourcererCC: Scaling Code Clone Detection to Big Code* (ICSE 2016), [arXiv:1512.06448](https://arxiv.org/abs/1512.06448).
- **Languages / Python.** Tokenizer is language-configurable; the README shows a `.py` configuration and a Python tokenizer step; also Java, C/C++, etc.
- **Self-host / privacy.** **Yes.** Local Java/Python pipeline.
- **Licence.** **GPL-3.0**.
- **Output.** Tokenised files, stats, and clone pairs (`id1,id2`), intended for database/statistical post-processing.
- **Strengths.** Scales to very large corpora; candidate filtering is directly relevant to OJ-scale data; language-agnostic token model.
- **Limits.** Clone detection, not classroom plagiarism detection; no evidence UI; GPL; tokenizer configuration is fiddly; threshold semantics differ from similarity scores.
- **What OJ Sentinel should borrow.** The **inverted-index/filter candidate-generation** approach and token-frequency weighting; OJ Sentinel already has a rare-fingerprint index, and this is the literature to cite when extending it.

### 3.7 AC2

- **Algorithm.** Uses **Normalized Compression Distance (NCD)** as its main similarity measure, with optional additional measures; ANTLR parsers for multiple languages; produces graphical similarity visualisations rather than a single "percentage copied" ([AC2 README](https://github.com/manuel-freire/ac2)).
- **Languages / Python.** Java, C/C++, **Python**, PHP, XML, ECMAScript, Pascal, VHDL, plain text.
- **Self-host / privacy.** **Yes.** Java desktop app / Docker; local.
- **Licence.** **GPL-3.0**.
- **Output.** Graphical views of similarity between submissions; no simple percentage claim.
- **Strengths.** Language-agnostic NCD, robust to some edits, local, strong visualisation philosophy, explicitly refuses to reduce plagiarism to one number.
- **Limits.** GPL; Java/UI-heavy; NCD is less localisable/explainable; no evidence snippets; no behaviour/AI.
- **What OJ Sentinel should borrow.** NCD as a **cheap secondary signal** for parse failures or unsupported languages, and the UI stance that instructors build their own explanation from multiple views.

### 3.8 Hosted/commercial tools

#### Codequiry

- **What it is.** Commercial hosted code-plagiarism and "AI-written code" detector; markets itself as a MOSS replacement, 65+ languages, 427B+ code sources, checks GitHub, Stack Overflow and "the open web", with API/CLI/LMS integrations ([Codequiry](https://codequiry.com/)).
- **Algorithm.** Not publicly specified; vendor claims line-by-line matching across web/public repos/AI output/prior submissions.
- **Self-host / privacy.** **No.** Hosted service; code is submitted for checking. The desktop app scans local folders, but we found no self-host/on-premise option; reports and API are cloud-based. Not compatible with OJ Sentinel's no-third-party-upload rule.
- **Licence.** Proprietary/commercial.
- **Output.** Web report; API; LMS integration.
- **Strengths.** Web-corpus coverage, convenient workflow, AI-detector feature.
- **Limits.** Privacy/retention cannot be verified from public docs; algorithm opaque; vendor accuracy claims have no independent peer-reviewed validation that we found; the "AI detector" is a black box.
- **What OJ Sentinel should borrow.** The idea of checking against a **local historical corpus** (past semesters, instructor solutions), the report/UX patterns, and nothing about the cloud deployment.

#### CodeGrade

- **What it is.** Commercial autograding/LMS platform with a built-in plagiarism checker: compares each submission pairwise, claims robustness to reordering and identifier renaming, supports old assignments/submissions and base-code exclusion, minimum average similarity default 50%, side-by-side highlighting, export to Docx/LaTeX ([CodeGrade plagiarism docs](https://help.codegrade.com/setup-assignment/analyze-your-assignment/checking-for-plagiarism.md); [CodeGrade](https://www.codegrade.com/)).
- **Languages / Python.** The autograder advertises 175+ languages; the plagiarism checker has a language selector and per-language file suffixes, and Python is among the supported languages ([CodeGrade](https://www.codegrade.com/); [plagiarism docs](https://help.codegrade.com/setup-assignment/analyze-your-assignment/checking-for-plagiarism.md)).
- **Algorithm.** Not disclosed in the public docs.
- **Self-host / privacy.** **No public self-host/on-prem option documented** (SaaS; LMS integration; FERPA/GDPR statements on the site). Not compatible with the local-only rule.
- **Licence.** Proprietary/commercial.
- **Output.** Web report, side-by-side highlights, Docx/LaTeX export.
- **Strengths.** Workflow integration, base code + old submissions, export formats, usable review UI.
- **Limits.** Opaque algorithm; hosted; no machine-readable open format documented; no behaviour/AI detail.
- **What OJ Sentinel should borrow.** **Base-code + previous-semester corpus**, a **default average-similarity threshold around 50%** as a starting point (not a verdict), and the export/annotation workflow.

#### Sourcery

- **What it is.** AI code review and security scanning for pull requests ([Sourcery](https://www.sourcery.ai/)). It is **not** a plagiarism detector or an AI-provenance detector.
- **Why it is mentioned.** The brief listed "Sourcery/CodeGrade-style tools"; this note records the category mismatch so future readers do not treat Sourcery as a similarity engine. Its review-comment UX is not relevant to pairwise provenance review.
- **What OJ Sentinel should borrow.** Nothing for detection; possibly the "explain the issue, point to the line, suggest a fix" presentation pattern for the AI review layer, but not its scoring.

### 3.9 Python-specific tools

#### pycode_similar

- **Algorithm.** Normalises Python ASTs and compares them with `difflib` (default) or tree edit distance (`zss`); reports, per function, the percentage of the *referenced* code structure that is "plagiarised" by the candidate; directional by design ([PyPI description](https://pypi.org/project/pycode-similar/); [repo](https://github.com/fyrestone/pycode_similar)).
- **Languages / Python.** Python only.
- **Self-host / privacy.** **Yes.** One file, no third-party dependency (except `zss` for TreeDiff), no network.
- **Licence.** **MIT**.
- **Output.** CLI text: overall percentage plus per-function matches with line numbers; library API returns structured results.
- **Strengths.** Directly targets Python AST structure, handles renaming better than token exact-match, local, MIT, easy to reimplement.
- **Limits.** Function-level only; `TreeDiff` is slow and reportedly poor on small functions; no clustering, evidence UI, candidate index, or multi-language support; the project is small/unmaintained (2023).
- **What OJ Sentinel should borrow.** The **AST normalisation + function-level diff** idea for Python, and the directional "how much of A appears in B" framing (which complements containment).

#### Pylint `duplicate-code` / `symilar`

- **Algorithm.** Line-level n-gram hashing: strip comments/docstrings (optionally imports/signatures), hash `n` successive lines (`min-similarity-lines`, default 4), find common hash buckets, merge adjacent matches into duplicated ranges ([`pylint/checkers/symilar.py`](https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/symilar.py); message docs [R0801](https://pylint.readthedocs.io/en/latest/user_guide/messages/refactor/duplicate-code.html)).
- **Languages / Python.** Python.
- **Self-host / privacy.** **Yes.** Local linter.
- **Licence.** **GPL-2.0**.
- **Output.** Pylint message `R0801` with duplicate line ranges; `symilar` CLI prints similarity percentages.
- **Strengths.** Zero extra dependencies for Python projects, configurable ignores, cheap, familiar to Python developers.
- **Limits.** Line-based, not semantic; comments/docstrings must be stripped; boilerplate and imports create noise; no cross-language support; GPL-2.0.
- **What OJ Sentinel should borrow.** The **ignore-comments/docstrings/imports/signatures** options and the cheap n-gram baseline; do not copy GPL code into the MIT core.

#### PMD CPD

- **Algorithm.** Token-based copy-paste detection using Karp–Rabin string matching; reports duplicated token sequences above a minimum token count; supports many languages including Python ([CPD docs](https://docs.pmd-code.org/latest/pmd_userdocs_cpd.html)).
- **Languages / Python.** Java, C/C++, C#, Go, Kotlin, Ruby, Swift, **Python**, and more.
- **Self-host / privacy.** **Yes.** Local CLI/Ant/Maven.
- **Licence.** **BSD-style** (PMD licence).
- **Output.** Text, XML, CSV, `csv_with_linecount_per_file`, Markdown.
- **Strengths.** Mature, fast, token-based, good report formats, permissive licence.
- **Limits.** Copy-paste detection, not academic plagiarism; token-level only; thresholds in tokens; no clustering/behaviour/AI.
- **What OJ Sentinel should borrow.** The **token-based minimum-length discipline** and the XML/CSV report vocabulary; CPD is a useful sanity check for "is this just duplication?".

#### OSLL `code-plagiarism`

- **Algorithm.** Compares **AST metadata** for Python 3, C and C++; the README's example configuration uses n-gram length 2 and threshold 70; optional MongoDB cache; CLI/Docker; generates CSV reports ([OSLL/code-plagiarism](https://github.com/OSLL/code-plagiarism)).
- **Languages / Python.** Python 3, C, C++.
- **Self-host / privacy.** **Yes.** Local/Docker.
- **Licence.** **MIT**.
- **Output.** CSV reports; CLI output.
- **Strengths.** Python AST metadata, MIT, Docker, threshold/CLI ergonomics, GitHub user/repo checks.
- **Limits.** Metadata comparison can miss detailed copied blocks; no evidence UI; C/C++ via clang; small project.
- **What OJ Sentinel should borrow.** Per-language AST feature extraction and a **CSV report** for downstream analysis; the threshold/CLI pattern.

#### Clone Digger (legacy)

- **Algorithm.** Extracts statement sequences from Python ASTs, finds similar sequences (suffix-tree style), clusters them with a distance threshold, and reports clone groups ([PyPI](https://pypi.org/project/clonedigger/); source inspection of `clone_detection_algorithm.py`).
- **Languages / Python.** Python (Java planned but not delivered in the 1.1.0 release).
- **Self-host / privacy.** **Yes.** Local.
- **Licence.** **GPL-3.0**.
- **Output.** Console/HTML report of clone groups.
- **Strengths.** Early AST-based Python clone detection; statement-sequence representation.
- **Limits.** Python 2 era, unmaintained; no modern packaging; GPL; not designed for pairwise classroom evidence.
- **What OJ Sentinel should borrow.** Historical validation that AST statement sequences work; no code reuse.

### 3.10 Comparison table

| Tool | Algorithm family | Python | Local / self-host | Licence | Output | Main strength | Main limit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MOSS | winnowing fingerprints | ✅ | ❌ hosted service | service, non-commercial | HTML pair report, highlighted matches | proven, shared-code elimination | code upload; no self-host |
| JPlag | structural tokens + Greedy String Tiling | ✅ 3.6 | ✅ | GPL-3.0 | `.jplag` JSON + viewer + clusters | explainable token matches, clustering | GPL, Java 25 |
| Dolos | tree-sitter + k-grams + winnowing | ✅ | ✅ | MIT | web UI, CSV, JS API | language-agnostic, longest fragment, graph | Node/tree-sitter |
| Sherlock | n-word digital signatures | ✅ (text) | ✅ | Unlicense | CSV-like percentages | trivial, language-agnostic | lexical, N², no structure |
| SIM (Grune) | token runs + hashing | ❌ | ✅ | BSD-3-Clause | side-by-side/diff/percent | efficient run matching | no Python |
| SourcererCC | token clone + inverted-index filter | ✅ (config) | ✅ | GPL-3.0 | pair IDs/stats | scales to big code | clone-focused, no evidence UI |
| AC2 | NCD + multiple measures | ✅ | ✅ | GPL-3.0 | graphical similarity views | language-agnostic, visual | GPL, opaque NCD |
| Codequiry | hosted matching + web corpus | ✅ | ❌ SaaS | proprietary | web report/API | web corpus, workflow | privacy, opaque |
| CodeGrade | hosted pairwise (undisclosed) | ✅ | ❌ SaaS | proprietary | web report, Docx/LaTeX | base code + old submissions | privacy, opaque |
| pycode_similar | Python AST + difflib/tree edit | ✅ | ✅ | MIT | CLI text per function | Python AST, directional | function-only, small |
| Pylint duplicate-code | line n-gram hashing | ✅ | ✅ | GPL-2.0 | R0801 / CLI | zero-dependency baseline | line-level, GPL |
| PMD CPD | token Karp–Rabin | ✅ | ✅ | BSD-style | text/XML/CSV | mature, fast | duplication, not plagiarism |
| OSLL code-plagiarism | AST metadata + n-grams | ✅ | ✅ | MIT | CSV | Python AST + CLI | metadata-only |
| Clone Digger | AST statement sequences + clustering | ✅ | ✅ | GPL-3.0 | console/HTML | early AST approach | Python 2, unmaintained |
| Sourcery | AI code review | — | ❌ SaaS | proprietary | PR comments | not a detector | category mismatch |

---

## 4. AI-generated-code detection: what the evidence actually supports

The detailed review is in [`ai-code-detection.md`](ai-code-detection.md). The short version:

- **Off-the-shelf text detectors do not work on code.** Pan et al. tested GPTZero, Sapling, GPT-2 Detector, DetectGPT and GLTR on 5,069 Python samples across 13 prompt variants. GPTZero's accuracy was ≈0.497 (it labelled almost everything human), DetectGPT was ≈0.40–0.54, GLTR ranged ≈0.484–0.769, and the detectors were highly sensitive to prompt variants ([arXiv:2401.03676](https://arxiv.org/abs/2401.03676)).
- **Code-specific classifiers can work in-distribution, but not as a general detector.** Whodunit reports F1/AUC 0.91 with 140 stylometry features + XGBoost on 798 human + 798 GPT-4 Python CodeChef solutions; excluding gameable whitespace features still gives 0.89 ([arXiv:2403.04013](https://arxiv.org/abs/2403.04013)). DetectCodeGPT is a zero-shot method that perturbs stylistic tokens and reports +7.6% AUC over a supervised baseline ([arXiv:2401.06461](https://arxiv.org/abs/2401.06461)). Both are evaluated on particular models/prompts/datasets.
- **Detectors age.** A 2026 evaluation finds a "detector half-life": performance varies across generators/prompts, and DetectCodeGPT/GPT-Sniffer achieved accuracy 0.50 but F1 0.00 in that evaluation because they classified almost everything as AI ([arXiv:2610.01664](https://arxiv.org/abs/2610.01664)).
- **Stylometry is not authorship proof in classrooms.** Caliskan-Islam et al. achieved 94%/98% accuracy on Google Code Jam C/C++ with 1,600/250 authors ([USENIX Security 2015](https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/caliskan-islam)), but a 2026 study found the same CodeBERT pipeline at or below chance on coursework: 0.2% Top-1 for 690 authors and 0.06% for 812 authors ([arXiv:2608.00227](https://arxiv.org/abs/2608.00227)). Process features help but remain weak (mean Top-1 0.094 → 0.233; pairwise AUC 0.556 → 0.752) and are explicitly "instructor-mediated decision support" ([arXiv:2607.07400](https://arxiv.org/abs/2607.07400)).
- **Watermarks do not solve student-code provenance.** Watermarking schemes (SWEET, CodeMark) assume the generator embeds a signal; arbitrary student submissions do not carry it. N-gram watermarks are broken by obfuscation ([arXiv:2507.05512](https://arxiv.org/abs/2507.05512)), and robustness studies show the same ([arXiv:2403.17983](https://arxiv.org/abs/2403.17983)).
- **Industry "code detection" is usually duplication matching, not AI detection.** GitHub Copilot's code referencing compares a suggestion plus ~150 characters of surrounding code with an index of public repositories, reports matches (typically <1% of suggestions), and can block or annotate them; private repositories and code outside GitHub are not included ([GitHub Docs](https://docs.github.com/en/copilot/concepts/completions/code-referencing)). That is provenance/duplication matching against known code, not a classifier of AI authorship. The transferable pattern for OJ Sentinel is a **local historical/class corpus index** with transparent match metadata.
- **Even the vendors disclaim binary use.** OpenAI withdrew its text classifier because of low accuracy; on its launch page it reported 26% true positives and 9% false positives, and said it is "very unreliable" on code ([OpenAI, archived](https://web.archive.org/web/2024/https://openai.com/index/new-ai-classifier-for-indicating-ai-written-text/)). GPTZero's own FAQ says results "should not be used to punish students" and that there are edge cases in both directions ([GPTZero FAQ](https://gptzero.me/faq)).

**Recommendation:** expose *AI-assistance indicators* only as weak, explainable signals in a separate panel, with model/version/date, confidence, and the exact evidence; default to no automated action. Do not call them "AI detection" in the UI. Do not send code to a third-party detector. Use stylometry/process signals within-student, not as a global classifier. The AI layer already specified in `docs/INTERFACES.md` is best used to **explain** a pair or diagnose a failure, not to pronounce authorship.

---

## 5. Behaviour/anomaly signals: what is defensible

The detailed review is in [`behavior-signals.md`](behavior-signals.md). The short version:

| Signal | What to compute from an OJ snapshot | Evidence / precedent | Main confound | Default use |
| --- | --- | --- | --- | --- |
| Submission bursts | submissions per 10 min, min inter-arrival, lines added/min between consecutive submissions | Argus uses ≥30 new lines at ≥15 lines/min as a burst; "pasting externally generated code rather than typing incrementally" ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)) | debugging sprees; pasting one's own code from an external editor | weak/medium reason code |
| Consecutive-submission diff | line/token/AST edit distance, rewrite ratio, time gap | Process data adds signal beyond final code ([arXiv:2607.07400](https://arxiv.org/abs/2607.07400)); Argus analyses full commit history | normal iterative development; copy-paste refactor | medium only with other evidence |
| First-submission success | accepted on first attempt, attempts-to-AC, solve time vs cohort | ChatGPT-assisted students completed tests ~2× faster with comparable performance ([arXiv:2309.16697](https://arxiv.org/abs/2309.16697)); CodeWorkout notes repeated similar attempts as an anomaly MOSS cannot see ([arXiv:2407.17475](https://arxiv.org/abs/2407.17475)) | strong students; prior experience; easy problem | low weight, cohort-relative |
| Stylometric drift | within-student feature distance across submissions | Code stylometry features are informative ([USENIX 2015](https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/caliskan-islam)); but classroom attribution is near chance ([arXiv:2608.00227](https://arxiv.org/abs/2608.00227)) | legitimate style growth; new problem domain; LLM suggestions | weak, corroboration required |
| Collusion graph | weighted pair graph, clusters, cross-problem edges, temporal direction | JPlag clustering ([wiki](https://github.com/jplag/JPlag/wiki/8.-Clustering-of-Submissions)); Dolos graph UI ([repo](https://github.com/dodona-edu/dolos)); collusion quantified with MOSS ([arXiv:2407.15014](https://arxiv.org/abs/2407.15014)) | shared templates; pair programming; base code | human-review subgraph, not a verdict |
| Paste/AI markers | markdown fences, "Here is", Unicode arrows, inconsistent indentation, advanced constructs not taught, comment-language mismatch | Argus stylistic indicators; vendors use similar heuristics ([arXiv:2609.36073](https://arxiv.org/abs/2609.36073)) | copied from lecture notes; IDE snippets; bilingual students | weak reason code only |

**Recommendation:** compute per-student baselines and cohort-relative percentiles; combine at least two independent signals before ranking a student for review; attach reason codes and raw metrics; never auto-penalise. The current `analysis.json` anomaly schema (`score`, `reasons[{code,weight,detail}]`, `evidence`, `metrics`) is a good foundation; add fields rather than replace it.

---

## 6. Actionable recommendations for OJ Sentinel

### 6.1 Pipeline shape

```
snapshot
  -> normalise (comments/strings/identifiers; per-language)
  -> candidate generation (rare fingerprints / inverted index; base-code filter)
  -> pairwise scoring (winnowing + containment + AST + GST/longest fragment)
  -> evidence extraction (line/token ranges)
  -> clustering (weighted graph; union-find for small, spectral/Louvain for large)
  -> behaviour features (per-student baseline, cohort percentiles)
  -> ranking + reason codes
  -> human review UI / report
```

The key architectural rule: **candidate generation must stay sub-quadratic**. A 4,700-submission homework has ~11 million unordered pairs; an all-pairs Python loop is not viable. OJ Sentinel's rare-fingerprint index is the right pattern; add a SourcererCC-style token-frequency filter and cache ASTs.

### 6.2 Algorithms to adopt (priority order)

| Priority | Change | Why | Effort |
| --- | --- | --- | --- |
| P0 | Keep winnowing; make `k`, `w` configurable and record them in `analysis.json.config` | reproducibility; tune per language/program length | small |
| P0 | Add **base-code/template exclusions** (instructor solutions, starter files, ubiquitous fingerprints) | removes the largest false-positive source; JPlag/MOSS/CodeGrade all support it | medium |
| P0 | Add **longest matching token run** and **total overlap** to each pair, in addition to Jaccard/containment | distinguishes a short shared idiom from a copied block; Dolos's most useful metric | medium |
| P0 | Treat `0.35` as candidate threshold, add review/high-priority bands | current single threshold conflates recall with evidence | small |
| P1 | Add **Greedy String Tiling** (or a suffix-automaton/LCS approximation) on structural token sequences for candidate pairs | JPlag's core; produces explainable long matches and resists reordering better than set overlap | medium/large |
| P1 | Add **Python AST function-level diff** (pycode_similar-style) | catches renamed/reordered code that token winnowing misses; cheap on candidates | medium |
| P1 | Add **weighted clustering** (Dice/overlap edges; require multiple edges/problems; cluster strength) | current union-find is single-linkage and can chain; JPlag's clustering is more informative | medium |
| P2 | Add **NCD (gzip)** as a fallback for parse failures/unsupported languages | language-agnostic secondary signal; AC2 precedent | small |
| P2 | Add **CSV export** with pair metrics and evidence ranges | interoperable with teachers' spreadsheets; Dolos/CPD precedent | small |
| P2 | Add **evaluation harness** (synthetic transforms + hard negatives; precision@k) | makes threshold tuning empirical instead of anecdotal | medium |

### 6.3 Thresholds to test (starting points, not validated cutoffs)

These are for **calibration experiments**, not for shipping as policy. Record the exact metric definitions alongside the numbers.

| Signal / metric | Starting band | Rationale / source |
| --- | --- | --- |
| Candidate pair score (current composite) | `>= 0.35` for the queue | current code; high recall candidate generation |
| Review priority | `>= 0.60` composite, or containment `>= 0.70` with `shared >= 15` | separates "worth reading" from "long shared code" |
| High priority | `>= 0.75` composite, or normalized exact match `= 1.0` | requires human confirmation; still not proof |
| Longest matching fragment | `>= 30–40` tokens | below this, idioms/boilerplate dominate; test per problem |
| AST similarity (function-level) | `>= 0.80` with token containment `>= 0.60` | high structural similarity plus shared text |
| Base-code/document frequency | exclude fingerprints present in base code or in `> 5–10%` of class | MOSS/JPlag/CodeGrade practice; tune with `max_df` |
| Cluster edge | `>= 0.60` (Dice/overlap) | below this, clusters explode; JPlag's default clustering threshold is a different metric |
| Collusion group | `>= 3` members with `>= 2` edges each, or a pair appearing on `>= 2` problems | reduces one-off template coincidence |
| Burst | `>= 30` new lines in `<= 2 min`, or `>= 15` lines/min, or `>= 4` submissions/10 min vs student baseline `p < 0.01` | Argus's empirical burst rule; OJ Sentinel can only use submission snapshots, not commits |
| First-submission success | AC on attempt 1 **and** solve time `<= 10th` percentile **and** code length `>=` median | weak; strong students and prior experience are confounds |
| Consecutive rewrite | AST/token similarity `<= 0.30` after a gap `>= 30 min`, or rewrite ratio `>= 0.60` | paste-like jump; normal debugging can also rewrite heavily |
| Stylometric drift | robust distance `> 3` from the student's own baseline over `>= 3` submissions | weak; require another signal; classroom attribution is near chance |

**How to calibrate.** Build a labelled set of pairs/submissions: (a) synthetic plagiarism (rename identifiers, reorder statements, extract/re-inline functions, add dead code/comments, translate between equivalent constructs); (b) hard benign negatives (same problem, different approach; shared template; pair-programming pairs with permission; same student's iterative submissions); (c) real cases already adjudicated by the instructor. Report **precision at the review budget** (e.g. top 20/50/100 pairs), **recall at that budget**, and **false positives per 100 students**. Tune per problem, because short/structured problems naturally have higher similarity.

### 6.4 What to add to the report / UI

- **Pair view:** similarity, containment, Jaccard, AST similarity, longest fragment, total overlap, shared rare fingerprints, base-code markings, matched token/line ranges; side-by-side highlighting; "why this pair" text generated from metrics (not an LLM verdict).
- **Distribution view:** histogram of all pair scores per problem (JPlag precedent), so teachers see whether 0.7 is unusual *for this assignment*.
- **Cluster view:** graph of users/submissions with edge weights and cross-problem edges; cluster strength and member count; export as JSON/CSV.
- **Student view:** timeline of submissions (timestamps, statuses, code size, diff from previous), behaviour reason codes, and a clear "signals are not findings" banner.
- **Report export:** HTML (human), CSV (spreadsheet), JSON (machine); redact names/code by default per `docs/INTERFACES.md`; never include credentials or raw judge data.

### 6.5 Evaluation and operations

- **Benchmark harness:** generate synthetic transforms and hard negatives; run the full pipeline; store precision/recall and threshold curves per problem; keep fixtures free of real student data.
- **Monitoring:** track score distribution, cluster sizes, anomaly rates, and false-positive complaints per term; recalibrate when the assignment, language, or cohort changes.
- **Human workflow:** require a second reviewer for high-priority cases; give students the evidence and an opportunity to explain; document the chain from signal to conversation; do not put a numeric score on a student's record without context.
- **Privacy:** all similarity computation local; AI layer only sends snippets the teacher explicitly selects (per `docs/INTERFACES.md`); no third-party plagiarism/AI-detection services; reports redacted by default.
- **Performance:** target <10 min for 5,000 submissions; parallelise pair scoring; cap evidence extraction to top N pairs (current code does top 300); cache ASTs/tokens; avoid recomputing base-code fingerprints.

### 6.6 Licence and reuse

- **MIT-compatible inspiration/code:** Dolos (MIT), pycode_similar (MIT), Sherlock (Unlicense), SIM (BSD-3-Clause), PMD CPD (BSD-style), OSLL (MIT), CodeGrade/MOSS ideas (no code).
- **GPL:** JPlag (GPL-3.0), SourcererCC (GPL-3.0), AC2 (GPL-3.0), Pylint (GPL-2.0), Clone Digger (GPL-3.0). Reimplement algorithms from papers; do not copy source into OJ Sentinel without a deliberate relicensing decision.
- **Attribution:** if code from Dolos/pycode_similar is reused, keep the copyright/licence notice and document it in the repo.

---

## 7. What NOT to do

1. **Do not upload student code** to MOSS, Codequiry, CodeGrade, commercial AI detectors, or any cloud LLM by default. It violates the project's privacy rule and may violate institutional policy.
2. **Do not ship a binary "AI-generated" verdict.** Off-the-shelf detectors are near chance on code; code-specific classifiers age and are generator-specific; stylometry fails in classrooms. A false accusation is a serious harm.
3. **Do not use GPTZero/OpenAI-classifier-style tools on code.** OpenAI's own page says the classifier is unreliable on code; Pan et al. measured near-chance accuracy on Python.
4. **Do not use watermark detection on arbitrary student code.** Watermarks only exist if the generating model embedded them; obfuscation breaks N-gram schemes.
5. **Do not treat a similarity score as proof.** MOSS's own documentation says relying solely on scores is a misuse. Report "suspicious similarity", not "plagiarism".
6. **Do not auto-penalise, auto-report, or auto-grade** from any similarity/behaviour/AI score. Require human review and a student response.
7. **Do not use absolute, one-size-fits-all thresholds.** Short problems and template-heavy assignments have different baselines; calibrate per problem/cohort.
8. **Do not run all-pairs comparisons** at OJ scale; use candidate generation/indexing and cap evidence work.
9. **Do not infer authorship from a single short submission** or from style alone; classroom stylometry is near chance, and style changes with learning.
10. **Do not silently collect IP/device/keystroke data.** OJ Sentinel only has submitted snapshots; adding covert surveillance would be an ethical and legal escalation, not a feature.
11. **Do not copy GPL code into the MIT core**; reimplement from published algorithms and cite them.
12. **Do not put student names, usernames, code, or raw judge data in git, logs, screenshots, or PR text.**

---

## 8. Open questions for the team

1. **What does Hydro expose for `submit_at`?** Timezone/precision determine whether burst windows are meaningful. If timestamps are coarse (e.g. minute-level), use wider windows and avoid second-level claims.
2. **Are all submissions retained, or only latest per problem?** Behaviour signals need the full sequence; the snapshot spec suggests all records, but this should be verified on a fixture.
3. **What is the base-code/template policy?** Instructors need a way to mark reference solutions/starter files; without it, similarity scores will be dominated by shared code.
4. **What is the review budget?** A realistic teacher can inspect ~20–50 pairs per assignment; thresholds should be tuned to that budget, not to a hypothetical perfect classifier.
5. **Is there any labelled historical data** (adjudicated cases) that can be used for calibration without exposing student identities? If not, start with synthetic transforms and hard negatives.

---

## 9. Sources

All URLs were checked against the cited primary record on 2026-10-23. Some publishers block automated clients (ACM/OpenAI/Stanford returned 403/000 in this environment); an archived copy or repository mirror was used for verification where available. arXiv items are preprints unless stated. Vendor claims are labelled as such.

### Tools and algorithms

1. MOSS, *A System for Detecting Software Similarity* — <https://theory.stanford.edu/~aiken/moss/> (archived copy used for verification: <https://web.archive.org/web/2024/https://theory.stanford.edu/~aiken/moss/>).
2. Schleimer, Wilkerson & Aiken, *Winnowing: Local Algorithms for Document Fingerprinting*, ACM SIGMOD 2003 — <https://doi.org/10.1145/872757.872770>; PDF <http://theory.stanford.edu/~aiken/publications/papers/sigmod03.pdf>.
3. JPlag repository — <https://github.com/jplag/JPlag>.
4. JPlag wiki: supported languages — <https://github.com/jplag/JPlag/wiki/2.-Supported-Languages>.
5. JPlag wiki: report file generation — <https://github.com/jplag/JPlag/wiki/6.-Report-File-Generation>.
6. JPlag wiki: clustering of submissions — <https://github.com/jplag/JPlag/wiki/8.-Clustering-of-Submissions>.
7. JPlag `GreedyStringTiling.java` — <https://github.com/jplag/JPlag/blob/main/core/src/main/java/de/jplag/comparison/GreedyStringTiling.java>.
8. Prechelt, Malpohl & Philippsen, *JPlag: finding plagiarisms among a set of programs*, KIT tech report — <https://publikationen.bibliothek.kit.edu/542000>.
9. Dolos repository — <https://github.com/dodona-edu/dolos>.
10. Dolos algorithm documentation — <https://github.com/dodona-edu/dolos/blob/main/docs/about/algorithm.md>.
11. Dolos language documentation — <https://github.com/dodona-edu/dolos/blob/main/docs/about/languages.md>.
12. Maertens et al., *Discovering and exploring cases of educational source code plagiarism with Dolos*, SoftwareX 2024 — <https://doi.org/10.1016/j.softx.2024.101755>.
13. Sherlock plagiarism detector repository — <https://github.com/diogocabral/sherlock>.
14. SIM (Dick Grune), *The software and text similarity tester SIM* — <https://dickgrune.com/Programs/similarity_tester/>; source/licence <https://dickgrune.com/Programs/similarity_tester/sim.zip>.
15. Gitchell & Tran, *Sim: a utility for detecting similarity in computer programs*, SIGCSE 1999 — <https://doi.org/10.1145/384266.299783>.
16. SourcererCC repository — <https://github.com/Mondego/SourcererCC>; Sajnani et al., *SourcererCC: Scaling Code Clone Detection to Big Code* — <https://arxiv.org/abs/1512.06448>.
17. AC2 repository — <https://github.com/manuel-freire/ac2>.
18. Codequiry — <https://codequiry.com/>.
19. CodeGrade plagiarism documentation — <https://help.codegrade.com/setup-assignment/analyze-your-assignment/checking-for-plagiarism.md>; CodeGrade — <https://www.codegrade.com/>.
20. Sourcery — <https://www.sourcery.ai/>.
21. pycode_similar — <https://pypi.org/project/pycode-similar/>; repo <https://github.com/fyrestone/pycode_similar>.
22. Pylint similarity checker source — <https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/symilar.py>; R0801 docs — <https://pylint.readthedocs.io/en/latest/user_guide/messages/refactor/duplicate-code.html>.
23. PMD CPD documentation — <https://docs.pmd-code.org/latest/pmd_userdocs_cpd.html>.
24. OSLL code-plagiarism — <https://github.com/OSLL/code-plagiarism>.
25. Clone Digger — <https://pypi.org/project/clonedigger/>.

### AI-generated-code detection

26. Pan et al., *Assessing AI Detectors in Identifying AI-Generated Code: Implications for Education* — <https://arxiv.org/abs/2401.03676>.
27. Idialu et al., *Whodunit: Classifying Code as Human Authored or GPT-4 Generated — A case study on CodeChef problems* — <https://arxiv.org/abs/2403.04013>.
28. Shi et al., *Between Lines of Code: Unraveling the Distinct Patterns of Machine and Human Programmers* (DetectCodeGPT) — <https://arxiv.org/abs/2401.06461>.
29. Orel et al., *AICD Bench: A Challenging Benchmark for AI-Generated Code Detection* — <https://arxiv.org/abs/2602.02079>.
30. Demirok et al., *MultiAIGCD: A Comprehensive dataset for AI Generated Code Detection* — <https://arxiv.org/abs/2507.21693>.
31. Djire, *Code Detectors Have a Half-Life: Obsolescence and Metric Illusions in LLM-Generated Code Detection* — <https://arxiv.org/abs/2610.01664>.
32. Gurioli, Gabbrielli & Zacchiroli, *Is This You, LLM? Recognizing AI-written Programs with Multilingual Code Stylometry* — <https://arxiv.org/abs/2412.14611>.
33. Caliskan-Islam et al., *De-anonymizing Programmers via Code Stylometry*, USENIX Security 2015 — <https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/caliskan-islam>.
34. Yemets & Horváth, *Source Code Authorship Attribution Does Not Generalize from Competitions to Classrooms* — <https://arxiv.org/abs/2608.00227>.
35. Horváth, *Evaluating Static and Process Evidence for Code Authorship in Programming Education* — <https://arxiv.org/abs/2607.07400>.
36. Li et al., *RoPGen: Towards Robust Code Authorship Attribution via Automatic Coding Style Transformation* — <https://arxiv.org/abs/2202.06043>.
37. Mitchell et al., *DetectGPT: Zero-Shot Machine-Generated Text Detection using Probability Curvature* — <https://arxiv.org/abs/2301.11305>.
38. Sadasivan et al., *Can AI-Generated Text be Reliably Detected?* — <https://arxiv.org/abs/2303.11156>.
39. Liang et al., *GPT detectors are biased against non-native English writers* — <https://arxiv.org/abs/2304.02819>.
40. OpenAI, *New AI classifier for indicating AI-written text* — <https://openai.com/index/new-ai-classifier-for-indicating-ai-written-text/> (archived copy: <https://web.archive.org/web/2024/https://openai.com/index/new-ai-classifier-for-indicating-ai-written-text/>).
41. GPTZero FAQ — <https://gptzero.me/faq>.
42. Lee et al., *Who Wrote this Code? Watermarking for Code Generation* (SWEET) — <https://arxiv.org/abs/2305.15060>.
43. Li et al., *Towards Tracing Code Provenance with Code Watermarking* (CodeMark) — <https://arxiv.org/abs/2305.12461>.
44. Zhang et al., *Disappearing Ink: Obfuscation Breaks N-gram Code Watermarks in Theory and Practice* — <https://arxiv.org/abs/2507.05512>.
45. Suresh et al., *Is The Watermarking Of LLM-Generated Code Robust?* — <https://arxiv.org/abs/2403.17983>.
46. GitHub Docs, *Code referencing for GitHub Copilot* — <https://docs.github.com/en/copilot/concepts/completions/code-referencing>.

### Behaviour and academic integrity

47. Racovan et al., *Argus: Academic Integrity in the Era of Generative AI* — <https://arxiv.org/abs/2609.36073>.
48. Hicks et al., *An Approach to Detect Abnormal Submissions for CodeWorkout Dataset* — <https://arxiv.org/abs/2407.17475>.
49. Karnalim et al., *Plagiarism and AI Assistance Misuse in Web Programming: Unfair Benefits and Characteristics* — <https://arxiv.org/abs/2310.20104>.
50. Toba et al., *Inappropriate Benefits and Identification of ChatGPT Misuse in Programming Tests: A Controlled Experiment* — <https://arxiv.org/abs/2309.16697>.
51. Amin et al., *Revision-Aware Success Prediction from Multi-Attempt Programming Trajectories* — <https://arxiv.org/abs/2608.26169>.
52. Cohen et al., *Plagiarism deterrence for introductory programming* — <https://arxiv.org/abs/2206.02848>.
53. Dickey, *The Failure of Plagiarism Detection in Competitive Programming* — <https://arxiv.org/abs/2505.08244>.
54. Sağlam & Schmid, *Evaluating Software Plagiarism Detection in the Age of AI: Automated Obfuscation and Lessons for Academic Integrity* — <https://arxiv.org/abs/2505.20158>.
55. Maisch et al., *Same Same But Different: Preventing Refactoring Attacks on Software Plagiarism Detection* — <https://arxiv.org/abs/2510.25057>.
56. Cheers, Lin & Smith, *Evaluating the robustness of source code plagiarism detection tools to pervasive plagiarism-hiding modifications* — <https://arxiv.org/abs/2102.03997>.
57. Parthasarathy et al., *Influence of Personality Traits on Plagiarism Through Collusion in Programming Assignments* — <https://arxiv.org/abs/2407.15014>.
58. Rau, Brown & Edwards, *Parse Tree Tracking Through Time for Programming Process Analysis at Scale* — <https://arxiv.org/abs/2509.03668>.
59. Gao, Marwan & Price, *Early Performance Prediction using Interpretable Patterns in Programming Process Data* — <https://arxiv.org/abs/2102.05765>.
60. de Souza Santos et al., *LLM Use, Cheating, and Academic Integrity in Software Engineering Education* — <https://arxiv.org/abs/2603.17060>.
61. Kannam et al., *Code Interviews: Design and Evaluation of a More Authentic Assessment for Introductory Programming Assignments* — <https://arxiv.org/abs/2410.01010>.

---

## 10. Recommended next steps (concrete)

1. **Ship base-code/template exclusion before adding more similarity metrics.** Ingest instructor solutions, starter code and previous-semester submissions; mark their fingerprints and subtract them. This is the single largest false-positive reduction available and is standard practice in MOSS/JPlag/CodeGrade.
2. **Split the thresholds.** Keep `0.35` as the candidate queue; add a `review` band around `0.60` and a `high priority` band around `0.75`, but calibrate them per problem on a labelled set. Record the exact metric definitions in `analysis.json.config`.
3. **Add two Dolos-style pair metrics first: longest matching fragment and total overlap.** They are cheap on candidate pairs and make evidence far more actionable than a single composite score.
4. **Prototype structural matching on candidates only.** Implement Greedy String Tiling (or an LCS/suffix-automaton approximation) over AST node-type sequences for Python; keep the O(n²) work limited to the candidate set. Measure precision@50 against the current composite before making it default.
5. **Make clustering weighted and multi-evidence.** Keep union-find for small sets, but add edge weights, cross-problem edges, temporal direction, and a cluster-strength/density gate. Require ≥3 members with ≥2 edges each, or a pair on ≥2 problems, before surfacing a cluster.
6. **Implement behaviour features behind reason codes, not a single "cheating score".** Start with `max_10min`/`burst_ratio` (already in the schema), consecutive-submission AST rewrite + gap, first-attempt AC percentile, and within-student style distance. Require corroboration and show benign explanations in the UI.
7. **Do not build an AI detector this round.** Add an optional "AI-assistance indicators" panel only after the behaviour/stylometry calibration exists; keep it local, teacher-initiated, `insufficient_evidence` by default, and never auto-penalise. If an LLM is used, restrict it to explaining teacher-selected evidence.
8. **Build the evaluation harness now.** Synthetic transforms + hard benign negatives + adjudicated cases; report precision at the review budget and false positives per 100 students. This is the only way to choose thresholds defensibly.
9. **Do a licence/privacy review before reusing code.** Dolos (MIT), pycode_similar (MIT) and Sherlock (Unlicense) are compatible; JPlag/SourcererCC/AC2/Pylint are GPL and require reimplementation or a deliberate relicensing decision. Keep all student data under `data/` and out of git/logs.
