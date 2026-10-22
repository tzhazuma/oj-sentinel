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


def compare(a: CodeProfile, b: CodeProfile, *, evidence: bool = False) -> Pair:
    pair = Pair(
        problem=a.problem, rid_a=a.rid, rid_b=b.rid, uid_a=a.user_id, uid_b=b.user_id,
        same_user=a.user_id == b.user_id, size_a=len(a.fingerprints), size_b=len(b.fingerprints),
    )
    if a.normalized_text and a.normalized_text == b.normalized_text:
        pair.score = 1.0
        pair.jaccard = pair.containment = pair.ast_cosine = 1.0
        pair.shared = len(a.fingerprints)
        pair.exact = True
        pair.method = "exact-normalised"
        if evidence:
            pair.evidence = line_evidence(a, b)
        return pair
    sa, sb = a.fingerprints, b.fingerprints
    if not sa or not sb:
        return pair
    inter = len(sa & sb)
    union = len(sa | sb)
    pair.shared = inter
    pair.jaccard = inter / union if union else 0.0
    pair.containment = inter / min(len(sa), len(sb)) if sa and sb else 0.0
    pair.ast_cosine = ast_cosine(a.ast_features, b.ast_features)
    score = 0.58 * pair.containment + 0.27 * pair.jaccard + 0.15 * pair.ast_cosine
    # Short programs are naturally similar; damp the score a little.
    min_tokens = min(len(a.tokens), len(b.tokens))
    if min_tokens < 40:
        score *= 0.75
    if style_distance(a.style, b.style) < 0.18:
        score = min(1.0, score + 0.02)
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
    candidates, _df = _candidate_pairs(profiles)
    scored: list[Pair] = []
    for i, j in candidates:
        a, b = profiles[i], profiles[j]
        if a.user_id == b.user_id:
            continue
        p = compare(a, b, evidence=False)
        if p.score < threshold:
            continue
        # Tiny fragments and largely-boilerplate overlap are weak evidence.
        if not p.exact and p.shared < 4:
            continue
        if not p.exact and min(len(a.tokens), len(b.tokens)) < 30 and p.score < 0.8:
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
