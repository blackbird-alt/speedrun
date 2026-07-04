# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Per-card AI tutor panel for the reviewer (the "Solve / Ask" affordance).

A single reused, non-modal, stay-on-top window (same pattern as the FE
calculator) that floats beside a review. It always reflects the card currently
on screen: the reviewer calls :func:`update_context` as each question is shown.

It offers two grounded actions, both routed through :mod:`aqt.fe_ai`:

* *Solve* — a worked solution/explanation for the current card.
* *Ask*   — a free-form question about the current card.

Every call runs on a background thread so a slow/hung request never freezes
review. Any failure (no key, timeout, network, bad reply) is caught and shown
inline as "AI is unavailable right now" — it never propagates into the review
loop, so grading and the next card are completely unaffected. All output is
clearly labelled as AI-generated assistant guidance, not verified deck content.
"""

from __future__ import annotations

import html
import json
from concurrent.futures import Future
from typing import Optional

from aqt import fe_ai
from aqt.qt import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
)
from aqt.webview import AnkiWebView

_helper_window: Optional["FeAiHelperWindow"] = None


class FeAiHelperWindow(QDialog):
    # Loaded once via stdHtml. feAiSetContent() swaps the inner markup and asks
    # the bundled MathJax to (re)typeset it, so replies with \( \) / \[ \] math
    # render properly instead of showing raw TeX source.
    _OUTPUT_BODY = """
<div id="fe-ai-content"
  style="font-family:system-ui;color:#d3ddf0;background:#0b1220;line-height:1.5;padding:8px;"></div>
