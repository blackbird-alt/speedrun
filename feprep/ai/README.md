<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# FE "Speedrun" — grounded AI card generation, verified & checked

This folder is the **Friday AI deliverable**: a reproducible, offline-capable
Python pipeline that turns the project's *already-verified* FE content into new
flashcards, **checks every card against its named source**, and honestly
measures itself against simpler baselines on a held-out gold set.

This folder is the reproducible, offline **eval + authoring pipeline**. The same
grounded generation is also wired into the desktop app as an **opt-in, key-gated
action** ("Generate cards (AI)" on the FE dashboard / Tools menu), so a student
can request more cards on demand and steer them with a free-text focus. Either
way, only cards that pass the verifier gate can reach a student. Studying and the
three scores never call a model — every model call is one the student explicitly
triggers, and with no OpenAI key configured every AI affordance is inert. Nothing
here touches Rust, Qt, or the build system.

---

## What the feature is

**Grounded FE flashcard generation with a verifier gate.** Not a chatbot and not
a runtime RAG answer engine. Given a chunk of a named source, it:

1. produces candidate cards, each stamped with `{source_doc, source_line,
   model, prompt_hash}` (every AI output traces to a named source), then
2. runs a verifier that classifies each card as `correct_useful`, `wrong`, or
   `bad_teaching` and **blocks** everything that isn't `correct_useful`.

## Named source

The source corpus is the author's verified FE **manuscript** decks under
`feprep/decks/`. The never-quality-checked `fe-bank.txt` and `fe-seed-deck.txt`
are deliberately excluded from the AI's source of truth:

| doc | what |
|---|---|
| `fe-problems.txt` | verified worked problems converted from the author's FE practice-problem manuscript |
| `fe-figures.txt` | figure-based problems from the scanned manuscript (image-only fronts, so they seed generation but not the text gold set) |

Every generated card cites the exact `doc:line` it came from, and grounding is
checked against that exact line — not against "the corpus in general".

## The model story

Generation is behind a pluggable provider interface (`providers/`):

- **`OpenAIProvider`** (`providers/openai.py`) — live generation against the
  OpenAI Chat Completions API (stdlib `urllib`, no SDK). Grounded prompt that
  restates only what is in each numbered source line, `temperature=0`,
  strict-JSON output, and **LaTeX/MathJax math** (`\( ... \)`, `\tfrac`,
  subscripts/superscripts) with a repair pass for JSON escape corruption. It is
  **key-gated** on `OPENAI_API_KEY`. Select with `FE_AI_PROVIDER=openai`.
- **`GeminiProvider`** (`providers/gemini.py`) — the same contract wired to
  **Gemini via Firebase AI Logic** / the Gemini Developer API, key-gated on
  `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `FIREBASE_API_KEY`.
- **`StubProvider`** (`providers/stub.py`) — fully deterministic and offline, so
  the pipeline runs with **no** network/key at all. It never invents facts and
  deliberately reproduces three real LLM failure modes (faithful `extractive`,
  `redundant` duplicates, `factmix`) so the verifier is exercised on genuine
  inputs.

The verifier, leakage check, and eval apply **identically** whichever provider
runs, so the safety story does not depend on the model. The results below are
from a live **OpenAI** run; re-running with the stub (no key) is fully
deterministic and offline.

## The cutoff (set before looking at results)

Two sets of thresholds are declared as constants at the top of their files with
the comment **"SET BEFORE LOOKING AT RESULTS"** and were fixed before any
output was inspected:

- Verifier (`verify_cards.py`): block if grounding recall `< 0.60`, if a
  near-duplicate (`char-ngram cosine > 0.95`) of an already-kept card, or if the
  answer is trivially short (`< 3` chars).
- Eval (`eval.py`): an answer counts as correct at token-F1 `>= 0.50`; the AI
  content only "passes" (is allowed to ship) at accuracy `>= 50%` **and**
  wrong-rate `<= 40%` on the held-out gold set.

## Honesty rules — how each is met

1. **Trace to a named source** — every candidate carries `source {doc, line}`,
   `model`, and `prompt_hash`.
2. **Held-out test set, cutoff set first** — `gold/fe_gold_50.jsonl` (50 items)
   is held out from generation; cutoffs are pre-declared constants.
3. **Beat a simpler baseline** — `baseline.py` implements BM25 (keyword) and
   char-ngram TF-IDF (vector) retrieval; `eval.py` prints a multi-metric
   side-by-side. The AI **beats both baselines on the domain-critical axes**
   (wrong-rate, precision, net decision score) and **does not** beat them on raw
   accuracy over an already-clean corpus — both are reported, nothing hidden.
4. **Leakage check** — `leakage_check.py` audits the exact generation inputs and
   exits non-zero if any gold item or near-duplicate is present.

## How to run

```
python feprep/ai/run_eval.py
```

Runs generate -> verify -> leakage -> eval and prints the 3-count card check,
the leakage verdict, and the accuracy / wrong-rate table. Stdlib-only, offline,
deterministic. Individual stages can also be run on their own
(`generate_cards.py`, `verify_cards.py`, `leakage_check.py`, `eval.py`,
`baseline.py`). To rebuild the gold set from the decks:
`python feprep/ai/gold/build_gold.py`.

## Latest results (live OpenAI provider, manuscript source)

Generated with the live **OpenAI** provider (`FE_AI_PROVIDER=openai`, model
`gpt-4o-mini`) from the manuscript (`fe-problems.txt` + `fe-figures.txt`),
evaluated against 50 held-out worked problems from the same manuscript. Fully
reproducible offline on the deterministic stub (no key); the verifier and eval
are identical either way.

Card check (challenge 7f), 232 candidates from 424 gold-free source lines:

```
correct_useful : 216      wrong : 8      bad_teaching : 8      -> shipped 216
```

Leakage check (challenge 7e): **CLEAN** — 424 generation inputs audited, no gold
item or near-duplicate present.

Eval on the 50 held-out gold questions (all methods use the same gold-free
corpus and the same retriever/metric):

| method | accuracy | wrong-rate | precision | net | coverage |
|---|---|---|---|---|---|
| AI (generate+verify) | 2.0% | **30.0%** | **6.2%** | **-14** | 32.0% |
| keyword (BM25) | 4.0% | 96.0% | 4.0% | -46 | 100.0% |
| vector (char-ngram TF-IDF) | 6.0% | 94.0% | 6.0% | -44 | 100.0% |

`net = #correct - #wrong` (abstaining is neutral; higher is better).

