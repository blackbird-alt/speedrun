<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Model: Memory score

*Implementation: `Collection::compute_fe_memory_score` in
`rslib/src/scheduler/points_at_stake.rs`; surfaced as `memory_score` over
protobuf and rendered by `_fe_mem_panel` in `qt/aqt/deckbrowser.py`.*

## What it measures

**How much of the material you have studied you can recall right now.** It is an
honest aggregate of Anki's *existing* FSRS retrievability — the per-card
probability that you would answer correctly at this instant — not a new model
and not a prediction about the exam. It answers "of the cards I've actually
studied, what share do I still know?"

## Inputs and method

- **Population:** cards matching the memory search (default
  `is:review OR is:learn`) — i.e. cards you have started studying.
- **Per-card signal:** for every card that has FSRS memory state, the engine
  computes current retrievability from `(stability, difficulty)`, the time
  elapsed since the last review, and the card's decay. Cards with no memory
  state contribute to *coverage* but not to the estimate.
- **Aggregate:** the **point estimate** is the mean per-card recall over cards
  that have memory state. Topic keys come from each note's first `fe::<key>`
  tag, and the weakest covered topic is reported as the main driver.

## How the range is computed

The displayed range is a **95% confidence interval around the mean recall**
(`confidence_interval`): mean ± `1.96 × standard error`, clamped to `0–100%`.
With only a single observation the interval widens to ±0.5 to reflect that thin
data honestly tells us little about the spread. The UI shows the point estimate
with "likely low–high%" beside it, never a bare single number.

## Pre-registered give-up rule

The score is **withheld** (shown as "not enough data yet") unless **all** hold:

- at least **50 graded reviews** (`feMemoryMinReviews`, default 50), across
- at least **3 distinct topics** (`feMemoryMinTopics`, default 3), and
- at least one card with FSRS memory state.

These thresholds are constants set **in advance** (PRD §7.3), not tuned after
seeing which number looked good. Below the line the app states the shortfall
(e.g. "12/50 graded reviews across 2/3 topics") instead of a number.

## Honest limitations

- It measures **recall of studied material**, not exam readiness and not
  coverage of the whole outline — a high memory score on three topics says
  nothing about the other fifteen. (Coverage is reported separately; see
  `feprep/coverage_map.py` and the readiness model.)
- Retrievability is only as good as FSRS's fit to your history; early on, with
  few reviews, the interval is wide by design.
- Cards without memory state (brand-new, never graded) are excluded from the
  estimate, so the number describes what you've *studied*, not what you *own*.
- It is a within-app measurement of your own reviews, not a validated predictor
  of scored-test performance.
