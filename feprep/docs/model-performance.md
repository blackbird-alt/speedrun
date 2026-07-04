<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Model: Performance score

*Implementation: `Collection::compute_fe_performance_score` in
`rslib/src/scheduler/points_at_stake.rs`; surfaced as `performance_score` over
protobuf.*

## What it measures

**How well you handle worked, exam-style problems** — not plain facts or
definitions. It is scoped to the *problem* population so it is a genuinely
different number from the memory score, not a relabelled copy. It answers "on
the kind of multi-step problems the FE actually asks, how am I doing?"

## Inputs and method

- **Population:** cards matching the performance search, default
  `tag:track::durable` — the worked, exam-style problem cards, as opposed to the
  fact/cram cards the memory score covers.
- **Per-card signal:** identical to the memory score — current FSRS
  retrievability from `(stability, difficulty)`, elapsed time, and decay — but
  computed only over this problem population.
- **Aggregate:** the point estimate is the mean per-card recall over
  problem cards that have memory state; topic keys come from `fe::<key>` tags,
  and the weakest covered topic is reported.

## How the range is computed

Same honest interval as the memory score: a **95% confidence interval** around
the mean (`confidence_interval` — mean ± `1.96 × standard error`, clamped to
`0–100%`, widened for tiny samples). Always shown as a point estimate plus a
low–high range, never a lone number.

## Pre-registered give-up rule

The score is **withheld** unless **both** hold:

- at least **30 graded exam-style reviews** (`fePerformanceMinReviews`,
  default 30), across
- at least **2 distinct topics** (`fePerformanceMinTopics`, default 2),

with at least one problem card carrying FSRS memory state. The thresholds are
lower than the memory score's because the exam-style card population is a subset
of all cards; they are still fixed in advance, not tuned to the data. Below the
line the app reports the shortfall instead of a number.

## Honest limitations

- **Early-stage.** The signal is still FSRS retrievability on problem-tagged
  cards, which is a proxy for problem-solving skill, not a direct grade of
  worked solutions. It does not yet parse steps, partial credit, or time taken.
- It depends on cards being correctly tagged `track::durable`; a deck without
  worked problems will simply keep the score withheld rather than invent one.
- Like the memory score, it describes performance on *studied* problem cards,
  not the full exam, and it is not a validated predictor of scored-test results.
- Because it is a subset of the memory population, a small deck may cross the
  memory threshold while performance stays honestly withheld.
