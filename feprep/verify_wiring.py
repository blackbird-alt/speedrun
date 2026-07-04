# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ad-hoc end-to-end check of the Speedrun fork's two engine features, verifying
the *actual* shipped contract (not a reordered live queue, which is deliberately
disabled — see below). Run with the fork's pyenv:

    out\\pyenv\\Scripts\\python feprep\\verify_wiring.py

Design contract being verified
------------------------------
1. The points-at-stake ordering is exposed as a read-only backend RPC
   (`points_at_stake_queue`) that returns the engine's due/new cards re-ordered
   by `topic weight x student weakness`. It never mutates scheduling state.
2. The *live* reviewer queue (`get_queued_cards`) is intentionally NOT
   re-ordered at the Python layer: the Rust queue's front is unchanged, so
   answering a card that is not that true front is rejected by the engine
   (`answer_card` -> InvalidInput). This script proves that the default review
   loop stays answerable, which is the property Wednesday requires.
"""

import sys
import tempfile
from pathlib import Path

sys.path[:0] = ["pylib", "qt", "out/pylib", "out/qt"]

from anki.collection import Collection
from anki.decks import DeckId


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "verify.anki2"))
        try:
            # Heavier weight on circuit_analysis than ethics; both new cards
            # carry maximum weakness, so weight alone decides the order.
            col.set_config("feTopicWeights", {"circuit_analysis": 10.0, "ethics": 1.0})

            basic = col.models.by_name("Basic")
            did = DeckId(1)

            def add(front: str, topic: str) -> int:
                note = col.new_note(basic)
                note["Front"] = front
                note["Back"] = "a"
                note.tags = [f"fe::{topic}"]
                col.add_note(note, did)
                return col.find_cards(f"nid:{note.id}")[0]

            light = add("ethics q", "ethics")
            heavy = add("circuit q", "circuit_analysis")

            # 1. The RPC surfaces the highest points-at-stake card first.
            order = list(
                col._backend.points_at_stake_queue(search="is:new").card_ids
            )
            assert order[0] == heavy, (
                f"points_at_stake_queue must rank the heavy card {heavy} first, "
                f"got {order} (ethics={light})"
            )
            print("OK: points_at_stake_queue RPC ranks highest-value topic first")

            # 2. The live reviewer queue stays answerable (engine order, not the
            #    RPC order). This is the property that makes the review loop work.
            qc = col.sched.get_queued_cards(fetch_limit=1).cards[0]
            card = col.get_card(qc.card.id)
            card.start_timer()
            states = col._backend.get_scheduling_states(card.id)
            answer = col.sched.build_answer(card=card, states=states, rating=3)
            col.sched.answer_card(answer)  # would raise InvalidInput if reordered
            print("OK: live review loop presents an answerable card and grades it")

            # 3. The feature flag defaults off, so the engine's integrity holds.
            assert col.get_config("feReorderQueue", False) is False
            print("OK: feReorderQueue defaults off (live queue not presentation-reordered)")
        finally:
            col.close()


if __name__ == "__main__":
    main()
