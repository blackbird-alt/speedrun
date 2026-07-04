# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""On-screen FE Reference Handbook viewer for the FE exam study fork.

The FE exam supplies the official NCEES FE Reference Handbook on-screen as a
searchable PDF. To practise under real exam conditions, this opens the
student's **own** copy of that handbook in a searchable PDF view.

The handbook is NCEES-copyrighted, so it is deliberately NOT bundled with or
committed to this (public, AGPL) repository. Instead the student imports their
own downloaded copy: the file is stored inside the collection's media folder as
``_fe_handbook.pdf``. The leading underscore marks it as protected media (Anki
never treats it as "unused"), and — crucially — media in the collection folder
is carried between the student's own devices by Anki's normal media sync. So
importing the handbook on the desktop makes it appear on the phone after a sync;
nothing copyrighted is copied into the project itself.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

import aqt
from aqt.qt import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QUrl,
    Qt,
    QVBoxLayout,
    QWebEngineSettings,
    QWebEngineView,
    QWidget,
    qconnect,
)

# Protected, sync-carried media filename holding the student's own handbook PDF.
HANDBOOK_MEDIA_NAME = "_fe_handbook.pdf"

_handbook_window: Optional["FeHandbookWindow"] = None


def _handbook_media_path() -> Optional[str]:
    """Absolute path to the handbook inside the collection's media folder.

    Files here sync between the student's devices, so a handbook imported on the
    desktop reaches the phone via Anki's media sync.
    """

    mw = aqt.mw
    if mw is None or mw.col is None:
        return None
    return os.path.join(mw.col.media.dir(), HANDBOOK_MEDIA_NAME)


def _stored_handbook_path() -> Optional[str]:
    path = _handbook_media_path()
    if path and os.path.isfile(path):
        return path
    return None


def _prompt_for_handbook(parent: Optional[QWidget]) -> Optional[str]:
    """Ask the student to choose their own FE Reference Handbook PDF and copy it
    into the collection's media folder (as protected, sync-carried media)."""

    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Select your FE Reference Handbook PDF (it will sync to your phone)",
        "",
        "PDF files (*.pdf)",
    )
    if not path:
        return None
    dest = _handbook_media_path()
    if dest is None:
        return None
    try:
        shutil.copyfile(path, dest)
    except OSError as exc:
        from aqt.utils import showWarning

        showWarning(f"Could not import the handbook: {exc}", parent=parent)
        return None
    return dest


# Blank empty-state shown when no handbook has been uploaded yet (no sample
# content — just a prompt pointing at the Upload button).
_BLANK_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0;background:#0b1220;color:#93a2bd;
  font-family:system-ui,'Segoe UI',Roboto,sans-serif;
  display:flex;align-items:center;justify-content:center;text-align:center;}
.box{max-width:460px;padding:0 24px;}
h2{color:#eaf1fc;font-weight:650;margin:0 0 10px;font-size:20px;}
.copper{color:#E0A45C;}
p{font-size:14px;line-height:1.55;margin:0;}
</style></head><body><div class="box">
<h2>No handbook uploaded</h2>
<p>Click <span class="copper">Upload handbook (PDF)</span> above to add your own
official NCEES FE Reference Handbook. It stays in your collection, is never
shared, and syncs to your other devices.</p>
</div></body></html>"""


class FeHandbookWindow(QDialog):
    """A resizable, non-modal, stay-on-top window that acts as an upload page +
    PDF viewer. With no handbook it shows a blank upload state; once a handbook
    is present it renders it with the built-in PDF viewer (search + zoom)."""

    def __init__(self, parent: Optional[QWidget], pdf_path: Optional[str]) -> None:
        super().__init__(None)
        self.setWindowTitle("FE Reference Handbook")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setModal(False)
        self.resize(900, 820)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        upload = QPushButton("Upload handbook (PDF)", self)
        qconnect(upload.clicked, self._upload)
        bar.addWidget(upload)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.web = QWebEngineView(self)
        settings = self.web.settings()
        # Chromium's built-in PDF viewer (gives an in-document search box + zoom).
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        layout.addWidget(self.web)

        self.render(pdf_path)

    def render(self, pdf_path: Optional[str]) -> None:
        """Show the handbook PDF, or the blank upload state when there is none."""
        if pdf_path and os.path.isfile(pdf_path):
            self.web.load(QUrl.fromLocalFile(pdf_path))
        else:
            self.web.setHtml(_BLANK_HTML)

    def _upload(self) -> None:
        path = _prompt_for_handbook(self)
        if path:
            self.render(path)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        global _handbook_window
        _handbook_window = None
        super().closeEvent(event)


def show_fe_handbook(parent: Optional[QWidget] = None, *, choose: bool = False) -> None:
    """Open (or raise) the FE handbook upload page / viewer.

    The window always opens: with no handbook it shows a blank upload state
    (never a sample, never an automatic file dialog). ``choose=True`` (the
    "Set FE Handbook…" action) opens the upload picker directly.
    """

    global _handbook_window

    if choose:
        _prompt_for_handbook(parent)
    path = _stored_handbook_path()

    if _handbook_window is not None:
        _handbook_window.render(path)
        _handbook_window.show()
        _handbook_window.raise_()
        _handbook_window.activateWindow()
        return

    _handbook_window = FeHandbookWindow(parent, path)
    _handbook_window.show()
    _handbook_window.raise_()
    _handbook_window.activateWindow()
