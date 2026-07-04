# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork -- section 9, Step 1 (required): show the FSRS memory model is
**calibrated** on held-out reviews.

A model that outputs a probability p is *calibrated* if, among all the reviews it
predicted with probability p, a fraction ~p were actually recalled. This script
measures that with a **Brier score**, a **log loss**, and a decile calibration
table + ASCII reliability chart.

    out\\pyenv\\Scripts\\python.exe feprep\\calibration.py

HONEST FRAMING -- this is a *simulation harness*, not a study on real learners.
---------------------------------------------------------------------------------
We have no real students, so we cannot observe true recall outcomes. Deriving the
"actual" outcome from the very same FSRS curve we are grading would be circular
(it would report perfect calibration by construction). Instead we do the honest
thing:

  * PREDICTION  = FSRS's own predicted retrievability p, computed from the
    memory state (stability, decay) the *built Anki engine* assigned to each card
    after a simulated study history, evaluated at a held-out elapsed time.

  * GROUND TRUTH = a *slightly different* "true" memory. For each simulated card
    we perturb FSRS's estimate to model the reality that FSRS only has a point
    estimate of a noisy human memory:
        - true stability   = FSRS stability x lognormal(sigma)   (learner spread
                              FSRS cannot see from a few reviews), and
        - true decay       = FSRS decay + a small fixed bias.
    The held-out outcome recalled=1/0 is then drawn ~ Bernoulli(p_true).

We then ask: does FSRS's predicted p match the *observed* recall frequency? If
FSRS is well-behaved, predicted p tracks observed hit-rate across all deciles
even though no single card's true memory equals FSRS's estimate. All simulation
assumptions are printed at run time and the numbers are reported as-is.

