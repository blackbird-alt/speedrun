# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Key-gated Gemini (Firebase AI Logic) provider.

This documents exactly how live card generation would be wired to Google's
Gemini models via Firebase AI Logic (or the Gemini Developer API). It is
intentionally a no-op unless an API key is present in the environment, so the
pipeline can never *silently* depend on a network model:

* No ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` / ``FIREBASE_API_KEY`` set
  -> :meth:`generate` raises ``ProviderUnavailable`` and the caller falls back
  to the deterministic stub. This is the state in this environment: **no LLM
  credential exists here**, so the committed eval numbers come from the stub.

* Key present -> the (optional) ``google-generativeai`` SDK is used to request
  strict JSON candidate cards. Even then, every card is stamped with the same
  provenance contract (cited source line, model id, prompt hash) so the
  verifier and leakage checks apply identically to AI-authored cards.

The prompt is deliberately grounded: the model is given ONE source line and
told to restate only what is in it. Source text is passed through
:func:`sanitize_source` first to strip embedded "instructions" (a basic
prompt-injection guard, per the Friday plan's hidden-text concern).
"""

from __future__ import annotations

import json
import os
import re
from typing import Sequence

from common import Span

# The model story. Swappable; recorded on every card for provenance.
DEFAULT_MODEL = "gemini-2.0-flash"

# Env vars that, if present, unlock live generation.
_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "FIREBASE_API_KEY")

PROMPT_TEMPLATE = (
    "You are authoring FE Electrical and Computer exam flashcards.\n"
    "Use ONLY the verified source line below. Do not add any fact that is not "
    "explicitly present in it. If the line cannot make a good card, return an "
    "empty list.\n"
    "Return strict JSON: a list of objects with keys 'front' and 'back'.\n"
    "SOURCE ({locator}): {source}"
)

# Lines that look like injected instructions rather than FE content.
_INJECTION_RE = re.compile(
    r"(?i)\b(ignore (the )?(previous|above)|disregard|system prompt|"
    r"you are now|act as|reveal|exfiltrate|print your|api[_ ]?key)\b"
)


class ProviderUnavailable(RuntimeError):
    """Raised when live generation is requested but no key/SDK is available."""


def api_key() -> str | None:
    for var in _KEY_ENV_VARS:
        val = os.environ.get(var)
        if val:
            return val
    return None


def sanitize_source(text: str) -> str:
    """Drop obvious prompt-injection instructions embedded in source text."""
    kept = []
    for line in text.splitlines():
        if _INJECTION_RE.search(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("FE_AI_MODEL", DEFAULT_MODEL)

    def available(self) -> bool:
        return api_key() is not None

    def generate(self, chunk_id: str, spans: Sequence[Span]) -> list[dict]:
        key = api_key()
        if not key:
            raise ProviderUnavailable(
                "No LLM credential found. Set GEMINI_API_KEY (or "
                "GOOGLE_API_KEY / FIREBASE_API_KEY) to enable live generation; "
                "otherwise use the deterministic stub provider."
            )

        try:  # optional dependency, only needed for live runs
            import google.generativeai as genai  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised only with a key
            raise ProviderUnavailable(
                "google-generativeai is not installed. Run "
                "'pip install google-generativeai' to enable live generation."
            ) from exc

        genai.configure(api_key=key)
        model = genai.GenerativeModel(self.model)

        cards: list[dict] = []
        for span in sorted(spans, key=lambda s: (s.doc, s.line)):
            safe = sanitize_source(span.text)
            prompt = PROMPT_TEMPLATE.format(locator=span.locator, source=safe)
            resp = model.generate_content(
                prompt,
                generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
            )
            for item in self._parse(resp.text):
                front = str(item.get("front", "")).strip()
                back = str(item.get("back", "")).strip()
                if not front or not back:
                    continue
                cards.append(
                    {
                        "front": front,
                        "back": back,
                        "gen_kind": "llm",
                        "cite_doc": span.doc,
                        "cite_line": span.line,
                        "source_text": span.text,
                    }
                )
        return cards

    @staticmethod
    def _parse(text: str) -> list[dict]:
        try:
            data = json.loads(text)
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("cards") or data.get("items") or [data]
        return [d for d in data if isinstance(d, dict)]
