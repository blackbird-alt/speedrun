# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Stop long MathJax/math from overflowing off the side of FE Prep cards.

Adds CSS to the 'FE Prep' note type so long inline/display math scales to the
card width and scrolls horizontally inside its box instead of running off the
screen. The note-type CSS syncs, so this fixes desktop AND phone card rendering
(question, answer, and worked-solution blocks) after a sync. Idempotent.

Anki must be CLOSED. Run:
    out\\pyenv\\Scripts\\python.exe feprep\\ai\\fix_math_css.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path[:0] = [os.path.join(REPO, "pylib"), os.path.join(REPO, "out", "pylib")]

from anki.collection import Collection  # noqa: E402

COL = r"C:\Users\ellie\AppData\Roaming\Anki2\User 1\collection.anki2"
MARKER = "fe-math-overflow"
CSS_FIX = """
/* fe-math-overflow: keep long MathJax/math inside the card (scale + scroll, never clip off-screen) */
.fe-q, .fe-a, .fe-x-body { max-width:100%; overflow-x:auto; overflow-y:hidden; overflow-wrap:anywhere; }
mjx-container { max-width:100%; }
mjx-container[display="true"] { overflow-x:auto; overflow-y:hidden; padding-bottom:2px; }
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    col = Collection(COL)
    try:
        fe = col.models.by_name("FE Prep")
        if fe is None:
            print("ERROR: 'FE Prep' note type not found")
            return 2
        if MARKER in fe["css"]:
            print("math-overflow CSS already present")
            return 0
        if not args.apply:
            print("would append math-overflow CSS to FE Prep (dry run)")
            return 0
        fe["css"] = fe["css"] + CSS_FIX
        col.models.update_dict(fe)
        print("appended math-overflow CSS to FE Prep note type")
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
