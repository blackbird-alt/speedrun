# Practicing under real FE exam conditions

The NCEES FE Electrical and Computer exam is closed-book except for two things:
an on-screen **approved calculator** and the **official NCEES FE Reference
Handbook**. Nothing else is allowed — no notes, no personal formula sheet, no
outside references. This fork's goal is to let a candidate practice inside those
exact constraints, so what they rehearse is what they will actually have on exam
day.

That principle drives two deliberate design decisions documented here: a
built-in calculator modelled on the approved on-screen model, and a *refusal* to
ship any substitute for the copyrighted official reference handbook.

## The calculator

A self-contained on-screen scientific calculator lives at
`feprep/calculator/fe-calculator.html`. It is a single HTML/JS file with no
external assets, styled in the FE dark-navy (`#0b1220`) + copper (`#E0A45C`)
theme that the rest of the app uses.

- **What it models.** Its function set is modelled on the **TI-30XS
  MultiView** — the exact calculator NCEES supplies on-screen during the FE
  exam. Practicing on the same function set means the candidate never builds a
  habit around a key or feature they won't have on test day.
- **Function set (allowed only).** Basic arithmetic; powers and roots
  (`x²`, `x³`, `xʸ`, `√`, `∛`, `ʸ√x`, `1/x`); trigonometry with a **DEG/RAD**
  toggle plus inverse trig; `ln`/`eˣ` and `log`/`10ˣ`; factorial (`n!`);
  constants `π` and `e`; scientific notation (`EE`, i.e. `×10^`); percentage;
  an `Ans` recall; and a single memory register (`M+`, `M−`, `MR`, `MC`). A
  `2nd` shift key exposes the inverse/secondary functions, mirroring the
  hardware.
- **Prohibited functions are intentionally excluded.** There is **no** graphing,
  **no** CAS (symbolic algebra), and **no** programmability. Those are banned on
  the FE, so they are deliberately absent rather than merely hidden — the tool
  can't teach a workflow the exam forbids.
- **Approved calculator families (FE, 2026).** NCEES permits only three
  families: Casio `fx-115` / `fx-991`, TI `TI-30X` / `TI-36X`, and HP `33s` /
  `35s`. Anything graphing, CAS-capable, or programmable is prohibited. The
  built-in calculator sits squarely inside this allowed envelope.
- **One file, both platforms.** Because it is fully self-contained, the same
  file is loaded in a Qt WebEngine dialog on desktop and in an Android WebView on
  the phone — one implementation, identical behaviour on both surfaces.
- **Accessible during review.** It is reachable from the problems, so a candidate
  can pull it up while working a card, exactly as they would reach for the
  on-screen calculator mid-exam.

## The reference handbook

The only correct reference for FE practice is the **official NCEES FE Reference
Handbook, version 10.6** (current as of July 1, 2026). It is obtained free
through a personal **MyNCEES** account and is supplied on-screen as a searchable
PDF on exam day. It is NCEES-copyrighted and version-specific.

**This project does not bundle a substitute or generated formula sheet, by
design.** An in-app official-handbook viewer is intentionally **deferred** until
the user supplies their own official copy from MyNCEES; the app is meant to load
the user's official file, not a stand-in.

Why this matters:

- **You study what you'll actually have.** On exam day the only reference is the
  official Handbook v10.6. Practicing against anything else trains equation
  numbers, notation, table layouts, and search behaviour that won't be present
  when it counts.
- **A generated substitute is actively detrimental.** A hand-rolled or
  AI-generated "formula sheet" would look convenient but would teach materials
  the candidate cannot use on exam day — building recall around the wrong
  artifact. Not shipping one is the safer choice for the candidate.
- **Copyright and versioning.** The Handbook is NCEES-copyrighted and revised by
  version. Redistributing a copy is not ours to do, and pinning to the correct
  version (v10.6) matters, so the app defers to the user's own official download.

The result: the calculator is provided (it's a re-implementable, allowed tool),
while the reference is intentionally left to the user's official copy (it isn't
ours to substitute, and a substitute would hurt more than help).

## Desktop / phone parity

Both platforms present the same FE surface so practice feels identical wherever
it happens:

- **Shared engine.** Desktop (`qt/aqt`, `rslib`) and the Android companion
  (`Anki-Android`) share the same Rust engine, so card data, tags, and scoring
  come from one source of truth.
- **Matching dashboard.** The desktop FE dashboard (`qt/aqt/deckbrowser.py`) and
  the phone FE dashboard (`FeDashboardFragment.kt`) render the same visual
  language: dark-navy panels, copper accent, and colour-coded knowledge-area
  tiles grouped into **Durable** and **Cram** tracks in points-at-stake weight
  order. The topic set, accent colours, and NCEES question-count ranges are the
  same on both (audited below).
- **Same calculator.** The single self-contained calculator file is used by both
  the desktop and the phone, so the allowed-tool experience is byte-for-byte the
  same across devices.

## Read-only parity audit: desktop vs. phone FE dashboard

