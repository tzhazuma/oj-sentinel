from oj_sentinel.analysis.normalize import build_profile
from oj_sentinel.analysis.similarity import cluster_pairs, compare, find_pairs

SRC_A = """def solve(values):
    total = 0
    for value in values:
        if value % 2 == 0:
            total += value
        else:
            total -= value
    return total
"""

SRC_B = """def solve(items):
    acc = 0
    for item in items:
        if item % 2 == 0:
            acc += item
        else:
            acc -= item
    return acc
"""

SRC_C = """import sys
data = sys.stdin.read().split()
print(len(data))
"""


def test_exact_copy_scores_one():
    a = build_profile("a", 1, "P", SRC_A)
    b = build_profile("b", 2, "P", SRC_A)
    p = compare(a, b)
    assert p.exact and p.score == 1.0
    assert p.same_user is False


def test_renamed_copy_is_high():
    a = build_profile("a", 1, "P", SRC_A)
    b = build_profile("b", 2, "P", SRC_B)
    p = compare(a, b)
    assert p.score > 0.75, p.score
    assert p.containment > 0.8


def test_unrelated_code_is_low():
    a = build_profile("a", 1, "P", SRC_A)
    c = build_profile("c", 3, "P", SRC_C)
    assert compare(a, c).score < 0.2


def test_find_pairs_and_cluster():
    profiles = [
        build_profile("a", 1, "P", SRC_A),
        build_profile("b", 2, "P", SRC_B),
        build_profile("c", 3, "P", SRC_A),
        build_profile("d", 4, "P", SRC_C),
    ]
    pairs = find_pairs(profiles, threshold=0.3, with_evidence=False)
    keys = {frozenset((p.rid_a, p.rid_b)) for p in pairs}
    assert frozenset(("a", "b")) in keys
    assert frozenset(("a", "c")) in keys
    clusters = cluster_pairs(pairs, threshold=0.3)
    assert any(set(c["members"]) >= {"a", "b", "c"} for c in clusters)


def test_same_user_not_candidate():
    p1 = build_profile("x1", 9, "P", SRC_A)
    p2 = build_profile("x2", 9, "P", SRC_A)
    pairs = find_pairs([p1, p2], threshold=0.1, with_evidence=False)
    assert pairs == []
