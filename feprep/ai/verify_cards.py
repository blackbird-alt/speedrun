# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Verifier gate for generated cards (Friday challenge 7f).

Pipeline stage 2. Each candidate is classified into exactly one of:

* ``correct_useful`` — grounded in its cited source line, not contradicting any
  gold answer, substantive, and not a duplicate. These are the ONLY cards that
  ship.
* ``wrong``          — fails grounding (its answer is not supported by the exact
  source line it cites) OR contradicts a known-correct gold answer.
* ``bad_teaching``   — vague / trivial / duplicate: a too-short answer or a
  near-duplicate (cosine > cutoff) of a card already kept.

Blocked cards (``wrong`` + ``bad_teaching``) are excluded from the shipped
output. Writes ``out/verified.jsonl`` (kept) and ``out/verified_all.jsonl``
(every card annotated with its label + reason).

Run: python feprep/ai/verify_cards.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C  # noqa: E402
from generate_cards import CANDIDATES_PATH  # noqa: E402

# ===========================================================================
# CUTOFFS — SET BEFORE LOOKING AT RESULTS
# These thresholds were fixed up front, before any candidate was inspected or
# any score was computed. Do not tune them to the outputs.
# ===========================================================================
GROUNDING_MIN_RECALL = 0.60   # >= this fraction of answer tokens must appear in the cited source line
DUP_COSINE = 0.95             # char-ngram cosine above this = near-duplicate -> bad_teaching
MIN_ANSWER_CHARS = 3          # answers shorter than this (spaces removed) are trivial -> bad_teaching
CONTRADICT_Q_COSINE = 0.80    # a card whose question matches a gold question this closely...
CONTRADICT_A_F1 = 0.40        # ...but whose answer F1 vs the gold answer is below this -> wrong
# ===========================================================================

VERIFIED_PATH = os.path.join(C.OUT_DIR, "verified.jsonl")
VERIFIED_ALL_PATH = os.path.join(C.OUT_DIR, "verified_all.jsonl")


def grounding_recall(back: str, source_text: str) -> float:
    back_tokens = C.content_tokens(back)
    if not back_tokens:
        return 0.0
    src_tokens = C.content_tokens(source_text)
    return len(back_tokens & src_tokens) / len(back_tokens)


def is_too_short(answer: str) -> bool:
    stripped = C.normalize_text(answer).replace(" ", "")
    return len(stripped) < MIN_ANSWER_CHARS


def contradicts_gold(front: str, back: str, gold: list[dict]) -> tuple[bool, str]:
    for g in gold:
        if C.char_cosine(front, g["question"]) >= CONTRADICT_Q_COSINE:
            if C.token_f1(back, g["answer"]) < CONTRADICT_A_F1:
                return True, f"contradicts {g['id']}"
    return False, ""


def _ngram_vec(text: str) -> dict[str, float]:
    counts = {}
    for g in C.char_ngrams(text, 3):
        counts[g] = counts.get(g, 0.0) + 1.0
    return counts


def classify(cards: list[dict], gold: list[dict]) -> list[dict]:
    """Annotate each card (in deterministic order) with label + reason."""
    accepted_vecs: list[dict[str, float]] = []  # ngram vectors of kept cards
    annotated: list[dict] = []
    for card in cards:
        front, back = card["front"], card["back"]
        label = "correct_useful"
        reason = "grounded, substantive, unique"

        recall = grounding_recall(back, card.get("source_text", ""))
        contra, contra_reason = contradicts_gold(front, back, gold)

        if recall < GROUNDING_MIN_RECALL:
            label, reason = "wrong", f"ungrounded (recall={recall:.2f})"
        elif contra:
            label, reason = "wrong", contra_reason
        elif is_too_short(back):
            label, reason = "bad_teaching", "answer too short/trivial"
        else:
            vec = _ngram_vec(f"{front} {back}")
            dup = any(
                C.cosine_counts(vec, kept) > DUP_COSINE for kept in accepted_vecs
            )
            if dup:
                label, reason = "bad_teaching", f"near-duplicate (cosine>{DUP_COSINE})"
            else:
                accepted_vecs.append(vec)

        out = dict(card)
        out["label"] = label
        out["reason"] = reason
        out["grounding_recall"] = round(recall, 3)
        annotated.append(out)
    return annotated


def verify() -> dict:
    C.ensure_out_dir()
    cards = C.read_jsonl(CANDIDATES_PATH)
    gold = C.load_gold()
    annotated = classify(cards, gold)

    counts = {"correct_useful": 0, "wrong": 0, "bad_teaching": 0}
    for c in annotated:
        counts[c["label"]] += 1

    kept = [c for c in annotated if c["label"] == "correct_useful"]
    C.write_jsonl(VERIFIED_ALL_PATH, annotated)
    C.write_jsonl(VERIFIED_PATH, kept)
    return {
        "n_total": len(annotated),
        "counts": counts,
        "n_kept": len(kept),
    }


def main() -> None:
    info = verify()
    c = info["counts"]
    print("=== verify_cards (challenge 7f) ===")
    print("cutoffs (SET BEFORE LOOKING AT RESULTS):")
    print(f"  grounding_min_recall = {GROUNDING_MIN_RECALL}")
    print(f"  dup_cosine           = {DUP_COSINE}")
    print(f"  min_answer_chars     = {MIN_ANSWER_CHARS}")
    print(f"total candidates       : {info['n_total']}")
    print(f"  correct_useful : {c['correct_useful']}")
    print(f"  wrong          : {c['wrong']}")
    print(f"  bad_teaching   : {c['bad_teaching']}")
    print(f"shipped (kept)         : {info['n_kept']}")
    print(f"-> {VERIFIED_PATH}")


if __name__ == "__main__":
    main()
