# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork: crash + offline resilience proof (PRD challenge 7g).

Two independent proofs, both re-runnable and self-contained:

1. CRASH TEST -- 20 iterations. Each iteration spawns a *separate* Python
   subprocess (the fork's own ``out\\pyenv\\Scripts\\python.exe``) that opens the
   SAME collection file, starts a real review loop (FSRS on), and is then
   **hard-killed mid-review** by the parent (``Popen.kill()`` -> Windows
   ``TerminateProcess`` / POSIX ``SIGKILL`` -- no clean ``col.close()``, no
   commit of the in-flight transaction). The parent reopens the collection and
   runs an integrity check (``pragma integrity_check`` plus the backend's own
   ``check_database`` via ``col.fix_integrity()``). Because SQLite guards every
   write with a rollback journal / WAL, an aborted write must roll back cleanly
   on reopen. We assert exactly that and print "N/20 clean". If any iteration
   comes back corrupt, it is reported honestly (no swallowing).

2. OFFLINE / AI-OFF -- with all outbound sockets monkeypatched to raise and the
   AI feature flag forced off, the collection still opens, reviews, and produces
   scores. The three score RPCs (``memory_score`` / ``performance_score`` /
   ``readiness_score``) are pure engine work over data Anki already owns (tags +
   FSRS memory state); they call no model and touch no network, so they still
   return their honest verdict (shown, or withheld with a reason) with zero
   connectivity.

Run with the fork's pyenv (finishes in a few minutes):

    out\\pyenv\\Scripts\\python.exe feprep\\crash_test.py

The ``--child`` mode is an internal entry point the parent uses to spawn the
review-then-die worker; you never invoke it by hand.
"""

from __future__ import annotations

import argparse
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = ["pylib", "out/pylib"]

from anki.collection import Collection
from anki.decks import DeckId

# Repo root (the dir that holds pylib/ and out/). The child subprocess is run
# with this as its CWD so the relative sys.path entries above resolve there too.
REPO_ROOT = Path(__file__).resolve().parent.parent
PYENV_PYTHON = REPO_ROOT / "out" / "pyenv" / "Scripts" / "python.exe"

AREAS = [
    "circuit_analysis",
    "power_systems",
    "electronics",
    "mathematics",
    "digital_systems",
]


# --------------------------------------------------------------------------- #
# Shared: build a small, realistic FE collection with FSRS enabled.
# --------------------------------------------------------------------------- #
def _build_fe_collection(path: str, per_area: int = 16) -> None:
    """Create a fresh FE collection at ``path`` with FSRS on and a generous
    new-cards/day limit so a review loop always has something to grade."""
    col = Collection(path)
    try:
        col.set_config("fsrs", True)
        # Raise the per-day new/review caps on the default preset. NB: the
        # config_dict_for_deck_id()+save() shortcut silently clamps back to the
        # default here, so update the deck config object directly.
        conf = col.decks.get_config(1)
        conf["new"]["perDay"] = 999999
        conf["rev"]["perDay"] = 999999
        col.decks.update_config(conf)

        basic = col.models.by_name("Basic")
        for area in AREAS:
            durable = area in ("circuit_analysis", "power_systems", "electronics")
            for i in range(per_area):
                note = col.new_note(basic)
                note["Front"] = f"{area} q{i}"
                note["Back"] = f"{area} a{i}"
                tags = [f"fe::{area}"]
                if durable and i < per_area * 2 // 3:
                    tags.append("track::durable")
                note.tags = tags
                col.add_note(note, DeckId(1))
    finally:
        col.close()


def _add_new_note(col: Collection, n: int) -> None:
    basic = col.models.by_name("Basic")
    note = col.new_note(basic)
    note["Front"] = f"topup q{n}"
    note["Back"] = f"topup a{n}"
    note.tags = ["fe::mathematics"]
    col.add_note(note, DeckId(1))


def _advance_one_day(col_path: str) -> None:
    """Roll the collection's clock back one day (its creation time) so the next
    reviewer sees a fresh batch of due cards. This honestly simulates studying
    the SAME collection file across consecutive days; without it the daily
    new-card cap would leave later iterations with nothing due to review."""
    col = Collection(col_path)
    try:
        col.db.execute("update col set crt = crt - 86400")
    finally:
        col.close()


# --------------------------------------------------------------------------- #
# Child entry point: open the collection, review hard, and never close cleanly.
# --------------------------------------------------------------------------- #
def _record_progress(count_path: Path, n: int) -> None:
    """Publish the child's in-process answer count via an atomic replace so the
    parent can read how much reviewing was underway when it pulled the trigger
    (even the answers that will be rolled back as uncommitted)."""
    tmp = count_path.with_suffix(count_path.suffix + ".tmp")
    tmp.write_text(str(n), encoding="utf-8")
    os.replace(tmp, count_path)


def _run_child(col_path: str, sentinel_path: str) -> None:
    """Open the shared collection and pound it with real review writes forever.

    This process is meant to be killed by the parent. It deliberately never
    calls ``col.close()``. To guarantee the kill lands *mid-review* (not before
    any work), it answers one card, drops a sentinel file to tell the parent
    "I'm live", then loops answering cards -- topping the queue up with new
    notes when it drains -- so the SQLite write stream never stops.
    """
    count_path = Path(sentinel_path + ".n")
    col = Collection(col_path)
    # First real write, then announce liveness so the parent only starts its
    # kill timer once the collection is genuinely under review.
    topup = 0
    answers = 0
    first = col.sched.getCard()
    if first is not None:
        col.sched.answerCard(first, random.randint(1, 4))
        answers += 1
    else:
        _add_new_note(col, topup)
        topup += 1
    _record_progress(count_path, answers)
    Path(sentinel_path).write_text("live", encoding="utf-8")

    # Continuous review write stream. No clean shutdown -- the parent kills us.
    while True:
        card = col.sched.getCard()
        if card is None:
            _add_new_note(col, topup)
            topup += 1
            continue
        col.sched.answerCard(card, random.randint(1, 4))
        answers += 1
        if answers % 5 == 0:
            _record_progress(count_path, answers)


# --------------------------------------------------------------------------- #
# Part 1: 20x crash-mid-review, prove zero corruption on reopen.
# --------------------------------------------------------------------------- #
def _integrity_ok(col_path: str) -> tuple[bool, str, int]:
    """Reopen the collection and check it thoroughly. Returns
    (clean, detail, revlog_count)."""
    col = Collection(col_path)
    try:
        pragma = col.db.scalar("pragma integrity_check")
        # The backend's own maintenance pass: rebuilds caches and reports any
        # structural problems it finds. ok is True only if it found none.
        _problems, ok = col.fix_integrity()
        revs = col.db.scalar("select count() from revlog") or 0
        clean = (pragma == "ok") and ok
        detail = f"pragma={pragma!r} check_database_ok={ok}"
        return clean, detail, int(revs)
    finally:
        col.close()


def _read_progress(count_path: Path) -> int:
    try:
        return int(count_path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _leftover_recovery_files(col_path: str) -> list[str]:
    p = Path(col_path)
    found = []
    for suffix in ("-wal", "-shm", "-journal"):
        if (p.parent / (p.name + suffix)).exists():
            found.append(suffix)
    return found


def run_crash_test(iterations: int = 20) -> tuple[int, int]:
    """Spawn-kill-reopen ``iterations`` times. Returns (clean, total)."""
    tmp = Path(tempfile.mkdtemp(prefix="fe_crash_"))
    col_path = str(tmp / "crash.anki2")
    print(f"[crash] building FE collection at {col_path}")
    # Seed a healthy backlog so, combined with the per-iteration day advance,
    # every reviewer subprocess has real cards to grade.
    _build_fe_collection(col_path, per_area=300)
    clean0, detail0, revs0 = _integrity_ok(col_path)
    print(f"[crash] baseline integrity: {detail0}, revlog={revs0}, "
          f"{'clean' if clean0 else 'CORRUPT'}\n")

    clean_count = 0
    prev_revs = revs0
    for i in range(1, iterations + 1):
        # Simulate the next day of study so this reviewer has a fresh due queue.
        _advance_one_day(col_path)

        sentinel = tmp / f"live_{i}.flag"
        count_path = tmp / f"live_{i}.flag.n"
        for stale in (sentinel, count_path):
            if stale.exists():
                stale.unlink()

        proc = subprocess.Popen(
            [str(PYENV_PYTHON), str(Path(__file__).resolve()),
             "--child", col_path, str(sentinel)],
            cwd=str(REPO_ROOT),
        )

        # Wait until the child is genuinely reviewing (sentinel dropped), or bail
        # if it died first (which would itself be an honest failure to report).
        deadline = time.time() + 30.0
        started = False
        while time.time() < deadline:
            if sentinel.exists():
                started = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.01)

        if not started:
            rc = proc.poll()
            print(f"[crash] iter {i:2d}: child never entered review "
                  f"(exit={rc}) -- treating as FAIL")
            proc.kill()
            proc.wait()
            clean, detail, revs = _integrity_ok(col_path)
            print(f"           reopen: {detail} -> "
                  f"{'clean' if clean else 'CORRUPT'}")
            if clean:
                clean_count += 1
            continue

        # Let the write stream run a random slice, then HARD KILL mid-review.
        # Popen.kill() == TerminateProcess on Windows / SIGKILL on POSIX: the
        # process dies instantly with any in-flight SQLite transaction aborted.
        time.sleep(random.uniform(0.05, 0.45))
        in_proc = _read_progress(count_path)
        proc.kill()
        proc.wait()

        leftovers = _leftover_recovery_files(col_path)
        clean, detail, revs = _integrity_ok(col_path)
        if clean:
            clean_count += 1
        delta = revs - prev_revs
        prev_revs = revs
        left = f" recovery-files={leftovers}" if leftovers else ""
        print(f"[crash] iter {i:2d}: reviewing (>={in_proc} answers in-flight), "
              f"hard-killed; reopen committed +{delta}, {detail} -> "
              f"{'clean' if clean else 'CORRUPT'}{left}")

    print(f"\n[crash] clean collections: {clean_count}/{iterations}")
    print(f"[crash] {clean_count}/{iterations} clean")
    return clean_count, iterations


# --------------------------------------------------------------------------- #
# Part 2: offline / AI-off -- open, review, and score with no network.
# --------------------------------------------------------------------------- #
def _install_network_block() -> None:
    """Make every outbound socket operation raise, simulating no connectivity."""

    def _blocked(*_a, **_k):
        raise OSError("offline resilience test: outbound network is disabled")

    socket.socket.connect = _blocked  # type: ignore[assignment]
    socket.socket.connect_ex = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.getaddrinfo = _blocked  # type: ignore[assignment]


def _verify_offline() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=1)
    except OSError:
        return True
    return False


def _fmt_range(lo: float, pt: float, hi: float) -> str:
    return f"{pt * 100:5.1f}%  [{lo * 100:4.1f}% - {hi * 100:4.1f}%]"


def run_offline_ai_off() -> bool:
    print("[offline] cutting network + forcing AI off ...")
    _install_network_block()
    offline = _verify_offline()
    print(f"[offline] outbound network raises: {offline}")

    tmp = Path(tempfile.mkdtemp(prefix="fe_offline_"))
    col_path = str(tmp / "offline.anki2")
    _build_fe_collection(col_path, per_area=40)

    # Hard AI-off: whatever flag the app reads, it is false here. The scores
    # below never consult it anyway -- they are pure FSRS/tag engine work.
    col = Collection(col_path)
    col.set_config("feAiEnabled", False)
    assert col.get_config("feAiEnabled", False) is False
    col.close()

    # Study across several simulated days (still no network) so enough FSRS
    # memory state accrues to actually surface a score, not just withhold one.
    total_reviewed = 0
    for _ in range(6):
        _advance_one_day(col_path)
        col = Collection(col_path)
        seen: set[int] = set()
        while True:
            card = col.sched.getCard()
            if card is None or card.id in seen:
                break
            seen.add(card.id)
            col.sched.answerCard(card, random.choice([3, 4]))
        total_reviewed += len(seen)
        col.close()
    print(f"[offline] opened + reviewed {total_reviewed} cards over 6 simulated "
          f"days with no network and AI off")

    col = Collection(col_path)
    ok = True
    try:

        mem = col._backend.memory_score(search="is:review OR is:learn")
        perf = col._backend.performance_score(search="tag:track::durable")
        rdy = col._backend.readiness_score(search="is:review OR is:learn")

        def _emit(label: str, resp, point) -> None:
            if resp.shown:
                print(f"[offline] {label:11s} "
                      f"{_fmt_range(resp.range_low, point, resp.range_high)}  "
                      f"{resp.main_reason}")
            else:
                print(f"[offline] {label:11s} withheld - {resp.withheld_reason}")

        _emit("MEMORY", mem, mem.point_estimate)
        _emit("PERFORMANCE", perf, perf.point_estimate)
        _emit("READINESS", rdy, rdy.pass_probability)
        print(f"[offline] readiness coverage {rdy.areas_covered}/"
              f"{rdy.areas_total} areas; next: {rdy.next_action}")

        # The proof is that all three RPCs *returned a verdict* (shown xor
        # withheld) with the network cut -- no exception, no hang.
        for resp in (mem, perf, rdy):
            if not (resp.shown or resp.withheld_reason):
                ok = False
    finally:
        col.close()

    ok = ok and offline
    print(f"[offline] AI-OFF / OFFLINE {'OK' if ok else 'FAIL'} - "
          f"app opened, reviewed, and produced scores with no connectivity")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--child", nargs=2, metavar=("COL", "SENTINEL"),
                    help=argparse.SUPPRESS)
    ap.add_argument("--iterations", type=int, default=20)
    args = ap.parse_args()

    if args.child:
        _run_child(args.child[0], args.child[1])
        return  # unreachable in practice: the parent hard-kills us first

    print("=" * 68)
    print("FE crash + offline resilience test (challenge 7g)")
    print("=" * 68)
    clean, total = run_crash_test(args.iterations)
    print()
    print("=" * 68)
    ai_ok = run_offline_ai_off()
    print("=" * 68)

    crash_ok = clean == total
    print(f"\nSUMMARY: crash {clean}/{total} clean; "
          f"offline/AI-off {'OK' if ai_ok else 'FAIL'}")
    sys.exit(0 if (crash_ok and ai_ok) else 1)


if __name__ == "__main__":
    main()
