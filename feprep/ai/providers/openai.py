# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Key-gated OpenAI provider for grounded FE flashcard generation.

Live card generation via the OpenAI Chat Completions API, using only the
standard library (``urllib``) so there is nothing extra to install. It is
key-gated on ``OPENAI_API_KEY``: with no key present :meth:`generate` raises
:class:`~providers.gemini.ProviderUnavailable` and the caller falls back to the
deterministic stub, so the pipeline can never *silently* depend on a network
model.

Grounding + provenance are identical to the other providers: the model is given
the area's verified source lines (each passed through
:func:`~providers.gemini.sanitize_source` to strip embedded prompt-injection
instructions) and must author cards that only restate a cited line. Each card
records the exact ``doc:line`` it came from, so the grounding verifier and the
leakage check apply to AI-authored cards unchanged.

One request is made per source chunk (one exam area), not one per line, to keep
live runs fast and cheap (~18 requests total).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Sequence

from common import Span
from providers.gemini import ProviderUnavailable, sanitize_source

# Swappable via FE_AI_MODEL; recorded on every card for provenance.
DEFAULT_MODEL = "gpt-4o-mini"

_KEY_ENV_VARS = ("OPENAI_API_KEY",)
API_URL = "https://api.openai.com/v1/chat/completions"

# The FE exam is math-heavy, so cards must carry real math notation. We tell the
# model to write every formula/variable/unit as LaTeX inside MathJax delimiters,
# which Anki renders natively on both desktop and AnkiDroid. (Backslashes are
# doubled here so the actual prompt text contains \\( \\) \\[ \\] and \\tau etc.)
PROMPT_TEMPLATE = (
    "You are authoring flashcards for the NCEES FE Electrical and Computer exam.\n"
    "You are given numbered source lines from a VERIFIED FE study deck. Each line "
    "is a fact or worked problem. For every line that makes a good exam flashcard, "
    "write one card that restates ONLY what is in that line. Never add a fact that "
    "is not explicitly in its source line. Skip lines that cannot make a good card.\n"
    "This is a math-heavy exam, so cards MUST carry the mathematics, and it must "
    "be typeset. Write EVERY formula, variable, unit, subscript, exponent, Greek "
    "letter, and fraction as LaTeX inside MathJax delimiters: inline as \\( ... \\) "
    "and displayed as \\[ ... \\]. Keep the exact quantities from the source line "
    "but format them properly, e.g. \\( \\tau = L/R \\), "
    "\\( R_{eq} = \\frac{R_1 R_2}{R_1 + R_2} \\), "
    "\\( W = \\tfrac{1}{2} C V^2 \\). Never write math as ambiguous plain text and "
    "never replace a formula with prose.\n"
    "Return strict JSON of the form: "
    '{"cards": [{"front": "...", "back": "...", "src": <the integer shown in '
    "brackets for the source line this card came from>}]}."
)


class ProviderError(RuntimeError):
    """A live-generation error (bad model, auth, rate limit, malformed reply).

    Distinct from :class:`ProviderUnavailable`: this propagates and fails the
    run loudly instead of silently falling back to the stub, so a misconfigured
    key or model is visible rather than masquerading as stub results.
    """


_DOTENV_LOADED = False


