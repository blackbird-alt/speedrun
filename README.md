# Anki — FE Electrical and Computer study fork

> ⬇ **[Windows installer (.msi)](https://github.com/blackbird-alt/speedrun/releases/download/fe-v26.05/anki-26.05-win-x64.msi)** &nbsp;·&nbsp; **[Android APK](https://github.com/blackbird-alt/speedrun/releases/download/fe-v26.05/AnkiDroid-play-x86_64-debug.apk)** &nbsp;·&nbsp; **[FE card deck (.apkg)](https://github.com/blackbird-alt/speedrun/releases/download/fe-v26.05/fe-electrical.apkg)** &nbsp;·&nbsp; [all releases](https://github.com/blackbird-alt/speedrun/releases/latest)
> — install the app, then **File → Import** the deck to load the **863 FE cards** into the dashboard. AI is **off by default**. (The deck is also committed at [`feprep/decks/fe-electrical.apkg`](./feprep/decks/fe-electrical.apkg).)

[![Build Status](https://github.com/ankitects/anki/actions/workflows/ci.yml/badge.svg)](https://github.com/ankitects/anki/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-dev--docs.ankiweb.net-blue)](https://dev-docs.ankiweb.net)

This repository is a fork of [Anki](https://apps.ankiweb.net) that adds a study
tool for the **NCEES FE Electrical and Computer** exam. It shares one Rust engine
across desktop and phone. The study features are honest by construction — scores
are shown as ranges with pre-registered give-up rules, and the app runs the same
whether or not any AI is involved. See [`feprep/README.md`](./feprep/README.md)
for the full write-up.

> **Credit:** This project is a fork of Anki by Ankitects Pty Ltd and
> contributors. The upstream project is licensed GNU AGPL-3.0-or-later, with some
> components under BSD-3-Clause; all credit for the underlying engine, scheduler,
> and FSRS integration belongs to the Anki authors. See [LICENSE](./LICENSE) and
> [CONTRIBUTORS](./CONTRIBUTORS).

## Download & install

**Desktop (Windows):** [**download `anki-26.05-win-x64.msi`**](https://github.com/blackbird-alt/speedrun/releases/download/fe-v26.05/anki-26.05-win-x64.msi)
and run it. It installs on a clean machine and opens into the FE study dashboard,
with AI **off by default** — studying and the three scores never call a model.
Then load the cards: **File → Import → [`fe-electrical.apkg`](https://github.com/blackbird-alt/speedrun/releases/download/fe-v26.05/fe-electrical.apkg)**
(863 cards). (Building from source is optional; see [Building](#building).)

**Phone (Android):** [**download `AnkiDroid-play-x86_64-debug.apk`**](https://github.com/blackbird-alt/speedrun/releases/download/fe-v26.05/AnkiDroid-play-x86_64-debug.apk)
(x86_64 emulator; build `arm64-v8a` from source for a physical device) and
install it. It runs on the same shared Rust engine and syncs with the desktop.
Load the same `fe-electrical.apkg` (Import), or sync it down from the desktop.

**The card deck:** [**download `fe-electrical.apkg`**](https://github.com/blackbird-alt/speedrun/releases/download/fe-v26.05/fe-electrical.apkg)
— 863 FE cards (301 AI-authored + worked-answer explanations, LaTeX-typeset),
also committed at [`feprep/decks/fe-electrical.apkg`](./feprep/decks/fe-electrical.apkg).
Import it on either app after installing.

> All downloads: **[github.com/blackbird-alt/speedrun/releases](https://github.com/blackbird-alt/speedrun/releases/latest)**.

## What this fork adds

1. **Three honest scores — each a range with a pre-registered give-up rule.**
   None is ever a bare single number, and each stays *hidden* until enough data
   exists to defend it. All three are read-only aggregates of Anki's existing
   FSRS retrievability, computed in the shared Rust engine
   (`rslib/src/scheduler/points_at_stake.rs`). One-page write-ups live in
   [`feprep/docs/`](./feprep/docs/):
   - **[Memory](./feprep/docs/model-memory.md)** — recall of studied material.
     Withheld below **50 graded reviews across 3 topics**
     (`feMemoryMinReviews` / `feMemoryMinTopics`).
   - **[Performance](./feprep/docs/model-performance.md)** — recall on worked,
     exam-style problems (`track::durable` cards). Withheld below **30 exam-style
     reviews across 2 topics** (`fePerformanceMinReviews` /
     `fePerformanceMinTopics`). Early-stage: a proxy, not a graded solution.
   - **[Readiness](./feprep/docs/model-readiness.md)** — a pass-probability
     range. **Deliberately abstains** below **200 graded reviews AND 50% area
     coverage** (`feReadinessMinReviews` / `feReadinessMinCoverage`); when it
     abstains it names the single best next action. Early-stage and uncalibrated
     against real pass/fail outcomes, so silence is the honest default.
2. **Points-at-stake queue (Rust)** — a read-only re-ordering of the due/new
   queue by `topic weight × student weakness`, interleaved so a session spans
   many areas while the heaviest/weakest surface most. In the shared engine
   (`rslib/src/scheduler/points_at_stake.rs`), exposed over protobuf, callable
   from Python. It writes no scheduling state, so undo and collection integrity
   are unaffected.
3. **AI card-generation pipeline (build-time, AI-off-safe)** — a reproducible
   [`feprep/ai/`](./feprep/ai/) pipeline that turns already-verified FE content
   into new cards: **grounded** (every card cites `doc:line`), **verifier-gated**
   (blocks anything not `correct_useful`), measured on a **held-out gold set**
   with cutoffs fixed in advance, and audited by a **leakage check**. Pluggable
   **OpenAI / stub** providers (plus a documented Gemini path); the stub is fully
   offline and deterministic. This is **authoring tooling only** — the running
   app never calls a model, so it opens, reviews, and scores identically with AI
   off or no network. Live generation needs an API key.
4. **Two-way sync** — the fork syncs with a server the same way upstream Anki
   does, so progress moves between desktop and phone.
5. **In-app FE calculator and reference-handbook viewer** — an exam-style
   calculator and the NCEES reference handbook are one click from the study
   dashboard (`fecalc` / `fehandbook`), so practice happens under exam
   conditions.

### Honesty by construction

Every score is a **range with a give-up rule set in advance** (PRD §7.3), not a
number tuned after the fact. Readiness will show nothing on a small or narrow
deck — that is intended. Run [`feprep/coverage_map.py`](./feprep/coverage_map.py)
to see, per NCEES area, what the deck covers and whether it clears the readiness
coverage line. AI card generation is opt-in and key-gated; with AI off the app is
unchanged.

## Building

Build steps are unchanged from upstream Anki; see
[Development](./docs/development.md) and [Contributing](./docs/contributing.md).
In short, from a clean checkout with the toolchain installed
(Rust `1.92.0`, Python 3.13, Node/yarn, and `protoc`, all handled by the build
scripts):

- Desktop dev run: `./run` (Linux/macOS) or `.\run.bat` (Windows).
- Rust engine tests, including this fork's additions:
  `cargo test -p anki scheduler::points_at_stake`.
- Python-through-protobuf test: `./ninja pylib/tests` (or
  `pytest pylib/tests/test_points_at_stake.py` against a built pylib).

The FE seed deck lives in [`feprep/decks/fe-seed-deck.txt`](./feprep/decks/fe-seed-deck.txt)
and can be imported directly (File > Import) or packaged with
`python feprep/build_apkg.py`.

## About Anki

Anki is a spaced repetition program. Please see the [website](https://apps.ankiweb.net) to learn more.

## Getting Started

### Contributing

Want to contribute to Anki? Check out the [Contribution Guidelines](./docs/contributing.md).

For more information on building and developing, please see [Development](./docs/development.md).

#### Contributors

The following people have contributed to Anki: [CONTRIBUTORS](./CONTRIBUTORS)

### Anki Betas

If you'd like to try development builds of Anki but don't feel comfortable
building the code, please see [Anki betas](https://betas.ankiweb.net/).

## License

Anki's license: [LICENSE](./LICENSE)
