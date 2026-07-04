# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Phase 3: merge the generated shards into the live collection.

Reads every feprep/ai/work/explain_out_*.jsonl, and for each note:
  * sets Front/Back to the LaTeX-typeset versions (already content-checked by
    explain_cards.py; falls back to originals there when unsafe), and
  * sets the new Explanation field to the worked solution.
Then updates the 'FE Prep' back template to render the worked solution (MathJax
typesets it natively) only when Explanation is non-empty.

Anki must be CLOSED. Run:
    out\\pyenv\\Scripts\\python.exe feprep\\ai\\explain_merge.py --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [os.path.join(REPO, "pylib"), os.path.join(REPO, "out", "pylib")]

from anki.collection import Collection  # noqa: E402

COL = r"C:\Users\ellie\AppData\Roaming\Anki2\User 1\collection.anki2"
WORK_DIR = os.path.join(HERE, "work")

# Back template: answer, then a worked-solution block shown only when present.
# {{Explanation}} is rendered by Anki with MathJax, so \( \) / \[ \] typeset.
BACK_TEMPLATE = """\
<div class="fe-card fe-card--answer" style="--chip: {{Accent}};">
  <div class="fe-top">
    <span class="fe-chip">{{Topic}}</span>
    <span class="fe-brand">FE&nbsp;·&nbsp;EE/CE</span>
  </div>
  <div class="fe-q">{{Front}}</div>
  <div class="fe-trace"></div>
  <div class="fe-a"><span class="fe-a-label">Answer</span>{{Back}}</div>
  {{#Explanation}}
  <div class="fe-x"><span class="fe-x-label">Worked solution &middot; AI</span><div class="fe-x-body">{{Explanation}}</div></div>
  {{/Explanation}}
</div>
"""

# Appended styling for the worked-solution block (kept visually distinct from
# the verified answer so students never confuse AI guidance with the answer).
EXTRA_CSS = """
.fe-x{
  margin-top:18px; border:1px dashed color-mix(in srgb,var(--chip) 40%,transparent);
  border-radius:11px; padding:14px 16px; background:color-mix(in srgb,var(--chip) 7%,transparent);
}
.fe-x-label{
  display:block; font:600 10px/1 var(--sans); letter-spacing:.16em;
  text-transform:uppercase; color:var(--muted); margin-bottom:9px;
}
.fe-x-body{ font:400 clamp(.98rem,.9rem+.5vw,1.12rem)/1.5 var(--sans); color:var(--text); }
.fe-q, .fe-a, .fe-x-body{ max-width:100%; overflow-x:auto; overflow-y:hidden; overflow-wrap:anywhere; }
mjx-container{ max-width:100%; }
mjx-container[display="true"]{ overflow-x:auto; overflow-y:hidden; padding-bottom:2px; }
"""


def load_outputs() -> dict[int, dict]:
    merged: dict[int, dict] = {}
    for path in sorted(glob.glob(os.path.join(WORK_DIR, "explain_out_*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                merged[int(row["nid"])] = row
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    outputs = load_outputs()
    print(f"loaded {len(outputs)} generated rows from shards")
    n_expl = sum(1 for r in outputs.values() if r.get("explanation"))
    print(f"  with a non-empty explanation: {n_expl}")

    if not args.apply:
        print("(dry run - pass --apply to write into the collection)")
        return 0

    col = Collection(COL)
    try:
        mm = col.models
        fe = mm.by_name("FE Prep")
        if fe is None:
            print("ERROR: 'FE Prep' note type not found")
            return 2
        if not any(f["name"] == "Explanation" for f in fe["flds"]):
            mm.add_field(fe, mm.new_field("Explanation"))

        # update the back template + css
        tmpl = fe["tmpls"][0]
        tmpl["afmt"] = BACK_TEMPLATE
        if ".fe-x{" not in fe["css"]:
            fe["css"] = fe["css"] + EXTRA_CSS
        mm.update_dict(fe)
        print("updated FE Prep back template + css")

        updated = expl_set = 0
        for nid, row in outputs.items():
            try:
                note = col.get_note(nid)
            except Exception:
                continue
            d = dict(note.items())
            if "Front" not in d:
                continue
            changed = False
            if row.get("front") and note["Front"] != row["front"]:
                note["Front"] = row["front"]
                changed = True
            if row.get("back") and note["Back"] != row["back"]:
                note["Back"] = row["back"]
                changed = True
            if "Explanation" in note and row.get("explanation"):
                note["Explanation"] = row["explanation"]
                expl_set += 1
                changed = True
            if changed:
                col.update_note(note)
                updated += 1
        print(f"updated {updated} notes ({expl_set} explanations set)")
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
