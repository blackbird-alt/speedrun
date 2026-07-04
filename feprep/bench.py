# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork: one-command performance benchmark (PRD challenge 7h) that
checks the section-10 speed targets against the *real built engine*.

Builds a large FE deck in a throwaway collection (default 50,000 cards tagged
`fe::<area>` across the ~18 NCEES areas, FSRS on), studies a chunk so the honest
scores have data, then measures p50 / p95 / worst latency for the hot-path
operations a student hits during review and on the dashboard. Nothing here is
mocked: every timing drives the same Rust engine (`rslib`) the desktop app and
AnkiDroid use, through the pylib API.

Section-10 targets checked:
  * answer a card      (col.sched.answerCard)          p95 < 50 ms
  * fetch next card    (col.sched.get_queued_cards)    p95 < 100 ms
  * dashboard 1st load (memory_score)                  p95 < 1000 ms
  * dashboard refresh  (memory_score, repeat)          p95 < 500 ms
Also timed (no hard target, reported for visibility): points_at_stake_queue,
performance_score, readiness_score.

Run from the repo root with the fork's built pyenv:

    out\\pyenv\\Scripts\\python.exe feprep\\bench.py
    out\\pyenv\\Scripts\\python.exe feprep\\bench.py --n 50000 --answered 3000
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = ["pylib", "out/pylib"]

from anki.collection import Collection
from anki.decks import DeckId, UpdateDeckConfigs, UpdateDeckConfigsMode

# The v3 engine caps the per-day new/review limit at 9999; larger values are
# rejected and silently fall back to the 20/day default, so we cap here. This is
# plenty to study a few-thousand-card chunk in one "day" for the benchmark.
MAX_PER_DAY = 9999

# The 18 NCEES FE (Electrical & Computer) areas, mirroring the engine's
# seed_topic_weights() in rslib/src/scheduler/points_at_stake.rs. The deck is
# spread across these so tag:fe::<area> queries and the readiness coverage
# calculation see a realistic population.
FE_AREAS = [
    "mathematics",
    "probability_statistics",
    "ethics",
    "engineering_economics",
    "properties_of_electrical_materials",
    "engineering_sciences",
    "circuit_analysis",
    "linear_systems",
    "signal_processing",
    "electronics",
    "power_systems",
    "electromagnetics",
    "control_systems",
    "communications",
    "computer_networks",
    "digital_systems",
    "computer_systems",
    "software_development",
]

# Areas whose worked problems land in the "durable" track (mirrors the desktop
# default in qt/aqt/deckbrowser.py); performance_score reads tag:track::durable.
DURABLE_AREAS = {"circuit_analysis", "power_systems", "electronics", "digital_systems"}


def _percentile(sorted_ms: list[float], p: float) -> float:
    """Linear-interpolated percentile over an ascending list of samples (ms)."""
    if not sorted_ms:
        return float("nan")
    if len(sorted_ms) == 1:
        return sorted_ms[0]
    rank = (p / 100.0) * (len(sorted_ms) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_ms) - 1)
    frac = rank - lo
    return sorted_ms[lo] + (sorted_ms[hi] - sorted_ms[lo]) * frac


class Stat:
    def __init__(self, name: str, samples_ms: list[float], target_ms: float | None):
        self.name = name
        s = sorted(samples_ms)
        self.n = len(s)
        self.p50 = _percentile(s, 50)
        self.p95 = _percentile(s, 95)
        self.worst = s[-1] if s else float("nan")
        self.target_ms = target_ms

    @property
    def passed(self) -> bool | None:
        if self.target_ms is None:
            return None
        return self.p95 < self.target_ms


def _time_calls(fn, iterations: int) -> list[float]:
    """Call fn() `iterations` times, returning per-call wall time in ms."""
    out: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def build_deck(col: Collection, n: int) -> int:
    """Add n Basic notes spread round-robin across the 18 FE areas. Returns the
    number of cards actually created."""
    col.set_config("fsrs", True)
    # Raise the per-day new/review limits (default is 20/day) so we can actually
    # study a few-thousand-card chunk. The legacy config-save path is a no-op for
    # the v3 scheduler in this build, so go through the modern update_deck_configs
    # RPC and disable the "cap new to review" rule.
    data = col.decks.get_deck_configs_for_update(DeckId(1))
    conf = data.all_config[0].config
    conf.config.new_per_day = MAX_PER_DAY
    conf.config.reviews_per_day = MAX_PER_DAY
    col.decks.update_deck_configs(UpdateDeckConfigs(
        target_deck_id=1,
        configs=[conf],
        mode=UpdateDeckConfigsMode.UPDATE_DECK_CONFIGS_MODE_NORMAL,
        new_cards_ignore_review_limit=True,
        fsrs=True,
    ))

    basic = col.models.by_name("Basic")
    did = DeckId(1)
    for i in range(n):
        area = FE_AREAS[i % len(FE_AREAS)]
        note = col.new_note(basic)
        note["Front"] = f"{area} q{i}"
        note["Back"] = f"a{i}"
        tags = [f"fe::{area}"]
        # Roughly a third of durable-area cards are worked problems.
        if area in DURABLE_AREAS and (i % 3 == 0):
            tags.append("track::durable")
        note.tags = tags
        col.add_note(note, did)
    return col.card_count()


