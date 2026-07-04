# Friday plan — AI added and checked; phone syncs with desktop

Exam: **NCEES FE Electrical and Computer** (pass/fail, criterion-referenced).
Milestone goal (from the PRD): *AI is added and checked; the phone syncs with the
desktop and shows readiness.* Nothing here weakens the Wednesday honesty rules.

This plan is organized as four workstreams (A–D). A and B are independent and run
in parallel; C depends on the score engine work; D is a cross-cutting gate.

---

## Wednesday status (entering Friday) — verified

- Desktop builds from source; **7 Rust unit tests** + **2 Python protobuf tests**
  pass; review loop is answerable end-to-end; memory score + pre-registered
  give-up rule work; installer MSI exists (`out/installer/dist`).
- Mobile (AnkiDroid fork) builds, installs, launches, and runs on the **shared
  Rust engine** (`rsdroid`/`rslib`) — reaches DeckPicker (collection opened).
- `feprep/verify_wiring.py` corrected to assert the real, shipped contract
  (read-only PAS RPC; live queue stays answerable; feature flag defaults off).

---

## Friday progress (implemented so far)

- **A. Sync — DONE & verified.** Self-hosted sync server runs; `feprep/sync_test.py`
  drives two clients through the shared Rust sync engine and passes: conflict rule
  (both revlog records kept, deterministic last-writer-wins) and no-loss /
  no-double-count (6+6 different cards → exactly 14 revlog rows on both). See
  `docs/sync-conflict-rule.md`.
- **B. AI pipeline — DONE & verified (live OpenAI, reproducible offline).**
  `feprep/ai/` runs generate → verify → leakage → eval via
  `python feprep/ai/run_eval.py`, grounded ONLY on the author's manuscript
  (`fe-problems.txt` + `fe-figures.txt`; the never-quality-checked `fe-bank.txt`
  was removed). Live OpenAI (`gpt-4o-mini`) card check 216/8/8
  (correct/wrong/bad-teaching) → 216 shipped; leakage CLEAN; held-out eval (50
  manuscript worked problems) prints an AI-vs-keyword-vs-vector table with a
  pre-declared cutoff. Honest result: the AI does not beat the baselines on raw
  accuracy but cuts the wrong-answer rate to ~a third (30% vs 94–96%) and wins on
  precision + net decision via the grounding verifier + abstention. Reproducible
  offline on the stub; live generation key-gated (`FE_AI_PROVIDER=openai` +
  `OPENAI_API_KEY`).
- **C. Score engine — DONE for the shared engine.** `PerformanceScore` and
  `ReadinessScore` RPCs added alongside `MemoryScore`, all honest (point + range +
  give-up rule; readiness abstains with the single best next action on the FE
  pass/fail scale). **11 Rust unit tests + 4 Python protobuf tests pass.**
  `feprep/scores_demo.py` prints all three on a studied deck.
- **D. AI-off — DONE.** Scoring is pure engine work on tags + FSRS; no model is
  ever called. `scores_demo.py` runs fully offline and produces the three scores.
- **C (phone UI) — REMAINING.** The scores live in `rslib`, so the phone gets
  them once the AnkiDroid backend (`Anki-Android-Backend`/`rsdroid`) is rebuilt
  against this forked `rslib` (Android NDK cross-compile per ABI) and a Kotlin
  screen calls the three RPCs. This is the main remaining Friday task.

## Workstream A — Two-way sync (desktop ⇄ phone)  [PRD "Mobile", challenge 7b]

**Why first:** the grading rubric hard-caps the whole project at **70%** with no
phone companion that shares the engine *and syncs*. This is the highest-leverage
Friday item.

The fork already ships a self-hosted sync server (`pylib/anki/syncserver.py` over
the Rust `rslib/src/sync/http_server`). Use it — no new protocol.

Steps:
1. **Run the server** on the dev box, bound to `0.0.0.0` so the emulator can reach
   it. Create one sync user. Command:
   `SYNC_USER1=fe:fe SYNC_BASE=... python -m anki.syncserver` (host + port noted
   in `deploy-and-run.md`). The Android emulator reaches the host at
   `http://10.0.2.2:<port>/`.
