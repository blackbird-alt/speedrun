# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Shared glue between the Anki desktop UI and the ``feprep/ai`` pipeline.

This is the single place the desktop app talks to the (optional) OpenAI-backed
FE features:

* :func:`ai_available` — the master gate. Every AI affordance (the card
  generator menu/button, the reviewer "Solve/Ask" helper) is shown only when
  this returns ``True``. It is ``True`` iff an OpenAI key is resolvable, from
  the ``OPENAI_API_KEY`` env var first, else from a **local** profile setting
  (never synced).
* :func:`get_key` / :func:`set_key` / :func:`clear_key` — manage that local key.
* :func:`generate_cards` — grounded card generation: source lines come from the
  deck's already-verified FE cards for one area, candidates come from
  :class:`providers.openai.OpenAIProvider`, and every candidate is run through
  the SAME verifier logic as ``feprep/ai/verify_cards.py`` so only grounded,
  substantive, non-duplicate cards are returned. Never raises to the UI: any
  failure returns ``[]``.
* :func:`solve` / :func:`ask` — grounded, card-scoped tutor calls. These raise a
  caught :class:`FeAiUnavailable` on failure so the reviewer can show an inline
  "AI is unavailable right now" and continue the review uninterrupted.

Design rules mirrored from the other feprep integrations:
* Import-safe: importing this module must not import PyQt-heavy or network code,
  must not touch the collection, and must never raise. ``feprep/ai`` is only put
  on ``sys.path`` and imported lazily inside the functions that need it.
* Fail closed: if anything about the AI is missing or broken, the features
  simply disappear or no-op — the app is never blocked and review never breaks.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# feprep/ai path wiring (lazy, matches fe_calculator's robust-path approach)
# --------------------------------------------------------------------------- #

# The profile-local key setting. Stored in ``mw.pm.meta`` (per-machine profile
# metadata) so it is NEVER written to the collection and NEVER synced.
_KEY_META = "feOpenAiKey"

_AI_DIR_RELATIVE = os.path.join("feprep", "ai")


def _find_ai_dir() -> Optional[str]:
    """Locate the ``feprep/ai`` directory robustly, like fe_calculator does for
    its HTML asset: derive from this file's location, then walk upward."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, "..", "..", _AI_DIR_RELATIVE)),
    ]
    search_dir = here
    while True:
        candidates.append(os.path.join(search_dir, _AI_DIR_RELATIVE))
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent
    for candidate in candidates:
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return None


def _ensure_ai_on_path() -> bool:
    """Put ``feprep/ai`` on ``sys.path`` so ``common``/``providers``/``tutor``
    import as they do when the pipeline is run directly. Returns success."""
    ai_dir = _find_ai_dir()
    if not ai_dir:
        return False
    if ai_dir not in sys.path:
        sys.path.insert(0, ai_dir)
    return True


# --------------------------------------------------------------------------- #
# Key management
# --------------------------------------------------------------------------- #


def _mw() -> Any:
    import aqt

    return aqt.mw


_DOTENV_LOADED = False


def _load_dotenv_once() -> None:
    """Load a local, git-ignored ``.env`` (walking up from this file to the repo
    root) into ``os.environ`` if the key isn't already set. This is author-only,
    local convenience: the ``.env`` is never committed and never shipped. Never
    raises."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    if os.environ.get("OPENAI_API_KEY"):
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


def _resolve_key() -> Optional[str]:
    """Return the OpenAI key from env first, else the local profile setting,
    else the collection config. Never raises."""
    _load_dotenv_once()
    env = os.environ.get("OPENAI_API_KEY")
    if env:
        return env
    mw = _mw()
    if mw is None:
        return None
    try:
        val = mw.pm.meta.get(_KEY_META)
        if val:
            return str(val)
    except Exception:
        pass
    try:
        if mw.col is not None:
            val = mw.col.get_config(_KEY_META, None)
            if val:
                return str(val)
    except Exception:
        pass
    return None


def _ensure_provider_key() -> Optional[str]:
    """Resolve the key and mirror it into ``OPENAI_API_KEY`` so the stdlib
    provider/tutor (which read only the env) pick it up without a restart."""
    key = _resolve_key()
    if key:
        os.environ["OPENAI_API_KEY"] = key
    return key


