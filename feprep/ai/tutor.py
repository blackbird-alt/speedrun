# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Grounded, key-gated FE tutor helper (in-app "Solve"/"Ask").

A tiny stdlib-only companion to :mod:`providers.openai`. Where that module
*authors deck cards*, this one answers a student's question about a single card
they are reviewing. It is deliberately constrained:

* It is key-gated on ``OPENAI_API_KEY`` exactly like the card generator: with no
  key, :func:`solve`/:func:`ask` raise :class:`TutorUnavailable` and the caller
  hides the feature / shows an inline "AI unavailable" note. The tutor can never
  silently depend on a network model.
* It is *grounded*: the model is told to explain/solve using ONLY the supplied
  card content (plus an optional handbook excerpt), and to say so when the card
  does not contain enough to answer. Card text is passed through
  :func:`providers.gemini.sanitize_source` first as a basic prompt-injection
  guard, identical to the generation path.
* Output is plain prose framed as an AI assistant answer, NOT deck content, so it
  is never confused with a verified card.

Temperature is 0 and the network timeout is short (~30s) so a hung request fails
fast and the review loop stays responsive.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from providers.gemini import sanitize_source
from providers.openai import API_URL, DEFAULT_MODEL, api_key

# Short by design: this is an interactive helper, not a batch job. A hung
# request should surface as "AI unavailable" quickly rather than freezing the
# review session.
DEFAULT_TIMEOUT = 30

# Shared framing so every answer is grounded and clearly labelled as assistant
# output rather than a verified deck fact.
_SYSTEM = (
    "You are an AI study assistant for a student taking the NCEES FE Electrical "
    "and Computer exam. You help with ONE flashcard at a time. Explain and solve "
    "using ONLY the card content the student gives you (plus any handbook excerpt "
    "provided). Do NOT invent facts, formulas, or numbers that are not supported "
    "by that content; if the card does not contain enough to answer, say so "
    "plainly and suggest what to look up in the FE Reference Handbook. Keep math "
    "typeset as LaTeX inside MathJax delimiters (inline \\( ... \\), displayed "
    "\\[ ... \\]). Be concise and exam-focused. Your reply is assistant guidance, "
    "not a verified deck card."
)


class TutorUnavailable(RuntimeError):
    """No credential/SDK available: the feature should hide or no-op."""


class TutorError(RuntimeError):
    """A live request failed (auth, rate limit, timeout, malformed reply)."""


def _chat(user: str, *, model: str | None, timeout: int) -> str:
    key = api_key()
    if not key:
        raise TutorUnavailable(
            "No OpenAI credential found. Set OPENAI_API_KEY (or store a key in "
            "the profile) to enable the AI tutor."
        )
    body = json.dumps(
        {
            "model": model or os.environ.get("FE_AI_MODEL", DEFAULT_MODEL),
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYSTEM},
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise TutorError(f"OpenAI API error {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TutorError(f"OpenAI API request failed: {exc}") from exc
    try:
        return str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise TutorError(f"Unexpected OpenAI response shape: {payload}") from exc


def _card_block(front: str, back: str, source_text: str, handbook: str) -> str:
    parts = [
        "CARD FRONT:\n" + sanitize_source(front or ""),
        "CARD BACK (answer):\n" + sanitize_source(back or ""),
    ]
    src = sanitize_source(source_text or "")
    if src and src not in (front or "", back or ""):
        parts.append("CITED SOURCE LINE:\n" + src)
    hb = sanitize_source(handbook or "")
    if hb:
        parts.append("HANDBOOK EXCERPT:\n" + hb)
    return "\n\n".join(parts)


def solve(
    front: str,
    back: str,
    source_text: str = "",
    handbook: str = "",
    *,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Return a grounded worked solution/explanation for one card.

    Raises :class:`TutorUnavailable` when no key is present and
    :class:`TutorError` on any live-request failure.
    """
    user = (
        "Give a clear, step-by-step worked solution or explanation for this FE "
        "flashcard, grounded only in the content below. Show the reasoning and "
        "any formula used, then state the final answer.\n\n"
        + _card_block(front, back, source_text, handbook)
    )
    return _chat(user, model=model, timeout=timeout)


def ask(
    question: str,
    card_context: str,
    handbook: str = "",
    *,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Answer a free-form question about the current card, grounded in its
    content. Same error contract as :func:`solve`."""
    user = (
        "The student is reviewing the flashcard below and asks a question about "
        "it. Answer using only the card content (and handbook excerpt if given).\n\n"
        "STUDENT QUESTION:\n" + sanitize_source(question or "") + "\n\n"
        "CARD CONTEXT:\n" + sanitize_source(card_context or "")
        + (("\n\nHANDBOOK EXCERPT:\n" + sanitize_source(handbook)) if handbook else "")
    )
    return _chat(user, model=model, timeout=timeout)
