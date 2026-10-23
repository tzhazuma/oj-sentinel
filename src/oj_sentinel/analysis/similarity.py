"""Pairwise similarity and clustering built on :mod:`normalize` profiles."""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from .normalize import CodeProfile, ast_cosine, style_distance


@dataclass
class Pair:
    problem: str
    rid_a: str
    rid_b: str
    uid_a: int
    uid_b: int
    username_a: str = ""
    username_b: str = ""
    score: float = 0.0
    jaccard: float = 0.0
    containment: float = 0.0
    ast_cosine: float = 0.0
    codebert_cosine: float | None = None
    longest_fragment: int = 0
    token_coverage: float = 0.0
    distinctive_ratio: float = 1.0
    template_only: bool = False
    shared: int = 0
    size_a: int = 0
    size_b: int = 0
    same_user: bool = False
    exact: bool = False
    method: str = "winnowing+ast"
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": self.problem,
            "rid_a": self.rid_a, "rid_b": self.rid_b,
            "uid_a": self.uid_a, "uid_b": self.uid_b,
            "username_a": self.username_a, "username_b": self.username_b,
            "score": round(self.score, 4),
            "jaccard": round(self.jaccard, 4),
            "containment": round(self.containment, 4),
            "ast_cosine": round(self.ast_cosine, 4),
            "codebert_cosine": (
                round(self.codebert_cosine, 4)
                if self.codebert_cosine is not None
                else None
            ),
            "longest_fragment": self.longest_fragment,
            "token_coverage": round(self.token_coverage, 4),
            "distinctive_ratio": round(self.distinctive_ratio, 4),
            "template_only": self.template_only,
            "shared": self.shared, "size_a": self.size_a, "size_b": self.size_b,
            "same_user": self.same_user, "exact": self.exact, "method": self.method,
            "evidence": self.evidence,
        }


def _code_hash(code: str) -> str:
    norm = "\n".join(ln.strip() for ln in code.strip().splitlines() if ln.strip())
    return hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()[:20]


def line_evidence(a: CodeProfile, b: CodeProfile, limit: int = 6) -> list[dict[str, Any]]:
    """Matching line blocks between two submissions (for human review)."""
    alines = a.code.splitlines()
    blines = b.code.splitlines()
    anorm = [ln.strip() for ln in alines]
    bnorm = [ln.strip() for ln in blines]
    sm = difflib.SequenceMatcher(None, anorm, bnorm, autojunk=False)
    blocks: list[dict[str, Any]] = []
    for m in sm.get_matching_blocks():
        if m.size < 2:
            continue
        snippet = "\n".join(alines[m.a : m.a + min(m.size, 12)])
        if not snippet.strip():
            continue
        blocks.append(
            {
                "a_start": m.a + 1,
                "a_end": m.a + m.size,
                "b_start": m.b + 1,
                "b_end": m.b + m.size,
                "lines": m.size,
                "snippet": snippet[:1200],
            }
        )
    blocks.sort(key=lambda x: -x["lines"])
    return blocks[:limit]


def fragment_metrics(a_tokens: list[str], b_tokens: list[str], *, min_block: int = 8) -> tuple[int, float]:
    """Longest matching token run and coverage (Greedy String Tiling style).

    Uses a k-gram index instead of difflib: for 500-3,000-token submissions this
    is effectively linear and avoids the O(n*m) worst case that made a full
    class analysis unusably slow.  ``min_block`` is the minimum meaningful
    copied run (the research recommendation is 30-40 tokens for review).
    """
    if not a_tokens or not b_tokens:
        return 0, 0.0
    if a_tokens == b_tokens:
        return len(a_tokens), 1.0
    k = max(3, min(min_block, len(a_tokens), len(b_tokens)))
    index: dict[tuple[str, ...], list[int]] = {}
    for j in range(len(b_tokens) - k + 1):
        key = tuple(b_tokens[j : j + k])
        bucket = index.get(key)
        if bucket is None:
            index[key] = [j]
        elif len(bucket) < 24:  # cap pathological repeated-token buckets
            bucket.append(j)
    best = 0
    for i in range(len(a_tokens) - k + 1):
        bucket = index.get(tuple(a_tokens[i : i + k]))
        if not bucket:
            continue
        for j in bucket:
            run = k
            while (
                i + run < len(a_tokens)
                and j + run < len(b_tokens)
                and a_tokens[i + run] == b_tokens[j + run]
            ):
                run += 1
            if run > best:
                best = run
    return best, min(1.0, best / max(1, min(len(a_tokens), len(b_tokens))))


