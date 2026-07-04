# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun fork (challenge 7c): FE Electrical & Computer coverage map.

Prints, for the shipped FE deck, which of the ~18 NCEES knowledge areas are
actually covered by at least one card, the overall **percent of areas covered**
and **percent of exam weight covered**, and whether the deck clears the
readiness coverage line (``feReadinessMinCoverage``, default 0.5) below which the
readiness score deliberately abstains.

This is a read-only diagnostic built entirely on data the engine already owns
(tags + config). No model is called and no network is used.

Runnable against the built engine:

    $env:PYTHONPATH = "pylib;out/pylib"
    out\\pyenv\\Scripts\\python.exe feprep\\coverage_map.py

The NCEES outline below mirrors ``FE_TOPICS`` / ``FE_TAG_KEYS`` in
``qt/aqt/deckbrowser.py`` and the seed weights in
``rslib/src/scheduler/points_at_stake.rs`` so the three stay in lock-step.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path[:0] = ["pylib", "out/pylib"]

from anki.collection import (
    Collection,
    ImportAnkiPackageOptions,
    ImportAnkiPackageRequest,
)

# Path to the shipped FE deck, relative to the repo root (this file lives in
# feprep/, so the deck sits alongside it under decks/).
DECK_PATH = Path(__file__).resolve().parent / "decks" / "fe-electrical.apkg"

# Default readiness coverage line; overridden by config key
# ``feReadinessMinCoverage`` if a collection sets it. Kept in sync with
# DEFAULT_READINESS_MIN_COVERAGE in points_at_stake.rs.
DEFAULT_READINESS_MIN_COVERAGE = 0.5
READINESS_MIN_COVERAGE_CONFIG_KEY = "feReadinessMinCoverage"


@dataclass(frozen=True)
class Area:
    """One NCEES knowledge area.

    ``display`` is the human name, ``key`` is the ``fe::<key>`` tag suffix on the
    cards, ``q_low``/``q_high`` are the NCEES question-count range, and ``weight``
    is the exam-weight midpoint used for the weighted-coverage percentage (the
    same seed value the Rust scheduler uses).
    """

    display: str
    key: str
    q_low: int
    q_high: int
    weight: float


# The full NCEES FE Electrical & Computer outline (current CBT spec, 110
# questions), heaviest exam areas first. ``key`` matches FE_TAG_KEYS and
# ``weight`` matches seed_topic_weights() in points_at_stake.rs.
NCEES_OUTLINE: list[Area] = [
    Area("Mathematics", "mathematics", 11, 17, 14.0),
    Area("Circuit Analysis", "circuit_analysis", 10, 15, 12.0),
    Area("Power Systems", "power_systems", 8, 12, 10.0),
    Area("Electronics", "electronics", 7, 11, 9.0),
    Area("Digital Systems", "digital_systems", 7, 11, 9.0),
    Area("Engineering Sciences", "engineering_sciences", 6, 9, 7.0),
    Area("Control Systems", "control_systems", 6, 9, 7.0),
    Area("Signal Processing", "signal_processing", 5, 8, 6.0),
    Area("Linear Systems", "linear_systems", 5, 8, 6.0),
    Area("Electromagnetics", "electromagnetics", 5, 8, 6.0),
    Area("Communications", "communications", 5, 8, 6.0),
    Area("Computer Systems", "computer_systems", 4, 6, 5.0),
    Area("Probability & Statistics", "probability_statistics", 4, 6, 5.0),
    Area("Electrical Materials", "properties_of_electrical_materials", 4, 6, 5.0),
    Area("Computer Networks", "computer_networks", 4, 6, 4.0),
    Area("Software Development", "software_development", 4, 6, 5.0),
    Area("Engineering Economics", "engineering_economics", 3, 5, 4.0),
    Area("Ethics", "ethics", 3, 5, 4.0),
]


