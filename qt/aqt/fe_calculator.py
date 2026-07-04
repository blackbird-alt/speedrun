# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""On-screen scientific calculator for the FE exam study fork.

Opens the self-contained ``feprep/calculator/fe-calculator.html`` asset in a
non-modal, stay-on-top window so students can practise problems (reviews) under
real FE exam conditions. A single instance is reused; opening it again just
raises the existing window.
"""

from __future__ import annotations

import os
from typing import Optional

from aqt.qt import (
    QDialog,
    QUrl,
    QVBoxLayout,
    Qt,
    QWebEngineView,
    QWidget,
)

# The single reused window instance.
_calculator_window: Optional["FeCalculatorWindow"] = None

_HTML_RELATIVE_PATH = os.path.join("feprep", "calculator", "fe-calculator.html")


def _find_calculator_html() -> Optional[str]:
    """Locate ``feprep/calculator/fe-calculator.html`` robustly.

    Derives the path from this module's location rather than hardcoding an
    absolute path, then falls back to walking up the directory tree.
    """

    # Most direct: this file lives at <repo>/qt/aqt/fe_calculator.py, so the
    # repo root is two directories up.
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, "..", "..", _HTML_RELATIVE_PATH)),
    ]

    # Fall back to searching upward from this file for the asset, which keeps
    # working if aqt is installed somewhere unexpected.
    search_dir = here
    while True:
        candidates.append(os.path.join(search_dir, _HTML_RELATIVE_PATH))
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent

    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


class FeCalculatorWindow(QDialog):
    """A resizable, non-modal, stay-on-top window hosting the calculator."""

    def __init__(self, parent: Optional[QWidget], html_path: str) -> None:
        # No parent so the window is fully independent of the main window and
        # can float beside a review; keep it on top via the window flag.
        super().__init__(None)
        self.setWindowTitle("FE Calculator")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        # Non-modal so review interaction continues underneath.
        self.setModal(False)
        self.resize(420, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.web = QWebEngineView(self)
        self.web.load(QUrl.fromLocalFile(html_path))
        layout.addWidget(self.web)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        global _calculator_window
        _calculator_window = None
        super().closeEvent(event)


def show_fe_calculator(parent: Optional[QWidget] = None) -> None:
    """Open (or raise) the FE calculator window."""

    global _calculator_window

    if _calculator_window is not None:
        _calculator_window.show()
        _calculator_window.raise_()
        _calculator_window.activateWindow()
        return

    html_path = _find_calculator_html()
    if not html_path:
        from aqt.utils import showWarning

        showWarning(
            "Could not locate the FE calculator asset "
            f"({_HTML_RELATIVE_PATH}).",
            parent=parent,
        )
        return

    _calculator_window = FeCalculatorWindow(parent, html_path)
    _calculator_window.show()
    _calculator_window.raise_()
    _calculator_window.activateWindow()