def ai_available() -> bool:
    """True iff an OpenAI key is resolvable. The master gate for every AI
    affordance. Never raises."""
    try:
        return bool(_ensure_provider_key())
    except Exception:
        return False


def get_key() -> Optional[str]:
    """Return the currently stored/resolved key (or None). Never raises."""
    try:
        return _resolve_key()
    except Exception:
        return None


def set_key(key: str) -> None:
    """Store the key locally (profile meta, not synced) and flip availability on
    immediately by mirroring it into the environment."""
    key = (key or "").strip()
    mw = _mw()
    if mw is not None:
        try:
            mw.pm.meta[_KEY_META] = key
            mw.pm.save()
        except Exception:
            pass
    if key:
        os.environ["OPENAI_API_KEY"] = key
    else:
        os.environ.pop("OPENAI_API_KEY", None)


def clear_key() -> None:
    """Remove the local key and turn the features back off."""
    set_key("")


# --------------------------------------------------------------------------- #
# Grounded card generation
# --------------------------------------------------------------------------- #

# Bound the request: enough source lines to yield fresh cards, but capped so a
# live run stays fast and cheap.
_MAX_SOURCE_SPANS = 120

# Self-correction loop: if the first pass doesn't clear the requested count, retry
# a few times feeding the verifier's rejection reasons back so the model fixes
# them (and rotating the source window so rounds differ). Bounded so a live run
# stays fast/cheap and always terminates.
_MAX_ROUNDS = 4
_ROUND_WINDOW = 48  # source lines shown per round


def _existing_area_notes(col: Any, area_key: str) -> list[Any]:
    """Return the collection's notes tagged ``fe::<area_key>`` (verified deck
    cards used as grounding source). Never raises."""
    try:
        nids = col.find_notes(f'"tag:fe::{area_key}"')
    except Exception:
        return []
    notes: list[Any] = []
    for nid in nids:
        try:
            notes.append(col.get_note(nid))
        except Exception:
            continue
    return notes


def _note_front_back(note: Any) -> tuple[str, str]:
    """Best-effort (front, back) from a note across note types."""
    try:
        fields = list(note.values())
    except Exception:
        fields = list(getattr(note, "fields", []) or [])
    front = fields[0] if len(fields) > 0 else ""
    back = fields[1] if len(fields) > 1 else ""
    return str(front).strip(), str(back).strip()


def generate_cards(area_key: str, n: int, focus: str = "") -> list[dict]:
    """Generate up to ``n`` grounded, verified, non-duplicate candidate cards
    for the FE area ``area_key``.

    Source = the deck's existing verified ``fe::<area_key>`` cards. Candidates
    come from the OpenAI provider and are filtered through the same verifier
    logic as ``feprep/ai/verify_cards.py``. Each returned card keeps its cited
    source (``source_text``). On ANY error/timeout/unavailability returns ``[]``
    — this must never raise into the UI.

    ``focus`` is the student's free-text steer ("what do you want more of?").
    When given, the grounding source lines are ranked by relevance to it and the
    most relevant are fed to the model, which is also told to concentrate on that
    topic. Grounding is unchanged: every card still restates a cited line, so the
    focus can only shift emphasis, never introduce an ungrounded fact.

    Generation is a bounded self-correction loop: each round verifies its cards
    with the same gate, and if fewer than ``n`` survive it feeds the rejection
    reasons back (and rotates the source window) so the next round fixes them and
    reaches the count, instead of silently returning too few.
    """
    if n <= 0:
        return []
    try:
        return _generate_cards_impl(area_key, n, focus)
    except Exception:
        return []


def _build_feedback(reasons: dict[str, int], produced_fronts: list[str], needed: int) -> str:
    """Turn the verifier's rejection reasons + the questions already produced into
    a short instruction that steers the NEXT round to fix them and not repeat
    itself. Kept compact so it never dominates the prompt."""
    parts: list[str] = []
    if reasons:
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:4]
        summary = "; ".join(f"{count}\u00d7 {reason}" for reason, count in top)
        parts.append(f"Your previous cards were rejected ({summary}).")
    parts.append(
        "Write NEW cards that fix this: the answer must restate wording that "
        "actually appears in the cited source line (no outside facts), must be "
        "substantive (never one word), and must not repeat an earlier card."
    )
    recent: list[str] = []
    for front in produced_fronts[-16:]:
        front = " ".join((front or "").split())
        if front:
            recent.append(front[:80])
    if recent:
        parts.append("Do NOT repeat these questions: " + " | ".join(recent))
    if needed > 0:
        parts.append(f"Aim for about {needed} more good cards.")
    return " ".join(parts)


