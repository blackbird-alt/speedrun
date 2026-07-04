# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Shared, dependency-free utilities for the FE "Speedrun" AI pipeline.

Everything here is stdlib-only and deterministic (no randomness, sorted
iteration) so the whole generate -> verify -> leakage -> eval pipeline
reproduces byte-identical numbers on any machine, offline.

The pipeline treats the *already verified* FE decks under ``feprep/decks/`` as
the named source corpus. No facts are invented here; the generator only ever
re-expresses text that is present in a cited source line.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Iterable

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

# feprep/ai/common.py -> feprep/ai -> feprep -> feprep/decks
AI_DIR = os.path.dirname(os.path.abspath(__file__))
FEPREP_DIR = os.path.dirname(AI_DIR)
DECKS_DIR = os.path.join(FEPREP_DIR, "decks")
OUT_DIR = os.path.join(AI_DIR, "out")
GOLD_PATH = os.path.join(AI_DIR, "gold", "fe_gold_50.jsonl")

# The named source documents. Order is fixed for determinism. Only the author's
# manuscript decks are trusted here: fe-bank.txt / fe-seed-deck.txt were never
# quality-checked, so they are deliberately excluded from the AI pipeline's
# source of truth (generation, gold, leakage, eval).
SOURCE_DOCS = ("fe-problems.txt", "fe-figures.txt")


def source_doc_path(doc: str) -> str:
    return os.path.join(DECKS_DIR, doc)


def ensure_out_dir() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# Deck parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Span:
    """One verified source line = one atomic fact with provenance."""

    doc: str
    line: int  # 1-based line number in the source doc
    front: str
    answer: str
    tags: str
    area: str

    @property
    def locator(self) -> str:
        return f"{self.doc}:{self.line}"

    @property
    def text(self) -> str:
        """Full source text of the span (what a card must be grounded in)."""
        return f"{self.front} {self.answer}".strip()


def _area_from_tags(tags: str) -> str:
    for tag in tags.split():
        if tag.startswith("fe::"):
            return tag[len("fe::") :]
    return "unknown"


def parse_deck(doc: str) -> list[Span]:
    """Parse a tab-separated FE deck.

    Skips the leading ``#separator``/``#html``/``#tags column`` directive block
    and any ``#`` comment lines. Line numbers are 1-based and match the file so
    provenance points at the real source line.
    """
    spans: list[Span] = []
    path = source_doc_path(doc)
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            front = parts[0].strip()
            answer = parts[1].strip()
            tags = parts[2].strip() if len(parts) > 2 else ""
            if not front or not answer:
                continue
            spans.append(
                Span(
                    doc=doc,
                    line=lineno,
                    front=front,
                    answer=answer,
                    tags=tags,
                    area=_area_from_tags(tags),
                )
            )
    return spans


def load_corpus(docs: Iterable[str] = SOURCE_DOCS) -> list[Span]:
    """Load and concatenate all source decks in deterministic order."""
    corpus: list[Span] = []
    for doc in docs:
        corpus.extend(parse_deck(doc))
    return corpus


# --------------------------------------------------------------------------- #
# Text normalization + tokenization
# --------------------------------------------------------------------------- #

# Map common unicode math glyphs to ascii so token overlap is meaningful.
_UNICODE_MAP = {
    "−": "-",
    "–": "-",
    "—": "-",
    "×": "x",
    "·": " ",
    "∙": " ",
    "•": " ",
    "√": "sqrt",
    "²": "2",
    "³": "3",
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
    "⁻": "-",
    "≈": "=",
    "≤": "<=",
    "≥": ">=",
    "∞": "inf",
    "π": "pi",
    "θ": "theta",
    "ω": "w",
    "Ω": "ohm",
    "Φ": "phi",
    "φ": "phi",
    "Δ": "delta",
    "λ": "lambda",
    "μ": "u",
    "ε": "e",
    "τ": "tau",
    "ζ": "zeta",
    "β": "beta",
    "�the": "the",
    "'": "'",
    "’": "'",
    "“": '"',
    "”": '"',
}

_STOPWORDS = frozenset(
    """a an the of to in on for and or is are be as at by with from into
    what which how when where does do it its that this these those you your
    equal equals value between two one there their they them then than about
    given define state express find write give under""".split()
)


