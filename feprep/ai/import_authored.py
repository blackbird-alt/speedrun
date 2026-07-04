# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Import the gate-passed authored problems (feprep/decks/generated/passed.jsonl)
into the live collection as 'FE Prep' notes, so they show typeset math + a worked
solution and live in the right NCEES area subdeck.

Each note: Front/Back/Explanation (LaTeX preserved), Topic + Accent for the area,
tagged `fe::<area> ai::generated`. Deduplicates against notes already in the
collection by (front, back) so re-running is safe.

Anki must be CLOSED. Run:
    out\\pyenv\\Scripts\\python.exe feprep\\ai\\import_authored.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [os.path.join(REPO, "pylib"), os.path.join(REPO, "out", "pylib")]

from anki.collection import Collection  # noqa: E402

COL = r"C:\Users\ellie\AppData\Roaming\Anki2\User 1\collection.anki2"
PASSED = os.path.join(REPO, "feprep", "decks", "generated", "passed.jsonl")
DECK_ROOT = "FE Electrical and Computer"

# area key -> (display name, chip accent) mirroring build_apkg.py TOPICS.
TOPICS = {
    "mathematics": ("Mathematics", "#5C9BFF"),
    "circuit_analysis": ("Circuit Analysis", "#E7A867"),
    "power_systems": ("Power Systems", "#F2849E"),
    "digital_systems": ("Digital Systems", "#5FD0C0"),
    "electronics": ("Electronics", "#C58BF2"),
    "control_systems": ("Control Systems", "#6FA8DC"),
    "signal_processing": ("Signal Processing", "#7FB2F0"),
    "linear_systems": ("Linear Systems", "#9AA7F0"),
    "communications": ("Communications", "#E0A45C"),
    "electromagnetics": ("Electromagnetics", "#8FD0A0"),
    "engineering_sciences": ("Engineering Sciences", "#D9A066"),
    "computer_systems": ("Computer Systems", "#7FC8D8"),
    "computer_networks": ("Computer Networks", "#6FB0C8"),
    "software_development": ("Software Development", "#B7A6F0"),
    "probability_statistics": ("Probability & Statistics", "#8FBCE0"),
    "engineering_economics": ("Engineering Economics", "#D8B36A"),
    "properties_of_electrical_materials": ("Electrical Materials", "#C79AD8"),
    "ethics": ("Ethics", "#8AC0A8"),
}
DEFAULT_ACCENT = "#2F6BFF"


def _disp(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\n", "<br>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(PASSED, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    print(f"passed authored problems: {len(rows)}")

    col = Collection(COL)
    try:
        fe = col.models.by_name("FE Prep")
        if fe is None:
            print("ERROR: 'FE Prep' note type missing")
            return 2
        field_names = {f["name"] for f in fe["flds"]}

        # existing (front, back) keys to avoid duplicate imports on re-run
        existing = set()
        for nid in col.find_notes(""):
            d = dict(col.get_note(nid).items())
            if "Front" in d:
                existing.add((d["Front"].strip(), d.get("Back", "").strip()))

        if not args.apply:
            new = sum(1 for r in rows if (r["front"].strip(), r["back"].strip()) not in existing)
            print(f"would import {new} new notes (dry run; pass --apply)")
            return 0

        added = skipped = 0
        deck_ids: dict[str, int] = {}
        for r in rows:
            front, back = r["front"].strip(), r["back"].strip()
            if (front, back) in existing:
                skipped += 1
                continue
            area = r.get("area", "")
            disp, accent = TOPICS.get(area, (area.replace("_", " ").title() or "FE Prep", DEFAULT_ACCENT))
            deck_name = f"{DECK_ROOT}::{disp}"
            if deck_name not in deck_ids:
                deck_ids[deck_name] = col.decks.id(deck_name)
            note = col.new_note(fe)
            note["Front"] = _disp(front)
            note["Back"] = _disp(back)
            if "Explanation" in field_names:
                note["Explanation"] = _disp(r.get("explanation", "").strip())
            note["Topic"] = disp
            note["Accent"] = accent
            note.tags = [f"fe::{area}", "ai::generated"] if area else ["ai::generated"]
            col.add_note(note, deck_ids[deck_name])
            existing.add((front, back))
            added += 1
        print(f"imported {added} new notes ({skipped} already present)")
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
