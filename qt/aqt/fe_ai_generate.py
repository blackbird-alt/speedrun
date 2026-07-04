# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""In-app AI card generator for the FE study fork.

A single reused, non-modal dialog that lets a student:

1. pick an NCEES FE area (the ~18 ``fe::<key>`` areas, mirrored from
   :class:`aqt.deckbrowser.DeckBrowser`) and a card count,
2. click *Generate* to author candidate cards with the grounded OpenAI provider,
3. preview only the cards that PASSED the verifier (front / back / cited source),
4. tick the ones to keep and add them to the collection as real notes tagged
   ``fe::<area> ai::generated``.

Everything routes through :mod:`aqt.fe_ai`, so the whole dialog degrades
gracefully: generation runs on a background thread (the UI never blocks), and
:func:`aqt.fe_ai.generate_cards` returns ``[]`` on any error/timeout instead of
raising. The entry point :func:`show_fe_ai_generate` refuses to open when no
OpenAI key is configured, matching the hidden menu/button state.
"""

from __future__ import annotations

from concurrent.futures import Future
from typing import Optional

from aqt import fe_ai
from aqt.operations import CollectionOp
from aqt.qt import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    Qt,
    QVBoxLayout,
)
from aqt.utils import showInfo, tooltip

# Single reused instance, like the FE calculator window.
_generate_dialog: Optional["FeAiGenerateDialog"] = None


def _fe_areas() -> list[tuple[str, str]]:
    """Return ``[(display_name, tag_key), ...]`` mirroring the dashboard.

    Imported lazily to avoid any import cycle at module load.
    """
    try:
        from aqt.deckbrowser import DeckBrowser

        return list(DeckBrowser.FE_TAG_KEYS.items())
    except Exception:
        return []


class FeAiGenerateDialog(QDialog):
    """Pick an area + count, generate grounded cards, preview and add them."""

    def __init__(self, mw) -> None:
        super().__init__(None)
        self.mw = mw
        self._candidates: list[dict] = []
        self._busy = False

        self.setWindowTitle("Generate FE cards (AI)")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setModal(False)
        self.resize(640, 620)

        layout = QVBoxLayout(self)

        note = QLabel(
            "Generate more FE cards on demand. Every card is grounded in your own "
            "verified FE cards for the chosen area, and only drafts that pass the "
            "same grounding verifier are shown. Say what you want more of, review "
            "the results, and add the ones you like."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        # OpenAI key row: lets a student switch the feature on right here, so it
        # is usable any time without hunting through the Tools menu. Hidden once
        # a key is configured. Stored locally on this device only (never synced).
        self.key_row = QFrame(self)
        key_layout = QHBoxLayout(self.key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(QLabel("OpenAI key:"))
        self.key_edit = QLineEdit(self)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(
            "sk-\u2026  (stored locally on this device, never synced)"
        )
        self.key_edit.returnPressed.connect(self._on_save_key)  # type: ignore[attr-defined]
        key_layout.addWidget(self.key_edit, 1)
        self.save_key_btn = QPushButton("Save key", self)
        self.save_key_btn.clicked.connect(self._on_save_key)  # type: ignore[attr-defined]
        key_layout.addWidget(self.save_key_btn)
        layout.addWidget(self.key_row)

        # "What do you want?" — the free-text steer.
        focus_row = QHBoxLayout()
        focus_row.addWidget(QLabel("Focus:"))
        self.focus_edit = QLineEdit(self)
        self.focus_edit.setPlaceholderText(
            "what do you want more of? e.g. three-phase power, RLC resonance, "
            "op-amp gain \u2014 leave blank for a mix"
        )
        self.focus_edit.returnPressed.connect(self._on_generate)  # type: ignore[attr-defined]
        focus_row.addWidget(self.focus_edit, 1)
        layout.addLayout(focus_row)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Area:"))
        self.area_combo = QComboBox(self)
        for disp, key in _fe_areas():
            self.area_combo.addItem(disp, key)
        controls.addWidget(self.area_combo, 1)

        controls.addWidget(QLabel("Count:"))
        self.count_spin = QSpinBox(self)
        self.count_spin.setRange(1, 50)
        self.count_spin.setValue(5)
        controls.addWidget(self.count_spin)

        self.generate_btn = QPushButton("Generate", self)
        self.generate_btn.clicked.connect(self._on_generate)  # type: ignore[attr-defined]
        controls.addWidget(self.generate_btn)
        layout.addLayout(controls)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.list = QListWidget(self)
        self.list.setWordWrap(True)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select all", self)
        self.select_all_btn.clicked.connect(lambda: self._set_all(True))  # type: ignore[attr-defined]
        self.select_none_btn = QPushButton("Select none", self)
        self.select_none_btn.clicked.connect(lambda: self._set_all(False))  # type: ignore[attr-defined]
        row.addWidget(self.select_all_btn)
        row.addWidget(self.select_none_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.buttons = QDialogButtonBox(self)
        self.add_btn = self.buttons.addButton(
            "Add selected", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.close_btn = self.buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.add_btn.clicked.connect(self._on_add)  # type: ignore[attr-defined]
        self.close_btn.clicked.connect(self.close)  # type: ignore[attr-defined]
        layout.addWidget(self.buttons)

        self._update_buttons()
        self._refresh_key_row()

    # -- key row ----------------------------------------------------------- #

    def _refresh_key_row(self) -> None:
        """Show the key row only until a key is configured, then collapse it."""
        have = fe_ai.ai_available()
        self.key_row.setVisible(not have)
        if have and not self.status.text():
            self.status.setText("AI ready. Say what you want, then Generate.")

    def _on_save_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.status.setText("Paste your OpenAI key, then Save key.")
            return
        fe_ai.set_key(key)
        self.key_edit.clear()
        self._refresh_key_row()
        if fe_ai.ai_available():
            tooltip("OpenAI key saved (local, never synced).", parent=self)
            self.status.setText("AI ready. Say what you want, then Generate.")
            # Reflect availability on the dashboard/menus too.
            try:
                self.mw._update_fe_ai_actions()
            except Exception:
                pass

    # -- generation -------------------------------------------------------- #

    def _current_area(self) -> tuple[str, str]:
        disp = self.area_combo.currentText()
        key = self.area_combo.currentData() or ""
        return disp, str(key)

    def _on_generate(self) -> None:
        if self._busy:
            return
        # Let the student turn the feature on inline: if no key is configured but
        # one is typed in the key row, save it first.
        if not fe_ai.ai_available():
            typed = self.key_edit.text().strip()
            if typed:
                fe_ai.set_key(typed)
                self.key_edit.clear()
                self._refresh_key_row()
            if not fe_ai.ai_available():
                self.status.setText("Enter your OpenAI key above, then Generate.")
                self.key_edit.setFocus()
                return
        _, key = self._current_area()
        if not key:
            return
        n = self.count_spin.value()
        focus = self.focus_edit.text().strip()
        self._busy = True
        self.list.clear()
        self._candidates = []
        if focus:
            self.status.setText(
                f"Generating\u2026 (grounding on your fe::{key} cards, focused on "
                f"\u201c{focus}\u201d)"
            )
        else:
            self.status.setText(f"Generating\u2026 (grounding on your fe::{key} cards)")
        self._update_buttons()

        def task() -> list[dict]:
            # Reads the collection to build source spans, then calls the
            # provider + verifier. Never raises (returns [] on any failure).
            return fe_ai.generate_cards(key, n, focus)

        self.mw.taskman.run_in_background(task, self._on_generated, uses_collection=True)

    def _on_generated(self, fut: Future) -> None:
        self._busy = False
        try:
            cards = fut.result() or []
        except Exception:
            cards = []
        self._candidates = cards
        self._populate(cards)
        if cards:
            self.status.setText(
                f"{len(cards)} card(s) passed the verifier. Tick the ones to add."
            )
        else:
            self.status.setText(
                "No cards were generated (the AI may be unavailable, or nothing "
                "new passed the grounding verifier). Nothing was added."
            )
        self._update_buttons()

    def _populate(self, cards: list[dict]) -> None:
        self.list.clear()
        for card in cards:
            front = card.get("front", "")
            back = card.get("back", "")
            src = card.get("source_text", "")
            explanation = card.get("explanation", "")
            text = f"Q: {front}\nA: {back}"
            if explanation:
                text += f"\nExplanation: {explanation}"
            if src:
                text += f"\nSource: {src}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def _checked_cards(self) -> list[dict]:
        out: list[dict] = []
        for i in range(self.list.count()):
            if self.list.item(i).checkState() == Qt.CheckState.Checked:
                out.append(self._candidates[i])
        return out

    # -- adding ------------------------------------------------------------ #

    def _on_add(self) -> None:
        if self._busy:
            return
        cards = self._checked_cards()
        if not cards:
            tooltip("No cards ticked.", parent=self)
            return
        _, key = self._current_area()

        def op(col):
            # Prefer the deck's own styled "FE Prep" notetype so generated cards
            # look identical to the manuscript cards (topic chip, accent, MathJax);
            # fall back to Basic only if it is somehow absent.
            model = col.models.by_name("FE Prep")
            use_fe_prep = model is not None
            if model is None:
                model = col.models.by_name("Basic") or col.models.current()
            deck_id = self._target_deck_id(col, key)
            topic, accent = fe_ai.fe_prep_presentation(key)
            count = 0
            changes = None
            for card in cards:
                note = col.new_note(model)
                front = fe_ai.fe_prep_field(card.get("front", ""))
                back = fe_ai.fe_prep_field(card.get("back", ""))
                explanation = fe_ai.fe_prep_field(card.get("explanation", ""))
                names = set(note.keys())
                if use_fe_prep and {"Front", "Back"} <= names:
                    note["Front"] = front
                    note["Back"] = back
                    if "Explanation" in names:
                        note["Explanation"] = explanation
                    if "Topic" in names:
                        note["Topic"] = topic
                    if "Accent" in names:
                        note["Accent"] = accent
                else:
                    # Basic fallback: the note type has no Explanation field, so
                    # fold the worked solution into the Back so it still shows.
                    back_with_expl = back
                    if explanation:
                        back_with_expl = (
                            f"{back}<hr><b>Explanation</b><br>{explanation}"
                        )
                    fields = note.fields
                    if len(fields) >= 1:
                        note.fields[0] = front
                    if len(fields) >= 2:
                        note.fields[1] = back_with_expl
                note.tags = [f"fe::{key}", "ai::generated"]
                changes = col.add_note(note, deck_id)
                count += 1
            self._added_count = count
            return changes

        def on_success(_changes) -> None:
            n = getattr(self, "_added_count", 0)
            tooltip(f"Added {n} card(s) to the FE deck.", parent=self.mw)
            # Remove the added rows and refresh the browser/overview counts.
            self._populate([])
            self._candidates = []
            self.status.setText(f"Added {n} card(s). Generate more any time.")
            try:
                self.mw.reset()
            except Exception:
                pass

        CollectionOp(parent=self, op=op).success(on_success).run_in_background()

    @staticmethod
    def _target_deck_id(col, key: str):
        """Add into the same deck as this area's existing cards; fall back to the
        named FE parent deck, then the current/default deck."""
        try:
            cids = col.find_cards(f'"tag:fe::{key}"')
            if cids:
                return col.get_card(cids[0]).did
        except Exception:
            pass
        try:
            did = col.decks.id_for_name("FE Electrical and Computer")
            if did:
                return did
        except Exception:
            pass
        try:
            return col.decks.current()["id"]
        except Exception:
            return 1

    # -- misc -------------------------------------------------------------- #

    def _update_buttons(self) -> None:
        self.generate_btn.setEnabled(not self._busy)
        self.add_btn.setEnabled(not self._busy)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        global _generate_dialog
        _generate_dialog = None
        super().closeEvent(event)


def show_fe_ai_generate(mw) -> None:
    """Open (or raise) the generator dialog.

    Opens even when no OpenAI key is configured: the dialog shows an inline key
    row so a student can switch the feature on right there, without hunting
    through the Tools menu. Only the "no FE areas at all" case is a hard stop."""
    global _generate_dialog

    if not _fe_areas():
        showInfo("No FE areas are available.", parent=mw)
        return

    if _generate_dialog is not None:
        _generate_dialog.show()
        _generate_dialog.raise_()
        _generate_dialog.activateWindow()
        return

    _generate_dialog = FeAiGenerateDialog(mw)
    _generate_dialog.show()
    _generate_dialog.raise_()
    _generate_dialog.activateWindow()
