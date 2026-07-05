# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork: reproducible two-client sync test (PRD challenge 7b).

Drives the *shared Rust sync engine* (the same `rslib` code AnkiDroid uses on the
phone) through the pylib API against a running self-hosted sync server. Proves:

  1. Reviews done on two separate clients ("desktop" A and "phone" B) all land in
     one place after syncing, with none lost and none double-counted.
  2. The conflict rule when the *same* card is reviewed on both clients offline:
     last-writer-wins for the card's scheduling state, while BOTH answer records
     are preserved in the revlog (nothing is silently dropped).

Prereqs: a sync server running (see feprep/docs/deploy-and-run.md), reachable at
--endpoint, with a user matching --user/--pass.

    $env:PYTHONPATH="pylib;out/pylib"
    out\\pyenv\\Scripts\\python.exe feprep\\sync_test.py \
        --endpoint http://127.0.0.1:27701/ --user fe --pass fe
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = ["pylib", "out/pylib"]

from anki.collection import Collection
from anki.decks import DeckId


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _start_sync_server(base: str, host: str, port: int, user: str, pw: str):
    """Spawn Anki's built-in Rust sync server (`python -m anki.syncserver`) so
    this test is one-command re-runnable with no external setup. Returns the
    Popen; caller terminates it. Waits until the port accepts connections."""
    env = os.environ.copy()
    env["SYNC_BASE"] = base
    env["SYNC_HOST"] = host
    env["SYNC_PORT"] = str(port)
    env["SYNC_USER1"] = f"{user}:{pw}"
    env["PYTHONPATH"] = os.pathsep.join(["pylib", "out/pylib"])
    proc = subprocess.Popen(
        [sys.executable, "-m", "anki.syncserver"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):  # up to ~30s for first-time startup
        if proc.poll() is not None:
            raise RuntimeError("sync server exited during startup")
        if _port_open(host, port):
            return proc
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"sync server did not come up on {host}:{port}")


def _sync(col: Collection, auth) -> str:
    """Run one sync round, handling the full-sync handshake exactly as the
    desktop app does (qt/aqt/sync.py). Returns a short status string."""
    out = col.sync_collection(auth, False)
    req = out.required
    if req in (out.NO_CHANGES, out.NORMAL_SYNC):
        return "normal" if req == out.NORMAL_SYNC else "no-changes"
    # Full-sync handshake. No media in this test, so server_usn is None.
    upload = req == out.FULL_UPLOAD
    col.close_for_full_sync()
    col.full_upload_or_download(auth=auth, server_usn=None, upload=upload)
    col.reopen(after_full_sync=True)
    return "full-upload" if upload else "full-download"


def _add_new_notes(col: Collection, n: int) -> None:
    basic = col.models.by_name("Basic")
    for i in range(n):
        note = col.new_note(basic)
        note["Front"] = f"Q{i}"
        note["Back"] = f"A{i}"
        note.tags = ["fe::circuit_analysis"]
        col.add_note(note, DeckId(1))


def _answer_n_new(col: Collection, n: int, ease: int = 4) -> list[int]:
    """Answer up to n *distinct* cards in queue order. ease=4 (Easy) graduates
    new cards straight to review so they don't reappear intraday. Returns the
    list of answered card ids."""
    answered: list[int] = []
    seen: set[int] = set()
    for _ in range(n):
        card = col.sched.getCard()
        if card is None or card.id in seen:
            break
        seen.add(card.id)
        col.sched.answerCard(card, ease)
        answered.append(card.id)
    return answered


def _revlog_count(col: Collection) -> int:
    return col.db.scalar("select count() from revlog")


