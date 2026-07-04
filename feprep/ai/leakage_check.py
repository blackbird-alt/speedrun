# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Leakage check (Friday challenge 7e).

Independently audits the generation inputs to prove that no gold/test item — or
near-duplicate of one — was ever fed to the generator (honesty rule #4). It
reads the exact input set written by ``generate_cards.py``
(``out/generation_inputs.jsonl``); if that file is missing it reconstructs the
inputs from the decks so the check can run standalone.

Prints a CLEAN / DIRTY verdict and **exits non-zero if dirty**, so it can gate
CI and the one-command run.

Run: python feprep/ai/leakage_check.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C  # noqa: E402
from generate_cards import INPUTS_PATH  # noqa: E402


def load_audit_spans() -> tuple[list[C.Span], str]:
    if os.path.exists(INPUTS_PATH):
        spans = [C.input_row_to_span(r) for r in C.read_jsonl(INPUTS_PATH)]
        return spans, INPUTS_PATH
    inputs, _ = C.build_generation_inputs()
    return inputs, "(reconstructed from decks)"


def run() -> dict:
    gold = C.load_gold()
    spans, source = load_audit_spans()
    leaks = C.find_leaks(spans, gold)
    return {
        "n_inputs": len(spans),
        "n_gold": len(gold),
        "leaks": leaks,
        "clean": len(leaks) == 0,
        "source": source,
    }


def main() -> int:
    info = run()
    print("=== leakage_check (challenge 7e) ===")
    print(f"generation inputs audited : {info['n_inputs']}  from {info['source']}")
    print(f"gold items                : {info['n_gold']}")
    if info["clean"]:
        print("verdict: CLEAN - no gold item or near-duplicate is in the generation inputs")
        return 0
    print(f"verdict: DIRTY - {len(info['leaks'])} leak(s) found:")
    for leak in info["leaks"][:20]:
        print(f"  {leak['span']} leaks {leak.get('gold_id','?')} ({leak['reason']})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
