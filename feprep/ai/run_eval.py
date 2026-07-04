# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""One command to run the whole FE AI pipeline, offline and reproducibly.

    python feprep/ai/run_eval.py

Runs, in order:

    1. generate_cards  — author candidate cards from the gold-free source inputs
    2. verify_cards    — 3-way gate (correct_useful / wrong / bad_teaching)
    3. leakage_check   — prove no gold item leaked into the generation inputs
    4. eval            — accuracy + wrong-rate vs keyword & vector baselines

Uses only the Python standard library. With no LLM credential in the
environment it runs the deterministic stub provider, so the printed numbers are
identical on every run and every machine.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # keep unicode output readable on legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import eval as eval_stage  # noqa: E402
import generate_cards  # noqa: E402
import leakage_check  # noqa: E402
import verify_cards  # noqa: E402


def _rule(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    provider = os.environ.get("FE_AI_PROVIDER")

    _rule("STEP 1/4  generate candidate cards")
    gen = generate_cards.generate(provider)
    print(f"provider   : {gen['provider']} (model={gen['model']})")
    print(f"inputs kept: {gen['n_inputs']}  (held out {gen['n_removed']} gold/near-dups)")
    print(f"candidates : {gen['n_candidates']}  {gen['kinds']}")

    _rule("STEP 2/4  verify cards (challenge 7f)")
    ver = verify_cards.verify()
    c = ver["counts"]
    print("cutoffs SET BEFORE LOOKING AT RESULTS: "
          f"grounding>={verify_cards.GROUNDING_MIN_RECALL}, "
          f"dup_cosine>{verify_cards.DUP_COSINE}, "
          f"min_answer_chars={verify_cards.MIN_ANSWER_CHARS}")
    print(f"correct_useful : {c['correct_useful']}")
    print(f"wrong          : {c['wrong']}")
    print(f"bad_teaching   : {c['bad_teaching']}")
    print(f"shipped        : {ver['n_kept']}")

    _rule("STEP 3/4  leakage check (challenge 7e)")
    leak = leakage_check.run()
    if leak["clean"]:
        print(f"verdict: CLEAN - audited {leak['n_inputs']} generation inputs, "
              f"no gold/near-dup present")
    else:
        print(f"verdict: DIRTY - {len(leak['leaks'])} leak(s):")
        for lk in leak["leaks"][:20]:
            print(f"  {lk['span']} leaks {lk.get('gold_id','?')} ({lk['reason']})")

    _rule("STEP 4/4  eval vs baselines (held-out gold)")
    ev = eval_stage.evaluate()
    eval_stage.print_report(ev)

    _rule("SUMMARY")
    print(f"cards: {c['correct_useful']} correct_useful / {c['wrong']} wrong / "
          f"{c['bad_teaching']} bad_teaching")
    print(f"leakage: {'CLEAN' if leak['clean'] else 'DIRTY'}")
    ai = ev["results"][0]
    print(f"AI accuracy={ai['accuracy']:.1%}  wrong-rate={ai['wrong_rate']:.1%}  "
          f"precision={ai['precision']:.1%}  net={ai['net_correct']:+d}")
    print(f"beats simpler method on domain-critical axes (wrong-rate + net): "
          f"{ev['beats_baselines']}  |  on raw accuracy: {ev['beats_accuracy']}")
    print(f"raw-accuracy ship cutoff: {'PASS' if ev['passed'] else 'FAIL (honest)'}")

    # Non-zero exit if leakage is dirty (a hard honesty failure). A FAIL gate is
    # a legitimate, honest result and does not error the command.
    return 0 if leak["clean"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