<script>
window.feAiSetContent = function (h) {
  var el = document.getElementById('fe-ai-content');
  if (!el) { return; }
  el.innerHTML = h;
  try {
    if (window.MathJax && MathJax.startup && MathJax.startup.promise) {
      MathJax.startup.promise
        .then(function () { MathJax.typesetClear(); return MathJax.typesetPromise([el]); })
        .catch(function () {});
    }
  } catch (e) {}
};
</script>
"""

    def __init__(self, mw) -> None:
        super().__init__(None)
        self.mw = mw
        self._front = ""
        self._back = ""
        self._source = ""
        self._busy = False

        self.setWindowTitle("FE AI Helper")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setModal(False)
        self.resize(440, 520)

        layout = QVBoxLayout(self)

        banner = QLabel(
            "AI assistant \u2014 answers are AI-generated guidance grounded in "
            "the current card, not verified deck content."
        )
        banner.setWordWrap(True)
        layout.addWidget(banner)

        btn_row = QHBoxLayout()
        self.solve_btn = QPushButton("Solve this card", self)
        self.solve_btn.clicked.connect(self._on_solve)  # type: ignore[attr-defined]
        btn_row.addWidget(self.solve_btn)
        layout.addLayout(btn_row)

        ask_row = QHBoxLayout()
        self.ask_edit = QLineEdit(self)
        self.ask_edit.setPlaceholderText("Ask a question about this card…")
        self.ask_edit.returnPressed.connect(self._on_ask)  # type: ignore[attr-defined]
        self.ask_btn = QPushButton("Ask", self)
        self.ask_btn.clicked.connect(self._on_ask)  # type: ignore[attr-defined]
        ask_row.addWidget(self.ask_edit, 1)
        ask_row.addWidget(self.ask_btn)
        layout.addLayout(ask_row)

        # Output is a web view so MathJax can typeset the \( \) / \[ \] math the
        # cards use. We load Anki's *bundled* MathJax through its local media
        # server (see stdHtml below) so it works fully offline — no CDN.
        self.output = AnkiWebView(self)
        layout.addWidget(self.output, 1)
        self.output.stdHtml(
            self._OUTPUT_BODY,
            head=(
                "<style>html,body{background:#0b1220;margin:0;overflow-x:hidden;}"
                "#fe-ai-content{max-width:100%;overflow-wrap:anywhere;}"
                "mjx-container{max-width:100%;}"
                'mjx-container[display="true"]{overflow-x:auto;overflow-y:hidden;}'
                "</style>"
            ),
            js=[
                "js/mathjax.js",
                "js/vendor/mathjax/tex-chtml-full.js",
            ],
            context=self,
        )

        self._render_output(
            "<p style='color:#93a2bd'>Pick <b>Solve</b> for a worked solution, or "
            "type a question and press <b>Ask</b>.</p>"
        )

    # -- context ----------------------------------------------------------- #

    def set_context(self, front: str, back: str, source: str = "") -> None:
        self._front = front or ""
        self._back = back or ""
        self._source = source or ""

    def _card_context(self) -> str:
        parts = [f"Front: {self._front}", f"Back: {self._back}"]
        if self._source:
            parts.append(f"Source: {self._source}")
        return "\n".join(parts)

    # -- actions ----------------------------------------------------------- #

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.solve_btn.setEnabled(not busy)
        self.ask_btn.setEnabled(not busy)

    def _on_solve(self) -> None:
        if self._busy:
            return
        if not (self._front or self._back):
            self._render_output("<p style='color:#93a2bd'>No card is open.</p>")
            return
        self._set_busy(True)
        self._render_output("<p style='color:#93a2bd'>Thinking…</p>")
        front, back, source = self._front, self._back, self._source

        def task() -> str:
            return fe_ai.solve(front, back, source)

        self.mw.taskman.run_in_background(
            task, self._on_result, uses_collection=False
        )

    def _on_ask(self) -> None:
        if self._busy:
            return
        question = self.ask_edit.text().strip()
        if not question:
            return
        if not (self._front or self._back):
            self._render_output("<p style='color:#93a2bd'>No card is open.</p>")
            return
        self._set_busy(True)
        self._render_output("<p style='color:#93a2bd'>Thinking…</p>")
        context = self._card_context()

        def task() -> str:
            return fe_ai.ask(question, context)

        self.mw.taskman.run_in_background(
            task, self._on_result, uses_collection=False
        )

    def _on_result(self, fut: Future) -> None:
        self._set_busy(False)
        try:
            answer = fut.result()
        except fe_ai.FeAiUnavailable:
            self._render_output(
                "<p style='color:#E0A45C'>AI is unavailable right now. Your "
                "review is unaffected \u2014 carry on.</p>"
            )
            return
        except Exception:
            # Absolutely never let an AI failure escape into the review loop.
            self._render_output(
                "<p style='color:#E0A45C'>AI is unavailable right now. Your "
                "review is unaffected \u2014 carry on.</p>"
            )
            return
        self._render_answer(answer or "")

    # -- rendering --------------------------------------------------------- #

    def _render_output(self, body_html: str) -> None:
        # Push new markup into the already-loaded page; eval is queued until the
        # DOM is ready, so this is safe even for the very first render.
        self.output.eval(f"feAiSetContent({json.dumps(body_html)});")

    def _render_answer(self, text: str) -> None:
        # html.escape neutralises HTML injection but leaves the math delimiters
        # (\( \) / \[ \]) untouched; the browser decodes entities in text nodes,
        # so MathJax still sees the real characters inside math regions.
        safe = html.escape(text).replace("\n", "<br>")
        self._render_output(
            "<div style='font-size:11px;letter-spacing:.08em;text-transform:"
            "uppercase;color:#E0A45C;margin-bottom:6px;'>AI assistant \u00b7 not "
            "deck content</div>"
            f"<div style='line-height:1.5;'>{safe}</div>"
        )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        global _helper_window
        _helper_window = None
        super().closeEvent(event)


def show_fe_ai_helper(
    mw, front: str = "", back: str = "", source: str = ""
) -> None:
    """Open (or raise) the AI helper for the current card. No-op when AI is
    unavailable (the reviewer hides the trigger in that state anyway)."""
    global _helper_window

    if not fe_ai.ai_available():
        return

    if _helper_window is None:
        _helper_window = FeAiHelperWindow(mw)
    _helper_window.set_context(front, back, source)
    _helper_window.show()
    _helper_window.raise_()
    _helper_window.activateWindow()


def update_context(front: str, back: str, source: str = "") -> None:
    """If the helper is open, point it at the newly shown card. Safe to call for
    every card; a no-op when the panel is closed."""
    if _helper_window is not None:
        _helper_window.set_context(front, back, source)
