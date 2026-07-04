# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Deterministic, offline stub card generator.

The stub stands in for a real LLM so the whole pipeline runs end-to-end with no
network and reproduces identical numbers every time. It is a *genuine, honest*
attempt at extractive card authoring, not a fake that is rigged to win:

* It never invents facts. Every card's answer is text copied from a real,
  cited source span (or, for the deliberate failure modes below, copied from a
  *different* span so the verifier has something real to catch).

* It reproduces three well-known LLM failure modes so the verifier gate is
  exercised on real inputs rather than a clean stream:

  1. ``extractive``  — faithful Q/A lifted from one source line (grounded).
  2. ``redundant``   — the same card emitted twice (LLMs over-produce
     near-duplicate cards). The verifier's dedupe should drop the copy.
  3. ``factmix``     — a card that keeps a line's question but pairs it with a
     *neighbouring* line's answer (LLMs confuse adjacent facts). It cites the
     question's line, so the answer is NOT grounded there and the verifier's
     grounding check should flag it as wrong.

Because the source decks are already high-quality Q/A, the extractive path adds
little over raw retrieval; it is entirely possible this stub does not beat the
baselines. That is a fair, expected outcome and is reported honestly by
``eval.py`` rather than hidden.
"""

from __future__ import annotations

from typing import Sequence

from common import Span

# Deterministic cadence of the simulated failure modes. Fixed constants (no
# randomness) so runs are reproducible.
REDUNDANT_EVERY = 9  # every Nth span is emitted a second time (duplicate)
FACTMIX_EVERY = 6    # every Nth span gets an extra fact-confused card

PROMPT_TEMPLATE = (
    "You are authoring one FE-exam flashcard from the following verified "
    "source line. Restate it as a single question and a short, correct answer. "
    "Do not add facts that are not in the source.\nSOURCE: {source}"
)


class StubProvider:
    name = "stub"
    model = "stub-extractor-v1"

    def generate(self, chunk_id: str, spans: Sequence[Span]) -> list[dict]:
        ordered = sorted(spans, key=lambda s: (s.doc, s.line))
        cards: list[dict] = []
        n = len(ordered)
        for i, span in enumerate(ordered):
            # 1) faithful extractive card
            cards.append(
                {
                    "front": span.front,
                    "back": span.answer,
                    "gen_kind": "extractive",
                    "cite_doc": span.doc,
                    "cite_line": span.line,
                    "source_text": span.text,
                }
            )

            # 2) redundant duplicate (simulated LLM over-production)
            if n > 1 and i % REDUNDANT_EVERY == 0:
                cards.append(
                    {
                        "front": span.front,
                        "back": span.answer,
                        "gen_kind": "redundant",
                        "cite_doc": span.doc,
                        "cite_line": span.line,
                        "source_text": span.text,
                    }
                )

            # 3) fact-mixed card (simulated LLM adjacent-fact confusion).
            # Keeps this line's question but borrows the NEXT line's answer,
            # while still citing THIS line -> answer is ungrounded there.
            if n > 1 and i % FACTMIX_EVERY == (FACTMIX_EVERY - 1):
                neighbour = ordered[(i + 1) % n]
                if neighbour.answer != span.answer:
                    cards.append(
                        {
                            "front": span.front,
                            "back": neighbour.answer,
                            "gen_kind": "factmix",
                            "cite_doc": span.doc,
                            "cite_line": span.line,
                            "source_text": span.text,
                        }
                    )
        return cards
