"""Code normalisation, fingerprints and structural features.

The design borrows the useful parts of MOSS (winnowing fingerprints), JPlag
(token normalisation) and Dolos (local, explainable pairs) while remaining
fully offline: student code never leaves the machine.
"""
from __future__ import annotations

import ast
import hashlib
import builtins
import io
import keyword
import re
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

BUILTIN_NAMES = {n for n in dir(builtins) if not n.startswith("_")} | {
    "True", "False", "None", "self", "cls", "__name__", "__main__",
}
KEEP_NAMES = BUILTIN_NAMES

C_STYLE_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
HASH_COMMENT = re.compile(r"#[^\n]*")

C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double",
    "else", "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long",
    "register", "restrict", "return", "short", "signed", "sizeof", "static", "struct",
    "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "_Bool",
    "class", "namespace", "template", "typename", "public", "private", "protected",
    "virtual", "override", "new", "delete", "this", "nullptr", "true", "false",
    "using", "try", "catch", "throw", "constexpr", "noexcept", "operator", "friend",
    "std", "cout", "cin", "cerr", "endl", "string", "vector", "map", "set", "pair",
    "printf", "scanf", "malloc", "calloc", "realloc", "free", "fopen", "fclose",
    "fgets", "fputs", "strlen", "strcmp", "strcpy", "memset", "memcpy", "qsort",
}
C_KEYWORDS_LOWER = {k.lower() for k in C_KEYWORDS}

C_INCLUDE = re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.M)
C_DEFINE = re.compile(r"^\s*#\s*define\s+(\w+)", re.M)
C_FUNC = re.compile(r"\b(?:\w[\w:<>\*&\s]*?)\s+(\w+)\s*\([^;{}]*\)\s*(?:const\s*)?\{")
C_CLASS = re.compile(r"\b(?:class|struct)\s+(\w+)")
C_LOOP = re.compile(r"\b(for|while)\s*\(")
C_IF = re.compile(r"\b(if|switch)\s*\(")


@dataclass
class CodeProfile:
    rid: str
    user_id: int
    problem: str
    code: str
    language: str = "python"
    family: str = "python"
    tokens: list[str] = field(default_factory=list)
    raw_tokens: list[str] = field(default_factory=list)
    fingerprints: set[int] = field(default_factory=set)
    kgrams: set[int] = field(default_factory=set)
    ast_features: dict[str, float] = field(default_factory=dict)
    style: dict[str, float] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)
    line_count: int = 0
    char_count: int = 0
    parse_ok: bool = False
    error: str = ""

    @property
    def normalized_text(self) -> str:
        return " ".join(self.tokens)


def _is_docstring_token(tok: tokenize.TokenInfo, prev_significant: str | None) -> bool:
    if tok.type != tokenize.STRING:
        return False
    text = tok.string.lstrip("rRbBuUfF")
    triple = text.startswith('"""') or text.startswith("'''")
    return triple and prev_significant in (None, "INDENT", "NEWLINE")


