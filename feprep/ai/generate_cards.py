# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Generate candidate FE flashcards from the verified source decks.

Pipeline stage 1. Steps:

1. Build the *generation inputs*: the source corpus with every gold item and
   near-duplicate held out (honesty rule #4 — no leakage). The exact input set
   is written to ``out/generation_inputs.jsonl`` so ``leakage_check.py`` can
   audit the real bytes that were fed to the generator.
2. Chunk the inputs by exam area (one "source chunk" per area).
3. Ask the configured provider to author candidate cards for each chunk.
4. Stamp every candidate with full provenance: ``source`` (doc + line),
   ``model``, and ``prompt_hash`` (honesty rule #1 — trace to a named source).

Writes ``out/candidates.jsonl``. Deterministic and offline with the default
stub provider.

Run: python feprep/ai/generate_cards.py [--provider stub|gemini]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C  # noqa: E402
from providers import get_provider  # noqa: E402
from providers.gemini import ProviderUnavailable  # noqa: E402

CANDIDATES_PATH = os.path.join(C.OUT_DIR, "candidates.jsonl")
INPUTS_PATH = os.path.join(C.OUT_DIR, "generation_inputs.jsonl")


def chunk_by_area(spans: list[C.Span]) -> list[tuple[str, list[C.Span]]]:
    by_area: dict[str, list[C.Span]] = {}
    for span in spans:
        by_area.setdefault(span.area, []).append(span)
    return [(area, by_area[area]) for area in sorted(by_area)]


_TRANSIENT_NET = (TimeoutError, urllib.error.URLError, ConnectionError)


def _generate_area(provider, area, spans, attempts: int = 4):
    """Call the provider for one area, retrying transient network errors so a
    single slow/timed-out API response doesn't abort the whole run. Returns the
    raw cards, or None if the area kept failing (it is then skipped)."""
    for i in range(1, attempts + 1):
        try:
            return provider.generate(area, spans)
        except _TRANSIENT_NET as exc:
            if i == attempts:
                print(f"[generate] area {area!r}: network error after {attempts} tries ({exc}); skipping")
                return None
            wait = 3 * i
            print(f"[generate] area {area!r}: net error ({exc}); retry {i}/{attempts - 1} in {wait}s")
            time.sleep(wait)
    return None


def generate(provider_name: str | None = None) -> dict:
    C.ensure_out_dir()
    corpus = C.load_corpus()
    gold = C.load_gold()

    inputs, removed = C.build_generation_inputs(corpus, gold)
    C.write_jsonl(INPUTS_PATH, [C.span_to_input_row(s) for s in inputs])

    provider = get_provider(provider_name)
    template = getattr(sys.modules[provider.__module__], "PROMPT_TEMPLATE", "")
    try:
        provider_used = provider.name
        candidates: list[dict] = []
        for area, spans in chunk_by_area(inputs):
            raw = _generate_area(provider, area, spans)
            if raw is None:
                continue
            for card in raw:
                src_text = card["source_text"]
                candidates.append(
                    {
                        "front": card["front"],
                        "back": card["back"],
                        "area": area,
                        "gen_kind": card.get("gen_kind", "unknown"),
                        "source": {"doc": card["cite_doc"], "line": card["cite_line"]},
                        "source_text": src_text,
                        "model": provider.model,
                        "prompt_hash": C.prompt_hash(template, src_text, provider.model),
                    }
                )
    except ProviderUnavailable as exc:
        # No key -> fall back to the deterministic stub so the pipeline still
        # runs offline. Reported honestly by the caller.
        print(f"[generate] provider unavailable ({exc}); falling back to stub")
        return generate("stub")

    # Deterministic ordering + stable candidate ids.
    candidates.sort(key=lambda c: (c["area"], c["source"]["line"], c["gen_kind"], c["front"]))
    for i, c in enumerate(candidates, start=1):
        c["id"] = f"c{i:04d}"

    n = C.write_jsonl(CANDIDATES_PATH, candidates)
    kinds: dict[str, int] = {}
    for c in candidates:
        kinds[c["gen_kind"]] = kinds.get(c["gen_kind"], 0) + 1
    return {
        "provider": provider_used,
        "model": provider.model,
        "n_inputs": len(inputs),
        "n_removed": len(removed),
        "n_candidates": n,
        "kinds": kinds,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", default=None, help="stub (default) or gemini")
    args = ap.parse_args()
    info = generate(args.provider)
    print("=== generate_cards ===")
    print(f"provider     : {info['provider']}  (model={info['model']})")
    print(f"inputs kept  : {info['n_inputs']}  (held out {info['n_removed']} gold/near-dups)")
    print(f"candidates   : {info['n_candidates']}")
    for kind in sorted(info["kinds"]):
        print(f"  {kind:12s} {info['kinds'][kind]}")
    print(f"-> {CANDIDATES_PATH}")


if __name__ == "__main__":
    main()
