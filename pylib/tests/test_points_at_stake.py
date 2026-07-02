# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork: exercise the points-at-stake queue and the honest memory
score through the protobuf boundary (Python -> _rsbridge -> Rust backend)."""

from tests.shared import getEmptyCol


def _add_tagged_note(col, front: str, topic: str):
    note = col.newNote()
    note["Front"] = front
    note["Back"] = "answer"
    note.tags = [f"fe::{topic}"]
    col.addNote(note)
    return note


def test_points_at_stake_queue_orders_by_weight_through_protobuf():
    col = getEmptyCol()
    # Weights live in the config table, not in engine code. Make circuit
    # analysis far heavier than ethics so the ordering is deterministic even
    # for brand-new cards (which all carry maximum weakness == 1.0).
    col.set_config(
        "feTopicWeights",
        {"circuit_analysis": 10.0, "ethics": 1.0},
    )

    heavy = _add_tagged_note(col, "Norton equivalent?", "circuit_analysis")
    light = _add_tagged_note(col, "Whistleblowing duty?", "ethics")

    heavy_cid = col.find_cards(f"nid:{heavy.id}")[0]
    light_cid = col.find_cards(f"nid:{light.id}")[0]

    # Call the new backend method across the protobuf boundary.
    resp = col._backend.points_at_stake_queue(search="is:new")

    assert list(resp.card_ids) == [heavy_cid, light_cid], (
        "the heavier-weight topic must surface first"
    )
    # Diagnostics travel alongside the ordering.
    assert len(resp.entries) == 2
    assert resp.entries[0].card_id == heavy_cid
    assert resp.entries[0].topic == "circuit_analysis"
    assert resp.entries[0].topic_weight == 10.0
    assert resp.entries[0].weakness == 1.0
    assert resp.entries[0].score == 10.0
    assert resp.entries[1].topic == "ethics"


def test_memory_score_is_withheld_on_a_fresh_deck():
    col = getEmptyCol()
    _add_tagged_note(col, "Norton equivalent?", "circuit_analysis")

    score = col._backend.memory_score(search="is:review OR is:learn")

    # The give-up rule withholds the score until the pre-registered threshold
    # is met; a fresh deck must never show a bare number.
    assert score.shown is False
    assert score.withheld_reason
    assert score.min_reviews_required == 50
    assert score.min_topics_required == 3