# LaTeX command -> the SAME canonical form the unicode glyphs fold to, so a card
# written in MathJax/LaTeX (nice typesetting) still token-matches the Unicode
# source it was grounded on. e.g. "\tau" and "τ" both become "tau".
_LATEX_CMD_MAP = {
    r"\tau": "tau", r"\omega": "w", r"\Omega": "ohm", r"\mu": "u", r"\pi": "pi",
    r"\theta": "theta", r"\Delta": "delta", r"\delta": "delta",
    r"\lambda": "lambda", r"\varepsilon": "e", r"\epsilon": "e", r"\zeta": "zeta",
    r"\beta": "beta", r"\alpha": "alpha", r"\gamma": "gamma", r"\phi": "phi",
    r"\varphi": "phi", r"\cdot": " ", r"\times": "x", r"\div": "/", r"\pm": "+-",
    r"\leq": "<=", r"\geq": ">=", r"\approx": "=", r"\infty": "inf",
    r"\sqrt": "sqrt", r"\left": "", r"\right": "", r"\quad": " ", r"\,": " ",
    r"\;": " ", r"\:": " ", r"\!": "",
}
_FRAC_RE = re.compile(r"\\[a-z]*frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")


def strip_latex(text: str) -> str:
    """Fold LaTeX/MathJax markup into a plain form matching the Unicode source.

    So ``\\( R_{eq} = \\frac{R_1 R_2}{R_1+R_2} \\)`` folds toward the same tokens
    as ``R1·R2/(R1+R2)``. Cheap no-op when the text has no LaTeX indicators.
    """
    if not any(ch in text for ch in ("\\", "$", "^", "_", "{")):
        return text
    for delim in ("\\(", "\\)", "\\[", "\\]", "$$", "$"):
        text = text.replace(delim, " ")
    for _ in range(3):  # a few passes for lightly-nested fractions
        new = _FRAC_RE.sub(r" \1 / \2 ", text)
        if new == text:
            break
        text = new
    for cmd, rep in _LATEX_CMD_MAP.items():
        text = text.replace(cmd, rep)
    text = re.sub(r"\\([a-zA-Z]+)", r"\1", text)  # any leftover \cmd -> cmd
    # Remove grouping + sub/superscript markers so base+script join
    # ("V^2" -> "V2", "R_{eq}" -> "Req"), matching the source's plain notation.
    for ch in ("{", "}", "^", "_"):
        text = text.replace(ch, "")
    return text


def normalize_text(text: str) -> str:
    """Lowercase and fold LaTeX + unicode math glyphs to a canonical ascii form."""
    text = strip_latex(text)
    for uni, ascii_ in _UNICODE_MAP.items():
        text = text.replace(uni, ascii_)
    return text.lower().strip()


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    toks = _TOKEN_RE.findall(normalize_text(text))
    if drop_stopwords:
        toks = [t for t in toks if t not in _STOPWORDS]
    return toks


def content_tokens(text: str) -> set[str]:
    return set(tokenize(text, drop_stopwords=True))


def char_ngrams(text: str, n: int = 3) -> list[str]:
    """Character n-grams over the normalized string (spaces collapsed).

    This is the "vector" feature space: a poor-man's embedding that needs no
    network and no model download but still captures sub-word similarity.
    """
    norm = re.sub(r"\s+", " ", normalize_text(text)).strip()
    if len(norm) < n:
        return [norm] if norm else []
    return [norm[i : i + n] for i in range(len(norm) - n + 1)]


# --------------------------------------------------------------------------- #
# Similarity metrics
# --------------------------------------------------------------------------- #


def token_f1(pred: str, gold: str) -> float:
    """Token-level F1 between two answers (order-independent overlap)."""
    p = content_tokens(pred)
    g = content_tokens(gold)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    overlap = len(p & g)
    if overlap == 0:
        return 0.0
    precision = overlap / len(p)
    recall = overlap / len(g)
    return 2 * precision * recall / (precision + recall)


