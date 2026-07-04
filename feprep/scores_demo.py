# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork: print the three honest scores (memory / performance /
readiness), each as a range with its give-up rule, on a studied deck.

Also serves as the **AI-off proof** (PRD hard rule): scoring is pure engine work
on data Anki already owns (tags + FSRS). No model is called, no network is used.

    $env:PYTHONPATH="pylib;out/pylib"
    out\\pyenv\\Scripts\\python.exe feprep\\scores_demo.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path[:0] = ["pylib", "out/pylib"]

from anki.collection import Collection
from anki.decks import DeckId

AREAS = [
    "circuit_analysis",
    "power_systems",
    "electronics",
    "mathematics",
    "digital_systems",
]


def _fmt_range(lo: float, pt: float, hi: float) -> str:
    return f"{pt*100:5.1f}%  [{lo*100:4.1f}% – {hi*100:4.1f}%]"


def main() -> None:
    col = Collection(str(Path(tempfile.mkdtemp()) / "demo.anki2"))
    try:
        # FSRS is what produces per-card memory state (the only signal the
        # scores read). Enable it, and raise the new-cards/day limit so we can
        # study a realistic amount.
        col.set_config("fsrs", True)
        conf = col.decks.config_dict_for_deck_id(DeckId(1))
        conf["new"]["perDay"] = 1000
        col.decks.save(conf)

        basic = col.models.by_name("Basic")
        # ~12 cards/area; roughly half of the engineering-core areas are worked
        # problems (track::durable) so the performance population is populated.
        for area in AREAS:
            durable_area = area in ("circuit_analysis", "power_systems", "electronics")
            for i in range(12):
                note = col.new_note(basic)
                note["Front"] = f"{area} q{i}"
                note["Back"] = "a"
                tags = [f"fe::{area}"]
                if durable_area and i < 8:
                    tags.append("track::durable")
                note.tags = tags
                col.add_note(note, DeckId(1))

        # Study: answer every queued card "Easy" (rating 4) so it graduates to
        # the review queue with FSRS memory state and revlog history (the only
        # things the scores read). Track distinct cards to avoid re-counting an
        # intraday learning card.
        answered: set[int] = set()
        while True:
            card = col.sched.getCard()
            if card is None or card.id in answered:
                break
            answered.add(card.id)
            col.sched.answerCard(card, 4)
        print(f"studied {len(answered)} cards across {len(AREAS)} areas (AI OFF, offline)\n")

        mem = col._backend.memory_score(search="is:review OR is:learn")
        perf = col._backend.performance_score(search="tag:track::durable")
        rdy = col._backend.readiness_score(search="is:review OR is:learn")

        print("MEMORY      ", end="")
        if mem.shown:
            print(_fmt_range(mem.range_low, mem.point_estimate, mem.range_high))
            print(f"            {mem.main_reason}")
        else:
            print("withheld -", mem.withheld_reason)

        print("PERFORMANCE ", end="")
        if perf.shown:
            print(_fmt_range(perf.range_low, perf.point_estimate, perf.range_high))
            print(f"            {perf.main_reason}")
        else:
            print("withheld -", perf.withheld_reason)

        print("READINESS   ", end="")
        if rdy.shown:
            print(_fmt_range(rdy.range_low, rdy.pass_probability, rdy.range_high))
            print(f"            {rdy.main_reason}")
        else:
            print("withheld -", rdy.withheld_reason)
        print(f"           coverage {rdy.areas_covered}/{rdy.areas_total} areas; "
              f"next: {rdy.next_action}")
    finally:
        col.close()


if __name__ == "__main__":
    main()