def study_chunk(col: Collection, count: int) -> int:
    """Answer up to `count` distinct queued cards Easy (rating 4) so they
    graduate to the review queue with FSRS memory state + revlog history, which
    is what the honest scores read. Returns the number answered."""
    answered = 0
    seen: set[int] = set()
    for _ in range(count):
        card = col.sched.getCard()
        if card is None or card.id in seen:
            break
        seen.add(card.id)
        card.start_timer()
        col.sched.answerCard(card, 4)
        answered += 1
    return answered


def bench_answer(col: Collection, iterations: int) -> list[float]:
    """Time only the answerCard call, consuming fresh new cards each iteration."""
    samples: list[float] = []
    seen: set[int] = set()
    for _ in range(iterations):
        card = col.sched.getCard()
        if card is None or card.id in seen:
            break
        seen.add(card.id)
        card.start_timer()
        t0 = time.perf_counter()
        col.sched.answerCard(card, 3)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


def _mem(col: Collection):
    return col._backend.memory_score(search="is:review OR is:learn")


def main() -> None:
    ap = argparse.ArgumentParser(description="FE-prep section-10 speed benchmark")
    ap.add_argument("--n", type=int, default=50000, help="target deck size (cards)")
    ap.add_argument("--answered", type=int, default=3000,
                    help="cards to study before measuring (populates the scores)")
    ap.add_argument("--answer-iters", type=int, default=2000)
    ap.add_argument("--fetch-iters", type=int, default=500)
    ap.add_argument("--score-iters", type=int, default=25)
    args = ap.parse_args()

    try:
        import psutil  # type: ignore
        proc = psutil.Process()
    except Exception:
        proc = None

    tmp = Path(tempfile.mkdtemp())
    col = Collection(str(tmp / "bench.anki2"))

    stats: list[Stat] = []
    try:
        print(f"building deck: target {args.n} cards across {len(FE_AREAS)} FE areas "
              f"(FSRS on)...")
        t0 = time.perf_counter()
        deck_size = build_deck(col, args.n)
        build_s = time.perf_counter() - t0
        print(f"  built {deck_size} cards in {build_s:.1f}s")

        print(f"studying {args.answered} cards (Easy) to populate the scores...")
        t0 = time.perf_counter()
        answered = study_chunk(col, args.answered)
        study_s = time.perf_counter() - t0
        print(f"  answered {answered} cards in {study_s:.1f}s\n")

        # --- latency measurements (all against the real engine) ---
        print("measuring latencies...")
        answer_ms = bench_answer(col, args.answer_iters)
        stats.append(Stat("answer a card (answerCard)", answer_ms, 50.0))

        fetch_ms = _time_calls(
            lambda: col.sched.get_queued_cards(fetch_limit=1), args.fetch_iters)
        stats.append(Stat("fetch next card (get_queued_cards)", fetch_ms, 100.0))

        # First-load vs refresh both drive memory_score; the engine recomputes
        # each call, so we report two honest batches against the two targets.
        mem_first = _time_calls(lambda: _mem(col), args.score_iters)
        stats.append(Stat("dashboard first load (memory_score)", mem_first, 1000.0))

        mem_refresh = _time_calls(lambda: _mem(col), args.score_iters)
        stats.append(Stat("dashboard refresh (memory_score)", mem_refresh, 500.0))

        # points_at_stake ranks the whole due/new population, so it is the most
        # expensive call on a 50k deck; fewer iterations keep the run short.
        pas_ms = _time_calls(
            lambda: col._backend.points_at_stake_queue(search="is:due OR is:new"),
            max(5, args.score_iters // 3))
        stats.append(Stat("points_at_stake_queue", pas_ms, None))

        perf_ms = _time_calls(
            lambda: col._backend.performance_score(search="tag:track::durable"),
            args.score_iters)
        stats.append(Stat("performance_score", perf_ms, None))

        rdy_ms = _time_calls(
            lambda: col._backend.readiness_score(search="is:review OR is:learn"),
            args.score_iters)
        stats.append(Stat("readiness_score", rdy_ms, None))
    finally:
        rss_mb = proc.memory_info().rss / (1024 * 1024) if proc else None
        col.close()

    # --- report ---
    print()
    print(f"deck size: {deck_size} cards | studied: {answered} | FSRS: on")
    if rss_mb is not None:
        print(f"process RSS: {rss_mb:.0f} MB")
    else:
        print("process RSS: (psutil not installed - skipped)")
    print()

    name_w = max(len(s.name) for s in stats)
    header = (f"{'operation':<{name_w}}  {'p50':>9}  {'p95':>9}  {'worst':>9}  "
              f"{'target':>9}  result")
    print(header)
    print("-" * len(header))
    all_pass = True
    for s in stats:
        tgt = f"<{s.target_ms:.0f}ms" if s.target_ms is not None else "-"
        if s.passed is None:
            result = "  -"
        elif s.passed:
            result = "PASS"
        else:
            result = "FAIL"
            all_pass = False
        print(f"{s.name:<{name_w}}  {s.p50:>7.2f}ms  {s.p95:>7.2f}ms  "
              f"{s.worst:>7.2f}ms  {tgt:>9}  {result}")

    print()
    graded = [s for s in stats if s.passed is not None]
    n_pass = sum(1 for s in graded if s.passed)
    print(f"==== BENCH {'PASSED' if all_pass else 'FAILED'}: "
          f"{n_pass}/{len(graded)} targets met ====")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