2. **Point desktop** at the local server (Preferences → Syncing → self-hosted, or
   `col.set_config` custom sync URL) and log in as `fe`.
3. **Point AnkiDroid** at the same server (Advanced → Custom sync server →
   `http://10.0.2.2:<port>/`), log in as `fe`, do a first full sync so both sides
   share one collection + the FE deck.
4. **Two-way demo (7b):**
   - Offline: review 10 cards on the phone (airplane mode) and 10 *different*
     cards on desktop.
   - Reconnect, sync both. Assert all 20 reviews land once — no loss, no
     double-count. Verify by comparing `revlog` counts before/after on both
     sides.
   - **Conflict case:** review the *same* card on both devices offline, then sync.
     Document the conflict rule. Anki's sync is last-writer-wins per object keyed
     by USN + modification time; the later `mod` wins and the revlog keeps both
     answer records (nothing is silently dropped). Write this rule down in
     `docs/sync-conflict-rule.md` with the observed result.
5. **Offline review + resync (PRD mobile bullet 2):** covered by step 4's offline
   halves; also show the phone queue works with no network and syncs on return.

Proof artifact: screen recording of a card reviewed on the phone appearing on the
desktop after sync, plus a before/after revlog-count table.

---

## Workstream B — AI card generation, grounded + checked  [PRD "Desktop (AI)", 7e, 7f]

**Feature:** *grounded FE flashcard generation with a verifier gate.* Not a
runtime chatbot and not a RAG answer pipeline (both out of scope in the Purpose
doc). It runs both as a **build-time authoring tool** and as an **opt-in, in-app
generator** the student invokes on demand ("Generate cards (AI)", steerable with a
free-text focus): either way it turns one named source into candidate cards, every
card carries a source locator, and a verifier blocks any card that is wrong or
bad-teaching before it can ever reach a student.

Named source: the **NCEES FE Reference Handbook** (official, free) and/or the
author's FE practice manuscript already vendored under `feprep/`. Each generated
card records `source` = `{document, section/page}` so every AI output traces back
to a named source (rubric hard requirement; AI section scores 0 without it).

Model backend (pluggable, named): Gemini via Firebase AI Logic *or* an API-key
provider. **Constraint:** no LLM credential is present in this environment, so
live generation needs a key supplied by the owner. The pipeline is built so that:
- outputs of a generation run are cached/committed as fixtures, and
- the eval + baseline are fully reproducible from those fixtures with **no
  network**, so `make eval` gives the same numbers to anyone re-running it.

Components (all under `feprep/ai/`):
1. **Gold set** `gold/fe_gold_50.jsonl` — 50 Q&A with known-correct answers,
   drawn from verified facts across the heaviest NCEES areas, each with a
   handbook citation. Held out from any generation prompt.
2. **Generator** `generate_cards.py` — from a source chunk, produce N candidate
   cards `{front, back, area, source, model, prompt_hash}`. Provider is behind an
   interface (`providers/gemini.py`, `providers/stub.py`).
3. **Verifier / checker** `verify_cards.py` (challenge 7f) — classify each card:
   **correct-and-useful**, **wrong** (fails grounding or contradicts gold),
   **correct-but-bad-teaching** (vague/trivial/duplicate). Grounding = the answer
   must be entailed by the cited source span. **Cutoff is set before looking at
   results** (e.g. block if not grounded, or if duplicate cosine > 0.95, or if
   flagged wrong). Blocked cards never ship.
4. **Eval** `eval.py` — on the held-out gold set, report **accuracy** and
   **wrong-answer rate**, print the pre-set cutoff, and refuse to "pass" content
   below it. Runs *before* students see anything (gated in the import path).
5. **Baseline** `baseline.py` (rubric: beat a simpler method) — keyword (BM25 over
   the handbook) and vector (embedding) retrieval that answers the gold questions
   by returning the top source span. Report a **side-by-side** table: AI
   generator+verifier vs keyword vs vector on accuracy / wrong-rate / useful-card
   yield. AI must win; if it does not, report that honestly.
