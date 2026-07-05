<!--
Copyright: Ellie Zhang. Project BrainLift for the Speedrun FE Electrical and
Computer study app. Structure follows Patrick's BrainLift outline (Owner,
Purpose, Spiky POVs [DOK4], Experts, Insights [DOK3], Knowledge Tree [DOK2]).
-->

# BrainLift — Speedrun: A Study App for the FE Electrical and Computer Exam

**Owner:** Ellie Zhang

## Purpose

Core goal: build an FE Electrical and Computer study tool on a forked Anki
engine, while treating that engine as a *starting codebase* rather than the right
learning model. The FE is a broad survey across roughly 17 knowledge areas, taken
once at a Pearson VUE center on a date the candidate picks, with a searchable
on-screen handbook as the only reference. What follows from that shape is the
product: the tool sorts a candidate's topics into the ones worth learning deeply
and the ones worth only cramming, treats each group with a different method,
schedules toward a single near-term date instead of lifetime memory, and produces
a readiness signal it can actually defend.

**In scope:** the NCEES FE Electrical and Computer exam (current CBT spec, ~17
areas, Pearson VUE, year-round; pass/fail, criterion-referenced); product
decisions (feature priority, content strategy, pedagogy, durable-vs-cram
routing); learning-science evidence for a timed, single-reference, one-time,
breadth-first problem-solving exam taken mostly by students and early-career
engineers.

**Out of scope:** authoring question content itself; a RAG/retrieval pipeline;
the PE exams and other FE disciplines (the durable-vs-cram engine would carry
over later, but the first build targets FE Electrical and Computer).

---

## DOK 4 — Spiky Points of View

### SPOV 1 — Most of what you study for this exam, you should plan to forget.

Learn only the areas your career will use; cram the rest to last until the test
date and let it rot afterward. A spaced-repetition app like Anki is built to stop
knowledge from fading — the *wrong* goal for a one-time, open-reference exam.

Anki/FSRS exist to keep knowledge alive over years with as few reviews as
possible. None of that is the FE's goal: the exam happens once, on a date the
candidate chooses, with an open searchable handbook. Spending durable-encoding
effort (more reviews, deeper processing) on a topic the candidate will never use
again is wasted. Cepeda et al. (2008) point the same way: the optimal study gap
shrinks as a proportion of the retention interval, so a near-term date wants
compressed, high-retention scheduling, not Anki's expanding lifetime intervals.

The spiky part is **the split**: ask the candidate their specialty, map it onto
the ~17 areas, give field-matched areas the **durable** track (deeper encoding, a
schedule built to last past the exam) and everything else the **cram** track
(recognition-and-lookup practice peaked on the test date, decay accepted after).

Two honest objections: (a) the spaced-repetition community will say you're
building fragile knowledge on purpose — correct, and *decay is the intended
outcome* for a throwaway topic; the risk is contained by still spacing cram
topics across the final weeks, not massing them. (b) FE takers often don't know
their specialty yet — handled by **defaulting to durable under uncertainty**,
letting the user edit any topic's flag, and never silently demoting something
marked important.

**Build implications:** field capture + mapping to areas; two scheduler tracks
(durable/cram) with a visible, user-editable per-area flag defaulting to durable;
cram still spaced across the final weeks. *Implemented as the whole-section
durable/cram track policy (`qt/aqt/deckbrowser.py`, `feTrackPolicy`), where cram
studies with reschedule off (peaks then decays) and durable with reschedule on.*

### SPOV 2 — Massed, repetitive drilling is the right way to *start* a hard topic.

Interleaving and spacing are oversold for beginners. Make a novice grind the same
problem type many times before mixing anything in, or the mixing is just noise.
The order that works is **blocked first, then interleaved once fluent.**

The contrarian half is the blocked phase. The expertise reversal effect (Kalyuga,
Ayres, Chandler, Sweller, 2003 — run on electrical apprentices reading wiring
diagrams) shows worked examples and heavy guidance help a novice, then start
hurting once a schema forms, at which point solving fresh problems wins. So: serve
several problems of one type in a row to build the pattern — then stop, because
staying blocked is its own trap (Taylor & Rohrer 2010: blocked practice gave ~99%
practice accuracy but ~half the delayed-test score — the false-confidence pattern).

The limit that keeps this from overclaiming: interleaving's effect size is not
reliable (d ≈ 0.35–0.83; Brunmair & Richter 2019 meta-analysis Hedges' g = 0.42;
no benefit on complex procedural material for non-fluent learners — Albaret &
Thon 1998; Yan et al. 2024). Hence a **fluency gate**: don't promote a candidate
into mixed sets until they clear a per-topic accuracy-and-speed bar, and don't
expect the headline effect on a novice population.

