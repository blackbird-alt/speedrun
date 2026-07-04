# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the FE Electrical and Computer deck(s) as an .apkg from the verified
tab-separated sources in `feprep/decks/`.

The .txt files are the canonical, human-verifiable content and can also be
imported directly through Anki's File > Import dialog. This script packages one
or more of them into a single .apkg with a custom, styled "FE Prep" notetype so
the review experience is polished and identical on desktop and phone.

Media (images): any image a card references -- via an HTML `<img src="name">`
tag or a `[img:name]` shorthand -- is looked up in `feprep/decks/media/` and
bundled into the package. `with_media` is enabled automatically only when at
least one referenced file is found, so text-only decks stay lean.

Requires a built `anki` pylib on PYTHONPATH (build the fork first). Usage:

    python feprep/build_apkg.py                       # packages fe-bank.txt
    python feprep/build_apkg.py --source decks/fe-bank.txt decks/fe-seed-deck.txt
    python feprep/build_apkg.py --preview             # also write a preview.html
"""

from __future__ import annotations

import argparse
import html
import re
import tempfile
from pathlib import Path

from anki.collection import Collection, ExportAnkiPackageOptions

HERE = Path(__file__).resolve().parent
DECKS_DIR = HERE / "decks"
MEDIA_DIR = DECKS_DIR / "media"
DEFAULT_SOURCES = [
    DECKS_DIR / "fe-problems.txt",
    DECKS_DIR / "fe-figures.txt",
    DECKS_DIR / "fe-seed-deck.txt",
]
DEFAULT_DECK_NAME = "FE Electrical and Computer"
DEFAULT_ACCENT = "#2F6BFF"

# ---------------------------------------------------------------------------
# Topic presentation: tag key -> (display name, chip accent color).
# Colors are a curated categorical palette (no acid greens / raw HSL), tuned to
# read well on both the light and dark card surfaces.
# ---------------------------------------------------------------------------
TOPICS: dict[str, tuple[str, str]] = {
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

# Desktop default track assignment (deckbrowser.py FE_DURABLE): field areas a
# candidate learns for keeps (Durable) vs. everything else (Cram). Keyed by the
# TOPICS display name.
DURABLE_AREAS = {
    "Mathematics",
    "Circuit Analysis",
    "Power Systems",
    "Electronics",
    "Electromagnetics",
    "Control Systems",
    "Signal Processing",
    "Linear Systems",
    "Engineering Sciences",
}

MODEL_NAME = "FE Prep"

FRONT_TEMPLATE = """\
<div class="fe-card" style="--chip: {{Accent}};">
  <div class="fe-top">
    <span class="fe-chip">{{Topic}}</span>
    <span class="fe-brand">FE&nbsp;·&nbsp;EE/CE</span>
  </div>
  <div class="fe-q">{{Front}}</div>
  <div class="fe-hint">Recall it, then flip.</div>