def _generate_cards_impl(area_key: str, n: int, focus: str = "") -> list[dict]:
    if not _ensure_provider_key():
        return []
    if not _ensure_ai_on_path():
        return []

    mw = _mw()
    if mw is None or mw.col is None:
        return []

    import common as C  # noqa: E402
    import verify_cards as V  # noqa: E402
    from providers.openai import OpenAIProvider  # noqa: E402

    # Build grounding spans from ALL existing verified cards in this area.
    notes = _existing_area_notes(mw.col, area_key)
    if not notes:
        return []

    all_spans: list[C.Span] = []
    existing_texts: list[str] = []
    for idx, note in enumerate(notes, start=1):
        front, back = _note_front_back(note)
        if not front or not back:
            continue
        try:
            tags = " ".join(note.tags)
        except Exception:
            tags = f"fe::{area_key}"
        all_spans.append(
            C.Span(
                doc="collection",
                line=idx,
                front=front,
                answer=back,
                tags=tags,
                area=area_key,
            )
        )
        existing_texts.append(f"{front} {back}")
    if not all_spans:
        return []

    # Steer WHICH source lines are fed to the model: with a focus, rank by
    # relevance to it (char-ngram cosine) and take the most relevant; otherwise
    # take the natural order. Cap either way so a live run stays fast/cheap.
    focus = (focus or "").strip()
    if focus:
        ranked = sorted(
            all_spans, key=lambda s: C.char_cosine(focus, s.text), reverse=True
        )[:_MAX_SOURCE_SPANS]
    else:
        ranked = all_spans[:_MAX_SOURCE_SPANS]

    provider = OpenAIProvider()
    # Near-duplicate guards (same char-ngram cosine + cutoff as the verifier):
    # against EXISTING deck cards (only add genuinely new cards) and against
    # cards already kept in THIS run (so rounds don't re-add the same card).
    existing_vecs = [V._ngram_vec(t) for t in existing_texts]

    kept: list[dict] = []
    kept_vecs: list[dict] = []
    produced_fronts: list[str] = []  # every front produced (kept or rejected)
    feedback = ""
    stall = 0

    # Self-correction loop: generate -> verify -> if short, feed the rejection
    # reasons back and try again on a rotated slice of source lines.
    for r in range(_MAX_ROUNDS):
        # Rotate the source window each round so a temp=0 first pass + the focus
        # ranking still yield DIFFERENT material instead of repeating.
        if len(ranked) <= _ROUND_WINDOW:
            batch = ranked
        else:
            start = (r * _ROUND_WINDOW) % len(ranked)
            batch = ranked[start : start + _ROUND_WINDOW]
            if len(batch) < _ROUND_WINDOW:
                batch = batch + ranked[: _ROUND_WINDOW - len(batch)]
        # Deterministic first pass (matches the offline pipeline); loosen a
        # little on revision rounds to get genuinely new phrasings.
        temperature = 0.0 if r == 0 else 0.5

        try:
            candidates = provider.generate(
                area_key, batch, focus=focus, feedback=feedback, temperature=temperature
            )
        except Exception:
            break
        if not candidates:
            if r == 0:
                break  # nothing at all on the first pass -> give up
            stall += 1
            if stall >= 2:
                break
            continue

        # SAME verifier logic as verify_cards.py. Empty gold: the gold set is the
        # held-out eval corpus and is not relevant to in-app generation, so the
        # contradiction-vs-gold check is a no-op; grounding, triviality and
        # near-duplicate checks all apply unchanged.
        annotated = V.classify(candidates, [])
        before = len(kept)
        reasons: dict[str, int] = {}
        for card in annotated:
            produced_fronts.append(card.get("front", ""))
            if card.get("label") != "correct_useful":
                reason = card.get("reason", "rejected")
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            vec = V._ngram_vec(f"{card['front']} {card['back']}")
            if any(C.cosine_counts(vec, ev) > V.DUP_COSINE for ev in existing_vecs):
                continue
            if any(C.cosine_counts(vec, kv) > V.DUP_COSINE for kv in kept_vecs):
                continue
            kept_vecs.append(vec)
            kept.append(
                {
                    "front": card["front"],
                    "back": card["back"],
                    "source_text": card.get("source_text", ""),
                    "grounding_recall": card.get("grounding_recall"),
                    "area_key": area_key,
                }
            )
            if len(kept) >= n:
                break

        if len(kept) >= n:
            break
        # No new cards this round? count a stall and bail after two dead rounds.
        if len(kept) == before:
            stall += 1
            if stall >= 2:
                break
        else:
            stall = 0
        feedback = _build_feedback(reasons, produced_fronts, needed=n - len(kept))

    kept = kept[:n]

    # Attach a grounded, worked-solution explanation to each FINAL card via the
    # same tutor the reviewer's Solve helper uses. Done once, after the loop, so
    # we never spend a tutor call on a card that gets dropped. Best-effort per
    # card: any failure leaves an empty string so the card is still usable.
    for card in kept:
        try:
            card["explanation"] = (
                solve(card["front"], card["back"], card.get("source_text", "")) or ""
            )
        except Exception:
            card["explanation"] = ""

    return kept


