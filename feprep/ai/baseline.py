# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Simpler retrieval baselines the AI pipeline must beat (or admit it doesn't).

Two dependency-free baselines answer a gold question by retrieving the most
similar source span from the *same* gold-free generation-input corpus the
generator saw, then returning that span's answer:

* :class:`KeywordBaseline` — a small, self-contained Okapi BM25 over word
  tokens (no external search library).
* :class:`VectorBaseline`  — cosine similarity in character-n-gram TF-IDF space
  (a local, network-free stand-in for a sentence-embedding index).

Both operate on exactly the same corpus and are scored by exactly the same
metric as the AI pipeline (see ``eval.py``), so the comparison is apples to
apples.

Run standalone for a quick look: python feprep/ai/baseline.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C  # noqa: E402
from generate_cards import INPUTS_PATH  # noqa: E402


def load_input_spans() -> list[C.Span]:
    """Load the gold-free generation-input corpus written by generate_cards."""
    if os.path.exists(INPUTS_PATH):
        return [C.input_row_to_span(r) for r in C.read_jsonl(INPUTS_PATH)]
    # fall back to constructing it directly (keeps baseline runnable alone)
    inputs, _ = C.build_generation_inputs()
    return inputs


class KeywordBaseline:
    name = "keyword (BM25)"

    def __init__(self, spans: list[C.Span]):
        self.spans = spans
        self.index = C.BM25Index([C.tokenize(s.front) for s in spans])

    def answer(self, question: str) -> tuple[str, float, str]:
        idx, score = self.index.best(C.tokenize(question))
        if idx < 0:
            return "", 0.0, ""
        span = self.spans[idx]
        return span.answer, score, span.locator


class VectorBaseline:
    name = "vector (char-ngram TF-IDF)"

    def __init__(self, spans: list[C.Span]):
        self.spans = spans
        self.index = C.TfidfNgramIndex([s.front for s in spans], n=3)

    def answer(self, question: str) -> tuple[str, float, str]:
        idx, score = self.index.best(question)
        if idx < 0:
            return "", 0.0, ""
        span = self.spans[idx]
        return span.answer, score, span.locator


def build_baselines() -> dict[str, object]:
    spans = load_input_spans()
    return {"keyword": KeywordBaseline(spans), "vector": VectorBaseline(spans)}


def main() -> None:
    gold = C.load_gold()
    baselines = build_baselines()
    for name, bl in baselines.items():
        hits = 0
        for g in gold:
            pred, _, _ = bl.answer(g["question"])
            if C.token_f1(pred, g["answer"]) >= 0.5:
                hits += 1
        print(f"{bl.name:32s} rough token-F1>=0.5 on gold: {hits}/{len(gold)}")


if __name__ == "__main__":
    main()