def _revlog_for_card(col: Collection, cid: int) -> int:
    return col.db.scalar("select count() from revlog where cid = ?", cid)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default=None,
                    help="external sync server; if omitted, one is started locally")
    ap.add_argument("--user", default="fe")
    ap.add_argument("--pass", dest="password", default="fe")
    ap.add_argument("--no-serve", dest="serve", action="store_false",
                    help="do not self-host; use --endpoint instead")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp())
    server = None
    # Self-host Anki's built-in sync server unless an external endpoint is given,
    # so the test runs with a single command and no manual setup.
    if args.serve and not args.endpoint:
        host, port = "127.0.0.1", 27701
        server = _start_sync_server(str(tmp / "server"), host, port, args.user, args.password)
        args.endpoint = f"http://{host}:{port}/"
        print(f"started local sync server @ {args.endpoint}")
    elif not args.endpoint:
        args.endpoint = "http://127.0.0.1:27701/"

    col_a = Collection(str(tmp / "deviceA.anki2"))  # "desktop"
    col_b = Collection(str(tmp / "deviceB.anki2"))  # "phone"

    ok = True
    try:
        auth_a = col_a.sync_login(args.user, args.password, args.endpoint)
        auth_b = col_b.sync_login(args.user, args.password, args.endpoint)
        print(f"login OK for both clients @ {args.endpoint}")

        # Seed A with 14 new cards, push to server, pull down to B so both start
        # from an identical collection.
        _add_new_notes(col_a, 14)
        print("A initial sync:", _sync(col_a, auth_a))
        print("B initial sync:", _sync(col_b, auth_b))
        assert col_a.card_count() == col_b.card_count() == 14, "initial share failed"
        print(f"both clients share {col_a.card_count()} cards\n")

        # --- Part 1: conflict on the SAME card, both offline ---
        c_conflict = col_a.sched.getCard().id
        # A answers it Good; B answers the same card Again — neither has synced.
        ca = col_a.get_card(c_conflict); ca.start_timer(); col_a.sched.answerCard(ca, 3)
        cb = col_b.get_card(c_conflict); cb.start_timer(); col_b.sched.answerCard(cb, 1)
        print(f"[conflict] card {c_conflict}: A=Good, B=Again (both offline)")
        # A syncs first, then B, then A again to converge.
        print("  A sync:", _sync(col_a, auth_a))
        print("  B sync:", _sync(col_b, auth_b))
        print("  A sync:", _sync(col_a, auth_a))
        rc_a = _revlog_for_card(col_a, c_conflict)
        rc_b = _revlog_for_card(col_b, c_conflict)
        state_a = col_a.get_card(c_conflict).queue
        state_b = col_b.get_card(c_conflict).queue
        print(f"  revlog entries for the card: A={rc_a} B={rc_b} (both answers kept)")
        print(f"  converged card queue state: A={state_a} B={state_b} (last-writer-wins)")
        conflict_ok = rc_a == rc_b == 2 and state_a == state_b
        ok &= conflict_ok
        print(f"  CONFLICT RULE {'OK' if conflict_ok else 'FAIL'}\n")

        # --- Part 2: DIFFERENT cards on each client, no loss / no double-count ---
        a_cards = _answer_n_new(col_a, 6)
        print(f"[no-loss] A reviewed {len(a_cards)} cards, syncing")
        print("  A sync:", _sync(col_a, auth_a))
        print("  B sync:", _sync(col_b, auth_b))  # B pulls A's reviews
        b_cards = _answer_n_new(col_b, 6)  # now B's queue starts past A's cards
        print(f"[no-loss] B reviewed {len(b_cards)} cards, syncing")
        print("  B sync:", _sync(col_b, auth_b))
        print("  A sync:", _sync(col_a, auth_a))  # A pulls B's reviews

        overlap = set(a_cards) & set(b_cards)
        total_a = _revlog_count(col_a)
        total_b = _revlog_count(col_b)
        # Expected: 2 (conflict) + 6 (A) + 6 (B) = 14 unique review records.
        expected = 2 + len(a_cards) + len(b_cards)
        print(f"\n  A/B reviewed different cards, overlap={len(overlap)} (want 0)")
        print(f"  final revlog count: A={total_a} B={total_b} (want {expected} on both)")
        noloss_ok = (
            not overlap
            and total_a == total_b == expected
            and len(a_cards) == len(b_cards) == 6
        )
        ok &= noloss_ok
        print(f"  NO-LOSS / NO-DOUBLE-COUNT {'OK' if noloss_ok else 'FAIL'}")
    finally:
        col_a.close()
        col_b.close()
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:
                server.kill()

    print("\n==== SYNC TEST", "PASSED ====" if ok else "FAILED ====")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
