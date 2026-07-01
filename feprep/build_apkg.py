# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the FE Electrical and Computer seed deck (.apkg) from the verified
tab-separated source in `feprep/decks/fe-seed-deck.txt`.

The .txt file is the canonical, human-verifiable content and can also be
imported directly through Anki's File > Import dialog. This script just packages
it so it can be shipped/loaded on desktop and phone without the import step.

Requires a built `anki` pylib on PYTHONPATH (build the fork first, then run
`./run` once, or install the wheel). Usage:

    python feprep/build_apkg.py [--out feprep/decks/fe-seed-deck.apkg]
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from anki.collection import Collection, ExportAnkiPackageOptions

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "decks" / "fe-seed-deck.txt"
DECK_NAME = "FE Electrical and Computer::Seed"


def parse_source(path: Path) -> list[tuple[str, str, str]]:
    """Return (front, back, tag) triples, skipping `#` directive lines."""
    rows: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"expected 3 tab-separated columns, got: {line!r}")
        front, back, tag = (p.strip() for p in parts)
        rows.append((front, back, tag))
    return rows


def build(out_path: Path) -> int:
    rows = parse_source(SOURCE)
    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "build.anki2"))
        try:
            basic = col.models.by_name("Basic")
            deck_id = col.decks.id(DECK_NAME)
            for front, back, tag in rows:
                note = col.new_note(basic)
                note["Front"] = front
                note["Back"] = back
                note.tags = [tag]
                col.add_note(note, deck_id)
            col.export_anki_package(
                out_path=str(out_path),
                options=ExportAnkiPackageOptions(
                    with_scheduling=False,
                    with_media=False,
                    legacy=True,
                ),
                limit=None,
            )
        finally:
            col.close()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=HERE / "decks" / "fe-seed-deck.apkg",
        help="output .apkg path",
    )
    args = parser.parse_args()
    count = build(args.out)
    print(f"wrote {count} cards to {args.out}")


if __name__ == "__main__":
    main()
