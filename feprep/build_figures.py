# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Crop figure-dependent FE problems out of the scanned manuscript PDF and emit
image-front cards into `decks/fe-figures.txt` (images into `decks/media/`).

The manuscript PDF is a full-page scan, so figures cannot be pulled out as
separate embedded images. Instead each problem's statement + diagram is cropped
from the page raster (answer options are excluded where they are plain text, and
kept where the options are themselves figures), then used as the card *front*.
The verified answer is the *back*.

BUILD-ONLY: the emitted deck uses the `[img:...]` shorthand, so it must be
packaged with `build_apkg.py` (which bundles `decks/media/`); a raw text import
will not carry the images.

    python feprep/build_figures.py --pdf "C:/path/My FE Practice Problems.pdf"

Requires PyMuPDF (`pip install pymupdf`).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import fitz  # PyMuPDF

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "decks" / "media"
OUT = HERE / "decks" / "fe-figures.txt"
DPI = 200

# Each chapter: display title, fe:: tag key, problem-set digit, 0-indexed page
# range, whether the answer options are figures (keep them in the crop), and the
# verified answers keyed by problem id. Extend this list to cover more chapters.
CHAPTERS: list[dict] = [
    {
        "title": "Chapter 7 - Circuit Analysis",
        "tag": "circuit_analysis",
        "set": "7",
        "pages": range(43, 54),
        "options_are_figures": False,
        "answers": {
            "7.1a": "-14.6 V", "7.1b": "6 mA", "7.1c": "1.87 V", "7.1d": "1.7 A",
            "7.1e": "3.1 mA", "7.1f": "875 uA",
            "7.2a": "3.4 kΩ", "7.2b": "2 kΩ", "7.2c": "3.5 kΩ",
            "7.2d": "0.825 kΩ", "7.2e": "6 kΩ",
            "7.3a": "5 V", "7.3c": "1.25 kΩ", "7.3d": "7.5 mA",
            "7.4e": "1 A",
            "7.5a": "1.8∠-120° A", "7.5c": "0.14∠11.3° A", "7.5d": "2665∠100° A",
            "7.6a": "12 - j Ω", "7.6b": "2 - 3j Ω", "7.6c": "50 - j26525 Ω",
            "7.6d": "(10 + j0.5 Ω) ∥ (-j100 Ω)",
        },
    },
    {
        "title": "Chapter 8 - Linear Systems", "tag": "linear_systems",
        "set": "8", "pages": range(54, 66), "options_are_figures": True,
        "answers": {
            "8.1a": "1 V", "8.1b": "10·e^(-t/2) V",
            "8.1c": "0.01(1 - e^(-500000t)) A", "8.1d": "22.6 μA", "8.1e": "67 mV",
            "8.2a": "2236 rad/s", "8.2c": "12 A",
            "8.5a": "z11 = z22 = 4 Ω; z12 = z21 = 2 Ω",
            "8.5b": "y11 = y22 = 1/5 S; y12 = y21 = -1/10 S",
            "8.5c": "y11 = y22 = 1/3 S; y12 = y21 = -1/6 S",
            "8.5d": "z11 = 11.11 Ω", "8.5e": "h12 = 0.66",
        },
    },
    {
        "title": "Chapter 9 - Signal Processing", "tag": "signal_processing",
        "set": "9", "pages": range(65, 72), "options_are_figures": True,
        "answers": {
            "9.1a": "Option C", "9.1b": "Option A", "9.1c": "Option D",
            "9.1d": "Option C", "9.1e": "Option C",
            "9.2a": "Option B: [0 4 10 14 10 4 0]",
            "9.2b": "Option C: [0 1 4 8 8 3 0]", "9.2d": "Option C", "9.2e": "Option C",
        },
    },
    {
        "title": "Chapter 10 - Electronics", "tag": "electronics",
        "set": "10", "pages": range(77, 91), "options_are_figures": True,
        "answers": {
            "10.2a": "D1 on, D2 off", "10.2b": "Option A", "10.2c": "(0 V, 0.66 mA)",
            "10.2d": "0.13 mA", "10.2e": "ID1 = 0, ID2 = 2.4 mA",
            "10.3a": "Ie = 2.09 mA, Vce = 1.68 V", "10.3b": "Ic = 1.13 mA, Vce = 1.3 V",
            "10.3c": "Cut-off", "10.3d": "Ie = 2.4 mA, Vce = 2.7 V",
            "10.3e": "Ie = 3.2 mA, Vec = 5.1 V", "10.3f": "Ie = 1.5 mA, Vec = 2 V",
            "10.4a": "3.2 mA", "10.4b": "Triode (ohmic) region", "10.4c": "3.28 V",
            "10.4d": "0.1 mA", "10.4e": "0.44 mA",
            "10.5a": "-5 V", "10.5b": "5 kΩ", "10.5c": "2.5 V", "10.5d": "-35 V",
            "10.5e": "0.5 V",
            "10.6d": "200 Ω", "10.6f": "1.98 V", "10.6g": "2.1 V", "10.6h": "2%",
        },
    },
    {
        "title": "Chapter 11 - Power", "tag": "power_systems",
        "set": "11", "pages": range(91, 95), "options_are_figures": True,
        "answers": {
            "11.1b": "1.96∠68.7° A", "11.1c": "200∠-67.3° VA", "11.1d": "9.19 W",
            "11.1e": "5 W", "11.1f": "12 - j Ω", "11.1g": "5 - 2j Ω",
        },
    },
    # Chapter 12 (Electromagnetics) has one figure problem (12.1l), but its OCR
    # marker is merged ("12.11)") and unrecoverable via a clean regex; its EM
    # content is already covered by text cards, so it is intentionally omitted.
    {
        "title": "Chapter 13 - Control Systems", "tag": "control_systems",
        "set": "13", "pages": range(106, 124), "options_are_figures": True,
        "answers": {
            "13.1a": "Option C", "13.1b": "Option C", "13.1c": "Option A",
            "13.1d": "Option A", "13.1e": "Option B",
            "13.2a": "Option A", "13.2b": "Option D",
            "13.3a": "0.24", "13.3b": "∞", "13.3c": "∞", "13.3d": "∞", "13.3e": "0.11",
            "13.5a": "Option A", "13.5b": "Option C", "13.5c": "Option D",
            "13.5d": "Option B", "13.5e": "Option A", "13.6e": "Option D",
        },
    },
    {
        "title": "Chapter 14 - Communications", "tag": "communications",
        "set": "14", "pages": range(123, 127), "options_are_figures": True,
        "answers": {"14.1f": "Option A"},
    },
    {
        "title": "Chapter 16 - Digital Systems", "tag": "digital_systems",
        "set": "16", "pages": range(136, 147), "options_are_figures": True,
        "answers": {
            "16.3a": "Option B", "16.3b": "Option C", "16.3c": "Option A",
            "16.3d": "Option C", "16.3e": "Option B",
            "16.4a": "Option B", "16.4b": "Option D", "16.4c": "Option C",
            "16.4d": "Option A", "16.4e": "Option C", "16.4f": "Option C",
            "16.5a": "010", "16.5b": "RS flip-flop", "16.5c": "D flip-flop",
            "16.5d": "JK flip-flop", "16.5e": "001", "16.5f": "111",
            "16.5g": "1100 (Q3Q2Q1Q0)", "16.5h": "Option A",
            "16.6a": "Option C", "16.6d": "Option A",
        },
    },
]