</div>
"""

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

CARD_CSS = """\
.card{
  --sans: system-ui,"Segoe UI",Roboto,-apple-system,"Helvetica Neue",sans-serif;
  --mono: ui-monospace,"JetBrains Mono","Cascadia Code","SF Mono",Consolas,monospace;
  --bg:#EDF1F8; --panel:#FFFFFF; --line:#DCE4F0; --grid:rgba(40,64,110,.055);
  --text:#182338; --muted:#5C6A85;
  --value:#8A4E1C; --value-bg:rgba(224,164,92,.16); --value-line:#D9863E;
  --chip:#2F6BFF;
  margin:0; padding:26px 14px; background:var(--bg); color:var(--text);
  font-family:var(--sans); line-height:1.5; -webkit-font-smoothing:antialiased;
}
.card.nightMode, .night_mode .card, .nightMode .card{
  --bg:#0E1726; --panel:#15213A; --line:#28374F; --grid:rgba(130,160,210,.06);
  --text:#E7EEFA; --muted:#94A2BD;
  --value:#E7A867; --value-bg:rgba(224,164,92,.10); --value-line:#E0A45C;
}
.fe-card{
  max-width:640px; margin:0 auto; text-align:left; position:relative;
  background:var(--panel); border:1px solid var(--line); border-radius:16px;
  padding:26px 30px 30px;
  background-image:
    linear-gradient(var(--grid) 1px,transparent 1px),
    linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:24px 24px;
  box-shadow:0 12px 34px -20px rgba(8,15,30,.55);
  overflow:hidden;
}
.fe-card::before{
  content:""; position:absolute; top:0; left:0; width:46px; height:3px;
  background:var(--chip); border-bottom-right-radius:3px;
}
.fe-top{
  display:flex; align-items:center; justify-content:space-between;
  gap:12px; margin-bottom:22px;
}
.fe-chip{
  font:600 11px/1 var(--sans); letter-spacing:.14em; text-transform:uppercase;
  color:var(--chip); padding:7px 12px 6px; border-radius:999px;
  background:color-mix(in srgb, var(--chip) 14%, transparent);
  border:1px solid color-mix(in srgb, var(--chip) 36%, transparent);
  display:inline-flex; align-items:center; gap:8px; white-space:nowrap;
}
.fe-chip::before{
  content:""; width:7px; height:7px; border-radius:2px; background:var(--chip);
  box-shadow:0 0 0 3px color-mix(in srgb, var(--chip) 22%, transparent);
}
.fe-brand{
  font:600 10px/1 var(--mono); letter-spacing:.12em; text-transform:uppercase;
  color:var(--muted); white-space:nowrap;
}
.fe-q{
  font:600 clamp(1.15rem,1rem+1.4vw,1.5rem)/1.36 var(--sans);
  letter-spacing:-.01em; color:var(--text); margin:0;
}
.fe-hint{
  margin-top:22px; font:500 12px/1.4 var(--sans); color:var(--muted);
  display:flex; align-items:center; gap:8px;
}
.fe-hint::before{
  content:""; width:13px; height:13px; border:1.5px solid var(--muted);
  border-radius:50%; opacity:.65;
}
.fe-trace{
  position:relative; height:2px; margin:24px 0 20px; border-radius:2px;
  background:linear-gradient(90deg, transparent, var(--value-line), transparent);
  opacity:.85;
}
.fe-trace::after{
  content:""; position:absolute; top:50%; left:26px; width:8px; height:8px;
  margin-top:-4px; border-radius:50%; background:var(--value-line);
  box-shadow:0 0 10px 1px var(--value-line);
}
.fe-a{
  font:600 clamp(1.2rem,1rem+1.6vw,1.65rem)/1.42 var(--mono); color:var(--value);
  background:var(--value-bg); border:1px solid color-mix(in srgb,var(--value-line) 34%,transparent);
  border-left:3px solid var(--value-line); border-radius:11px;
  padding:15px 18px; word-break:break-word;
}
.fe-a-label{
  display:block; font:600 10px/1 var(--sans); letter-spacing:.16em;
  text-transform:uppercase; color:var(--muted); margin-bottom:9px;
}
.fe-card img{ max-width:100%; height:auto; border-radius:10px; margin-top:14px; }
@media (prefers-reduced-motion: no-preference){
  .fe-card--answer .fe-trace{ animation:fe-trace .5s ease both; }
  .fe-card--answer .fe-a{ animation:fe-rise .32s ease both .05s; }
}
@keyframes fe-rise{ from{opacity:0; transform:translateY(7px)} to{opacity:1; transform:none} }
@keyframes fe-trace{ from{opacity:0; transform:scaleX(.4)} to{opacity:.85; transform:none} }
.fe-x{
  margin-top:18px; border:1px dashed color-mix(in srgb,var(--chip) 40%,transparent);
  border-radius:11px; padding:14px 16px; background:color-mix(in srgb,var(--chip) 7%,transparent);
}
.fe-x-label{
  display:block; font:600 10px/1 var(--sans); letter-spacing:.16em;
  text-transform:uppercase; color:var(--muted); margin-bottom:9px;
}
.fe-x-body{ font:400 clamp(.98rem,.9rem+.5vw,1.12rem)/1.5 var(--sans); color:var(--text); }
/* fe-math-overflow: keep long MathJax/math inside the card (scale + scroll, never clip off-screen) */
.fe-q, .fe-a, .fe-x-body{ max-width:100%; overflow-x:auto; overflow-y:hidden; overflow-wrap:anywhere; }
mjx-container{ max-width:100%; }
mjx-container[display="true"]{ overflow-x:auto; overflow-y:hidden; padding-bottom:2px; }
"""

# Matches <img src="foo.png"> / <img src='foo.png'> and the [img:foo.png] shorthand.
_IMG_TAG_RE = re.compile(r"""<img[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# Same, but consumes the entire tag (used when normalizing raw tags to shorthand).
_IMG_FULL_TAG_RE = re.compile(
    r"""<img[^>]*?\bsrc\s*=\s*["']([^"']+)["'][^>]*>""", re.IGNORECASE
)
_IMG_SHORTHAND_RE = re.compile(r"\[img:([^\]]+)\]")


def _prepare_field(text: str) -> str:
    """Escape plain-text content to safe HTML, keeping image references alive.

    Raw `<img src="...">` tags are normalized to the `[img:...]` shorthand
    *before* escaping (otherwise they would be escaped into literal text), then
    the shorthand is expanded back into a real tag afterwards. This keeps both
    documented syntaxes working while everything else is treated as plain text.
    """
    normalized = _IMG_FULL_TAG_RE.sub(lambda m: f"[img:{m.group(1).strip()}]", text)
    escaped = html.escape(normalized, quote=False)
    return _IMG_SHORTHAND_RE.sub(
        lambda m: f'<img src="{m.group(1).strip()}">', escaped
    )


def _referenced_media(text: str) -> list[str]:
    names = _IMG_TAG_RE.findall(text)
    names += [m.strip() for m in _IMG_SHORTHAND_RE.findall(text)]
    return names


def topic_of(tags: list[str]) -> tuple[str, str]:
    """Map the first fe:: tag to (display name, accent). Falls back gracefully."""
    for tag in tags:
        low = tag.lower()
        if low.startswith("fe::"):
            key = low[4:].split("::", 1)[0]
            if key in TOPICS:
                return TOPICS[key]
            return (key.replace("_", " ").title(), DEFAULT_ACCENT)
    return ("FE Prep", DEFAULT_ACCENT)


def parse_source(path: Path) -> tuple[str, list[tuple[str, str, list[str]]]]:
    deck_name = DEFAULT_DECK_NAME
    rows: list[tuple[str, str, list[str]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            if line.lower().startswith("#deck:"):
                deck_name = line.split(":", 1)[1].strip()
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"{path.name}: expected 3 tab-separated columns, got: {line!r}")
        front, back, tag_field = (p.strip() for p in parts)
        tags = [t for t in tag_field.replace(",", " ").split() if t]
        rows.append((front, back, tags))
    return deck_name, rows


def build_fe_model(col: Collection):
    mm = col.models
    model = mm.new(MODEL_NAME)
    for field in ("Front", "Back", "Topic", "Accent", "Explanation"):
        mm.add_field(model, mm.new_field(field))
    template = mm.new_template("Card 1")
    template["qfmt"] = FRONT_TEMPLATE
    template["afmt"] = BACK_TEMPLATE
    mm.add_template(model, template)
    model["css"] = CARD_CSS
    mm.add(model)
    return model


def build(
    sources: list[Path],
    out_path: Path,
    sections: bool = False,
    tracks: bool = False,
) -> tuple[int, int]:
    """Build the .apkg.

    Deck placement, in order of precedence:
    - `tracks` or `sections`: one monolithic subdeck per NCEES area,
      `FE Electrical and Computer::<Area>`. Durable vs. cram is a per-area policy
      applied at runtime by the desktop dashboard (qt/aqt/deckbrowser.py): a whole
      section is studied durable or cram (no per-card split), so it is not baked
      into the deck tree.
    - otherwise: the deck named by each source's `#deck:` header (Bank/Seed/...).
    """
    total_cards = 0
    media_added: set[str] = set()
    missing_media: set[str] = set()
    deck_id_cache: dict[str, int] = {}

    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "build.anki2"))
        try:
            model = build_fe_model(col)

            def deck_for(topic_name: str, source_deck: str) -> int:
                if tracks or sections:
                    # One monolithic subdeck per area; durable vs. cram is a
                    # runtime per-area policy (deckbrowser.py), not a split.
                    name = f"{DEFAULT_DECK_NAME}::{topic_name}"
                else:
                    name = source_deck
                if name not in deck_id_cache:
                    deck_id_cache[name] = col.decks.id(name)
                return deck_id_cache[name]

            for source in sources:
                deck_name, rows = parse_source(source)
                for raw_front, raw_back, tags in rows:
                    front = _prepare_field(raw_front)
                    back = _prepare_field(raw_back)
                    for name in _referenced_media(front) + _referenced_media(back):
                        if name in media_added:
                            continue
                        candidate = MEDIA_DIR / name
                        if candidate.exists():
                            col.media.add_file(str(candidate))
                            media_added.add(name)
                        else:
                            missing_media.add(name)
                    topic_name, accent = topic_of(tags)
                    note = col.new_note(model)
                    note["Front"] = front
                    note["Back"] = back
                    note["Topic"] = topic_name
                    note["Accent"] = accent
                    note.tags = tags
                    col.add_note(note, deck_for(topic_name, deck_name))
                    total_cards += 1

            col.export_anki_package(
                out_path=str(out_path),
                options=ExportAnkiPackageOptions(
                    with_scheduling=False,
                    with_media=bool(media_added),
                    legacy=True,
                ),
                limit=None,
            )
        finally:
            col.close()

    if missing_media:
        print(
            f"WARNING: referenced images not found in {MEDIA_DIR} "
            f"(skipped): {sorted(missing_media)}"
        )
    return total_cards, len(media_added)


