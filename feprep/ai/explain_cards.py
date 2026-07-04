# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Phase 2: generate a LaTeX-typeset front/back + a worked-solution explanation
for one shard of FE cards. Reads the work file exported by explain_prep.py,
calls OpenAI per card, and writes its own output shard. NEVER opens the Anki
collection (so many shards can run in parallel with no lock contention).

The AI is told to change NOTHING except typesetting math as LaTeX, and to add a
worked solution. A strip-LaTeX self-check compares the reformatted front/back to
the original: if the content drifts, the ORIGINAL text is kept (only the
explanation is added), so the AI can never silently alter a card's facts.

Run one shard:
    out\\pyenv\\Scripts\\python.exe feprep\\ai\\explain_cards.py --shard 0 --num-shards 8
Output: feprep/ai/work/explain_out_<shard>.jsonl  ({nid, front, back, explanation})
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]

import common as C  # noqa: E402
from providers.openai import OpenAIProvider, ProviderError, _repair_latex, api_key  # noqa: E402

WORK_FILE = os.path.join(HERE, "work", "explain_work.jsonl")

PROMPT = (
    "You are formatting ONE flashcard for the NCEES FE Electrical and Computer "
    "exam and writing its worked solution. You are given the card FRONT "
    "(question) and BACK (answer).\n"
    "CRITICAL: every mathematical expression, variable, number-with-unit, Greek "
    "letter, subscript, exponent, and fraction MUST be wrapped in MathJax "
    "delimiters: inline math in \\( ... \\) and displayed math in \\[ ... \\]. "
    "Use single-backslash LaTeX commands (\\cos, \\frac, \\omega, \\angle, "
    "\\sqrt, \\sin). NEVER write a bare command like \\cos(x) outside "
    "delimiters, and NEVER use a double backslash before a command.\n"
    "Return STRICT JSON with three string fields:\n"
    '  "front": the SAME question, wording unchanged, but with every math bit '
    "wrapped in \\( \\). Do NOT add/remove/reword facts, numbers, or units.\n"
    '  "back": the SAME answer, wording/values unchanged, math wrapped in \\( \\).\n'
    '  "explanation": a concise worked solution (2-5 short steps) with ALL math '
    "in \\( \\) / \\[ \\], grounded strictly in the given Q/A and standard FE "
    "method.\n"
    "WORKED EXAMPLES (follow this formatting exactly):\n"
    'FRONT "What is the derivative of cos(x) with respect to x?" -> '
    '"What is the derivative of \\( \\cos(x) \\) with respect to \\( x \\)?"\n'
    'BACK "-sin(x)" -> "\\( -\\sin(x) \\)"\n'
    'BACK "1/(jwC)" -> "\\( \\dfrac{1}{j\\omega C} \\)"\n'
    'explanation -> "\\[ \\frac{d}{dx}\\cos(x) = -\\sin(x) \\]"\n'
    'Return exactly: {"front": "...", "back": "...", "explanation": "..."}'
)


_DBL_CMD = re.compile(r"\\\\(?=[a-zA-Z(\[])")


def _fix_latex(text: str) -> str:
    """Repair the two escape mistakes the model makes inside JSON strings:
    control-char collapse (\\tfrac -> TAB+frac) and a doubled backslash before a
    command/delimiter (\\\\cos -> \\cos, \\\\( -> \\()."""
    text = _repair_latex(text)
    text = _DBL_CMD.sub(r"\\", text)
    return text


_LATEX_CMD = re.compile(r"\\[a-zA-Z]")


def _has_delims(s: str) -> bool:
    return "\\(" in s or "\\[" in s


def _has_bare_latex(s: str) -> bool:
    return bool(_LATEX_CMD.search(s)) or "^" in s or "_" in s


def _shard_rows(rows: list[dict], shard: int, num: int) -> list[dict]:
    return [r for i, r in enumerate(rows) if i % num == shard]


def _accept_reformat(original: str, reformatted: str) -> str:
    """Keep the AI's LaTeX ONLY if it (a) preserves the content (facts
    unchanged) and (b) is properly delimited. If the model emitted bare LaTeX
    with no \\( \\) delimiters (which would not typeset and could render as
    garbage), fall back to the original plain text - never store broken markup."""
    if not reformatted:
        return original
    # content must be preserved (LaTeX folded away before comparison)
    n_orig, n_new = C.normalize_text(original), C.normalize_text(reformatted)
    if not (n_orig == n_new or C.token_f1(n_orig, n_new) >= 0.9):
        return original
    # if it introduced LaTeX commands, they must be inside delimiters
    if _has_bare_latex(reformatted) and not _has_delims(reformatted):
        return original
    return reformatted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--num-shards", type=int, required=True)
    args = ap.parse_args()

    if not api_key():
        print("ERROR: no OPENAI_API_KEY (.env or env). Aborting.", file=sys.stderr)
        return 2

    with open(WORK_FILE, encoding="utf-8") as fh:
        all_rows = [json.loads(line) for line in fh if line.strip()]
    rows = _shard_rows(all_rows, args.shard, args.num_shards)
    out_path = os.path.join(HERE, "work", f"explain_out_{args.shard}.jsonl")

    provider = OpenAIProvider()
    key = api_key()
    done = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for r in rows:
            front, back = r["front"], r["back"]
            user = f"{PROMPT}\n\nFRONT: {front}\nBACK: {back}"
            data = None
            for attempt in range(2):
                try:
                    content = provider._chat(key, user)
                    data = json.loads(content)
                    break
                except (ProviderError, json.JSONDecodeError, Exception):
                    data = None
                    continue
            if not isinstance(data, dict):
                # keep original text, no explanation, so merge still typesets nothing
                out.write(json.dumps({"nid": r["nid"], "front": front, "back": back,
                                      "explanation": ""}, ensure_ascii=False) + "\n")
                continue
            nf = _accept_reformat(front, _fix_latex(str(data.get("front", "")).strip()))
            nb = _accept_reformat(back, _fix_latex(str(data.get("back", "")).strip()))
            expl = _fix_latex(str(data.get("explanation", "")).strip())
            out.write(json.dumps({"nid": r["nid"], "front": nf, "back": nb,
                                  "explanation": expl}, ensure_ascii=False) + "\n")
            out.flush()
            done += 1
            if done % 20 == 0:
                print(f"shard {args.shard}: {done}/{len(rows)}")
    print(f"shard {args.shard}: DONE {done}/{len(rows)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