def tokenize_python(code: str) -> tuple[list[str], list[str], list[str], str]:
    """Return (normalised tokens, raw tokens, comments, error)."""
    tokens: list[str] = []
    raw: list[str] = []
    comments: list[str] = []
    prev_sig: str | None = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            ttype, val = tok.type, tok.string
            if ttype in (tokenize.ENCODING, tokenize.ENDMARKER, tokenize.COMMENT,
                         tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
                if ttype == tokenize.COMMENT:
                    comments.append(val)
                continue
            if ttype == tokenize.STRING:
                if _is_docstring_token(tok, prev_sig):
                    prev_sig = "DOCSTRING"
                    continue
                tokens.append('"S"')
                raw.append(val)
                prev_sig = "STRING"
                continue
            if ttype == tokenize.NUMBER:
                tokens.append("0")
                raw.append(val)
                prev_sig = "NUMBER"
                continue
            if ttype == tokenize.NAME:
                if keyword.iskeyword(val):
                    tokens.append(val)
                elif val in KEEP_NAMES:
                    tokens.append(val)
                else:
                    tokens.append("V")
                raw.append(val)
                prev_sig = "NAME"
                continue
            if ttype == tokenize.OP:
                tokens.append(val)
                raw.append(val)
                prev_sig = "OP"
                continue
            if ttype in (tokenize.NEWLINE,):
                tokens.append("\n")
                prev_sig = "NEWLINE"
                continue
            if ttype == tokenize.ERRORTOKEN:
                if val.isspace():
                    continue
                tokens.append(val)
                raw.append(val)
                prev_sig = "OP"
                continue
            if ttype == tokenize.FSTRING_START if hasattr(tokenize, "FSTRING_START") else False:  # type: ignore[attr-defined]
                tokens.append('"S"')
                raw.append(val)
                prev_sig = "STRING"
                continue
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        return _squeeze_newlines(tokens), raw, comments, str(exc)
    return _squeeze_newlines(tokens), raw, comments, ""


def _squeeze_newlines(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        if t == "\n":
            if out and out[-1] != "\n":
                out.append(t)
        else:
            out.append(t)
    while out and out[0] == "\n":
        out.pop(0)
    while out and out[-1] == "\n":
        out.pop()
    return out


def language_family(language: str) -> str:
    """Map a Hydro language label to a comparison family."""
    text = (language or "").strip().lower()
    if text.startswith("py") or "python" in text:
        return "python"
    if "c++" in text or "cpp" in text or "g++" in text or "clang++" in text:
        return "cpp"
    if text in {"c", "c11", "c99", "gnu c", "gcc", "clang"} or text.startswith("c ") or text.endswith(" c"):
        return "c"
    return "other"


def tokenize_generic(code: str, family: str = "other") -> list[str]:
    code = C_STYLE_COMMENT.sub(" ", code)
    if family not in {"python"}:
        code = HASH_COMMENT.sub(" ", code)
    code = re.sub(r'"(?:\\.|[^"\\])*"', " STR ", code)
    code = re.sub(r"'(?:\\.|[^'\\])*'", " CHR ", code)
    code = re.sub(r"\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[fFlLuU]*)\b", " 0 ", code)
    raw = re.findall(r"[A-Za-z_]\w*|[^\s\w]", code)
    out: list[str] = []
    for tok in raw:
        if tok == "STR":
            out.append('"S"')
        elif tok == "CHR":
            out.append("'C'")
        elif re.fullmatch(r"[A-Za-z_]\w*", tok):
            out.append(tok if tok.lower() in C_KEYWORDS_LOWER else "V")
        else:
            out.append(tok)
    return out


def c_family_features(code: str, family: str) -> dict[str, float]:
    includes = C_INCLUDE.findall(code)
    defines = C_DEFINE.findall(code)
    funcs = C_FUNC.findall(code)
    classes = C_CLASS.findall(code)
    lines = [ln for ln in code.splitlines() if ln.strip()]
    feats = {
        "includes": float(len(includes)),
        "defines": float(len(defines)),
        "functions": float(len(funcs)),
        "classes": float(len(classes)),
        "loops": float(len(C_LOOP.findall(code))),
        "conditionals": float(len(C_IF.findall(code))),
        "pointers": float(code.count("*") + code.count("->")),
        "malloc": float(len(re.findall(r"\b(malloc|calloc|realloc|new)\b", code))),
        "printf": float(len(re.findall(r"\b(printf|cout|cerr|puts|fputs)\b", code))),
        "scanf": float(len(re.findall(r"\b(scanf|cin|gets|fgets|getline)\b", code))),
        "templates": float(len(re.findall(r"\btemplate\s*<", code))),
        "stl": float(len(re.findall(r"\b(vector|map|set|unordered_map|unordered_set|queue|stack|deque|algorithm)\b", code))),
        "loc": float(len(lines)),
        "includes_text": float(sum(len(x) for x in includes)),
    }
    return {f"c_{k}": v for k, v in feats.items()}


def _stable_token_hash(tokens: list[str]) -> int:
    """Stable 64-bit hash; Python's ``hash`` is randomized per process.

    Winnowing keeps the *minimum* hash in every window, so a randomized hash
    changes which k-grams survive and made pair counts differ between runs.
    """
    payload = "\x1f".join(tokens).encode("utf-8", "replace")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def kgram_hashes(tokens: list[str], k: int = 5) -> list[int]:
    if len(tokens) < k:
        return [_stable_token_hash(tokens)] if tokens else []
    out: list[int] = []
    for i in range(len(tokens) - k + 1):
        out.append(_stable_token_hash(tokens[i : i + k]))
    return out


def winnow(hashes: list[int], w: int = 4) -> set[int]:
    """MOSS-style winnowing: keep the minimum hash in each window of w."""
    if not hashes:
        return set()
    if len(hashes) <= w:
        return {min(hashes)}
    out: set[int] = set()
    for i in range(len(hashes) - w + 1):
        out.add(min(hashes[i : i + w]))
    return out


def ast_features(code: str) -> tuple[dict[str, float], bool, str]:
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError) as exc:
        return {}, False, str(exc)
    counts: Counter[str] = Counter()
    depth = 0
    max_depth = 0

    class V(ast.NodeVisitor):
        def generic_visit(self, node):
            nonlocal depth, max_depth
            counts[type(node).__name__] += 1
            depth += 1
            max_depth = max(max_depth, depth)
            super().generic_visit(node)
            depth -= 1

    V().visit(tree)
    total = max(1, sum(counts.values()))
    feats: dict[str, float] = {f"n_{k}": v / total for k, v in counts.items()}
    for name in ("FunctionDef", "ClassDef", "For", "While", "If", "Try", "With", "Lambda",
                 "ListComp", "DictComp", "SetComp", "GeneratorExp", "Call", "Attribute",
                 "Subscript", "Return", "Yield", "Import", "ImportFrom", "Assign", "AugAssign"):
        feats[f"c_{name}"] = float(counts.get(name, 0))
    feats["max_depth"] = float(max_depth)
    feats["loc"] = float(len(code.splitlines()))
    feats["total_nodes"] = float(total)
    return feats, True, ""


def style_features(code: str, comments: list[str]) -> dict[str, float]:
    lines = code.splitlines()
    n = max(1, len(lines))
    nonempty = [ln for ln in lines if ln.strip()]
    comment_chars = sum(len(c) for c in comments)
    names = re.findall(r"\b[A-Za-z_]\w*\b", code)
    snake = sum(1 for x in names if "_" in x and x.lower() == x)
    camel = sum(1 for x in names if re.search(r"[a-z][A-Z]", x))
    defs = re.findall(r"def\s+([A-Za-z_]\w*)", code)
    docstrings = len(re.findall(r'^\s*(?:"""|\'\'\')', code, re.M))
    fstrings = len(re.findall(r"\bf['\"]", code))
    type_hints = len(re.findall(r"def\s+\w+\s*\([^)]*:\s*\w+", code))
    semicolons = code.count(";")
    return {
        "lines": float(n),
        "nonempty_lines": float(len(nonempty)),
        "avg_line_len": sum(len(x) for x in nonempty) / max(1, len(nonempty)),
        "max_line_len": float(max((len(x) for x in lines), default=0)),
        "comment_lines": float(len(comments)),
        "comment_ratio": comment_chars / max(1, len(code)),
        "snake_case": float(snake),
        "camel_case": float(camel),
        "def_count": float(len(defs)),
        "docstrings": float(docstrings),
        "fstrings": float(fstrings),
        "type_hints": float(type_hints),
        "semicolons": float(semicolons),
        "main_guard": 1.0 if "__main__" in code else 0.0,
        "sys_stdin": 1.0 if "sys.stdin" in code else 0.0,
        "try_except": 1.0 if "try:" in code else 0.0,
        "markdown_fence": 1.0 if "```" in code else 0.0,
        "unicode_arrows": 1.0 if re.search(r"[→←⇒]", code) else 0.0,
        "tabs": 1.0 if "\t" in code else 0.0,
    }


def build_profile(rid: str, user_id: int, problem: str, code: str, language: str = "python") -> CodeProfile:
    family = language_family(language)
    code = str(code or "").lstrip("\ufeff")
    prof = CodeProfile(rid=rid, user_id=user_id, problem=problem, code=code,
                       language=language, family=family,
                       line_count=len(code.splitlines()), char_count=len(code))
    if family == "python":
        tokens, raw, comments, err = tokenize_python(code)
        prof.tokens, prof.raw_tokens, prof.comments, prof.error = tokens, raw, comments, err
        prof.parse_ok = not err
    else:
        tokens = tokenize_generic(code, family)
        prof.tokens = tokens
        prof.raw_tokens = tokens
        prof.parse_ok = True
    prof.kgrams = set(kgram_hashes(prof.tokens, k=5))
    prof.fingerprints = winnow(kgram_hashes(prof.tokens, k=5), w=4)
    if family == "python":
        feats, ok, err = ast_features(code)
        prof.ast_features = feats
        if not ok and not prof.error:
            prof.error = err
    else:
        prof.ast_features = c_family_features(code, family)
        prof.parse_ok = True
    prof.style = style_features(code, prof.comments)
    return prof


def ast_cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = sum(a[k] ** 2 for k in keys) ** 0.5
    nb = sum(b[k] ** 2 for k in keys) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def style_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """Normalised L1 distance of selected style signals (0 = same)."""
    keys = ["avg_line_len", "comment_ratio", "snake_case", "camel_case", "def_count",
            "docstrings", "fstrings", "type_hints", "main_guard", "sys_stdin", "try_except"]
    total = 0.0
    for k in keys:
        av, bv = a.get(k, 0.0), b.get(k, 0.0)
        scale = max(1.0, abs(av), abs(bv))
        total += abs(av - bv) / scale
    return total / len(keys)
