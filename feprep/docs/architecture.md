# Architecture overview

This is a fork of Anki that adds a **NCEES FE Electrical and Computer** study
tool. The guiding principle is *one engine, two apps*: every FE feature that
requires logic — the points-at-stake queue and the three honest scores — is
computed **once in the Rust core** and reaches the desktop app and the Android
companion through the **same protobuf methods**. Nothing is reimplemented per
platform.

## 1. The shared engine, two apps

Anki is layered, and the FE work slots into the existing layers rather than
bolting a new stack on the side:

```
                +-------------------------------------------+
                |  Rust core: rslib/  (+ fsrs crate)        |
                |  scheduling, collection, FE queue+scores  |
                +-------------------------------------------+
                                    |
                    protobuf contract: proto/anki/*.proto
                                    |
        +---------------------------+---------------------------+
        |                                                       |
  PyO3 bridge (_rsbridge)                              JNI / same rslib
  pylib/  -> Python backend                            build in AnkiDroid
        |                                                       |
   qt/aqt (Python) + ts/ (TS/Svelte)                 Anki-Android (Kotlin)
   DESKTOP app                                        PHONE app
```

- **Rust core (`rslib`) + FSRS.** All scheduling and memory math lives here.
  The FSRS spaced-repetition model (the `fsrs` crate) owns per-card memory
  state and retrievability. The FE additions call straight into FSRS for
  retrievability and never touch scheduling state — they are read-only.
- **Protobuf RPC contract (`proto/anki/*.proto`).** The boundary between the
  core and every front-end is a set of protobuf service methods. Adding a
  feature means adding an RPC here; both apps then get it for free.
- **Python bridge (`pylib`, PyO3 `_rsbridge`).** `pylib` wraps the compiled
  Rust core via the PyO3 `_rsbridge` extension, exposing the backend to Python.
- **Desktop front-end (`qt/aqt` + `ts`).** The Qt/Python app (`qt/aqt`) with a
  TypeScript/Svelte web layer (`ts`) renders the UI and calls the backend.
- **Android companion.** `C:\Users\ellie\speedrun\Anki-Android` is a fork of
  **AnkiDroid** built against the **same `rslib`**. Its Kotlin UI calls the
  identical backend methods.

**Why this matters:** the FSRS/engine change and the three scores are computed
once in Rust. The desktop (`col._backend.memory_score(...)`) and the phone
(`backend.memoryScore(...)`) call the *same* protobuf methods and get the same
numbers. There is no second implementation to keep in sync.

## 2. Where the FE additions live

### Engine (Rust core)

- `rslib/src/scheduler/points_at_stake.rs` — the whole engine-side feature:
  the **points-at-stake queue** (`build_points_at_stake_queue`, a read-only
  re-ordering of the due/new queue by `topic weight × student weakness`,
  weighted-*interleaved* across topics so a session spans many areas), plus the
  three score computations — `compute_fe_memory_score`,
  `compute_fe_performance_score`, `compute_fe_readiness_score`. Topic weights
  are seeded from the NCEES question-count ranges but live in the collection
  `config` table (source of truth), not in code. Each score is an aggregate of
  FSRS retrievability presented as a **range with a pre-registered give-up
  rule** — it abstains (with a single best next action) when data is thin.
- `proto/anki/scheduler.proto` — the RPCs on `SchedulerService`:
  `PointsAtStakeQueue`, `MemoryScore`, `PerformanceScore`, `ReadinessScore`
  (with their request/response messages).
- `rslib/src/scheduler/service/mod.rs` — wires those RPCs to the core methods
  (e.g. `points_at_stake_queue` → `self.build_points_at_stake_queue(...)`).
- `pylib/tests/test_points_at_stake.py` — Python-level test exercising the
  feature across the bridge.

### Desktop UI (`qt/aqt`)

- `qt/aqt/deckbrowser.py` — the **FE dashboard** (`_fe_dashboard_html`):
  color-coded areas, the three scores, the durable/cram tracks, and the
  Calculator / Handbook / AI buttons.
- `qt/aqt/fe_calculator.py` — the exam-style calculator dialog.
- `qt/aqt/fe_handbook.py` — the FE Reference Handbook viewer.
- `qt/aqt/fe_ai.py` — AI master gate (`ai_available`, key storage) — plus
  `qt/aqt/fe_ai_generate.py` (card generation UI) and
  `qt/aqt/fe_ai_helper.py` (in-review AI helper).
- `qt/aqt/reviewer.py` — reviewer hooks: bottom-bar Calculator/Handbook/AI
  buttons and the AI helper refresh.
- `qt/aqt/main.py` — menu wiring (`setup_fe_calculator_action`,
  `setup_fe_handbook_action`, `setup_fe_ai_actions`).

### Phone UI (Anki-Android repo, `AnkiDroid/src/main/java/com/ichi2/anki/`)

- `FeDashboardFragment.kt` — the phone dashboard; calls `backend.memoryScore`,
  `backend.performanceScore`, `backend.readinessScore` (the same RPCs).
- `FeCalculatorDialog.kt`, `FeHandbookDialog.kt` — calculator + handbook.
- `FeAiClient.kt`, `FeCardGeneratorDialog.kt`, `FeAiHelperDialog.kt` — AI client
  and dialogs (with `FeAi.kt` as the availability gate).
- `Reviewer.kt` — reviewer wiring (calculator, handbook opener, AI helper menu
  item gated on `FeAi.isAvailable`).

### AI pipeline (build-time only, `feprep/ai/`)