def write_preview(out_html: Path) -> None:
    """Render a few sample cards (light + dark) to a standalone HTML file so the
    card design can be eyeballed in a browser without launching Anki."""
    samples = [
        ("Circuit Analysis", "#E7A867", "What is the impedance of a capacitor C at angular frequency \u03c9?", "1 / (j\u03c9C)"),
        ("Mathematics", "#5C9BFF", "For z = 3 + 4j, find the magnitude and argument in degrees.", "|z| = 5, arg = 53.13\u00b0"),
        ("Control Systems", "#6FA8DC", "For BIBO stability, where must all poles of a system lie?", "In the open left half of the s-plane"),
        ("Power Systems", "#F2849E", "A 60 Hz, 4-pole synchronous generator runs at what synchronous speed?", "1800 rpm (N = 120\u00b7f/P)"),
    ]

    def card(topic, accent, q, a, answer=False):
        cls = "fe-card fe-card--answer" if answer else "fe-card"
        body = (
            f'<div class="fe-trace"></div><div class="fe-a"><span class="fe-a-label">Answer</span>{html.escape(a, quote=False)}</div>'
            if answer
            else '<div class="fe-hint">Recall it, then flip.</div>'
        )
        return (
            f'<div class="{cls}" style="--chip: {accent};">'
            f'<div class="fe-top"><span class="fe-chip">{html.escape(topic)}</span>'
            f'<span class="fe-brand">FE&nbsp;·&nbsp;EE/CE</span></div>'
            f'<div class="fe-q">{html.escape(q, quote=False)}</div>{body}</div>'
        )

    def panel(night: bool) -> str:
        cls = "card nightMode" if night else "card"
        label = "Dark (night mode)" if night else "Light"
        cards = "".join(
            card(t, c, q, a, answer=i % 2 == 1)
            for i, (t, c, q, a) in enumerate(samples)
        )
        return (
            f'<div class="{cls} col">'
            f'<div class="col-label">{label}</div>'
            f'<div class="stack">{cards}</div></div>'
        )

    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>{CARD_CSS}\n"
        "body{margin:0;display:flex;flex-wrap:wrap;align-items:flex-start}"
        ".col{flex:1 1 420px;padding:30px 22px 40px}"
        ".col-label{font:600 11px/1 var(--mono);letter-spacing:.18em;"
        "text-transform:uppercase;color:var(--muted);margin:0 auto 20px;max-width:640px}"
        ".stack{display:grid;gap:24px}"
        "</style></head><body>"
        f"{panel(False)}{panel(True)}</body></html>"
    )
    out_html.write_text(doc, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, nargs="+", default=DEFAULT_SOURCES)
    parser.add_argument("--out", type=Path, default=DECKS_DIR / "fe-electrical.apkg")
    parser.add_argument("--preview", action="store_true", help="also write decks/preview.html")
    parser.add_argument(
        "--sections",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="place cards into per-NCEES-area subdecks (mirrors the desktop "
        "sections). On by default; pass --no-sections to keep each source's "
        "own #deck (Bank/Seed/Problems).",
    )
    parser.add_argument(
        "--tracks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="nest area subdecks under Durable/Cram groups using the desktop's "
        "default track assignment (implies --sections). On by default; pass "
        "--no-tracks for flat per-area sections.",
    )
    args = parser.parse_args()
    sources = [s if s.is_absolute() else (HERE / s) for s in args.source]
    for s in sources:
        if not s.exists():
            raise SystemExit(f"source not found: {s}")
    cards, media = build(sources, args.out, sections=args.sections, tracks=args.tracks)
    print(f"wrote {cards} cards ({media} media file(s)) to {args.out}")
    if args.preview:
        preview = DECKS_DIR / "preview.html"
        write_preview(preview)
        print(f"wrote preview to {preview}")


if __name__ == "__main__":
    main()
