# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ad-hoc check that the v3 scheduler actually presents cards in points-at-stake
order (the reviewer's get_queued_cards path). Run with the fork's pyenv:

    out\\pyenv\\Scripts\\python feprep\\verify_wiring.py
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
            # carry maximum weakness, so weight alone must decide the order.
            col.set_config("feTopicWeights", {"circuit_analysis": 10.0, "ethics": 1.0})

            basic = col.models.by_name("Basic")
            # Use the current (Default) deck so the study queue includes them.
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

            top = col.sched.get_queued_cards(fetch_limit=1).cards[0].card.id
            assert top == heavy, (
                f"expected heavy card {heavy} first, got {top} "
                f"(ethics={light})"
            )
            print("OK: reviewer presents the highest points-at-stake card first")

            # Toggle the feature off -> falls back to engine order.
            col.set_config("feReorderQueue", False)
            all_ids = [
                c.card.id
                for c in col.sched.get_queued_cards(fetch_limit=10).cards
            ]
            assert heavy in all_ids and light in all_ids
            print("OK: toggle off falls back to normal order without error")
        finally:
            col.close()


if __name__ == "__main__":
    main()