def _load_dotenv_once() -> None:
    """Load a local, git-ignored ``.env`` (walking up from this file to the repo
    root) into ``os.environ`` if the key isn't already set. Author-only local
    convenience; the ``.env`` is never committed or shipped. Never raises."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    if any(os.environ.get(v) for v in _KEY_ENV_VARS):
        return
    search = os.path.dirname(os.path.abspath(__file__))
    while True:
        candidate = os.path.join(search, ".env")
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass
            return
        parent = os.path.dirname(search)
        if parent == search:
            return
        search = parent


def api_key() -> str | None:
    _load_dotenv_once()
    for var in _KEY_ENV_VARS:
        val = os.environ.get(var)
        if val:
            return val
    return None


# Control chars that a single-backslash LaTeX command collapses to when the
# model emits it inside a JSON string (e.g. "\tfrac" -> TAB + "frac",
# "\frac" -> FORMFEED + "rac"). Reconstruct the backslash command so the stored
# math is valid LaTeX (renders correctly) and still grounds against the source.
_CTRL_REPAIR = {
    "\t": r"\t",
    "\x0c": r"\f",
    "\x08": r"\b",
    "\r": r"\r",
    "\x0b": r"\v",
}


def _repair_latex(text: str) -> str:
    for ctrl, rep in _CTRL_REPAIR.items():
        text = text.replace(ctrl, rep)
    return text


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("FE_AI_MODEL", DEFAULT_MODEL)

    def available(self) -> bool:
        return api_key() is not None

    def generate(
        self,
        chunk_id: str,
        spans: Sequence[Span],
        focus: str = "",
        feedback: str = "",
        temperature: float = 0.0,
    ) -> list[dict]:
        key = api_key()
        if not key:
            raise ProviderUnavailable(
                "No OpenAI credential found. Set OPENAI_API_KEY to enable live "
                "generation; otherwise use the deterministic stub provider."
            )

        ordered = sorted(spans, key=lambda s: (s.doc, s.line))
        numbered: list[str] = []
        index_map: dict[int, Span] = {}
        for i, span in enumerate(ordered):
            safe = sanitize_source(span.text)
            if not safe:
                continue
            index_map[i] = span
            numbered.append(f"[{i}] {safe}")
        if not numbered:
            return []

        # Optional user steer ("what do you want?"). It only re-orders emphasis
        # toward relevant source lines; grounding is unchanged — every card must
        # still restate a cited line, so the focus can never inject new facts.
        focus = " ".join((focus or "").split())[:300]
        focus_note = ""
        if focus:
            focus_note = (
                "\n\nFOCUS: The student specifically asked for cards about: "
                f"\u201c{focus}\u201d. Prefer the source lines most relevant to "
                "this and write cards on that topic. Still restate ONLY what is "
                "in a cited source line; skip lines unrelated to the focus."
            )

        # Self-correction feedback: on a revision round the caller passes the
        # automatic checker's reasons for rejecting the previous attempt so the
        # model can fix them (grounding, length, duplication) and not repeat
        # itself. Grounding is still enforced downstream — feedback can only
        # change phrasing/coverage, never bypass the verifier.
        feedback = " ".join((feedback or "").split())[:1600]
        feedback_note = ""
        if feedback:
            feedback_note = (
                "\n\nREVISION FEEDBACK (an automatic checker rejected your "
                f"previous attempt): {feedback}"
            )

        user = (
            PROMPT_TEMPLATE
            + focus_note
            + feedback_note
            + f"\n\nSOURCE LINES (area: {chunk_id}):\n"
            + "\n".join(numbered)
        )
        content = self._chat(key, user, temperature=temperature)

        cards: list[dict] = []
        for item in self._parse(content):
            front = _repair_latex(
                str(item.get("front") or item.get("question") or item.get("q") or "").strip()
            )
            back = _repair_latex(
                str(item.get("back") or item.get("answer") or item.get("a") or "").strip()
            )
            if not front or not back:
                continue
            span = self._resolve_span(item, index_map)
            if span is None:
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
    def _resolve_span(item: dict, index_map: dict[int, Span]) -> Span | None:
        """Map a card back to its cited source line, tolerating the ways models
        report the index: an int, a string like "[3]" or "3", or under a
        differently-named key. Falls back to the sole span when unambiguous."""
        raw = None
        for key in ("src", "source", "index", "line", "src_index"):
            if key in item and not isinstance(item[key], bool):
                raw = item[key]
                break
        idx: int | None = None
        if isinstance(raw, int):
            idx = raw
        elif isinstance(raw, str):
            m = re.search(r"\d+", raw)
            if m:
                idx = int(m.group())
        if idx is not None and idx in index_map:
            return index_map[idx]
        # Unambiguous fallback: exactly one source line in this chunk.
        if len(index_map) == 1:
            return next(iter(index_map.values()))
        return None

    def _chat(self, key: str, user: str, temperature: float = 0.0) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "You output only strict JSON. No prose.",
                    },
                    {"role": "user", "content": user},
                ],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ProviderError(
                f"OpenAI API error {exc.code} for model {self.model!r}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"OpenAI API request failed: {exc}") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Unexpected OpenAI response shape: {payload}") from exc

    @staticmethod
    def _parse(content: str) -> list[dict]:
        try:
            data = json.loads(content)
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("cards") or data.get("items") or []
        return [d for d in data if isinstance(d, dict)]
