# FE Electrical and Computer study fork

Fork-specific assets and documentation for the NCEES FE Electrical and Computer
study tool built on Anki's shared Rust engine. Everything here is **AI-free** and
built only on data the engine already owns (tags + FSRS memory state).

## Contents

- `decks/fe-seed-deck.txt` — the verified-correct seed deck (see below).
- `build_apkg.py` — packages the seed deck into an `.apkg`.
- `docs/rust-change-note.md` — the one-page note on why the Rust change belongs
  in Rust, the upstream files touched, and the merge-difficulty estimate.

## The two features

### 1. Points-at-stake queue

A read-only re-ordering of the due/new queue by `topic weight × student
weakness`. Lives in `rslib/src/scheduler/points_at_stake.rs`, exposed as the
`PointsAtStakeQueue` RPC on `SchedulerService`.

- **Topic weight** — static per-area value seeded from the NCEES question-count
  ranges (Mathematics and Circuit Analysis outweigh Ethics and Engineering
  Economics). Stored in the collection config table under `feTopicWeights`, not
  in engine code. Unknown/untagged topics fall back to `feDefaultTopicWeight`.
- **Student weakness** — `1 − mean recall` for the topic, from FSRS
  retrievability over the topic's cards with history. A topic with no history
  defaults to maximum weakness (1.0) so unseen high-weight areas surface early.
- **Ordering** — `weight × weakness` descending; ties break on FSRS
  due/overdue ordering, so it degrades to normal Anki behaviour when weights are
  equal.
- It writes no scheduling state → undo and collection integrity are unaffected.

### 2. Honest memory score

Aggregates Anki's existing FSRS retrievability into a single **memory** score.
Exposed as the `MemoryScore` RPC. It is presented honestly:

- a point estimate,
- a likely range around it (a 95% interval), not a single figure,
- coverage: how much of the studied material the estimate is based on,
- when it was last updated,
- the main reason behind the current value (the weakest covered topic),
- and the give-up rule that governs when it shows nothing.

#### Give-up rule (pre-registered)

No memory score is shown until there are **at least 50 graded reviews across at
least 3 topics** (and at least one card with FSRS memory state). Below that line
the app reports that it does not yet have enough data. The thresholds are
parameters set in advance — config keys `feMemoryMinReviews` (default 50) and
`feMemoryMinTopics` (default 3) — not tuned after seeing the results.

A single blended readiness number is deliberately **not** built. Only the memory
score exists, and it follows the honesty rule above.

## The seed deck

`decks/fe-seed-deck.txt` is a small-but-real, **verified-correct** seed deck:

- 40 cards across 9 NCEES knowledge areas (Mathematics, Circuit Analysis,
  Electronics, Power Systems, Digital Systems, Control Systems, Ethics,
  Engineering Economics, Probability & Statistics).
- Every card is tagged with exactly one area as `fe::<area_key>`, which is what
  the points-at-stake queue and the per-topic memory aggregation key off.
- Correctness is the floor: these are foundational, checkable facts (Ohm's law,
  op-amp virtual short, √3 line/phase relationships, two's complement, the NSPE
  paramount-obligation clause, `F = P(1+i)^n`, …). Full exam-item authoring is
  out of scope; a smaller correct deck beats a larger deck that trains false
  confidence.

### Using the deck

- **Import directly:** File > Import and choose `decks/fe-seed-deck.txt`. The
  header directives set the note type (Basic), deck, and the tags column.
- **Package it:** `python feprep/build_apkg.py` (requires a built `anki` pylib)
  writes `decks/fe-seed-deck.apkg`.

The topic-key convention is `fe::<snake_case_area>`. The recognised keys match
the `feTopicWeights` seed in `rslib/src/scheduler/points_at_stake.rs`
(`seed_topic_weights`).

## Tests

- Rust: `cargo test -p anki scheduler::points_at_stake` runs the ordering,
  tie-break, degenerate-input, and memory-score tests.
- Python (through protobuf): `pytest pylib/tests/test_points_at_stake.py`
  against a built pylib.
