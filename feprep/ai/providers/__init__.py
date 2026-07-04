# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Pluggable card-generation providers for the FE AI pipeline.

A provider turns a *source chunk* (a list of verified source spans, all from
one exam area) into candidate flashcards. Every candidate must carry enough
provenance for the verifier to check grounding against its cited source line.

Two providers ship:

* :class:`~providers.stub.StubProvider` — fully deterministic and offline. It
  never contacts a network and never invents facts; it only re-expresses text
  that is present in a cited source line. This is what runs in CI / on any
  machine so the eval reproduces identical numbers.

* :class:`~providers.gemini.GeminiProvider` — documents exactly how a real
  Gemini (Firebase AI Logic) call would be wired. It is *key-gated*: with no
  ``GEMINI_API_KEY`` / ``FIREBASE_API_KEY`` in the environment it is a no-op
  that refuses to run, so the pipeline can never silently depend on a model.

Select a provider with :func:`get_provider`.
"""

from __future__ import annotations

import os
from typing import Protocol, Sequence

from common import Span


class Provider(Protocol):
    name: str
    model: str

    def generate(self, chunk_id: str, spans: Sequence[Span]) -> list[dict]:
        """Return candidate cards for one source chunk.

        Each returned dict has at least::

            {
              "front": str,
              "back": str,
              "gen_kind": str,          # extractive | redundant | factmix | ...
              "cite_doc": str,          # source doc the card claims to come from
              "cite_line": int,         # source line the card claims to come from
              "source_text": str,       # exact cited span text (for grounding)
            }

        The generator (:mod:`generate_cards`) is responsible for stamping the
        ``model`` and ``prompt_hash`` provenance fields on top of these.
        """
        ...


def get_provider(name: str | None = None) -> Provider:
    """Return a provider instance.

    Defaults to the stub. Set ``FE_AI_PROVIDER=gemini`` (and supply an API key)
    to attempt live generation. If ``gemini`` is requested but no key is
    present the Gemini provider will refuse to generate.
    """
    name = (name or os.environ.get("FE_AI_PROVIDER", "stub")).lower()
    if name == "stub":
        from providers.stub import StubProvider

        return StubProvider()
    if name == "gemini":
        from providers.gemini import GeminiProvider

        return GeminiProvider()
    if name == "openai":
        from providers.openai import OpenAIProvider

        return OpenAIProvider()
    raise ValueError(
        f"unknown provider: {name!r} (expected 'stub', 'openai', or 'gemini')"
    )
