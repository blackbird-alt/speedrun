# Deck media

Drop image files referenced by cards here (e.g. `circuit-1.png`).

A card references an image in either source-deck column (front or back) using:

- an HTML tag: `<img src="circuit-1.png">`, or
- the shorthand: `[img:circuit-1.png]` (rewritten to an `<img>` tag at build time).

`build_apkg.py` bundles only the referenced files that exist here and enables
media packaging automatically. Text-only decks stay lean (no media section).