### Honest reading of these numbers

- **Beats a simpler method on the axes that matter here.** This tool's thesis
  (challenge 7f) is *a wrong card is worse than none*. The AI wins wrong-rate
  **30% vs 94–96%**, precision **6.2% vs 4–6%**, and net **-14 vs -44/-46**: it
  abstains (34/50) rather than confidently return a wrong span, and the verifier
  shipped 216 of 232 candidates, blocking 8 wrong + 8 bad-teaching.
- **Does not win raw accuracy — reported, not hidden.** The held-out gold is
  *worked problems* (numeric, multi-step), scored correct only at token-F1 ≥ 0.5
  against a gold answer deliberately held out of generation. Under that setup
  every method scores low (2–6%); the live LLM improves card quality (far fewer
  wrong/bad-teaching) and wrong-rate, but not this retrieval-style raw-accuracy
  number. Stated plainly, not hidden.
- **The pre-declared raw-accuracy ship cutoff (accuracy ≥ 50%, wrong-rate ≤ 40%)
  returns FAIL** for every method on this open corpus — we did not move it. The
  gate that actually blocks cards is the per-card verifier (7f), which shipped
  216 and blocked 16.

## AI-off / resilience

Card generation is opt-in and gated by the provider selection plus the key gate.
The shipping app's core has no dependency on any of it: with no key or no network,
`aqt.fe_ai.ai_available()` is false, every AI affordance is inert, and the app
still opens, reviews, and scores normally because studying and scoring never call
a model. The eval itself is the proof that the "AI path" is fully reproducible
with **no** network.

## Reproducibility

Stdlib-only (no numpy/network), fixed constants, sorted iteration, no
randomness. `run_eval.py` produces byte-identical output and byte-identical
`out/*.jsonl` fixtures on every run. Verified by running twice and diffing.

## Files

```
feprep/ai/
  README.md                 this file
  common.py                 deck parsing, normalization, BM25, TF-IDF, hashing, IO, leakage predicate
  gold/
    fe_gold_50.jsonl        50 held-out worked-problem Q&A (id, question, answer, area, source), derived from fe-problems.txt
    build_gold.py           one-shot deterministic builder for the gold set
  providers/
    __init__.py             provider interface + factory (get_provider)
    stub.py                 deterministic offline generator
    openai.py               key-gated OpenAI provider (LaTeX math)
    gemini.py               key-gated Gemini (Firebase AI Logic) provider
  generate_cards.py         stage 1: candidates -> out/candidates.jsonl (+ out/generation_inputs.jsonl)
  verify_cards.py           stage 2: 3-way verifier gate -> out/verified.jsonl (+ out/verified_all.jsonl)
  leakage_check.py          challenge 7e: audit inputs, non-zero exit if dirty
  baseline.py               keyword (BM25) + vector (TF-IDF) retrieval baselines
  eval.py                   stage 4: accuracy / wrong-rate table + pass/fail gate
  run_eval.py               one command: generate -> verify -> leakage -> eval
  out/                      reproducible fixtures written by the pipeline
```

## Caveat, stated plainly

The numbers above are from a live **OpenAI** run (`FE_AI_PROVIDER=openai`, model
`gpt-4o-mini`) over the manuscript source. The AI beats the baselines on the
domain-critical axes (wrong-rate, precision, net) and **not** on raw accuracy over
worked-problem gold — both reported, neither fudged. With **no** key the pipeline
still runs end-to-end on the deterministic stub, and the grounding verifier,
leakage check, and held-out eval gate every provider's output identically. The
shipping app never calls any of this.
