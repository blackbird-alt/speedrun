# Sync conflict rule (PRD challenge 7b)

The Speedrun fork uses Anki's built-in sync engine (shared `rslib`, self-hosted
server — see `deploy-and-run.md`). This documents the merge behaviour and the
observed result of the required conflict test.

## The rule

- **Reviews (revlog) are append-only and never conflict.** Each answer is a row
  keyed by its own millisecond timestamp id. Sync unions the revlog from both
  sides, so a review done on the phone and a review done on the desktop both land
  exactly once — none lost, none double-counted.
- **Mutable objects (a card's scheduling state) use last-writer-wins**, keyed by
  update sequence number (USN) and modification time. When the *same* card is
  reviewed on two devices while both are offline, after both sync the card's
  current scheduling state is the later writer's. The earlier writer's answer is
  **not** discarded from history — its revlog row is preserved; only the card's
  single "current state" can hold one value, and that value is deterministic
  (later `mod` wins).

This is the correct, defensible behaviour for a study log: you never lose the fact
that a review happened, and the card's live schedule resolves deterministically.

## Observed result (`feprep/sync_test.py`)

Two clients ("desktop" A, "phone" B) share a 14-card collection, then:

1. **Conflict case** — card X answered **Good on A** and **Again on B**, both
   offline; sync A, sync B, sync A:
   - revlog entries for card X: **A=2, B=2** (both answers retained)
   - converged card queue state: **A == B** (last-writer-wins, deterministic)
2. **No-loss case** — A reviews 6 cards, B reviews 6 *different* cards, both then
   sync:
   - card overlap between the two review sets: **0**
   - final revlog count: **A=14, B=14** (= 2 conflict + 6 + 6), identical on both

Re-run to reproduce (server must be running):

```powershell
$env:PYTHONPATH="pylib;out/pylib"
out\pyenv\Scripts\python.exe feprep\sync_test.py --endpoint http://127.0.0.1:27701/ --user fe --pass fe
```

## Phone ⇄ desktop (real devices)

The same engine backs AnkiDroid. Point both the desktop fork and AnkiDroid
(Advanced → Custom sync server → `http://10.0.2.2:27701/` from the emulator) at
the self-hosted server, log in as the same user, and the identical merge rules
apply. The Friday proof recording captures a card reviewed on the phone appearing
on the desktop after sync.