def compare(a: CodeProfile, b: CodeProfile, *, evidence: bool = False,
            fps_a: set[int] | None = None, fps_b: set[int] | None = None,
            compute_fragment: bool = True) -> Pair:
    pair = Pair(
        problem=a.problem, rid_a=a.rid, rid_b=b.rid, uid_a=a.user_id, uid_b=b.user_id,
        same_user=a.user_id == b.user_id, size_a=len(a.fingerprints), size_b=len(b.fingerprints),
    )
    if a.normalized_text and a.normalized_text == b.normalized_text:
        pair.score = 1.0
        pair.jaccard = pair.containment = pair.ast_cosine = 1.0
        pair.shared = len(a.fingerprints)
        pair.longest_fragment = min(len(a.tokens), len(b.tokens))
        pair.token_coverage = 1.0
        pair.exact = True
        pair.method = "exact-normalised"
        if evidence:
            pair.evidence = line_evidence(a, b)
        return pair
    sa = set(fps_a) if fps_a is not None else a.fingerprints
    sb = set(fps_b) if fps_b is not None else b.fingerprints
    pair.distinctive_ratio = (
        min(len(sa), len(sb)) / max(1, min(len(a.fingerprints), len(b.fingerprints)))
    )
    pair.template_only = bool(a.fingerprints and b.fingerprints and (not sa or not sb))
    if not sa or not sb:
        # Everything in common is class boilerplate: keep the pair out of the
        # review queue unless the whole normalised file is identical.
        return pair
    inter = len(sa & sb)
    union = len(sa | sb)
    pair.shared = inter
    pair.jaccard = inter / union if union else 0.0
    pair.containment = inter / min(len(sa), len(sb)) if sa and sb else 0.0
    pair.ast_cosine = ast_cosine(a.ast_features, b.ast_features)
    if compute_fragment:
        pair.longest_fragment, pair.token_coverage = fragment_metrics(a.tokens, b.tokens)
        min_tokens = max(1, min(len(a.tokens), len(b.tokens)))
        fragment_score = min(1.0, pair.longest_fragment / min_tokens)
        # Research-guided weights: distinctive-token overlap + long contiguous
        # runs matter more than raw AST bag-of-nodes similarity.
        score = (
            0.40 * pair.containment
            + 0.20 * pair.jaccard
            + 0.13 * pair.ast_cosine
            + 0.27 * fragment_score
        )
        # A short contiguous match is weak; require a meaningful run for top ranks.
        if pair.longest_fragment < 12 and not pair.exact:
            score *= 0.82
    else:
        # Cheap prefilter score; fragment is computed only for the best
        # candidates (a k-gram run is not free at class scale).
        score = 0.55 * pair.containment + 0.27 * pair.jaccard + 0.18 * pair.ast_cosine
    # Short programs are naturally similar; damp the score a little.
    min_tokens = min(len(a.tokens), len(b.tokens))
    if min_tokens < 40:
        score *= 0.75
    if style_distance(a.style, b.style) < 0.18:
        score = min(1.0, score + 0.02)
    if pair.template_only:
        score *= 0.55
    pair.score = max(0.0, min(1.0, score))
    if evidence and pair.score >= 0.35:
        pair.evidence = line_evidence(a, b)
    return pair


