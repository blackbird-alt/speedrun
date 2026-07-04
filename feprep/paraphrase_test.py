# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork: the paraphrase test (PRD challenge 7d).

Goal: prove that a *performance* signal must be more than a copy of the
*memory* signal. FSRS memory says "will you recall this card"; it does not say
"can you answer the same idea when the exam rewords the question". This script
measures both on the SAME 30 verified FE cards and reports the gap.

It is deterministic, offline, and AI-off:

  1. Load 30 reworded-question rows from ``feprep/data/paraphrase_set.jsonl`` and
     VERIFY every one against the real verified decks (``feprep/decks/*.txt``) so
     no fact is invented -- each row's answer/question must match its cited
     source line byte-for-byte after light normalization.
  2. Build the 30 base cards in a temp collection, enable FSRS, and simulate a
     learner studying them (answer them through their learning steps) so each
     card gets FSRS memory state and a high current retrievability.
  3. MEMORY RECALL = the engine's own FSRS memory score (mean per-card current
     retrievability) over those 30 cards -- pure engine work, no model call.
  4. REWORDED ACCURACY = grade the learner's answers to the 60 paraphrase
     questions. With no live learner we model "answering" honestly: the learner
     has memorised (question -> answer) for the 30 cards, so when shown a
     reworded question they recall the answer of whichever memorised question is
     the closest match, then we grade that recalled answer against the
     question's known-correct answer with the token-overlap grader
     (``feprep/ai/common.token_f1``). This exposes the real failure mode -- a
     reworded cue that retrieves the wrong memorised fact -- instead of faking a
     high number by comparing an answer to itself.
  5. Report the GAP (memory% vs reworded-accuracy%). If they are ~equal, the
     "performance" signal is just copying memory and proves nothing new.

    $env:PYTHONPATH="pylib;out/pylib"
    out\\pyenv\\Scripts\\python.exe feprep\\paraphrase_test.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# The FE decks are full of Unicode math (β, ·, √, ²); make stdout UTF-8 so the
# summary prints identically on a cp1252 Windows console and a UTF-8 shell.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

sys.path[:0] = ["pylib", "out/pylib"]

# feprep/ is a sibling of this file's dir; add it so we can import ai.common.
_FEPREP_DIR = os.path.dirname(os.path.abspath(__file__))
if _FEPREP_DIR not in sys.path:
    sys.path.insert(0, _FEPREP_DIR)

from ai.common import (  # noqa: E402  (import after sys.path juggling)
    char_cosine,
    load_corpus,
    normalize_text,
    read_jsonl,
    token_f1,
)

from anki.collection import Collection  # noqa: E402
from anki.decks import DeckId  # noqa: E402

DATA_PATH = os.path.join(_FEPREP_DIR, "data", "paraphrase_set.jsonl")

# A recalled answer counts as "answers the reworded question" when it overlaps
# the known-correct answer at least this much. Pre-registered, not tuned after
# seeing the numbers.
CORRECT_F1_THRESHOLD = 0.5


def _fmt_pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def load_and_verify_rows() -> list[dict]:
    """Load the paraphrase rows and prove each is grounded in a verified card.

    Every row cites a source line (``card_id_hint`` = ``doc:line``). We parse the
    real decks and assert the row's answer and base question match that line, so
    the test cannot silently drift from the verified source.
    """
    rows = read_jsonl(DATA_PATH)
    corpus = load_corpus()
    by_locator = {span.locator: span for span in corpus}

    problems: list[str] = []
    for row in rows:
        hint = row["card_id_hint"]
        span = by_locator.get(hint)
        if span is None:
            problems.append(f"{hint}: no such verified source line")
            continue
        if normalize_text(span.answer) != normalize_text(row["answer"]):
            problems.append(
                f"{hint}: answer mismatch\n    row : {row['answer']!r}\n    src : {span.answer!r}"
            )
        if normalize_text(span.front) != normalize_text(row["base_question"]):
            problems.append(
                f"{hint}: question mismatch\n    row : {row['base_question']!r}"
                f"\n    src : {span.front!r}"
            )
    if problems:
        print("SOURCE VERIFICATION FAILED -- rows are not grounded in the decks:")
        for p in problems:
            print("  -", p)
        sys.exit(2)

    print(f"verified {len(rows)} rows against {len(corpus)} source cards (no invented facts)")
    return rows


def build_and_study(col: Collection, rows: list[dict]) -> list[int]:
    """Create the 30 base cards and simulate the learner studying them.

    Returns the list of created card ids (order matches ``rows``).
    """
    col.set_config("fsrs", True)
    conf = col.decks.config_dict_for_deck_id(DeckId(1))
    conf["new"]["perDay"] = 1000
    col.decks.save(conf)

    basic = col.models.by_name("Basic")
    card_ids: list[int] = []
    for row in rows:
        note = col.new_note(basic)
        note["Front"] = row["base_question"]
        note["Back"] = row["answer"]
        note.tags = [f"fe::{row['area']}"]
        col.add_note(note, DeckId(1))
        card_ids.append(note.cards()[0].id)

    # Study: answer every queued card "Good" (rating 3). New cards pass through
    # their learning steps (each answer is one graded review, so reps accumulate)
    # and then graduate to the review queue with FSRS memory state -- exactly the
    # signal the memory score reads. Cap iterations as a guard against a card
    # that never leaves the queue.
    reps = 0
    cap = len(rows) * 12
    while reps < cap:
        card = col.sched.getCard()
        if card is None:
            break
        card.start_timer()
        col.sched.answerCard(card, 3)
        reps += 1
    print(f"studied {len(card_ids)} cards with {reps} graded reviews (FSRS on, offline)")
    return card_ids


def compute_memory_recall(col: Collection) -> tuple[bool, float, str]:
    """MEMORY RECALL: the engine's FSRS memory score over the studied cards.

    ``point_estimate`` is the mean per-card current retrievability -- the model's
    best guess of "will you recall this card right now". Read-only engine work.
    """
    mem = col._backend.memory_score(search="is:review OR is:learn")
    if mem.shown:
        return True, float(mem.point_estimate), mem.main_reason
    return False, 0.0, mem.withheld_reason


def compute_reworded_accuracy(col: Collection, rows: list[dict], card_ids: list[int]):
    """REWORDED ACCURACY: does recalling the base fact answer the reworded question?

    Model of the learner: they have memorised (question -> answer) for all 30
    cards. Shown a reworded question, they recall the answer of the memorised
    question that best matches it (char-ngram cosine, deterministic). We then
    grade that recalled answer against the reworded question's known-correct
    answer with token_f1. A reworded cue that lands on the wrong memorised card
    yields a wrong answer -- the gap we are trying to expose.
    """
    # What the learner memorised: the base question text and its answer.
    known_questions = [col.get_card(cid).note()["Front"] for cid in card_ids]
    known_answers = [col.get_card(cid).note()["Back"] for cid in card_ids]

    per_paraphrase: list[dict] = []
    for idx, row in enumerate(rows):
        expected = row["answer"]
        for para in row["paraphrases"]:
            # Retrieve the memorised card whose QUESTION best matches the reworded
            # cue. Ties break to the lowest index for determinism.
            best_i, best_sim = -1, -1.0
            for j, q in enumerate(known_questions):
                sim = char_cosine(para, q)
                if sim > best_sim:
                    best_sim, best_i = sim, j
            recalled = known_answers[best_i]
            f1 = token_f1(recalled, expected)
            per_paraphrase.append(
                {
                    "area": row["area"],
                    "paraphrase": para,
                    "expected": expected,
                    "retrieved_correct_card": best_i == idx,
                    "recalled": recalled,
                    "f1": f1,
                    "correct": f1 >= CORRECT_F1_THRESHOLD,
                }
            )

    n = len(per_paraphrase)
    frac_correct = sum(p["correct"] for p in per_paraphrase) / n
    mean_f1 = sum(p["f1"] for p in per_paraphrase) / n
    retrieval_hit = sum(p["retrieved_correct_card"] for p in per_paraphrase) / n
    return frac_correct, mean_f1, retrieval_hit, per_paraphrase


def main() -> None:
    rows = load_and_verify_rows()
    if len(rows) != 30:
        print(f"WARNING: expected 30 base cards, found {len(rows)}")

    col = Collection(str(Path(tempfile.mkdtemp()) / "paraphrase.anki2"))
    try:
        card_ids = build_and_study(col, rows)
        mem_shown, mem_pct, mem_reason = compute_memory_recall(col)
        frac_correct, mean_f1, retrieval_hit, details = compute_reworded_accuracy(
            col, rows, card_ids
        )
    finally:
        col.close()

    n_para = sum(len(r["paraphrases"]) for r in rows)

    print("\n================ PARAPHRASE TEST (7d) ================")
    print(f"base cards           : {len(rows)}")
    print(f"reworded questions   : {n_para} (2 per card)")
    print()
    if mem_shown:
        print(f"MEMORY RECALL        : {_fmt_pct(mem_pct)}  (FSRS mean current retrievability)")
        print(f"                       {mem_reason}")
    else:
        print(f"MEMORY RECALL        : withheld -- {mem_reason}")
    print()
    print(f"REWORDED ACCURACY    : {_fmt_pct(frac_correct)}  "
          f"(paraphrases whose recalled answer matches, token_f1 >= {CORRECT_F1_THRESHOLD})")
    print(f"  mean answer token_f1 : {_fmt_pct(mean_f1)}")
    print(f"  correct-card recall  : {_fmt_pct(retrieval_hit)}  "
          f"(reworded cue retrieved the right memorised fact)")

    if mem_shown:
        gap = mem_pct - frac_correct
        print()
        print(f"GAP (memory - reworded): {gap * 100:+.1f} percentage points")
        print()
        if abs(gap) < 0.05:
            print("VERDICT: memory% and reworded-accuracy% are ~equal. On this set the")
            print("         performance signal is essentially COPYING the memory signal --")
            print("         it proves nothing a memory score doesn't already tell you.")
        else:
            print("VERDICT: reworded accuracy trails memory recall. Knowing you'll recall a")
            print("         card (memory) is NOT the same as answering the same idea when the")
            print("         exam rewords it (performance). The gap is exactly the part a")
            print("         memory score cannot see -- so performance must be measured")
            print("         separately, not derived from memory.")

    # Show the reworded questions the learner got wrong -- the honest evidence.
    misses = [d for d in details if not d["correct"]]
    if misses:
        print(f"\nreworded questions answered WRONG ({len(misses)}/{n_para}):")
        for d in misses:
            print(f"  [{d['area']}] {d['paraphrase']}")
            print(f"      expected: {d['expected']}")
            print(f"      recalled: {d['recalled']}  (f1={d['f1']:.2f}, "
                  f"right card={d['retrieved_correct_card']})")

    print("\n=====================================================")


if __name__ == "__main__":
    main()