- `generate_cards.py`, `verify_cards.py`, `leakage_check.py`, `eval.py`
  (`run_eval.py`), `baseline.py` — generate → verify → leakage-check → evaluate
  against a `baseline`, using `providers/` and the `gold/` gold set.
- `verify_authored.py` — the authored-problem gate for hand-written content.

### Content (`feprep/decks/`, `feprep/build_apkg.py`)

- `feprep/decks/` — `fe-seed-deck.txt`, `fe-problems.txt`, `fe-figures.txt`,
  generated cards, media, and the packaged `fe-electrical.apkg`.
- `feprep/build_apkg.py` — packages the deck with the styled **"FE Prep"** note
  type (`MODEL_NAME = "FE Prep"`).

## 3. Data flow (a review + a score)

A review is stock Anki: the front-end asks the backend for the next card,
shows it, and sends the answer back; the Rust core updates FSRS memory state
and appends a revlog row.

A score adds nothing new to that loop — it just *reads* the state FSRS already
owns:

```
[UI: deckbrowser.py / FeDashboardFragment.kt]
        |  request: MemoryScore(search="is:review OR is:learn")
        v
[protobuf RPC  -> SchedulerService]
        v
[Rust: rslib/src/scheduler/service/mod.rs]
        |  self.compute_fe_memory_score(search)
        v
[points_at_stake.rs]
   for each matching card:
       FSRS.current_retrievability(memory_state, elapsed, decay)
        |  aggregate -> mean recall + 95% range, per-topic weakness
        |  apply pre-registered give-up rule (abstain if data thin)
        v
[MemoryScoreResponse: shown, point_estimate, range_low/high,
                      coverage, next_action, withheld_reason, ...]
        v
[back up the same path to the UI on desktop AND phone]
```

The queue follows the same request → protobuf → Rust → FSRS → aggregate → UI
path; it returns an ordered list of card ids plus per-card diagnostics.

## 4. Sync

Sync is **two-way over the exact same AnkiWeb / sync-server path as upstream**
(shared `rslib`; a self-hosted server is used for the demo). No FE-specific
sync code exists.

- The conflict rule is documented in `sync-conflict-rule.md` (this folder):
  the revlog is **append-only and never conflicts** (both devices' reviews are
  retained), while a card's mutable scheduling state resolves by
  **last-writer-wins** (deterministic, keyed by USN + mod time).
- Because the FE features are read-only, they add nothing to sync. The note
  type's CSS and the generated cards are ordinary collection objects, so they
  **sync to the phone** like any other note/notetype.

## 5. AI is build-time and off by default

- The **running apps never call a model.** The queue and all three scores are
  **model-free** — pure FSRS + tag arithmetic in Rust.
- The `feprep/ai/` pipeline runs **at build time only**, to author and verify
  deck content; its output ships as static cards in `fe-electrical.apkg`.
- Any **live** in-app AI affordance is **key-gated**: on desktop
  `fe_ai.ai_available()` (an `OPENAI_API_KEY` must be present) and on the phone
  `FeAi.isAvailable(...)` control visibility. With no key, the AI buttons/menu
  items don't appear and no network call is made.

## 6. FE file map

| Path | Role |
| --- | --- |
| `rslib/src/scheduler/points_at_stake.rs` | Engine: points-at-stake queue + memory/performance/readiness scores (FSRS aggregation, give-up rule) |
| `proto/anki/scheduler.proto` | RPC contract: `PointsAtStakeQueue`, `MemoryScore`, `PerformanceScore`, `ReadinessScore` |
| `rslib/src/scheduler/service/mod.rs` | Wires the RPCs to the core methods |
| `pylib/tests/test_points_at_stake.py` | Python-level test across the bridge |
| `qt/aqt/deckbrowser.py` | Desktop FE dashboard (areas, 3 scores, tracks, buttons) |
| `qt/aqt/fe_calculator.py` | Desktop calculator dialog |
| `qt/aqt/fe_handbook.py` | Desktop FE Reference Handbook viewer |
| `qt/aqt/fe_ai.py` | AI master gate + key storage (desktop) |
| `qt/aqt/fe_ai_generate.py` | Desktop AI card-generation UI |
| `qt/aqt/fe_ai_helper.py` | Desktop in-review AI helper |
| `qt/aqt/reviewer.py` | Reviewer hooks (calculator/handbook/AI buttons) |
| `qt/aqt/main.py` | Menu wiring for the FE actions |
| `Anki-Android/.../FeDashboardFragment.kt` | Phone dashboard (calls the same score RPCs) |
| `Anki-Android/.../FeCalculatorDialog.kt` | Phone calculator |
| `Anki-Android/.../FeHandbookDialog.kt` | Phone handbook |
| `Anki-Android/.../FeAiClient.kt` | Phone AI client |
| `Anki-Android/.../FeCardGeneratorDialog.kt` | Phone AI card generator |
| `Anki-Android/.../FeAiHelperDialog.kt` | Phone in-review AI helper |
| `Anki-Android/.../Reviewer.kt` | Phone reviewer wiring (gated on `FeAi`) |
| `feprep/ai/` | Build-time AI pipeline: generate / verify / leakage / eval / baseline, `providers/`, `gold/` |
| `feprep/ai/verify_authored.py` | Authored-problem verification gate |
| `feprep/decks/` | Seed/problems/figures + media + packaged `fe-electrical.apkg` |
| `feprep/build_apkg.py` | Builds the deck with the "FE Prep" note type |
| `feprep/docs/sync-conflict-rule.md` | Documented sync conflict rule + test result |