def _candidate_pairs(profiles: list[CodeProfile], *, max_df_ratio: float = 0.08,
                     min_shared: int = 4) -> tuple[set[tuple[int, int]], dict[str, int]]:
    """Rare-fingerprint inverted index (MOSS-style anchoring).

    Fingerprints shared by a large share of the class are boilerplate; using
    them for candidates would produce O(n^2) noise.  Only rare anchors vote.
    """
    n = len(profiles)
    if n < 2:
        return set(), {}
    df: dict[int, int] = {}
    for p in profiles:
        for fp in p.fingerprints:
            df[fp] = df.get(fp, 0) + 1
    max_df = max(8, int(n * max_df_ratio))
    index: dict[int, list[int]] = {}
    for i, p in enumerate(profiles):
        for fp in p.fingerprints:
            if 1 < df.get(fp, 0) <= max_df:
                index.setdefault(fp, []).append(i)
    counts: dict[tuple[int, int], int] = {}
    for fp, ids in index.items():
        if len(ids) < 2 or len(ids) > 60:
            continue
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                i, j = ids[x], ids[y]
                if profiles[i].user_id == profiles[j].user_id:
                    continue
                key = (i, j)
                counts[key] = counts.get(key, 0) + 1
    candidates = {
        key for key, shared in counts.items()
        if shared >= max(min_shared, int(0.02 * min(len(profiles[key[0]].fingerprints), len(profiles[key[1]].fingerprints))))
    }
    # exact-copy buckets (same normalised code) are always candidates
    buckets: dict[str, list[int]] = {}
    for i, p in enumerate(profiles):
        buckets.setdefault(_code_hash(p.code), []).append(i)
    for ids in buckets.values():
        if len(ids) > 1:
            for x in range(len(ids)):
                for y in range(x + 1, len(ids)):
                    if profiles[ids[x]].user_id != profiles[ids[y]].user_id:
                        candidates.add((ids[x], ids[y]))
    return candidates, df


def find_pairs(profiles: list[CodeProfile], *, threshold: float = 0.6,
               with_evidence: bool = True, max_pairs: int = 20000) -> list[Pair]:
    if len(profiles) < 2:
        return []
    candidates, df = _candidate_pairs(profiles)
    # Base-code / template exclusion: fingerprints shared by a large share of
    # the class are boilerplate and must not inflate a pair's score.
    max_df = max(8, int(len(profiles) * 0.06))
    distinctive = [
        {fp for fp in p.fingerprints if df.get(fp, 0) <= max_df}
        for p in profiles
    ]
    # Phase 1: cheap overlap score for every candidate.  Phase 2: compute the
    # expensive longest-run metric only for the most promising candidates.
    cheap: list[Pair] = []
    for i, j in candidates:
        a, b = profiles[i], profiles[j]
        if a.user_id == b.user_id:
            continue
        p = compare(a, b, evidence=False, fps_a=distinctive[i], fps_b=distinctive[j],
                    compute_fragment=False)
        # The fragment term can add at most 0.27; keep anything that could
        # possibly cross the threshold.
        if p.score + 0.27 < threshold:
            continue
        # Tiny fragments and largely-boilerplate overlap are weak evidence.
        if not p.exact and p.shared < 4:
            continue
        if not p.exact and min(len(a.tokens), len(b.tokens)) < 30 and p.score < 0.8:
            continue
        cheap.append(p)
    cheap.sort(key=lambda p: (-p.score, -p.shared))
    by_rid = {p.rid: p for p in profiles}
    index_of = {id(p): i for i, p in enumerate(profiles)}
    fragment_limit = 400
    scored: list[Pair] = []
    for index, p in enumerate(cheap):
        a, b = by_rid.get(p.rid_a), by_rid.get(p.rid_b)
        if a is None or b is None:
            continue
        if index < fragment_limit:
            p = compare(a, b, evidence=False,
                        fps_a=distinctive[index_of[id(a)]], fps_b=distinctive[index_of[id(b)]])
        if p.score < threshold:
            continue
        scored.append(p)
    scored.sort(key=lambda p: -p.score)
    if len(scored) > max_pairs:
        scored = scored[:max_pairs]
    if with_evidence:
        by_rid = {p.rid: p for p in profiles}
        for p in scored[:300]:
            a, b = by_rid.get(p.rid_a), by_rid.get(p.rid_b)
            if a and b:
                p.evidence = line_evidence(a, b)
    return scored


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_pairs(pairs: Iterable[Pair], *, threshold: float = 0.55) -> list[dict[str, Any]]:
    uf = UnionFind()
    involved: dict[str, Pair] = {}
    for p in pairs:
        if p.score < threshold or p.same_user:
            continue
        uf.union(p.rid_a, p.rid_b)
        involved[p.rid_a] = p
        involved[p.rid_b] = p
    groups: dict[str, list[str]] = {}
    for rid in uf.parent:
        groups.setdefault(uf.find(rid), []).append(rid)
    out = []
    for root, rids in groups.items():
        if len(rids) < 2:
            continue
        out.append({"root": root, "members": sorted(rids), "size": len(rids)})
    out.sort(key=lambda g: -g["size"])
    return out