**Build implications:** each durable topic moves blocked → mixed after clearing a
per-topic bar. **Pre-registered hypothesis:** blocked-to-interleaved practice
raises accuracy on new mixed-topic questions at equal study time, versus both
pure interleaving and plain Anki. A bank that serves wrong or trivial problems is
worse than none (it trains false confidence): **verified-correct content is the
floor.** *The study-feature test lives in `feprep/ablation_test.py` (three builds,
equal budget); the "verified-correct floor" is enforced by the AI verifier gate.*

---

## Experts

- **Robert A. Bjork** (UCLA) — "desirable difficulties," New Theory of Disuse; why
  spacing/interleaving/testing build durable retention and massed practice yields
  fast-but-fragile gains. Backbone for SPOV 2 and SPOV 1's "crammed knowledge
  decays." <https://bjorklab.psych.ucla.edu/>
- **John Sweller & Slava Kalyuga** (UNSW) — Cognitive Load Theory & the expertise
  reversal effect; blocked/guided practice first for novices. Evidence for SPOV
  2's contrarian half. <https://link.springer.com/article/10.1007/s11251-009-9102-0>
- **Doug Rohrer** (USF) — interleaving vs blocking in STEM RCTs (d ≈ 0.83, 2020).
  Evidence for the interleaved phase and its stated limits. <http://uweb.cas.usf.edu/~drohrer/>
- **Veronica X. Yan** (UT Austin) — 2024 interleaving-limitations review; when
  interleaving fails (complex material, novices, overload). Justifies the fluency
  gate. <https://educationalpsychology.utexas.edu/>
- **Roediger & Karpicke** (WashU/Purdue) — testing effect; recall beats
  recognition; illusion of competence from restudy. Grounds the problem-first
  default. <https://psych.wustl.edu/>
- **John Dunlosky** (Kent State) — 2013 PSPI technique ranking; high-utility
  (practice testing, distributed practice) vs low-utility (rereading,
  highlighting). Justifies what to build/discourage. <https://www.kent.edu/psychology/profile/john-dunlosky>
- **ASEE, "Open-Book Problem Solving in Engineering"** — reference-search time
  dominates and is negatively associated with performance; backs the
  recognition-and-lookup emphasis of the cram track (one exploratory paper,
  treated as suggestive). <https://peer.asee.org/open-book-problem-solving-in-engineering-an-exploratory-study.pdf>
- **Wasim Asghar (studyforfe.com)** — full-coverage FE review; defines the
  comprehensive-coverage bar the product deliberately refuses to match, and a
  model of good FE explanations. <https://www.studyforfe.com/>
- **Justin Kauwale (Engineering Pro Guides)** — clearest public mapping of
  question counts to areas; feeds the points-at-stake weighting.
  <https://www.engproguides.com/fe-electrical-exam-pass.html>

---

## DOK 3 — Insights

**From exam structure**
1. *(Contrarian)* The FE forces breadth a career won't. Of ~17 areas, a specialty
   uses a minority, so the highest-leverage decision is which areas to *skip
   understanding on entirely*. (→ SPOV 1)
2. The searchable handbook doesn't make navigation free: keyword search returns
   scattered hits, and under ~3 min/question there's no time to skim, so knowing
   the handbook's structure cold is a trainable performance skill. (→ SPOV 1)
3. The exam is fully interleaved across areas, so topic-by-topic banks train the
   wrong recognition skill — but a novice needs a blocked phase first. (→ SPOV 2)

**From learning science**
4. *(Contrarian)* Novices and near-experts need opposite instruction (expertise
   reversal): blocked/guided first, problem-solving later — and it matters more
   for the FE than PE because FE takers skew novice. (→ SPOV 2)
5. Spacing isn't one-size-fits-all (Cepeda): a near-term date needs compressed,
   high-retention scheduling — the first crack in generic FSRS. (→ SPOV 1 & 2)
6. *(Contrarian)* Durable retention is the wrong objective for most of this exam;
   for out-of-field areas, peak on one date and let it decay. (→ SPOV 1)
