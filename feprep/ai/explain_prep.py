# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Phase 1 for the 'typeset math + AI worked answer' upgrade.

--apply: (1) ensure the 'FE Prep' note type has an 'Explanation' field, and
(2) export a work file of every FE note ({nid, front, back, area}) for the
explanation-generation subagents. Does NOT call AI and does NOT rewrite content
or templates here.

Default (no --apply): just report the notes it would export. Writes nothing.

Anki must be CLOSED. Run:
    out\\pyenv\\Scripts\\python.exe feprep\\ai\\explain_prep.py --apply
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [os.path.join(REPO, "pylib"), os.path.join(REPO, "out", "pylib"), HERE]

from anki.collection import Collection  # noqa: E402

COL = r"C:\Users\ellie\AppData\Roaming\Anki2\User 1\collection.anki2"
WORK_DIR = os.path.join(HERE, "work")
WORK_FILE = os.path.join(WORK_DIR, "explain_work.jsonl")


def area_of(tags: list[str]) -> str:
    for t in tags:
        if t.lower().startswith("fe::"):
            return t.lower()[4:].split("::", 1)[0]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    col = Collection(COL)
    try:
        rows = []
        for nid in col.find_notes(""):
            note = col.get_note(nid)
            d = dict(note.items())
            if "Front" not in d or "Back" not in d:
                continue
            rows.append({
                "nid": nid,
                "front": html.unescape(d["Front"]),
                "back": html.unescape(d["Back"]),
                "area": area_of(note.tags),
            })
        print(f"FE notes to process: {len(rows)}")

        if not args.apply:
            print("(dry run - nothing written; pass --apply)")
            return 0

        mm = col.models
        fe = mm.by_name("FE Prep")
        if fe is not None and not any(f["name"] == "Explanation" for f in fe["flds"]):
            mm.add_field(fe, mm.new_field("Explanation"))
            mm.update_dict(fe)
            print("added 'Explanation' field to FE Prep")
        else:
            print("'Explanation' field already present (or FE Prep missing)")

        os.makedirs(WORK_DIR, exist_ok=True)
        with open(WORK_FILE, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote work file: {WORK_FILE} ({len(rows)} rows)")
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
