<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Model: Readiness score

*Implementation: `Collection::compute_fe_readiness_score` in
`rslib/src/scheduler/points_at_stake.rs`; surfaced as `readiness_score` over
protobuf.*

## What it measures

**Whether you are ready to pass the FE.** The FE is pass/fail, so this is the
most consequential — and most easily overstated — number in the app. It is
deliberately conservative: it either reports a pass-probability range it can
defend, or it **abstains** and tells you the single best next action.

## Inputs and method

- **Population:** cards matching the readiness search (default
  `is:review OR is:learn`) — your studied cards.
- **Per-card signal:** current FSRS retrievability, exactly as the memory score.
- **Coverage:** the fraction of the ~18 seeded NCEES areas
  (`seed_topic_weights().len()`) that have any graded, memory-bearing card. This
  is the same outline `feprep/coverage_map.py` prints.
- **Aggregate:** when it does not abstain, the pass-probability point estimate is
  the mean recall across all graded cards; coverage and graded-review counts are
  reported alongside.

## How the range is computed

Same honest interval as the other two scores: a **95% confidence interval**
around the mean recall (`confidence_interval`, clamped to `0–100%`, widened for
tiny samples). The result is a pass-probability with a low–high band, never a
bare percentage.

## Pre-registered give-up rule

Readiness is **withheld** unless **both** hold:

- at least **200 graded reviews** (`feReadinessMinReviews`, default 200), **and**
- at least **50% area coverage** (`feReadinessMinCoverage`, default 0.5).

These are strict by design (PRD example). For a small or narrow deck, readiness
**abstains** — and when it abstains it still names the **single best next
action**: the highest-weight NCEES area not yet covered, or, if every area is
covered, your weakest covered area. Abstaining is the honest, rewarded outcome,
not a failure. Coverage is checked against the config line, so
`feprep/coverage_map.py` tells you exactly whether the deck clears the coverage
half of the rule (the shipped FE deck covers 18/18 areas = 100%, above the 50%
line, so coverage never blocks it; the 200-review half still applies).

## Honest limitations

- **Early-stage and deliberately abstaining.** With the default thresholds a
  fresh profile will show no number at all, by design. That is the point: the
  app refuses to tell you you're ready until there is enough evidence to defend
  the claim.
- The pass probability is currently mean FSRS recall over covered areas, **not**
  a model fitted to real FE pass/fail outcomes. It is not calibrated against
  scored exams and should be read as "how well am I recalling across the
  outline", not a certified pass chance.
- Coverage counts an area as covered if it has *any* graded card; breadth is
  measured, depth per area is not. Two of these signals (breadth, recall) can
  disagree, and the give-up rule intentionally errs toward silence.