7. Confidence is a biased readiness estimator (Dunlosky & Rawson; Morphew) —
   part of why repeat-takers who study the same way pass at a lower rate. A
   calibrated readiness output should be a *range* and hand the stop/go call to
   the user. (The 64%→31% first-to-repeat drop is partly a selection effect, so
   it's weaker evidence for "method, not effort" than it looks.)

**From market & product**
8. The FE prep market is comprehensive-coverage courses and large banks (PPI,
   studyforfe, PrepFE), mostly subscriptions — misaligned with a one-date goal. A
   cheap, one-time tool that covers *less on purpose* and leans on the split is a
   structural opening.
9. Calculator fluency (complex-number/phasor arithmetic on an approved TI-36X /
   fx-991) is a separate trainable sub-skill no current FE tool drills — cheap
   leverage. (→ the in-app FE calculator.)

---

## DOK 2 — Knowledge Tree (curated sources)

**Category 1 — The exam**
- *Spec & format:* NCEES FE Electrical and Computer CBT specs — CBT at Pearson
  VUE, year-round; 110 questions; 6-hour appointment (~5h20 testing, <3 min/Q);
  ~17 areas; pass/fail; $225; up to 3 attempts/12 months.
  <https://ncees.org/wp-content/uploads/FE-Electrical-and-Computer-CBT-specs.pdf>
- *Reference & calculator:* the FE Reference Handbook (free, searchable PDF on the
  exam) is the only reference; approved calculators TI-30X/36X, Casio fx-115/991,
  HP 33s/35s. → binding skills are recognition speed + handbook fluency.
  <https://ncees.org/exams/fe-exam/>
- *Topic weighting:* heaviest areas by question count — Mathematics (11–17),
  Circuit Analysis (10–15), Power (8–12), Engineering Sciences (6–9), Control
  (6–9); Electromagnetics/Linear Systems/Signal Processing ~5–8; smaller blocks
  for Prob & Stats, Software, Ethics, Economics. → drives the points-at-stake
  weighting. <https://www.engproguides.com/fe-electrical-exam-pass.html>

**Category 2 — Statistics**
- Pass rate & effort: 64% first-time, 31% repeat (NCEES Squared 2025); ~200–400
  study hours over ~3 months. Read the drop with the selection effect in mind.
  <https://fetestprep.com/blog/fe-electrical-exam-study-guide.html>

**Category 3 — Learning science**
- Retrieval practice: Dunlosky et al. (2013) PSPI.
  <https://journals.sagepub.com/doi/abs/10.1177/1529100612453266>
- Cognitive load & expertise reversal: Kalyuga et al. (2003); Kalyuga et al.
  (2001). <https://link.springer.com/article/10.1007/s11251-009-9102-0>
- Interleaving (for): Rohrer et al. (2020); Taylor & Rohrer (2010).
  <http://uweb.cas.usf.edu/~drohrer/pdfs/Rohrer_et_al_2020JEdPsych.pdf>
- Interleaving (against/limits): Yan et al. (2024); Albaret & Thon (1998);
  Brunmair & Richter (2019). <https://www.sciencedirect.com/science/article/pii/S0959475225002312>
- Spacing schedule: Cepeda et al. (2008), Psychological Science.
  <https://laplab.ucsd.edu/articles/Cepeda%20et%20al%202008_psychsci.pdf>
- Open-book performance: ASEE exploratory study.
  <https://peer.asee.org/open-book-problem-solving-in-engineering-an-exploratory-study.pdf>

**Category 4 — Market & competitors**
- PPI, studyforfe (Wasim Asghar), PrepFE, Engineering Pro Guides — compete on
  coverage and volume, mostly subscriptions. The opening: a cheap, one-time tool
  that covers less on purpose and targets the durable-vs-cram split + recognition
  speed. <https://ppi2pass.com/fe-exam/electrical-computer>

---

## How this BrainLift shaped the build (POV → feature)

| BrainLift claim | What it became in the app |
|---|---|
| SPOV 1: split into durable vs cram; decay is fine for throwaway topics | Whole-section **durable/cram track policy** (`deckbrowser.py`, `feTrackPolicy`); cram studies reschedule-off (peaks then decays), durable reschedule-on |
| SPOV 1 / Insight 3: breadth-first, exam-weighted study | **Points-at-stake queue** orders/interleaves by `topic weight × weakness` (weights seeded from NCEES question counts) — `rslib/src/scheduler/points_at_stake.rs` |
| SPOV 2: blocked-then-interleaved, with a fluency gate | **Pre-registered hypothesis + ablation** (`feprep/ablation_test.py`): three builds, equal study budget |
| SPOV 2: verified-correct content is the floor | **AI verifier gate** blocks any card that isn't grounded/correct (`feprep/ai/verify_cards.py`); authored-problem gate `verify_authored.py` |
| Insight 7: confidence is a biased readiness estimator | **Readiness abstains** unless ≥200 reviews AND ≥50% coverage, and is shown as a range, never a bare number |
| Insight 9: calculator fluency is untrained leverage | **In-app exam-style calculator** + reference handbook, one click from the dashboard |