# --------------------------------------------------------------------------- #
# FE Prep note presentation (so generated cards match the deck's styled type)
# --------------------------------------------------------------------------- #


def _ensure_feprep_on_path() -> bool:
    """Put the ``feprep`` package dir (parent of ``feprep/ai``) on ``sys.path``
    so ``build_apkg`` — which owns the styled notetype + field prep — imports."""
    ai_dir = _find_ai_dir()
    if not ai_dir:
        return False
    feprep_dir = os.path.dirname(ai_dir)
    if feprep_dir and feprep_dir not in sys.path:
        sys.path.insert(0, feprep_dir)
    return bool(feprep_dir)


def fe_prep_presentation(area_key: str) -> tuple[str, str]:
    """Return ``(topic_display, accent)`` for ``area_key`` using the same mapping
    ``build_apkg`` uses, so a generated card carries the right chip + colour.
    Falls back to a titled key + default accent. Never raises."""
    try:
        if _ensure_feprep_on_path():
            import build_apkg  # noqa: E402

            return build_apkg.topic_of([f"fe::{area_key}"])
    except Exception:
        pass
    return area_key.replace("_", " ").title(), "#2F6BFF"


def fe_prep_field(text: str) -> str:
    """Escape a generated field to safe HTML exactly like the shipped deck does
    (keeping MathJax ``\\( ... \\)`` intact). Falls back to a plain HTML escape."""
    text = text or ""
    try:
        if _ensure_feprep_on_path():
            import build_apkg  # noqa: E402

            return build_apkg._prepare_field(text)
    except Exception:
        pass
    import html

    return html.escape(text, quote=False)


# --------------------------------------------------------------------------- #
# Grounded tutor (Solve / Ask)
# --------------------------------------------------------------------------- #


class FeAiUnavailable(Exception):
    """AI tutor could not answer (no key, network/timeout, or bad reply).

    Callers catch this and show an inline "AI is unavailable right now"; the
    review loop is never affected.
    """


def _tutor():
    if not _ensure_provider_key():
        raise FeAiUnavailable("No OpenAI key configured.")
    if not _ensure_ai_on_path():
        raise FeAiUnavailable("feprep/ai is unavailable.")
    try:
        import tutor  # noqa: E402

        return tutor
    except Exception as exc:  # pragma: no cover - defensive
        raise FeAiUnavailable(f"AI tutor unavailable: {exc}") from exc


def solve(front: str, back: str, source_text: str = "") -> str:
    """Grounded worked solution for the current card. Raises
    :class:`FeAiUnavailable` on any failure."""
    tutor = _tutor()
    try:
        return tutor.solve(front, back, source_text)
    except tutor.TutorUnavailable as exc:
        raise FeAiUnavailable(str(exc)) from exc
    except Exception as exc:
        raise FeAiUnavailable(f"AI request failed: {exc}") from exc


def ask(question: str, card_context: str) -> str:
    """Grounded answer to a question about the current card. Raises
    :class:`FeAiUnavailable` on any failure."""
    tutor = _tutor()
    try:
        return tutor.ask(question, card_context)
    except tutor.TutorUnavailable as exc:
        raise FeAiUnavailable(str(exc)) from exc
    except Exception as exc:
        raise FeAiUnavailable(f"AI request failed: {exc}") from exc
