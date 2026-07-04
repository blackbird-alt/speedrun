# FE Electrical and Computer study fork

Fork-specific assets and documentation for the NCEES FE Electrical and Computer
study tool built on Anki's shared Rust engine. The study experience and its three
scores are **model-free** — built only on data the engine already owns (tags +
FSRS memory state). AI is strictly **opt-in**: a key-gated, on-demand
"Generate cards (AI)" action (see `ai/`) that never runs unless you ask it to, and
whose output is grounded in your own verified cards and passed through a verifier
before you add it.

## Contents

- `decks/fe-seed-deck.txt` — the original verified-correct seed deck (40 cards).
- `decks/fe-problems.txt` — worked exam-style problems converted from the
  author's FE practice-problem manuscript, tagged by area **and** by study track
  (`track::durable` / `track::cram`).
- `decks/fe-figures.txt` — figure-based problems whose front is a high-res crop
  of the problem statement + its diagram (the manuscript is a full-page scan).
  Built by `build_figures.py`; **package with `build_apkg.py`** so the images are
  bundled. 114 cards covering the figure-dependent problems in Chapters 7-16
  (Circuit Analysis, Linear Systems, Signal Processing, Electronics, Power,
  Control Systems, Communications, Digital Systems).
- `build_figures.py` — crops figure-dependent problems out of the scanned PDF
  into `decks/media/` and regenerates `decks/fe-figures.txt`. Extend its
  `CHAPTERS` config to cover more chapters. Requires PyMuPDF.
- `decks/media/` — drop images referenced by cards here (auto-bundled).
- `build_apkg.py` — packages one or more source decks into a styled `.apkg`
  (custom "FE Prep" note type, media-aware).
- `docs/deploy-and-run.md` — **how to build, open, package, and deploy the app.**
- `docs/rust-change-note.md` — the one-page note on why the Rust change belongs
  in Rust, the upstream files touched, and the merge-difficulty estimate.

## Quick start

Build, then open the app (see `docs/deploy-and-run.md` for full detail):

```powershell
.\tools\ninja pylib qt
$env:ANKIDEV = "1"
& "out\pyenv\Scripts\pythonw.exe" tools\run.py -b "$env:TEMP\anki_fe_profile"
```

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
- **Ordering** — topics are *interleaved* with frequency proportional to
  `weight × weakness` (stride scheduling), so a study session spans many areas
  instead of front-loading the single heaviest one (which would otherwise serve,
  say, 20 straight Circuit Analysis cards on a fresh profile). Within a topic,
  cards fall back to FSRS due/overdue order then card id.
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

## Durable vs. Cram tracks

The dashboard splits the deck into two study tracks, reflecting that the FE is a
one-time, open-reference exam: **Durable** ("learn for keeps") for the skills a
candidate's career will use, and **Cram** ("peak for test day") for lookup-able
facts that may decay afterwards.

- **Whole-section tracks (no per-card split).** Each FE section (NCEES area) is
  studied **entirely durable or entirely cram** — every card in the area follows
  the area's track. There is no per-card routing.
- **Per-area policy** — resolved in `qt/aqt/deckbrowser.py`, stored under the
  collection-config key `feTrackPolicy`, one of:
  - `durable` — the whole area is learned for keeps. The day-to-day engineering
    core (`FE_DURABLE`) defaults here.
  - `cram` — the whole area is drilled for test day. Everything outside the core
    (Ethics, Economics, Communications, …) defaults to `cram`.
- **User override.** Each area tile shows its current track; clicking it toggles
  the whole section `durable ↔ cram`. Overrides persist in config only — no card
  or scheduling data is touched, so undo and collection integrity are unaffected.
- **How each track studies (the behavioural difference).** Both "Study …
  track" buttons build a native filtered deck from the cards routed to that
  track, but they differ in one setting — `reschedule` — resolved in
  `_fe_start_track` (`qt/aqt/deckbrowser.py`):
  - **Durable** studies with **reschedule on**: answers update FSRS memory
    state and push intervals out, so the material is learned for keeps.
  - **Cram** studies with **reschedule off**: the drill lets the candidate peak
    the cards for test day without writing to the real schedule or memory, so
    nothing durable is built and the cards decay naturally after the exam.
  - The routing/policy above only decides *which* cards land in each track; this
    setting decides *how* each track's session affects long-term memory.

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