@dataclass
class Row:
    area: Area
    cards: int

    @property
    def covered(self) -> bool:
        return self.cards > 0


def _open_collection_with_deck() -> Collection:
    """Create a throwaway collection and import the shipped FE deck into it."""
    if not DECK_PATH.exists():
        raise SystemExit(
            f"FE deck not found at {DECK_PATH}. Build it first "
            f"(e.g. python feprep/build_apkg.py) or check the path."
        )
    col_path = Path(tempfile.mkdtemp()) / "coverage.anki2"
    col = Collection(str(col_path))
    col.import_anki_package(
        ImportAnkiPackageRequest(
            package_path=str(DECK_PATH),
            options=ImportAnkiPackageOptions(),
        )
    )
    return col


def _count(col: Collection, key: str) -> int:
    try:
        return len(col.find_cards(f"tag:fe::{key}"))
    except Exception:
        return 0


def _print_table(rows: list[Row]) -> None:
    name_w = max(len(r.area.display) for r in rows)
    header = (
        f"  {'#':>2}  {'Knowledge area':<{name_w}}  "
        f"{'NCEES Q':>8}  {'weight':>6}  {'cards':>6}  status"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for i, r in enumerate(rows, 1):
        q = f"{r.area.q_low}-{r.area.q_high}"
        mark = "[x] covered" if r.covered else "[ ] MISSING"
        print(
            f"  {i:>2}  {r.area.display:<{name_w}}  {q:>8}  "
            f"{r.area.weight:>6.0f}  {r.cards:>6}  {mark}"
        )


def main() -> None:
    col = _open_collection_with_deck()
    try:
        rows = [Row(area, _count(col, area.key)) for area in NCEES_OUTLINE]

        total_areas = len(rows)
        covered_areas = sum(1 for r in rows if r.covered)
        total_weight = sum(r.area.weight for r in rows)
        covered_weight = sum(r.area.weight for r in rows if r.covered)
        total_cards = sum(r.cards for r in rows)

        area_pct = covered_areas / total_areas if total_areas else 0.0
        weight_pct = covered_weight / total_weight if total_weight else 0.0

        try:
            min_cov = col.get_config(
                READINESS_MIN_COVERAGE_CONFIG_KEY, DEFAULT_READINESS_MIN_COVERAGE
            )
        except Exception:
            min_cov = DEFAULT_READINESS_MIN_COVERAGE

        print("FE Electrical & Computer - NCEES coverage map")
        print(f"deck: {DECK_PATH.name}  ({total_cards} FE-tagged cards)\n")

        _print_table(rows)

        print()
        print(
            f"  Areas covered:      {covered_areas}/{total_areas}  "
            f"({area_pct * 100:.1f}% of NCEES knowledge areas)"
        )
        print(
            f"  Exam weight covered: {covered_weight:.0f}/{total_weight:.0f}  "
            f"({weight_pct * 100:.1f}% of NCEES exam weight)"
        )

        missing = [r.area.display for r in rows if not r.covered]
        if missing:
            print(f"  Not yet covered:    {', '.join(missing)}")

        print()
        print("  Abstain rule (readiness):")
        print(
            "    The readiness score withholds any number until area coverage is"
        )
        print(
            f"    at least the coverage line feReadinessMinCoverage = "
            f"{min_cov * 100:.0f}% (AND >=200 graded reviews)."
        )
        if area_pct >= min_cov:
            print(
                f"    This deck's area coverage {area_pct * 100:.1f}% is ABOVE the "
                f"{min_cov * 100:.0f}% line -- coverage no longer blocks readiness."
            )
            print(
                "    (Readiness may still abstain if there are <200 graded reviews.)"
            )
        else:
            print(
                f"    This deck's area coverage {area_pct * 100:.1f}% is BELOW the "
                f"{min_cov * 100:.0f}% line -- readiness abstains on coverage alone."
            )
    finally:
        col.close()


if __name__ == "__main__":
    main()