OPT_RE = re.compile(r"^\(?[A-D]\)")  # option list start, tolerant of OCR


def crop_problem(page, top: float, end: float, x0: float, x1: float) -> fitz.Pixmap:
    """Render [top, end] of the page, then trim trailing whitespace."""
    pix = page.get_pixmap(clip=fitz.Rect(x0, top, x1, end), dpi=DPI)
    w, h, n, s = pix.width, pix.height, pix.n, pix.samples
    scale = DPI / 72.0
    bottom = h - 1
    for y in range(h - 1, -1, -1):
        base = y * w * n
        if sum(1 for x in range(0, w, 3) if s[base + x * n] < 210) >= 8:
            bottom = y
            break
    new_end = top + (bottom + 1) / scale + 10
    if new_end < end - 6:
        pix = page.get_pixmap(clip=fitz.Rect(x0, top, x1, new_end), dpi=DPI)
    return pix


def build(pdf_path: Path) -> int:
    MEDIA.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    header = [
        "#separator:tab", "#html:false", "#notetype:Basic",
        "#deck:FE Electrical and Computer::Problems (figures)", "#tags column:3",
        "# Figure-based FE problems: front = cropped scan (statement + diagram),",
        "# back = verified answer. BUILD-ONLY: package with build_apkg.py so the",
        "# [img:...] refs expand and decks/media/ files are bundled.",
    ]
    lines: list[str] = []
    for ch in CHAPTERS:
        # Tolerate a stray leading page-number digit the OCR sometimes prepends.
        marker_re = re.compile(rf"^\s*\d{{0,3}}\s*Problem\s+{ch['set']}\.(\d)\s*([a-z])\)", re.I)
        lines.append(f"# === {ch['title']} (figures) ===")
        emitted: set[str] = set()
        for pno in ch["pages"]:
            page = doc[pno]
            W, H = page.rect.width, page.rect.height
            blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
            blocks.sort(key=lambda b: b[1])
            bounds, marks = [], []
            for b in blocks:
                t = b[4].strip()
                is_mark = bool(marker_re.match(t))
                if is_mark or (OPT_RE.match(t) and not ch["options_are_figures"]):
                    bounds.append(b[1])
                if is_mark:
                    m = marker_re.match(t)
                    marks.append((f"{ch['set']}.{m.group(1)}{m.group(2)}", b[1]))
            for pid, y0 in marks:
                if pid not in ch["answers"]:
                    continue
                later = [y for y in bounds if y > y0 + 1]
                end = min(min(later) if later else H, H - 90)
                pix = crop_problem(page, y0 - 8, end - 4, 16, W - 16)
                img = f"fe_fig_{pid.replace('.', '_')}.png"
                pix.save(str(MEDIA / img))
                lines.append(f"[img:{img}]\t{ch['answers'][pid]}\tfe::{ch['tag']} track::durable")
                emitted.add(pid)
        miss = sorted(set(ch["answers"]) - emitted)
        print(f"{ch['title']}: {len(emitted)} cards" + (f"  MISSING {miss}" if miss else ""))
    doc.close()
    OUT.write_text("\n".join(header) + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return sum(1 for line in lines if not line.startswith("#"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, required=True, help="path to the scanned manuscript PDF")
    args = ap.parse_args()
    if not args.pdf.exists():
        raise SystemExit(f"PDF not found: {args.pdf}")
    n = build(args.pdf)
    print(f"wrote {n} figure cards to {OUT}")


if __name__ == "__main__":
    main()