6. **Leakage check** `leakage_check.py` (challenge 7e) — scan the generation
   inputs for any gold item or near-duplicate (normalized text + high cosine) and
   fail loudly if found. Show a clean result.
7. **Prompt-injection guard** — strip/deny instructions embedded in source text
   (PRD "source file with hidden text"); log any that were caught.

One command: `feprep/ai/run_eval.py` (wire into `make eval`) prints all numbers.

Proof artifact: the eval table (accuracy + wrong-rate + cutoff), the
AI-vs-baseline side-by-side, and the 3-count card-check report.

---

## Workstream C — Three scores with ranges on the phone  [PRD "Mobile" bullet 3]

Friday requires the **phone** to show memory, performance, and readiness, each as
a **range** with the give-up rule — never a single blended number, never a
made-up readiness (automatic-fail rule).

Because the engine is shared, compute all three in Rust so desktop and phone
agree:
1. **Memory** — already shipped (`MemoryScore` RPC): point estimate + 95% range +
   coverage + give-up rule. Reuse as-is.
2. **Performance** — new `PerformanceScore` RPC: probability of answering a *new*
   exam-style question in an area, from topic mastery (FSRS), item difficulty, and
   coverage. Friday scope: compute honestly from held-out exam-style items where
   available, with a range; **abstain** (give-up rule) where an area has too few
   graded items. The paraphrase gap (7d) is measured Sunday; Friday ships the
   score + abstention.
3. **Readiness** — new `ReadinessScore` RPC on the FE pass/fail scale: output a
   **pass-probability range** + "how sure" + % of exam covered + top next action,
   **or abstain** with a clear "not enough data" when coverage/among-graded is
   below the pre-registered line (e.g. < 200 graded reviews or < 50% coverage).
   For a fresh/small deck this will *correctly abstain* — which the PRD explicitly
   rewards over a polished number we cannot back up.
4. **Phone UI** — an AnkiDroid screen (DeckPicker action) showing the three
   scores, each rendered as `estimate [low–high]`, the coverage %, last-updated,
   the main reason, and the abstain message when withheld. Pulls via the shared
   backend JSON/proto path (`rsdroid`), so no scoring logic is reimplemented in
   Kotlin.

Each RPC gets Rust unit tests (shown vs withheld, range monotonicity) and a
Python protobuf test, mirroring the Wednesday pattern.

---

## Workstream D — App still gives a score with AI switched off  [PRD hard rule]

- Every model call is opt-in and key-gated: with no OpenAI key,
  `aqt.fe_ai.ai_available()` is false and *all* AI affordances are inert. Studying
  and the three scores never call a model — the only model calls are ones the
  student explicitly triggers (on-demand "Generate cards (AI)", or the card-scoped
  Solve/Ask helper).
- Add a check (script + test) that with no key and no network, the desktop and
  phone still open, review, and produce the three scores (memory real;
  performance/readiness abstaining as data dictates). This is also the offline /
  rate-limited / broken-output resilience the PRD lists.

---

## Sequencing & ownership

| Order | Workstream | Heavy build? | Notes |
|---|---|---|---|
| 1 | A. Sync | no (server + emulator) | hard-limit gate; do first |
| 2 | B. AI eval pipeline | no (pure Python) | parallel to A; live gen needs a key |
| 3 | C. Score RPCs (Rust) | yes (cargo ~4 min) | one heavy compile; then pylib/qt |
| 4 | C. Phone 3-score UI | yes (gradle) | after RPCs land |
| 5 | D. AI-off gate + test | light | after B/C |

Proof to capture for the Friday hand-in: eval numbers + baseline comparison, and a
recording of a phone-reviewed card appearing on the desktop after sync.

## Known external dependency

Live AI generation requires an LLM credential (Gemini via Firebase AI Logic or an
API key) that is **not** in this environment. Everything else — sync, the eval
harness, baselines, leakage check, the three score RPCs and their abstention — is
buildable and testable without it, and the eval reproduces from committed
generation fixtures.
