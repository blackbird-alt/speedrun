# The Rust change: why it belongs in Rust, files touched, merge difficulty

## What the change is

A new **points-at-stake queue**: a read-only ordering of the existing due/new
queue by `topic weight × student weakness`, so the highest-value cards for the
NCEES FE Electrical and Computer exam surface first. Alongside it, three
**honest scores** — **memory**, **performance**, and **readiness** — each
aggregate the engine's existing FSRS retrievability into a range with a
pre-registered give-up rule (see `feprep/docs/model-*.md`). All four live in the
same Rust module and are exposed as four RPCs on `SchedulerService`.

- Topic weight is a static per-knowledge-area value, seeded from the NCEES
  question-count ranges and stored in the collection **config table**, not in
  engine code.
- Student weakness is `1 − mean recall` for a topic, derived from existing FSRS
  memory state. A topic with no history defaults to maximum weakness (1.0), so
  unseen high-weight areas surface early.
- Ordering is `weight × weakness` descending; ties fall back to FSRS
  due/overdue ordering, so the queue degrades to normal Anki behaviour when
  weights are equal.

The change **does not mutate** card scheduling state, intervals, or the review
log. It only reads cards, tags, and FSRS memory state and returns a list of card
ids. That is what keeps undo and collection integrity trivially intact.

## Why Rust, not Python

1. **It runs inside the queue-building path on every card fetch.** Ordering the
   due queue is a hot path; on large decks it must stay within the per-card
   latency target (p95 < 100 ms to show the next card). Computing FSRS
   retrievability per card and aggregating per-topic weakness in Python — across
   the PyO3 boundary, with protobuf encode/decode per card — would blow that
   budget. The engine already computes retrievability in Rust for the stats
   graphs (`rslib/src/stats/graphs/retrievability.rs`); reusing that path keeps
   it fast and consistent.
2. **One engine, two platforms.** The desktop and the phone share the Rust
   backend. Putting the ordering in Rust means the phone gets the identical
   ordering for free through the same protobuf method — a Python or Swift/JS
   reimplementation would drift and would not count as a shared engine.
3. **It builds on engine-owned data.** FSRS memory state, decay, and
   `seconds_since_last_review` are Rust-side concepts. Doing the math where the
   data lives avoids marshalling every card's memory state out to Python just to
   sort it.
4. **Correctness and testability.** The ordering is covered by Rust unit tests
   that run against a real in-memory collection, plus one Python-through-protobuf
   test that proves the boundary is wired correctly.

## Upstream files touched

New files (no merge conflict risk — additive):

- `rslib/src/scheduler/points_at_stake.rs` — the queue + all three score
  computations (`memory` / `performance` / `readiness`) and its Rust unit tests.
- `pylib/tests/test_points_at_stake.py` — the Python-through-protobuf tests
  (queue ordering + the three scores' give-up behaviour).
- `feprep/**` — fork assets (seed deck, deck builder, docs, AI tooling). Outside
  the upstream tree.

Modified upstream files:

| File | Change | Merge difficulty |
|------|--------|------------------|
| `proto/anki/scheduler.proto` | Added **4 RPCs** to `SchedulerService` (appended after `FuzzDelta`: `PointsAtStakeQueue`, `MemoryScore`, `PerformanceScore`, `ReadinessScore`) plus their request/response messages at the end of the file. | **Low.** Appended, so no reordering of existing method indices; conflicts only if upstream also appends to the same spots. |
| `rslib/src/scheduler/mod.rs` | Added `pub mod points_at_stake;`. | **Low.** One line in the module list. |
| `rslib/src/scheduler/service/mod.rs` | Implemented the **4 new trait methods** (`points_at_stake_queue`, `memory_score`, `performance_score`, `readiness_score`). | **Low–medium.** New methods appended to the existing `SchedulerService` impl; conflicts only if upstream edits the same tail region. |
| `rslib/src/tests.rs` | Added a `tags(&[String])` builder to the test-only `NoteAdder`. | **Low.** Test helper, additive. |
| `README.md` | Fork banner, feature summary, the three give-up rules, build pointers. | **Low.** Documentation. |

## Protobuf / service-registration notes

The new RPCs are declared on `SchedulerService` (the collection-level service),
so the build-time codegen (`rslib/proto` + `rslib/build.rs` +
`anki_proto_gen::get_services`) auto-generates:

- the Rust dispatch arm in `run_service_method`,
- the Python stubs in `_backend_generated.py` (`points_at_stake_queue`,
  `memory_score`, `performance_score`, `readiness_score`), and
- the TypeScript client stubs.

Because the methods were **appended** to the service, the load-bearing
`(service_index, method_index)` values for all existing methods are unchanged.
No manual service-index edits were required — the known Anki-fork friction point
(hand-registering a backend method) is avoided by declaring on the collection
service and letting the generator forward it to the backend.

## Future extension (out of scope for Wednesday)

The queue is the seed of the later weighted scheduler: weight and weakness are
already separated and config-driven, so the durable-vs-cram split, deadline
tuning, and the blocked-then-interleaved scheduler can extend the same ordering
without a schema change.
