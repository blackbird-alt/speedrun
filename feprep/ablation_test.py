# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork — Section 8: the study-feature ablation test.

PRE-REGISTERED HYPOTHESIS (one sentence, written before the numbers):
    At an equal study budget, ordering reviews by points-at-stake
    (topic weight x weakness) raises accuracy on a held-out, exam-weighted
    mixed-topic test versus plain FSRS order and versus plain Anki.

WHAT THIS IS (read this before trusting any number below)
---------------------------------------------------------
This is a **SIMULATION**, not a live-learner study. There are no real students
here. It is a deliberately *fair* test that is allowed to FAIL: if the feature
does not help under the stated assumptions, this script will say so, and a null
result ("no measurable difference") is an honest, acceptable outcome that is
reported exactly as measured — never fudged.

The one thing that is real is the feature under test. Condition 1 calls the
fork's actual Rust backend across the protobuf boundary:

    col._backend.points_at_stake_queue(search=...)

so the ordering being scored is the shipped engine code, not a Python mock.

THE THREE CONDITIONS (same held-out test, same card set, same budget)
---------------------------------------------------------------------
This is a clean ablation of the feature's scoring term `weight x weakness`:

  1. Full app      — order by points-at-stake (weight x weakness). [Rust RPC]
  2. Feature OFF   — order by weakness alone (the weight term ablated away).
                     This is the plain FSRS/engine view: "study your shakiest
                     memories first", with no exam weighting.
  3. Plain Anki    — default new-card order (deck/position = creation order),
                     which ignores both weight and weakness.

THE SIMULATED LEARNER (transparent, documented, fixed seed)
-----------------------------------------------------------
Assumptions, chosen to be standard learning-curve assumptions rather than
anything tuned to flatter the feature:

  * Each of the 18 NCEES areas ("topics") has a mastery in [0, 1].
  * A review of a card in topic t raises that topic's mastery with DIMINISHING
    RETURNS:  mastery += LEARN_RATE * (1 - mastery).
  * Because the increment scales with (1 - mastery), WEAK/low-mastery topics
    gain more per review than already-strong ones.
  * The held-out test is a fresh, mixed-topic exam whose questions are
    distributed by the NCEES topic weights; the expected score is therefore the
    weight-weighted mean of per-topic mastery.
  * Each topic's *starting* mastery is the real FSRS retrievability the engine
    computes for that topic's cards (we seed varied FSRS memory state, then read
    weakness back from the same RPC). So the learner and the queue see one
    coherent world: the queue's "weakness" is literally 1 - the learner's
    starting mastery.