This is a factual, read-only comparison of the two dashboard sources. No code was
modified.

**Sources compared**

- Desktop: `qt/aqt/deckbrowser.py` — `FE_TOPICS`, `FE_DURABLE`,
  `_fe_dashboard_html()`, and the `_FE_DASH_STYLE` CSS.
- Phone: `Anki-Android/.../com/ichi2/anki/FeDashboardFragment.kt` — its
  `FE_TOPICS` list and inline CSS in `buildHtml()`.

### Theme colours — MATCH

| Token | Desktop (`_FE_DASH_STYLE`) | Phone (`buildHtml` CSS) |
| --- | --- | --- |
| Background | `#0b1220` | `#0b1220` |
| Panel | `#141f36` | `#141f36` |
| Panel 2 (gradient end) | `#0f1830` | `#0f1830` |
| Line/border | `#26344f` | `#26344f` |
| Text | `#eaf1fc` | `#eaf1fc` |
| Muted | `#93a2bd` | `#93a2bd` |
| Copper accent | `#E0A45C` | `#E0A45C` |
| Durable track dot | `#E0A45C` | `#E0A45C` |
| Cram track dot | `#7FB2F0` | `#7FB2F0` |

### Topic set, accent colours, and question-count ranges — MATCH

All 18 knowledge areas match exactly on **name**, **accent colour**, **question
range**, and **order** (points-at-stake, heaviest first):

| # | Topic | Accent | NCEES range |
| --- | --- | --- | --- |
| 1 | Mathematics | `#5C9BFF` | 11-17 |
| 2 | Circuit Analysis | `#E7A867` | 10-15 |
| 3 | Power Systems | `#F2849E` | 8-12 |
| 4 | Electronics | `#C58BF2` | 7-11 |
| 5 | Digital Systems | `#5FD0C0` | 7-11 |
| 6 | Engineering Sciences | `#D9A066` | 6-9 |
| 7 | Control Systems | `#6FA8DC` | 6-9 |
| 8 | Signal Processing | `#7FB2F0` | 5-8 |
| 9 | Linear Systems | `#9AA7F0` | 5-8 |
| 10 | Electromagnetics | `#8FD0A0` | 5-8 |
| 11 | Communications | `#E0A45C` | 5-8 |
| 12 | Computer Systems | `#7FC8D8` | 4-6 |
| 13 | Probability & Statistics | `#8FBCE0` | 4-6 |
| 14 | Electrical Materials | `#C79AD8` | 4-6 |
| 15 | Computer Networks | `#6FB0C8` | 4-6 |
| 16 | Software Development | `#B7A6F0` | 4-6 |
| 17 | Engineering Economics | `#D8B36A` | 3-5 |
| 18 | Ethics | `#8AC0A8` | 3-5 |

### Track grouping (Durable vs. Cram) — MATCH (by default)

Both platforms place the same 9 areas in the **Durable** track and the remaining
9 in **Cram**:

- **Durable:** Mathematics, Circuit Analysis, Power Systems, Electronics,
  Engineering Sciences, Control Systems, Signal Processing, Linear Systems,
  Electromagnetics.
- **Cram:** Digital Systems, Communications, Computer Systems,
  Probability & Statistics, Electrical Materials, Computer Networks,
  Software Development, Engineering Economics, Ethics.

Desktop encodes this via the `FE_DURABLE` set (which drives each area's default
per-area policy). Phone encodes the identical split via the `durable: Boolean`
flag on each `FeTopic`. The default grouping is the same on both.

### Observed differences (outside the five audited dimensions)

The five audited dimensions (theme colours, topic set, accent colours, question
ranges, track grouping) all match. The two dashboards do differ in scope, which
is worth recording but is not a colour/range/topic discrepancy:

1. **Score panels.** Desktop renders a **single** Memory panel
   (`_fe_mem_panel`, from `memory_score`). Phone renders **three** score panels —
   **Memory, Performance, and Readiness** (from `memoryScore`,
   `performanceScore`, and `readinessScore`). So the phone surfaces two scores
   the desktop dashboard does not.
2. **Memory score search scope.** Desktop calls `memory_score(search="")` (whole
   collection); phone calls `memoryScore(search = "is:review OR is:learn")`.
   Minor query difference feeding the Memory panel.
3. **Track grouping is dynamic on desktop, fixed on phone.** Desktop supports a
   per-area, user-overridable policy (`split → durable → cram`) stored in
   `feTrackPolicy`, so a user can move areas between tracks; the defaults match
   the phone. Phone uses the static `durable` boolean with no in-app override.
4. **Interactivity / layout.** Desktop tiles and hero are interactive (open a
   deck, "Study all", per-track study buttons, per-area move control) and the
   hero shows new/due badges. Phone tiles are non-interactive and the hero shows
   a stats row (total cards, areas covered, studied today) instead of action
   buttons.

**Bottom line:** on the audited dimensions the desktop and phone FE dashboards
are a faithful match. The differences are additive/scope differences (extra
scores and interactivity on one side vs. the other), not conflicting values.