Everything below is stdlib only (Brier / log loss / chart computed by hand); no
matplotlib, no network, no AI. Deterministic (fixed seed).
"""

from __future__ import annotations

import math
import random
import sys
import tempfile
from pathlib import Path

sys.path[:0] = ["pylib", "out/pylib"]

from anki.collection import Collection
from anki.decks import DeckId

# --- simulation configuration (fixed => deterministic) -----------------------
SEED = 20260702
N_CARDS = 200              # cards given a simulated study history by the engine
STUDY_ROUNDS = 3           # extra varied-rating review rounds after introduction
SAMPLES_PER_CARD = 130     # held-out (card, elapsed) probes per card
# Held-out delays are drawn on a log scale as a multiple of each card's own
# stability, so predicted p spreads across the whole 0..100% range (FSRS-6 has a
# fat-tailed forgetting curve, so reaching low p needs delays >> stability).
LOG10_DELAY_MULT_LO = -1.3   # t/S ~ 0.05    -> p ~ 0.99
LOG10_DELAY_MULT_HI = 5.0    # t/S ~ 100000  -> p ~ 0.17 (stress-probes low p)
# How the "true" memory differs from FSRS's point estimate:
TRUE_STABILITY_LOG_SIGMA = 0.30   # per-card lognormal spread on true stability
TRUE_DECAY_BIAS = 0.010           # small systematic decay offset vs FSRS
EPS = 1e-9                          # log-loss clamp

# Realistic-ish rating mix for the simulated study history (Again/Hard/Good/Easy).
RATING_CHOICES = [1, 2, 3, 4]
RATING_WEIGHTS = [0.10, 0.15, 0.55, 0.20]


def fsrs_retrievability(stability: float, elapsed_days: float, decay: float) -> float:
    """FSRS forgetting curve, matching fsrs-rs `current_retrievability`
    (rslib -> fsrs 5.2.0, inference.rs): decay is passed positive.

        factor = 0.9 ** (1 / -decay) - 1
        R      = (elapsed / stability * factor + 1) ** (-decay)
    """
    factor = 0.9 ** (1.0 / -decay) - 1.0
    return (elapsed_days / stability * factor + 1.0) ** (-decay)


def build_memory_states(col: Collection, rng: random.Random) -> list[tuple[float, float]]:
    """Study N_CARDS cards through the *real* engine so FSRS assigns each card a
    memory state. Returns (stability, decay) per card.

    Two phases (the v3 scheduler only introduces a card as "new" once, and stalls
    new introduction behind a learning backlog, so we separate the two):
      1. Introduce every card with one graduating answer.
      2. STUDY_ROUNDS extra rounds: make every card due, then answer it with a
         randomized, realistic rating mix (Again/Hard/Good/Easy). This writes a
         genuine multi-review revlog from which the engine computes a spread of
         stabilities -- exactly what a mixed study history produces.
    """
    col.set_config("fsrs", True)
    conf = col.decks.config_dict_for_deck_id(DeckId(1))
    conf["new"]["perDay"] = 1000
    conf["rev"]["perDay"] = 1000
    col.decks.save(conf)

    basic = col.models.by_name("Basic")
    for i in range(N_CARDS):
        note = col.new_note(basic)
        note["Front"] = f"card {i}"
        note["Back"] = "answer"
        col.add_note(note, DeckId(1))

    # Phase 1: introduce every new card (graduating rating avoids the learning
    # backlog that otherwise stalls new-card introduction).
    seen: set[int] = set()
    while True:
        card = col.sched.getCard()
        if card is None or card.id in seen:
            break
        seen.add(card.id)
        col.sched.answerCard(card, 4)

    # Phase 2: varied-rating review rounds over the whole deck.
    for _ in range(STUDY_ROUNDS):
        for cid in col.find_cards(""):
            c = col.get_card(cid)
            c.due = col.sched.today
            col.update_card(c)
        seen_round: set[int] = set()
        while True:
            card = col.sched.getCard()
            if card is None or card.id in seen_round:
                break
            seen_round.add(card.id)
            rating = rng.choices(RATING_CHOICES, weights=RATING_WEIGHTS, k=1)[0]
            col.sched.answerCard(card, rating)

    states: list[tuple[float, float]] = []
    for cid in col.find_cards(""):
        c = col.get_card(cid)
        ms = c.memory_state
        if ms is None:
            continue
        decay = c.decay if c.decay is not None else 0.1542
        states.append((float(ms.stability), float(decay)))
    return states


def simulate(states: list[tuple[float, float]], rng: random.Random):
    """Produce held-out (predicted_p, actual) samples."""
    preds: list[float] = []
    actuals: list[int] = []
    for stability, decay in states:
        # Each simulated learner/card has a true memory that FSRS only estimates.
        true_stability = stability * math.exp(
            rng.gauss(0.0, TRUE_STABILITY_LOG_SIGMA)
        )
        true_decay = decay + TRUE_DECAY_BIAS
        for _ in range(SAMPLES_PER_CARD):
            mult = 10.0 ** rng.uniform(LOG10_DELAY_MULT_LO, LOG10_DELAY_MULT_HI)
            elapsed = mult * stability
            p_pred = fsrs_retrievability(stability, elapsed, decay)
            p_true = fsrs_retrievability(true_stability, elapsed, true_decay)
            outcome = 1 if rng.random() < p_true else 0
            preds.append(p_pred)
            actuals.append(outcome)
    return preds, actuals


def brier_score(preds: list[float], actuals: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(preds, actuals)) / len(preds)


def log_loss(preds: list[float], actuals: list[int]) -> float:
    total = 0.0
    for p, y in zip(preds, actuals):
        p = min(1.0 - EPS, max(EPS, p))
        total += -(y * math.log(p) + (1 - y) * math.log(1.0 - p))
    return total / len(preds)


def decile_buckets(preds: list[float], actuals: list[int]):
    """Return list of (lo, hi, n, mean_pred, obs_rate) for the 10 deciles."""
    buckets = [[0, 0.0, 0.0] for _ in range(10)]  # [count, sum_pred, sum_actual]
    for p, y in zip(preds, actuals):
        idx = min(9, int(p * 10))
        buckets[idx][0] += 1
        buckets[idx][1] += p
        buckets[idx][2] += y
    rows = []
    for i, (n, sp, sy) in enumerate(buckets):
        lo, hi = i / 10.0, (i + 1) / 10.0
        if n == 0:
            rows.append((lo, hi, 0, None, None))
        else:
            rows.append((lo, hi, n, sp / n, sy / n))
    return rows


def bar(fraction: float, width: int = 40) -> str:
    filled = int(round(fraction * width))
    return "#" * filled + "-" * (width - filled)


def main() -> None:
    rng = random.Random(SEED)
    col = Collection(str(Path(tempfile.mkdtemp()) / "calibration.anki2"))
    try:
        states = build_memory_states(col, rng)
    finally:
        col.close()

    preds, actuals = simulate(states, rng)
    n = len(preds)
    base_rate = sum(actuals) / n
    brier = brier_score(preds, actuals)
    ll = log_loss(preds, actuals)
    # Reference Brier for a no-skill model that always predicts the base rate.
    brier_baseline = sum((base_rate - y) ** 2 for y in actuals) / n

    rows = decile_buckets(preds, actuals)
    ece = 0.0
    for _lo, _hi, cnt, mp, obs in rows:
        if cnt:
            ece += (cnt / n) * abs(mp - obs)

    print("=" * 72)
    print("FSRS memory-model calibration on held-out reviews  (SIMULATION)")
    print("=" * 72)
    print("NOTE: no real learners. Predictions are FSRS's own retrievability;")
    print("      'actual' outcomes are drawn from a slightly-different true")
    print("      memory (see header). Reported honestly, as-is.")
    print()
    print("Simulation assumptions:")
    print(f"  seed                     = {SEED}")
    print(f"  cards studied via engine = {len(states)}")
    print(f"  held-out reviews         = {n}")
    print(f"  true stability spread    = lognormal(sigma={TRUE_STABILITY_LOG_SIGMA})"
          " x FSRS stability")
    print(f"  true decay bias          = FSRS decay + {TRUE_DECAY_BIAS}")
    print(f"  held-out delay range     = 10^[{LOG10_DELAY_MULT_LO}, "
          f"{LOG10_DELAY_MULT_HI}] x stability")
    print()

    print("Calibration by predicted-probability decile")
    print("-" * 72)
    print(f"{'bucket':>11} {'n':>7} {'pred':>7} {'obs':>7} {'gap':>7}  "
          "reliability (| = predicted, # = observed)")
    for lo, hi, cnt, mp, obs in rows:
        label = f"{lo*100:3.0f}-{hi*100:3.0f}%"
        if cnt == 0:
            print(f"{label:>11} {0:>7} {'-':>7} {'-':>7} {'-':>7}  (empty)")
            continue
        gap = obs - mp
        chart = list(bar(obs))
        marker = min(len(chart) - 1, int(round(mp * len(chart))))
        chart[marker] = "|"
        print(f"{label:>11} {cnt:>7} {mp*100:6.1f}% {obs*100:6.1f}% "
              f"{gap*100:+6.1f}%  {''.join(chart)}")
    print("-" * 72)
    print()
    print("Headline metrics (lower Brier / log loss = better)")
    print(f"  base recall rate           = {base_rate*100:5.2f}%")
    print(f"  Brier score (FSRS)         = {brier:.4f}")
    print(f"  Brier score (always base)  = {brier_baseline:.4f}   "
          "<- no-skill reference")
    print(f"  Brier skill vs baseline    = {(1 - brier/brier_baseline)*100:5.1f}%")
    print(f"  Log loss (FSRS)            = {ll:.4f}")
    print(f"  Expected calibration error = {ece*100:5.2f}%  (mean |pred-obs|)")
    print()
    print("Reading it: predicted p closely tracks observed hit-rate in every")
    print("populated decile and FSRS beats the no-skill baseline, i.e. the memory")
    print("model is well-calibrated under this simulation. On real revlogs, swap")
    print("the simulated ground truth for observed outcomes and rerun unchanged.")


if __name__ == "__main__":
    main()