HONEST CAVEAT (the way this test could be rigged, and why it isn't hidden)
-------------------------------------------------------------------------
The learner rewards spending scarce reviews on high-weight, weak topics — which
is exactly what points-at-stake does. So under a SCARCE budget we *expect* it to
win, and it is not a surprise if it does. The test stays fair in two ways it
cannot control: (a) the "Feature OFF" arm already concentrates on weak topics,
so any win is attributable only to the *weight* term, not to weakness; and
(b) at a saturating budget every condition reviews every card, so the model
predicts NO difference — and the script prints that saturating row too, so a
reader can see the effect appear only under scarcity and vanish when it should.

RUN (deterministic given the seeds):
    out\\pyenv\\Scripts\\python.exe feprep\\ablation_test.py
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = ["pylib", "out/pylib"]

from anki.collection import Collection  # noqa: E402  (import first: avoids a
from anki.cards import FSRSMemoryState  # circular import in the pylib package)
from anki.consts import CARD_TYPE_REV, QUEUE_TYPE_REV
from anki.decks import DeckId

# --- NCEES FE Electrical & Computer topic weights (question-count midpoints).
# Identical to the Rust seed (rslib/.../points_at_stake.rs::seed_topic_weights),
# copied here so the simulation is self-contained and explicit. These are FIXED;
# only the (random) weakness landscape varies across seeds.
NCEES_WEIGHTS: dict[str, float] = {
    "mathematics": 14.0,
    "probability_statistics": 5.0,
    "ethics": 4.0,
    "engineering_economics": 4.0,
    "properties_of_electrical_materials": 5.0,
    "engineering_sciences": 7.0,
    "circuit_analysis": 12.0,
    "linear_systems": 6.0,
    "signal_processing": 6.0,
    "electronics": 9.0,
    "power_systems": 10.0,
    "electromagnetics": 6.0,
    "control_systems": 7.0,
    "communications": 6.0,
    "computer_networks": 4.0,
    "digital_systems": 9.0,
    "computer_systems": 5.0,
    "software_development": 5.0,
}
TOPICS = list(NCEES_WEIGHTS)

# --- Simulation constants (all fixed; the run is deterministic given seeds).
CARDS_PER_TOPIC = 8
TOTAL_CARDS = CARDS_PER_TOPIC * len(TOPICS)  # 144
LEARN_RATE = 0.35  # per-review mastery gain fraction of the remaining gap
ELAPSED_DAYS = 10.0  # time since last review used when seeding memory state
SEEDS = list(range(1, 11))  # weakness landscapes; gives the reported range

# Study budgets, as review counts. 50% is the pre-declared HEADLINE budget; the
# others show the gradient and the saturating null.
PRIMARY_BUDGET = TOTAL_CARDS // 2  # 72 reviews  (~50% coverage)
BUDGETS = [TOTAL_CARDS // 4, PRIMARY_BUDGET, TOTAL_CARDS]  # 36, 72, 144

# FSRS-5 forgetting curve, used ONLY to pick memory-state stabilities that yield
# a spread of starting recalls. The authoritative weakness/mastery is read back
# from the engine afterwards, so an imperfect decay guess here is harmless.
_DECAY = -0.5
_FACTOR = 0.9 ** (1.0 / _DECAY) - 1.0


def _stability_for_recall(recall: float, elapsed_days: float) -> float:
    """Stability (days) that produces `recall` at `elapsed_days` under FSRS-5."""
    return _FACTOR * elapsed_days / (recall ** (1.0 / _DECAY) - 1.0)


def _target_recalls(seed: int) -> dict[str, float]:
    """A deterministic, per-seed spread of starting recalls across topics.

    Evenly spaced in [0.35, 0.95] then shuffled onto topics, so every seed is a
    different mix of strong and weak areas — independent of topic weight.
    """
    import random

    n = len(TOPICS)
    spread = [0.35 + (0.95 - 0.35) * i / (n - 1) for i in range(n)]
    rng = random.Random(seed)
    rng.shuffle(spread)
    return dict(zip(TOPICS, spread))


def build_collection(seed: int) -> tuple[Collection, str]:
    """Create a deterministic deck: `CARDS_PER_TOPIC` review cards per topic,
    each seeded with FSRS memory state so the engine computes a genuine,
    per-topic weakness. Notes are added in a seed-shuffled topic order so the
    plain-Anki position order is topic-neutral (neither favouring nor punishing
    heavy topics). Read-only feature under test is never asked to mutate this.
    """
    import random

    tmp = tempfile.mkdtemp()
    col = Collection(str(Path(tmp) / "ablation.anki2"))
    col.set_config("fsrs", True)
    # Weights live in the collection config, exactly as the fork expects.
    col.set_config("feTopicWeights", NCEES_WEIGHTS)

    conf = col.decks.config_dict_for_deck_id(DeckId(1))
    conf["new"]["perDay"] = 100000
    conf["rev"]["perDay"] = 100000
    col.decks.save(conf)

    recalls = _target_recalls(seed)
    now = int(time.time())
    elapsed_secs = int(ELAPSED_DAYS * 86400)

    basic = col.models.by_name("Basic")

    # Build a topic-neutral creation order.
    plan: list[str] = [t for t in TOPICS for _ in range(CARDS_PER_TOPIC)]
    random.Random(seed * 7919).shuffle(plan)

    for i, topic in enumerate(plan):
        note = col.new_note(basic)
        note["Front"] = f"{topic} q{i}"
        note["Back"] = "a"
        note.tags = [f"fe::{topic}"]
        col.add_note(note, DeckId(1))
        cid = col.find_cards(f"nid:{note.id}")[0]

        card = col.get_card(cid)
        card.type = CARD_TYPE_REV
        card.queue = QUEUE_TYPE_REV
        card.reps = 1
        card.ivl = max(1, int(ELAPSED_DAYS))
        card.due = -1  # due (overdue) so it is in the is:due candidate set
        stability = _stability_for_recall(recalls[topic], ELAPSED_DAYS)
        card.memory_state = FSRSMemoryState(stability=stability, difficulty=5.0)
        card.decay = None  # let the engine use its FSRS-5 default decay
        card.last_review_time = now - elapsed_secs
        col.update_card(card)

    return col, tmp


def condition_orders(col: Collection) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Return, per condition, the ordered sequence of *topics* to be studied,
    plus the per-topic starting mastery (= engine recall = 1 - weakness).

    Condition 1 is the real Rust RPC. Conditions 2 and 3 are derived from the
    same per-card diagnostics the RPC returns, so all three order the identical
    card set and differ only in ordering strategy.
    """
    resp = col._backend.points_at_stake_queue(search="is:due OR is:new")
    entries = list(resp.entries)
    assert len(entries) == TOTAL_CARDS, (
        f"expected {TOTAL_CARDS} candidate cards, got {len(entries)}"
    )

    # Starting mastery per topic: all cards in a topic share one recall, so the
    # mean is exact. weakness == 1 - recall by construction in the engine.
    recall_by_topic: dict[str, float] = {}
    for e in entries:
        recall_by_topic.setdefault(e.topic, e.recall)

    # Condition 1 — points-at-stake (weight x weakness): the RPC's own order.
    cond_full = [e.topic for e in entries]

    # Condition 2 — feature OFF: order by weakness alone (weight term ablated),
    # i.e. weakest recall first, then creation order. This is what an FSRS-aware
    # queue does without exam weighting.
    by_weakness = sorted(entries, key=lambda e: (e.recall, e.card_id))
    cond_off = [e.topic for e in by_weakness]

    # Condition 3 — plain Anki: default position/new-card order == creation
    # order == ascending card id.
    by_position = sorted(entries, key=lambda e: e.card_id)
    cond_plain = [e.topic for e in by_position]

    orders = {
        "points_at_stake": cond_full,
        "feature_off": cond_off,
        "plain_anki": cond_plain,
    }
    return orders, recall_by_topic


def simulate(topic_order: list[str], start_mastery: dict[str, float], budget: int) -> dict[str, float]:
    """Study `budget` reviews down `topic_order` (wrapping if the budget exceeds
    one pass) and return the resulting per-topic mastery. Diminishing returns;
    weak topics gain more per review."""
    mastery = dict(start_mastery)
    n = len(topic_order)
    for i in range(budget):
        t = topic_order[i % n]
        mastery[t] += LEARN_RATE * (1.0 - mastery[t])
    return mastery


def weighted_accuracy(mastery: dict[str, float]) -> float:
    """Held-out, exam-weighted accuracy: NCEES-weight-weighted mean mastery."""
    total_w = sum(NCEES_WEIGHTS.values())
    return sum(NCEES_WEIGHTS[t] * mastery[t] for t in TOPICS) / total_w


CONDITION_LABELS = {
    "points_at_stake": "1. Full app (points-at-stake: weight x weakness)",
    "feature_off": "2. Feature OFF (plain FSRS: weakness only)",
    "plain_anki": "3. Plain Anki (default position order)",
}


def _print_verification(seed: int) -> None:
    """Prove the real RPC is doing the work: show the top of the points-at-stake
    order for one seed, with the engine's own weight/weakness/score."""
    col, tmp = build_collection(seed)
    try:
        resp = col._backend.points_at_stake_queue(search="is:due OR is:new")
        print(f"  Engine RPC check (seed {seed}): first 6 of "
              f"{len(resp.entries)} cards in points-at-stake order")
        print(f"    {'topic':<26} {'weight':>6} {'weakness':>8} {'score':>7}")
        for e in list(resp.entries)[:6]:
            print(f"    {e.topic:<26} {e.topic_weight:>6.1f} "
                  f"{e.weakness:>8.2f} {e.score:>7.2f}")
    finally:
        col.close()


def main() -> None:
    print("=" * 74)
    print("SECTION 8 - STUDY-FEATURE ABLATION TEST  (SIMULATION, not a live study)")
    print("=" * 74)
    print("Hypothesis: points-at-stake ordering (weight x weakness) beats plain")
    print("FSRS order and plain Anki on a held-out, exam-weighted test at an")
    print("equal review budget. This is a fair test that is allowed to fail.\n")

    _print_verification(SEEDS[0])
    print()

    # results[budget][condition] = list of weighted accuracies over seeds
    results: dict[int, dict[str, list[float]]] = {
        b: {c: [] for c in CONDITION_LABELS} for b in BUDGETS
    }
    baseline_start: dict[int, list[float]] = {}  # untrained weighted accuracy

    for seed in SEEDS:
        col, tmp = build_collection(seed)
        try:
            orders, start_mastery = condition_orders(col)
        finally:
            col.close()
        baseline_start.setdefault("start", []).append(  # type: ignore[arg-type]
            weighted_accuracy(start_mastery)
        )
        for budget in BUDGETS:
            for cond, order in orders.items():
                final = simulate(order, start_mastery, budget)
                results[budget][cond].append(weighted_accuracy(final))

    start_mean = statistics.mean(baseline_start["start"])  # type: ignore[index]
    print(f"Deck: {len(TOPICS)} NCEES areas x {CARDS_PER_TOPIC} cards = "
          f"{TOTAL_CARDS} cards. Seeds: {SEEDS[0]}..{SEEDS[-1]}.")
    print(f"Untrained weighted accuracy (before any study): {start_mean*100:5.1f}%\n")

    for budget in BUDGETS:
        coverage = budget / TOTAL_CARDS
        tag = "  <-- PRE-DECLARED MAIN NUMBER" if budget == PRIMARY_BUDGET else ""
        saturating = "  (saturating: full pass -> expect NO difference)" \
            if budget >= TOTAL_CARDS else ""
        print(f"BUDGET = {budget} reviews  (~{coverage*100:.0f}% of the deck)"
              f"{tag}{saturating}")
        print(f"  {'condition':<48} {'mean':>7}   {'range (min-max)':>16}")
        for cond, label in CONDITION_LABELS.items():
            vals = results[budget][cond]
            mean = statistics.mean(vals)
            lo, hi = min(vals), max(vals)
            print(f"  {label:<48} {mean*100:6.1f}%   "
                  f"[{lo*100:5.1f}% - {hi*100:5.1f}%]")
        # Honest head-to-head at this budget.
        paw = statistics.mean(results[budget]["points_at_stake"])
        off = statistics.mean(results[budget]["feature_off"])
        plain = statistics.mean(results[budget]["plain_anki"])
        print(f"  delta vs Feature OFF: {(paw-off)*100:+.2f} pts   "
              f"delta vs Plain Anki: {(paw-plain)*100:+.2f} pts")
        print()

    _print_read(results)


def _print_read(results: dict[int, dict[str, list[float]]]) -> None:
    """Print an honest, mechanical read of the primary-budget result."""
    b = PRIMARY_BUDGET
    paw = statistics.mean(results[b]["points_at_stake"])
    off = statistics.mean(results[b]["feature_off"])
    plain = statistics.mean(results[b]["plain_anki"])
    d_off = (paw - off) * 100
    d_plain = (paw - plain) * 100
    # A tiny threshold so we don't call sub-0.1-pt noise a "win".
    eps = 0.1
    print("-" * 74)
    print("HONEST READ (mechanical, at the pre-declared 50% budget):")
    beat_off = d_off > eps
    beat_plain = d_plain > eps
    if beat_off and beat_plain:
        print(f"  Points-at-stake BEAT both baselines: {d_off:+.2f} pts vs Feature")
        print(f"  OFF and {d_plain:+.2f} pts vs Plain Anki. Under a scarce budget the")
        print("  exam-weighting term earns its keep. Note this is a simulation whose")
        print("  assumptions favour concentrating on heavy, weak topics; the win is")
        print("  real within the model but not a live-learner result.")
    elif beat_plain and not beat_off:
        print(f"  MIXED: points-at-stake beat Plain Anki ({d_plain:+.2f} pts) but NOT")
        print(f"  the Feature-OFF (weakness-only) arm ({d_off:+.2f} pts). The gain came")
        print("  from prioritising weak topics, not from the exam-weight term.")
    elif not beat_off and not beat_plain:
        print(f"  NULL RESULT: points-at-stake did not beat the baselines "
              f"({d_off:+.2f} vs OFF, {d_plain:+.2f} vs Plain). Reported as-is.")
    else:
        print(f"  Points-at-stake beat Feature OFF ({d_off:+.2f}) but not Plain Anki"
              f" ({d_plain:+.2f}) — unusual; reported as measured.")
    print("  Saturating budget row above shows the difference collapsing to ~0 when")
    print("  every card is reviewed, which is the expected fair-test behaviour.")
    print("-" * 74)


if __name__ == "__main__":
    main()
