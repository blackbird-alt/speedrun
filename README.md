# Anki — FE Electrical and Computer study fork

[![Build Status](https://github.com/ankitects/anki/actions/workflows/ci.yml/badge.svg)](https://github.com/ankitects/anki/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-dev--docs.ankiweb.net-blue)](https://dev-docs.ankiweb.net)

This repository is a fork of [Anki](https://apps.ankiweb.net) that adds a study
tool for the **NCEES FE Electrical and Computer** exam. It shares one Rust engine
across desktop and phone and adds two small, honest, AI-free features. See
[`feprep/README.md`](./feprep/README.md) for the full write-up.

> **Credit:** This project is a fork of Anki by Ankitects Pty Ltd and
> contributors. The upstream project is licensed GNU AGPL-3.0-or-later, with some
> components under BSD-3-Clause; all credit for the underlying engine, scheduler,
> and FSRS integration belongs to the Anki authors. See [LICENSE](./LICENSE) and
> [CONTRIBUTORS](./CONTRIBUTORS).

## What this fork adds

1. **Points-at-stake queue** — a read-only re-ordering of the due/new queue by
   `topic weight × student weakness`, so the highest-value cards surface first.
   Implemented in the shared Rust engine (`rslib/src/scheduler/points_at_stake.rs`),
   exposed over protobuf, and callable from Python. It writes no scheduling
   state, so undo and collection integrity are unaffected.
2. **Honest memory score** — an aggregate of Anki's existing FSRS retrievability,
   presented as a **range with a stated give-up rule**, never a bare single
   number. No AI, no new model — just honest aggregation.

### The memory-score give-up rule (pre-registered)

The app shows **no memory score until there are at least 50 graded reviews across
at least 3 topics**, and at least one card with FSRS memory state. Below that
line it says it does not yet have enough data rather than showing a number. These
thresholds are parameters set in advance (config keys `feMemoryMinReviews` /
`feMemoryMinTopics`), not tuned after seeing which number looks good.

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