def _counts(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return out


def cosine_counts(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    # iterate over the smaller dict for speed
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(v * large.get(k, 0.0) for k, v in small.items())
    if dot == 0.0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def char_cosine(a: str, b: str, n: int = 3) -> float:
    """Cosine similarity in char-ngram term-frequency space."""
    va = {k: float(v) for k, v in _counts(char_ngrams(a, n)).items()}
    vb = {k: float(v) for k, v in _counts(char_ngrams(b, n)).items()}
    return cosine_counts(va, vb)


# --------------------------------------------------------------------------- #
# BM25 (keyword baseline) — small, self-contained Okapi BM25
# --------------------------------------------------------------------------- #


class BM25Index:
    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs_tokens = docs_tokens
        self.n = len(docs_tokens)
        self.doc_len = [len(t) for t in docs_tokens]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf: list[dict[str, int]] = [_counts(t) for t in docs_tokens]
        df: dict[str, int] = {}
        for tfmap in self.tf:
            for term in tfmap:
                df[term] = df.get(term, 0) + 1
        self.idf: dict[str, float] = {}
        for term, dfi in df.items():
            # Okapi BM25 idf with +1 to keep it non-negative.
            self.idf[term] = math.log(1 + (self.n - dfi + 0.5) / (dfi + 0.5))

    def score(self, query_tokens: list[str], idx: int) -> float:
        tfmap = self.tf[idx]
        dl = self.doc_len[idx]
        score = 0.0
        for term in query_tokens:
            if term not in tfmap:
                continue
            idf = self.idf.get(term, 0.0)
            freq = tfmap[term]
            denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            score += idf * (freq * (self.k1 + 1)) / denom
        return score

    def rank(self, query_tokens: list[str]) -> list[tuple[int, float]]:
        scored = [(i, self.score(query_tokens, i)) for i in range(self.n)]
        # deterministic: sort by score desc, then index asc
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored

    def best(self, query_tokens: list[str]) -> tuple[int, float]:
        ranked = self.rank(query_tokens)
        return ranked[0] if ranked else (-1, 0.0)


# --------------------------------------------------------------------------- #
# Char-ngram TF-IDF vector index (vector baseline)
# --------------------------------------------------------------------------- #


class TfidfNgramIndex:
    """TF-IDF over character n-grams with cosine similarity.

    A deterministic, network-free stand-in for a sentence-embedding index.
    """

    def __init__(self, texts: list[str], n: int = 3):
        self.n = n
        self.texts = texts
        self.doc_ngrams = [_counts(char_ngrams(t, n)) for t in texts]
        df: dict[str, int] = {}
        for grams in self.doc_ngrams:
            for g in grams:
                df[g] = df.get(g, 0) + 1
        num = len(texts)
        self.idf = {g: math.log((1 + num) / (1 + dfi)) + 1.0 for g, dfi in df.items()}
        self.vectors = [self._vectorize_counts(c) for c in self.doc_ngrams]

    def _vectorize_counts(self, counts: dict[str, int]) -> dict[str, float]:
        return {g: c * self.idf.get(g, 0.0) for g, c in counts.items()}

    def vectorize(self, text: str) -> dict[str, float]:
        counts = _counts(char_ngrams(text, self.n))
        return {g: c * self.idf.get(g, 0.0) for g, c in counts.items()}

    def rank(self, query: str) -> list[tuple[int, float]]:
        qv = self.vectorize(query)
        scored = [(i, cosine_counts(qv, dv)) for i, dv in enumerate(self.vectors)]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored

    def best(self, query: str) -> tuple[int, float]:
        ranked = self.rank(query)
        return ranked[0] if ranked else (-1, 0.0)


# --------------------------------------------------------------------------- #
# Hashing / provenance
# --------------------------------------------------------------------------- #


def sha1_short(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def prompt_hash(template: str, source_text: str, model: str) -> str:
    """Stable hash binding a card to the exact prompt that produced it.

    A real provider hashes the fully-rendered prompt; the stub hashes the same
    inputs so provenance is meaningful and reproducible either way.
    """
    payload = json.dumps(
        {"template": template, "source": source_text, "model": model},
        ensure_ascii=False,
        sort_keys=True,
    )
    return "ph_" + sha1_short(payload, 16)


# --------------------------------------------------------------------------- #
# JSONL IO (deterministic)
# --------------------------------------------------------------------------- #


def write_jsonl(path: str, rows: Iterable[dict]) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            count += 1
    return count


def read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_gold(path: str = GOLD_PATH) -> list[dict]:
    return read_jsonl(path)


# --------------------------------------------------------------------------- #
# Leakage predicate + generation-input construction
# --------------------------------------------------------------------------- #

# Shared thresholds for "is this span a gold item or a near-duplicate of one?".
# Used both to BUILD the generation inputs (remove leaks) and to AUDIT them
# (leakage_check.py). Keeping them in one place guarantees the two agree.
LEAK_FRONT_COSINE = 0.85   # question near-identical
LEAK_ANSWER_COSINE = 0.75  # answer near-identical
LEAK_COMBINED_COSINE = 0.90  # whole card near-identical


def leak_reason(front: str, answer: str, gold_q: str, gold_a: str) -> str | None:
    """Return why (front, answer) leaks a gold (gold_q, gold_a), or None.

    Catches exact items and paraphrase near-duplicates so no gold answer can be
    trivially retrievable from the generation inputs.
    """
    if normalize_text(answer) == normalize_text(gold_a):
        # identical answer: leak if the question is even loosely related
        if char_cosine(front, gold_q) >= 0.55:
            return "same-answer+similar-question"
    fc = char_cosine(front, gold_q)
    ac = char_cosine(answer, gold_a)
    if fc >= LEAK_FRONT_COSINE and ac >= LEAK_ANSWER_COSINE:
        return f"near-dup(front={fc:.2f},answer={ac:.2f})"
    cc = char_cosine(f"{front} {answer}", f"{gold_q} {gold_a}")
    if cc >= LEAK_COMBINED_COSINE:
        return f"near-dup(card={cc:.2f})"
    return None


def find_leaks(spans: list[Span], gold: list[dict]) -> list[dict]:
    """Independent audit: which spans leak which gold items."""
    leaks: list[dict] = []
    gold_src = {(g["source"]["doc"], g["source"]["line"]) for g in gold}
    for span in spans:
        if (span.doc, span.line) in gold_src:
            leaks.append(
                {
                    "span": span.locator,
                    "gold_id": next(
                        g["id"]
                        for g in gold
                        if (g["source"]["doc"], g["source"]["line"])
                        == (span.doc, span.line)
                    ),
                    "reason": "exact-source-line",
                }
            )
            continue
        for g in gold:
            reason = leak_reason(span.front, span.answer, g["question"], g["answer"])
            if reason:
                leaks.append(
                    {"span": span.locator, "gold_id": g["id"], "reason": reason}
                )
                break
    return leaks


def build_generation_inputs(
    corpus: list[Span] | None = None, gold: list[dict] | None = None
) -> tuple[list[Span], list[dict]]:
    """Return (clean_inputs, removed) with all gold + near-dups held out."""
    corpus = corpus if corpus is not None else load_corpus()
    gold = gold if gold is not None else load_gold()
    gold_src = {(g["source"]["doc"], g["source"]["line"]) for g in gold}

    clean: list[Span] = []
    removed: list[dict] = []
    for span in corpus:
        if (span.doc, span.line) in gold_src:
            removed.append({"span": span.locator, "reason": "exact-source-line"})
            continue
        hit = None
        for g in gold:
            reason = leak_reason(span.front, span.answer, g["question"], g["answer"])
            if reason:
                hit = (g["id"], reason)
                break
        if hit:
            removed.append(
                {"span": span.locator, "gold_id": hit[0], "reason": hit[1]}
            )
        else:
            clean.append(span)
    return clean, removed


def span_to_input_row(span: Span) -> dict:
    return {
        "doc": span.doc,
        "line": span.line,
        "front": span.front,
        "answer": span.answer,
        "tags": span.tags,
        "area": span.area,
    }


def input_row_to_span(row: dict) -> Span:
    return Span(
        doc=row["doc"],
        line=row["line"],
        front=row["front"],
        answer=row["answer"],
        tags=row.get("tags", ""),
        area=row.get("area", "unknown"),
    )
