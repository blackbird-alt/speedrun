# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the held-out gold set ``fe_gold_50.jsonl`` from the author's manuscript.

Gold is drawn ONLY from the manuscript decks in ``common.SOURCE_DOCS``
(``fe-problems.txt`` worked problems + ``fe-figures.txt``); the never-checked
``fe-bank.txt`` / ``fe-seed-deck.txt`` are excluded. Figure cards whose front is
only an ``[img:...]`` reference are skipped (there is no text for the held-out
eval / baselines to match).

This is a one-shot, deterministic derivation tool. It does NOT invent facts:
every gold item is copied verbatim from a real, cited source line. The output is
committed and treated as a fixed fixture; the generator holds these lines (and
near-duplicates) out of its inputs (see ``leakage_check.py``).

Run: python feprep/ai/gold/build_gold.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import GOLD_PATH, load_corpus, write_jsonl  # noqa: E402

TARGET = 50


def _is_image_only(front: str) -> bool:
    """True if the front is essentially just an ``[img:...]`` reference with no
    textual question -- such a card cannot serve as a text gold/eval item."""
    stripped = re.sub(r"\[img:[^\]]*\]", "", front)
    return not re.search(r"[A-Za-z0-9]", stripped)


def build() -> list[dict]:
    corpus = load_corpus()

    # Group spans by area, preserving deterministic (doc, line) order.
    by_area: dict[str, list] = {}
    for span in corpus:
        by_area.setdefault(span.area, []).append(span)

    areas = sorted(a for a in by_area if a != "unknown")

    # Round-robin across areas so the 50 items span the exam broadly.
    # Prefer concise, recall-style answers (short) as "known-correct" gold.
    picked: list = []
    cursors = {a: 0 for a in areas}
    while len(picked) < TARGET:
        progressed = False
        for area in areas:
            if len(picked) >= TARGET:
                break
            spans = by_area[area]
            idx = cursors[area]
            # advance to the next reasonable gold candidate for this area
            while idx < len(spans):
                span = spans[idx]
                idx += 1
                # skip degenerate ultra-short answers that make poor gold
                if len(span.answer) < 2:
                    continue
                # skip figure cards whose question is only an image reference:
                # there's no text for the held-out eval / baselines to match.
                if _is_image_only(span.front):
                    continue
                picked.append(span)
                progressed = True
                break
            cursors[area] = idx
        if not progressed:
            break

    picked.sort(key=lambda s: (s.area, s.doc, s.line))

    rows = []
    for i, span in enumerate(picked, start=1):
        rows.append(
            {
                "id": f"g{i:03d}",
                "question": span.front,
                "answer": span.answer,
                "area": span.area,
                "source": {"doc": span.doc, "line": span.line},
            }
        )
    return rows


def main() -> None:
    rows = build()
    n = write_jsonl(GOLD_PATH, rows)
    areas: dict[str, int] = {}
    for r in rows:
        areas[r["area"]] = areas.get(r["area"], 0) + 1
    print(f"wrote {n} gold items -> {GOLD_PATH}")
    for area in sorted(areas):
        print(f"  {area:32s} {areas[area]}")


if __name__ == "__main__":
    main()
