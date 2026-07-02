# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aqt
import aqt.operations
from anki.collection import OpChanges
from anki.scheduler import UnburyDeck
from aqt import gui_hooks
from aqt.deckdescription import DeckDescriptionDialog
from aqt.deckoptions import display_options_for_deck
from aqt.operations import QueryOp
from aqt.operations.scheduling import (
    empty_filtered_deck,
    rebuild_filtered_deck,
    unbury_deck,
)
from aqt.sound import av_player
from aqt.toolbar import BottomBar
from aqt.utils import askUserDialog, openLink, shortcut, tooltip, tr


class OverviewBottomBar:
    def __init__(self, overview: Overview) -> None:
        self.overview = overview


@dataclass
class OverviewContent:
    """Stores sections of HTML content that the overview will be
    populated with.

    Attributes:
        deck {str} -- Plain text deck name
        shareLink {str} -- HTML of the share link section
        desc {str} -- HTML of the deck description section
        table {str} -- HTML of the deck stats table section
    """

    deck: str
    shareLink: str
    desc: str
    table: str


class Overview:
    "Deck overview."

    def __init__(self, mw: aqt.AnkiQt) -> None:
        self.mw = mw
        self.web = mw.web
        self.bottom = BottomBar(mw, mw.bottomWeb)
        self._refresh_needed = False

    def show(self) -> None:
        av_player.stop_and_clear_queue()
        self.web.set_bridge_command(self._linkHandler, self)
        self.mw.setStateShortcuts(self._shortcutKeys())
        self.refresh()

    def refresh(self) -> None:
        def success(_counts: tuple) -> None:
            self._refresh_needed = False
            self._renderPage()
            self._renderBottom()
            self.mw.web.setFocus()
            gui_hooks.overview_did_refresh(self)

        QueryOp(
            parent=self.mw, op=lambda col: col.sched.counts(), success=success
        ).run_in_background()

    def refresh_if_needed(self) -> None:
        if self._refresh_needed:
            self.refresh()

    def op_executed(
        self, changes: OpChanges, handler: object | None, focused: bool
    ) -> bool:
        if changes.study_queues:
            self._refresh_needed = True

        if focused:
            self.refresh_if_needed()

        return self._refresh_needed

    # Handlers
    ############################################################

    def _linkHandler(self, url: str) -> bool:
        if url == "study":
            self.mw.col.startTimebox()
            self.mw.moveToState("review")
            if self.mw.state == "overview":
                tooltip(tr.studying_no_cards_are_due_yet())
        elif url == "anki":
            print("anki menu")
        elif url == "opts":
            display_options_for_deck(self.mw.col.decks.current())
        elif url == "cram":
            aqt.dialogs.open("FilteredDeckConfigDialog", self.mw)
        elif url == "refresh":
            self.rebuild_current_filtered_deck()
        elif url == "empty":
            self.empty_current_filtered_deck()
        elif url == "decks":
            self.mw.moveToState("deckBrowser")
        elif url == "review":
            openLink(f"{aqt.appShared}info/{self.sid}?v={self.sidVer}")
        elif url in {"studymore", "customStudy"}:
            self.onStudyMore()
        elif url == "unbury":
            self.on_unbury()
        elif url == "description":
            self.edit_description()
        elif url.lower().startswith("http"):
            openLink(url)
        return False

    def _shortcutKeys(self) -> list[tuple[str, Callable]]:
        return [
            ("o", lambda: display_options_for_deck(self.mw.col.decks.current())),
            ("r", self.rebuild_current_filtered_deck),
            ("e", self.empty_current_filtered_deck),
            ("c", self.onCustomStudyKey),
            ("u", self.on_unbury),
        ]

    def _current_deck_is_filtered(self) -> int:
        return self.mw.col.decks.current()["dyn"]

    def rebuild_current_filtered_deck(self) -> None:
        rebuild_filtered_deck(
            parent=self.mw, deck_id=self.mw.col.decks.selected()
        ).run_in_background()

    def empty_current_filtered_deck(self) -> None:
        empty_filtered_deck(
            parent=self.mw, deck_id=self.mw.col.decks.selected()
        ).run_in_background()

    def onCustomStudyKey(self) -> None:
        if not self._current_deck_is_filtered():
            self.onStudyMore()

    def on_unbury(self) -> None:
        mode = UnburyDeck.Mode.ALL
        info = self.mw.col.sched.congratulations_info()
        if info.have_sched_buried and info.have_user_buried:
            opts = [
                tr.studying_manually_buried_cards(),
                tr.studying_buried_siblings(),
                tr.studying_all_buried_cards(),
                tr.actions_cancel(),
            ]

            diag = askUserDialog(tr.studying_what_would_you_like_to_unbury(), opts)
            diag.setDefault(0)
            ret = diag.run()
            if ret == opts[0]:
                mode = UnburyDeck.Mode.USER_ONLY
            elif ret == opts[1]:
                mode = UnburyDeck.Mode.SCHED_ONLY
            elif ret == opts[3]:
                return

        unbury_deck(
            parent=self.mw, deck_id=self.mw.col.decks.get_current_id(), mode=mode
        ).run_in_background()

    onUnbury = on_unbury

    # HTML
    ############################################################

    def _renderPage(self) -> None:
        deck = self.mw.col.decks.current()
        self.sid = deck.get("sharedFrom")
        if self.sid:
            self.sidVer = deck.get("ver", None)
            shareLink = '<a class=smallLink href="review">Reviews and Updates</a>'
        else:
            shareLink = ""
        if self.mw.col.sched._is_finished():
            self._show_finished_screen()
            return
        content = OverviewContent(
            deck=deck["name"],
            shareLink=shareLink,
            desc=self._desc(deck),
            table=self._table() + self._fe_memory_score_html(),
        )
        gui_hooks.overview_will_render_content(self, content)
        content.deck = html.escape(content.deck)
        self.web.stdHtml(
            self._body % content.__dict__,
            css=["css/overview.css"],
            js=["js/vendor/jquery.min.js"],
            context=self,
        )

    def _show_finished_screen(self) -> None:
        self.web.load_sveltekit_page("congrats")

    def _desc(self, deck: dict[str, Any]) -> str:
        if deck["dyn"]:
            desc = tr.studying_this_is_a_special_deck_for()
            desc += f" {tr.studying_cards_will_be_automatically_returned_to()}"
            desc += f" {tr.studying_deleting_this_deck_from_the_deck()}"
        else:
            desc = deck.get("desc", "")
            if deck.get("md", False):
                desc = self.mw.col.render_markdown(desc)
        if not desc:
            return "<p>"
        if deck["dyn"]:
            dyn = "dyn"
        else:
            dyn = ""
        return f'<div class="descfont descmid description {dyn}">{desc}</div>'

    def _table(self) -> str:
        counts = list(self.mw.col.sched.counts())
        current_did = self.mw.col.decks.get_current_id()
        deck_node = self.mw.col.sched.deck_due_tree(current_did)

        but = self.mw.button
        if self.mw.col.v3_scheduler():
            assert deck_node is not None
            buried_new = deck_node.new_count - counts[0]
            buried_learning = deck_node.learn_count - counts[1]
            buried_review = deck_node.review_count - counts[2]
        else:
            buried_new = buried_learning = buried_review = 0
        buried_label = tr.studying_counts_differ()

        def number_row(title: str, klass: str, count: int, buried_count: int) -> str:
            buried = f"{buried_count:+}" if buried_count else ""
            return f"""
<tr>
    <td>{title}:</td>
    <td>
        <b>
            <span class={klass}>{count}</span>
            <span class=bury-count title="{buried_label}">{buried}</span>
        </b>
    </td>
</tr>
"""

        return f"""
<table width=400 cellpadding=5>
<tr><td align=center valign=top>
<table cellspacing=5>
{number_row(tr.actions_new(), "new-count", counts[0], buried_new)}
{number_row(tr.scheduling_learning(), "learn-count", counts[1], buried_learning)}
{number_row(tr.studying_to_review(), "review-count", counts[2], buried_review)}
</table>
</td><td align=center>
{but("study", tr.studying_study_now(), id="study", extra=" autofocus")}</td></tr></table>"""

    _FE_MEMORY_STYLE = """
<style>
.fe-mem{--fe-copper:#C77B3C;--fe-panel:#ffffff;--fe-line:#dce4f0;--fe-text:#182338;
  --fe-muted:#5c6a85;--fe-grid:rgba(40,64,110,.05);--fe-band:rgba(199,123,60,.20);
  max-width:460px;margin:22px auto 0;padding:18px 20px 20px;text-align:left;
  background:var(--fe-panel);border:1px solid var(--fe-line);border-radius:14px;
  background-image:linear-gradient(var(--fe-grid) 1px,transparent 1px),
    linear-gradient(90deg,var(--fe-grid) 1px,transparent 1px);background-size:22px 22px;
  box-shadow:0 12px 30px -22px rgba(8,15,30,.5);position:relative;overflow:hidden;
  font-family:system-ui,"Segoe UI",Roboto,sans-serif;}
.night_mode .fe-mem,.nightMode .fe-mem{--fe-copper:#E0A45C;--fe-panel:#15213a;
  --fe-line:#28374f;--fe-text:#e7eefa;--fe-muted:#94a2bd;
  --fe-grid:rgba(130,160,210,.06);--fe-band:rgba(224,164,92,.18);}
.fe-mem::before{content:"";position:absolute;top:0;left:0;width:44px;height:3px;
  background:var(--fe-copper);border-bottom-right-radius:3px;}
.fe-mem-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;}
.fe-mem-chip{font:600 10px/1 system-ui;letter-spacing:.16em;text-transform:uppercase;
  color:var(--fe-copper);padding:6px 11px;border-radius:999px;
  background:color-mix(in srgb,var(--fe-copper) 15%,transparent);
  border:1px solid color-mix(in srgb,var(--fe-copper) 34%,transparent);
  display:inline-flex;align-items:center;gap:7px;}
.fe-mem-chip::before{content:"";width:6px;height:6px;border-radius:2px;background:var(--fe-copper);}
.fe-mem-meta{font:600 10px/1 ui-monospace,Consolas,monospace;letter-spacing:.08em;
  text-transform:uppercase;color:var(--fe-muted);}
.fe-mem-val{display:flex;align-items:baseline;gap:10px;}
.fe-mem-pct{font:700 40px/1 ui-monospace,Consolas,monospace;color:var(--fe-copper);}
.fe-mem-cap{font:600 11px/1 system-ui;letter-spacing:.04em;color:var(--fe-muted);text-transform:uppercase;}
.fe-mem-range{font:600 13px/1 ui-monospace,Consolas,monospace;color:var(--fe-muted);}
.fe-mem-bar{position:relative;height:8px;margin:14px 0 10px;border-radius:6px;
  background:color-mix(in srgb,var(--fe-muted) 22%,transparent);}
.fe-mem-band{position:absolute;top:0;bottom:0;background:var(--fe-band);border-radius:6px;
  border:1px solid color-mix(in srgb,var(--fe-copper) 40%,transparent);}
.fe-mem-mark{position:absolute;top:-3px;width:3px;height:14px;border-radius:2px;
  background:var(--fe-copper);box-shadow:0 0 8px 1px var(--fe-copper);transform:translateX(-50%);}
.fe-mem-note{font:400 12px/1.45 system-ui;color:var(--fe-muted);margin-top:6px;}
.fe-mem-note b{color:var(--fe-text);font-weight:600;}
/* Speedrun fork: theme the whole overview page to match the dashboard */
html,body{background:#0b1220!important;color:#eaf1fc!important;
  font-family:system-ui,"Segoe UI",Roboto,sans-serif!important;}
h1,h2,h3{color:#eaf1fc!important;letter-spacing:-.01em;}
h3{font:700 26px/1.2 system-ui!important;margin-top:14px!important;}
.descfont,.description{color:#93a2bd!important;}
#outer,#header,center>table{background:transparent!important;}
center table td{color:#c7d2e6!important;font-size:15px!important;}
.new-count{color:#5C9BFF!important;}
.learn-count{color:#E7A867!important;}
.review-count{color:#5FD0C0!important;}
button#study,#study{background:linear-gradient(180deg,#ecb877,#E0A45C)!important;
  color:#2a1b08!important;border:none!important;border-radius:12px!important;
  padding:13px 30px!important;font-weight:700!important;font-size:16px!important;
  box-shadow:0 12px 28px -10px rgba(224,164,92,.6)!important;cursor:pointer;}
.fe-mem{--fe-copper:#E0A45C!important;--fe-panel:#141f36!important;--fe-line:#26344f!important;
  --fe-text:#eaf1fc!important;--fe-muted:#93a2bd!important;--fe-band:rgba(224,164,92,.18)!important;
  max-width:560px!important;box-shadow:0 18px 44px -28px rgba(0,0,0,.7)!important;}
</style>"""

    def _fe_memory_score_html(self) -> str:
        """Speedrun fork: honest memory score, shown as a range with a
        pre-registered give-up rule, never a bare single number. Styled to match
        the FE Prep card design (instrument-panel / copper readout)."""
        try:
            score = self.mw.col._backend.memory_score(search="")
        except Exception:
            return ""

        def updated(ts: int) -> str:
            if not ts:
                return "no reviews yet"
            import time

            days = (time.time() - ts) / 86400
            if days < 1 / 24:
                return "updated just now"
            if days < 1:
                return f"updated {int(days * 24)}h ago"
            return f"updated {int(days)}d ago"

        if not score.shown:
            return self._FE_MEMORY_STYLE + f"""
<div class="fe-mem">
  <div class="fe-mem-top">
    <span class="fe-mem-chip">Memory · hidden</span>
    <span class="fe-mem-meta">give-up rule</span>
  </div>
  <div class="fe-mem-cap">Not enough data yet</div>
  <div class="fe-mem-note" style="margin-top:8px;">
    <b>{score.graded_reviews}</b>/{score.min_reviews_required} graded reviews ·
    <b>{score.topics_covered}</b>/{score.min_topics_required} topics. The score
    stays hidden until the pre-registered threshold is met, so it never overstates
    what it knows.
  </div>
</div>"""

        pct = round(score.point_estimate * 100)
        low = round(score.range_low * 100)
        high = round(score.range_high * 100)
        cov = round(score.coverage * 100)
        band_left = max(0, min(100, low))
        band_right = max(0, min(100, 100 - high))
        return self._FE_MEMORY_STYLE + f"""
<div class="fe-mem">
  <div class="fe-mem-top">
    <span class="fe-mem-chip">Memory</span>
    <span class="fe-mem-meta">{updated(score.last_updated)}</span>
  </div>
  <div class="fe-mem-val">
    <span class="fe-mem-pct">{pct}%</span>
    <span class="fe-mem-cap">recall now</span>
    <span class="fe-mem-range" style="margin-left:auto;">likely {low}–{high}%</span>
  </div>
  <div class="fe-mem-bar">
    <div class="fe-mem-band" style="left:{band_left}%;right:{band_right}%;"></div>
    <div class="fe-mem-mark" style="left:{pct}%;"></div>
  </div>
  <div class="fe-mem-note">
    Based on <b>{cov}%</b> of studied material · <b>{score.graded_reviews}</b> reviews ·
    <b>{score.topics_covered}</b> topics.<br>{html.escape(score.main_reason)}
  </div>
</div>"""

    _body = """
<center>
<h3>%(deck)s</h3>
%(shareLink)s
%(desc)s
%(table)s
</center>
"""

    def edit_description(self) -> None:
        DeckDescriptionDialog(self.mw)

    # Bottom area
    ######################################################################

    def _renderBottom(self) -> None:
        links = [
            ["O", "opts", tr.actions_options()],
        ]
        is_dyn = self.mw.col.decks.current()["dyn"]
        if is_dyn:
            links.append(["R", "refresh", tr.actions_rebuild()])
            links.append(["E", "empty", tr.studying_empty()])
        else:
            links.append(["C", "studymore", tr.actions_custom_study()])
            # links.append(["F", "cram", _("Filter/Cram")])
        if self.mw.col.sched.have_buried():
            links.append(["U", "unbury", tr.studying_unbury()])
        if not is_dyn:
            links.append(["", "description", tr.scheduling_description()])
        link_handler = gui_hooks.overview_will_render_bottom(
            self._linkHandler,
            links,
        )
        if not callable(link_handler):
            link_handler = self._linkHandler
        buf = ""
        for b in links:
            if b[0]:
                b[0] = tr.actions_shortcut_key(val=shortcut(b[0]))
            buf += """
<button title="%s" onclick='pycmd("%s")'>%s</button>""" % tuple(b)
        self.bottom.draw(
            buf=buf,
            link_handler=link_handler,
            web_context=OverviewBottomBar(self),
        )

    # Studying more
    ######################################################################

    def onStudyMore(self) -> None:
        import aqt.customstudy

        aqt.customstudy.CustomStudy.fetch_data_and_show(self.mw)
