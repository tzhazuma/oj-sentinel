from oj_sentinel.analysis.normalize import (
    ast_cosine,
    build_profile,
    kgram_hashes,
    style_distance,
    tokenize_python,
    winnow,
)


def test_identifier_and_string_normalisation():
    a = "def add(x, y):\n    # comment\n    return x + y\n"
    b = "def add(a, b):\n    return a + b\n"
    ta, _, ca, ea = tokenize_python(a)
    tb, _, _, _ = tokenize_python(b)
    assert not ea
    assert ta == tb
    assert ca == ["# comment"]
    assert "x" not in ta and "V" in ta


def test_docstrings_are_ignored():
    a = '"""module doc"""\ndef f():\n    """doc"""\n    return 1\n'
    b = "def f():\n    return 1\n"
    assert tokenize_python(a)[0] == tokenize_python(b)[0]


def test_winnowing_is_stable_subset():
    toks = list("abcdefghijklmnop")
    hashes = kgram_hashes(toks, k=4)
    fps = winnow(hashes, w=3)
    assert fps and fps <= set(hashes)


def test_profile_similarity_basics():
    a = build_profile("a", 1, "P", "def f(x):\n    return x + 1\n")
    b = build_profile("b", 2, "P", "def f(y):\n    return y + 1\n")
    c = build_profile("c", 3, "P", "import sys\nfor line in sys.stdin:\n    print(line.upper())\n")
    assert a.normalized_text == b.normalized_text
    assert a.normalized_text != c.normalized_text
    assert a.parse_ok


def test_ast_cosine_and_style_distance():
    a = build_profile("a", 1, "P", "for i in range(3):\n    print(i)\n")
    b = build_profile("b", 2, "P", "for j in range(5):\n    print(j)\n")
    c = build_profile("c", 3, "P", "class X:\n    pass\n")
    assert ast_cosine(a.ast_features, b.ast_features) > 0.9
    assert ast_cosine(a.ast_features, c.ast_features) < 0.9
    assert style_distance(a.style, a.style) == 0


def test_c_family_tokenisation_and_features():
    c = """#include <stdio.h>
int main(void) {
    int n;
    scanf("%d", &n);
    for (int i = 0; i < n; i++) {
        printf("%d\\n", i);
    }
    return 0;
}
"""
    cpp = """#include <iostream>
using namespace std;
int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; ++i) cout << i << "\\n";
    return 0;
}
"""
    a = build_profile("c1", 1, "P", c, "C")
    b = build_profile("c2", 2, "P", cpp, "C++")
    assert a.family == "c" and b.family == "cpp"
    assert "scanf" in a.tokens and "printf" in a.tokens
    assert "cin" in b.tokens and "cout" in b.tokens
    assert a.ast_features["c_functions"] >= 1
    assert b.ast_features["c_includes"] >= 1
    # comments and literals are normalised away
    assert '"S"' in a.tokens
    assert "#include" not in " ".join(a.tokens)
