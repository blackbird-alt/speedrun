# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Quality gate for AUTHORED FE problems (the Opus-generated set).

The 7f verifier (verify_cards.py) grounds each card against a cited SOURCE LINE,
which doesn't apply to newly-authored problems. This gate is the authored-problem
equivalent: it enforces everything that CAN be checked mechanically before a
correctness re-solve (done separately by checker subagents):

  * well-formed  - has front, back, and a worked-solution explanation; any LaTeX
                   command is inside \\( \\)/\\[ \\] delimiters (no bare markup)
  * substantive  - answer not trivially short
  * unique       - not a near-duplicate of an existing deck card or of another
                   accepted authored problem (char-ngram cosine > 0.95)
  * no leakage   - NOT a near-copy of any held-out gold TEST item (challenge 7e):
                   reject if its question is too close to a gold question

Reads feprep/decks/generated/*.jsonl (skips passed.jsonl), uses the existing
deck fronts/backs from feprep/ai/work/explain_work.jsonl for dedup, and the gold
set for the leakage check. Writes feprep/decks/generated/passed.jsonl and prints
a per-section pass table.

    out\\pyenv\\Scripts\\python.exe feprep\\ai\\verify_authored.py
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [HERE]

import common as C  # noqa: E402

GEN_DIR = os.path.join(REPO, "feprep", "decks", "generated")
PASSED_PATH = os.path.join(GEN_DIR, "passed.jsonl")
WORK_FILE = os.path.join(HERE, "work", "explain_work.jsonl")

DUP_COSINE = 0.95          # vs existing deck + already-accepted authored
GOLD_LEAK_COSINE = 0.80    # question this close to a gold question = leakage
MIN_ANSWER_CHARS = 3

_LATEX_CMD = re.compile(r"\\[a-zA-Z]")


def _has_bare_latex(s: str) -> bool:
    return bool(_LATEX_CMD.search(s)) or "^" in s or "_" in s


def _has_delims(s: str) -> bool:
    return "\\(" in s or "\\[" in s


def _ngram_vec(text: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for g in C.char_ngrams(text, 3):
        counts[g] = counts.get(g, 0.0) + 1.0
    return counts


def load_generated() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(GEN_DIR, "*.jsonl"))):
        if os.path.basename(path) == "passed.jsonl":
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("front") and row.get("back"):
                    rows.append(row)
    return rows


def main() -> int:
    gen = load_generated()
    print(f"authored problems found: {len(gen)}")

    # existing deck vectors (dedup source) from the work file
    existing_vecs: list[dict[str, float]] = []
    if os.path.exists(WORK_FILE):
        with open(WORK_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                existing_vecs.append(_ngram_vec(f"{r.get('front','')} {r.get('back','')}"))

    gold = C.load_gold()

    accepted_vecs: list[dict[str, float]] = []
    passed: list[dict] = []
    reasons: dict[str, int] = {}
    per_section_pass: dict[str, int] = {}
    per_section_total: dict[str, int] = {}

    def bump(d, k):
        d[k] = d.get(k, 0) + 1

    for row in gen:
        front, back = str(row["front"]).strip(), str(row["back"]).strip()
        expl = str(row.get("explanation", "")).strip()
        area = row.get("area", "")
        bump(per_section_total, area)

        # well-formed
        if not expl:
            bump(reasons, "no_explanation")
            continue
        if _has_bare_latex(front + back) and not _has_delims(front + back):
            bump(reasons, "bare_latex")
            continue
        if len(C.normalize_text(back).replace(" ", "")) < MIN_ANSWER_CHARS:
            bump(reasons, "trivial")
            continue

        # leakage vs gold test items
        leaked = any(
            C.char_cosine(front, g["question"]) >= GOLD_LEAK_COSINE for g in gold
        )
        if leaked:
            bump(reasons, "gold_leak")
            continue

        # dedup vs existing deck + accepted
        vec = _ngram_vec(f"{front} {back}")
        if any(C.cosine_counts(vec, ev) > DUP_COSINE for ev in existing_vecs):
            bump(reasons, "dup_existing")
            continue
        if any(C.cosine_counts(vec, av) > DUP_COSINE for av in accepted_vecs):
            bump(reasons, "dup_authored")
            continue

        accepted_vecs.append(vec)
        passed.append(row)
        bump(per_section_pass, area)

    os.makedirs(GEN_DIR, exist_ok=True)
    with open(PASSED_PATH, "w", encoding="utf-8") as fh:
        for row in passed:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nPASSED: {len(passed)}   (rejections: {reasons})")
    print("\nper-section (passed / total authored):")
    for area in sorted(per_section_total):
        p, t = per_section_pass.get(area, 0), per_section_total[area]
        flag = "" if p >= 10 else "   <-- under 10!"
        print(f"  {area:38s} {p:3d} / {t:3d}{flag}")
    print(f"\n-> {PASSED_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
