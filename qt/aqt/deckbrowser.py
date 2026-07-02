# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import html
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import aqt
import aqt.operations
from anki.collection import Collection, OpChanges, OpChangesWithId
from anki.decks import DeckCollapseScope, DeckId, DeckTreeNode, FilteredDeckConfig
from aqt import AnkiQt, gui_hooks
from aqt.deckoptions import display_options_for_deck_id
from aqt.operations import CollectionOp, QueryOp
from aqt.operations.deck import (
    add_deck_dialog,
    remove_decks,
    rename_deck,
    reparent_decks,
    set_current_deck,
    set_deck_collapsed,
)
from aqt.qt import *
from aqt.sound import av_player
from aqt.toolbar import BottomBar
from aqt.utils import getOnlyText, openLink, shortcut, showInfo, tooltip, tr


class DeckBrowserBottomBar:
    def __init__(self, deck_browser: DeckBrowser) -> None:
        self.deck_browser = deck_browser


@dataclass
class RenderData:
    """Data from collection that is required to show the page."""

    tree: DeckTreeNode
    current_deck_id: DeckId
    studied_today: str
    sched_upgrade_required: bool


@dataclass
class DeckBrowserContent:
    """Stores sections of HTML content that the deck browser will be
    populated with.

    Attributes:
        tree {str} -- HTML of the deck tree section
        stats {str} -- HTML of the stats section
    """

    tree: str
    stats: str


@dataclass
class RenderDeckNodeContext:
    current_deck_id: DeckId


class DeckBrowser:
    _render_data: RenderData

    def __init__(self, mw: AnkiQt) -> None:
        self.mw = mw
        self.web = mw.web
        self.bottom = BottomBar(mw, mw.bottomWeb)
        self.scrollPos = QPoint(0, 0)
        self._refresh_needed = False

    def show(self) -> None:
        av_player.stop_and_clear_queue()
        self.web.set_bridge_command(self._linkHandler, self)
        # redraw top bar for theme change
        self.mw.toolbar.redraw()
        self.refresh()

    def refresh(self) -> None:
        self._renderPage()
        self._refresh_needed = False

    def refresh_if_needed(self) -> None:
        if self._refresh_needed:
            self.refresh()

    def op_executed(
        self, changes: OpChanges, handler: object | None, focused: bool
    ) -> bool:
        if changes.study_queues and handler is not self:
            self._refresh_needed = True

        if focused:
            self.refresh_if_needed()

        return self._refresh_needed

    # Event handlers
    ##########################################################################

    def _linkHandler(self, url: str) -> Any:
        if ":" in url:
            (cmd, arg) = url.split(":", 1)
        else:
            cmd = url
            arg = ""
        if cmd == "open":
            self.set_current_deck(DeckId(int(arg)))
        elif cmd == "festudy":
            set_current_deck(
                parent=self.mw, deck_id=DeckId(int(arg))
            ).success(lambda _: self.mw.moveToState("review")).run_in_background(
                initiator=self
            )
        elif cmd == "festrack":
            self._fe_start_track(arg)
        elif cmd == "fesetpolicy":
            self._fe_cycle_policy(arg)
        elif cmd == "opts":
            self._showOptions(arg)
        elif cmd == "shared":
            self._onShared()
        elif cmd == "import":
            self.mw.onImport()
        elif cmd == "create":
            self._on_create()
        elif cmd == "drag":
            source, target = arg.split(",")
            self._handle_drag_and_drop(DeckId(int(source)), DeckId(int(target or 0)))
        elif cmd == "collapse":
            self._collapse(DeckId(int(arg)))
        elif cmd == "v2upgrade":
            self._confirm_upgrade()
        elif cmd == "v2upgradeinfo":
            if self.mw.col.sched_ver() == 1:
                openLink("https://faqs.ankiweb.net/the-anki-2.1-scheduler.html")
            else:
                openLink("https://faqs.ankiweb.net/the-2021-scheduler.html")
        elif cmd == "select":
            set_current_deck(
                parent=self.mw, deck_id=DeckId(int(arg))
            ).run_in_background()
        return False

    def set_current_deck(self, deck_id: DeckId) -> None:
        set_current_deck(parent=self.mw, deck_id=deck_id).success(
            lambda _: self.mw.onOverview()
        ).run_in_background(initiator=self)

    def _fe_start_track(self, track: str) -> None:
        """Build (or rebuild) the native filtered deck for a whole track
        (durable/cram) and drop the user into review.

        Uses the v3 filtered-deck API: col.sched.get_or_create_filtered_deck()
        to fetch a FilteredDeckForUpdate (id 0 => new template), we point its
        single search term at the track's fe::<key> tags, then
        col.sched.add_or_update_filtered_deck() which normalizes the search,
        adds/updates the deck, rebuilds it (re-pulling current cards) and sets
        it as the current deck. Defensive: any failure shows a tooltip and
        refreshes back to the dashboard instead of crashing."""
        track = (track or "").strip().lower()
        deck_name = self.FE_TRACK_DECK_NAMES.get(track)
        if not deck_name:
            return

        policy = self._fe_track_policy(self.mw.col)
        search = self._fe_track_search(policy, self.FE_TAG_KEYS, track)
        if not search:
            tooltip("No areas found for this track.", parent=self.mw)
            return

        def op(col: Collection) -> OpChangesWithId:
            existing = col.decks.id_for_name(deck_name)
            deck = col.sched.get_or_create_filtered_deck(
                deck_id=existing or DeckId(0)
            )
            deck.name = deck_name
            config = deck.config
            # Durable vs. Cram differ here, and only here, in whether the
            # session writes back to the FSRS schedule:
            #   durable -> reschedule on. Answers update memory state and push
            #     intervals out, so the material is learned for keeps.
            #   cram    -> reschedule off. The drill lets the candidate peak
            #     these cards for test day without touching real scheduling or
            #     memory, so nothing durable is built and they decay naturally
            #     after the exam ("good now, forget later").
            config.reschedule = track == "durable"
            del config.search_terms[:]
            config.search_terms.append(
                FilteredDeckConfig.SearchTerm(
                    search=search,
                    limit=9999,
                    order=FilteredDeckConfig.SearchTerm.Order.RANDOM,
                )
            )
            # Adds/updates, rebuilds (re-pulling current cards) and sets the
            # deck current, returning OpChangesWithId so UI change
            # notifications fire properly.
            return col.sched.add_or_update_filtered_deck(deck)

        def success(_out: OpChangesWithId) -> None:
            self.mw.moveToState("review")

        def failure(exc: Exception) -> None:
            tooltip(f"Couldn't build track: {exc}", parent=self.mw)
            self.refresh()

        CollectionOp(parent=self.mw, op=op).success(success).failure(
            failure
        ).run_in_background()

    # Durable vs. Cram track policy (per-area, user-overridable)
    ##########################################################################

    # Per-area policy values:
    #   "split"   -> route each card by its own track:: tag (durable unless the
    #                card is tagged track::cram). Day-to-day core areas default
    #                here: the real worked problems are durable, a few formulas
    #                and definitions are cram.
    #   "durable" -> study the whole area durable (learn for keeps).
    #   "cram"    -> study the whole area cram (peak for test day, decay after).
    # Defaults come from FE_DURABLE. The user can move any section; the override
    # is stored per fe:: key in the collection config under "feTrackPolicy".
    _FE_POLICY_CYCLE = ("split", "durable", "cram")

    def _fe_default_policy(self, disp: str) -> str:
        return "split" if disp in self.FE_DURABLE else "cram"

    def _fe_track_policy(self, col: Collection) -> dict[str, str]:
        """Resolve display-name -> policy, merging user overrides over defaults."""
        try:
            overrides = col.get_config("feTrackPolicy", {}) or {}
        except Exception:
            overrides = {}
        policy: dict[str, str] = {}
        for disp, key in self.FE_TAG_KEYS.items():
            pol = overrides.get(key, self._fe_default_policy(disp))
            if pol not in self._FE_POLICY_CYCLE:
                pol = self._fe_default_policy(disp)
            policy[disp] = pol
        return policy

    @staticmethod
    def _fe_track_search(
        policy: dict[str, str], tag_keys: dict[str, str], track: str
    ) -> str:
        """Build the Anki search selecting one track's cards.

        Pure (no collection access) so it can be unit-tested. In a "split" area a
        card is cram only when explicitly tagged track::cram; everything else
        (including untagged legacy cards) defaults to durable.
        """
        want_durable = track == "durable"
        terms: list[str] = []
        for disp, key in tag_keys.items():
            pol = policy.get(disp, "cram")
            if pol == "split":
                if want_durable:
                    terms.append(f"(tag:fe::{key} -tag:track::cram)")
                else:
                    terms.append(f"(tag:fe::{key} tag:track::cram)")
            elif (pol == "durable") == want_durable:
                terms.append(f"tag:fe::{key}")
        if not terms:
            return ""
        return "(" + " OR ".join(terms) + ")"

    def _fe_cycle_policy(self, key: str) -> None:
        """Advance one area's track policy (split -> durable -> cram -> split)."""
        key = (key or "").strip()
        disp = next((d for d, k in self.FE_TAG_KEYS.items() if k == key), None)
        if disp is None:
            return

        def op(col: Collection) -> OpChanges:
            pol = dict(col.get_config("feTrackPolicy", {}) or {})
            cur = pol.get(key, self._fe_default_policy(disp))
            if cur not in self._FE_POLICY_CYCLE:
                cur = self._fe_default_policy(disp)
            nxt = self._FE_POLICY_CYCLE[
                (self._FE_POLICY_CYCLE.index(cur) + 1) % len(self._FE_POLICY_CYCLE)
            ]
            pol[key] = nxt
            return col.set_config("feTrackPolicy", pol)

        CollectionOp(parent=self.mw, op=op).success(
            lambda _: self.refresh()
        ).run_in_background()

    # HTML generation
    ##########################################################################

    _body = """
<center>
<table cellspacing=0 cellpadding=3>
%(tree)s
</table>

<br>
%(stats)s
</center>
"""

    def _renderPage(self, reuse: bool = False) -> None:
        if not reuse:

            def get_data(col: Collection) -> RenderData:
                return RenderData(
                    tree=col.sched.deck_due_tree(),
                    current_deck_id=col.decks.get_current_id(),
                    studied_today=col.studied_today(),
                    sched_upgrade_required=not col.v3_scheduler(),
                )

            def success(output: RenderData) -> None:
                self._render_data = output
                self.__renderPage(None)

            QueryOp(
                parent=self.mw,
                op=get_data,
                success=success,
            ).run_in_background()
        else:
            self.web.evalWithCallback("window.pageYOffset", self.__renderPage)

    def __renderPage(self, offset: int | None) -> None:
        data = self._render_data
        content = DeckBrowserContent(
            tree=self._renderDeckTree(data.tree),
            stats=self._renderStats(),
        )
        gui_hooks.deck_browser_will_render_content(self, content)
        dash = self._fe_dashboard_html()
        if dash:
            body = self._v1_upgrade_message(data.sched_upgrade_required) + dash
        else:
            body = (
                self._v1_upgrade_message(data.sched_upgrade_required)
                + self._body % content.__dict__
            )
        self.web.stdHtml(
            body,
            css=["css/deckbrowser.css"],
            js=[
                "js/vendor/jquery.min.js",
                "js/vendor/jquery-ui.min.js",
                "js/deckbrowser.js",
            ],
            context=self,
        )
        self._drawButtons()
        if offset is not None:
            self._scrollToOffset(offset)
        gui_hooks.deck_browser_did_render(self)

    def _scrollToOffset(self, offset: int) -> None:
        self.web.eval("window.scrollTo(0, %d, 'instant');" % offset)

    def _renderStats(self) -> str:
        return '<div id="studiedToday"><span>{}</span></div>'.format(
            self._render_data.studied_today
        )

    # Speedrun fork: full-page study dashboard + honest memory score
    ##########################################################################

    # (display name, chip accent, NCEES question-count range) in points-at-stake
    # weight order (heaviest exam areas first).
    FE_TOPICS = [
        ("Mathematics", "#5C9BFF", "11-17"),
        ("Circuit Analysis", "#E7A867", "10-15"),
        ("Power Systems", "#F2849E", "8-12"),
        ("Electronics", "#C58BF2", "7-11"),
        ("Digital Systems", "#5FD0C0", "7-11"),
        ("Engineering Sciences", "#D9A066", "6-9"),
        ("Control Systems", "#6FA8DC", "6-9"),
        ("Signal Processing", "#7FB2F0", "5-8"),
        ("Linear Systems", "#9AA7F0", "5-8"),
        ("Electromagnetics", "#8FD0A0", "5-8"),
        ("Communications", "#E0A45C", "5-8"),
        ("Computer Systems", "#7FC8D8", "4-6"),
        ("Probability & Statistics", "#8FBCE0", "4-6"),
        ("Electrical Materials", "#C79AD8", "4-6"),
        ("Computer Networks", "#6FB0C8", "4-6"),
        ("Software Development", "#B7A6F0", "4-6"),
        ("Engineering Economics", "#D8B36A", "3-5"),
        ("Ethics", "#8AC0A8", "3-5"),
    ]

    # "Durable vs. Cram" study strategy. DURABLE = field areas a candidate will
    # use in their career and should learn for keeps (deeper encoding, schedule
    # to last). Anything not listed here falls into CRAM = outside their field,
    # studied to peak on the test date and allowed to decay after. This is a
    # sensible engineering-heavy default until per-user field mapping exists.
    FE_DURABLE = {
        "Mathematics",
        "Circuit Analysis",
        "Power Systems",
        "Electronics",
        "Electromagnetics",
        "Control Systems",
        "Signal Processing",
        "Linear Systems",
        "Engineering Sciences",
    }

    # Exact map from FE_TOPICS display name -> the fe::<snake_key> tag suffix used
    # on the cards. Kept explicit (rather than derived) so the filtered-deck
    # search always matches, even where the display name diverges from the key
    # (e.g. "Electrical Materials" -> properties_of_electrical_materials).
    FE_TAG_KEYS = {
        "Mathematics": "mathematics",
        "Circuit Analysis": "circuit_analysis",
        "Power Systems": "power_systems",
        "Electronics": "electronics",
        "Digital Systems": "digital_systems",
        "Engineering Sciences": "engineering_sciences",
        "Control Systems": "control_systems",
        "Signal Processing": "signal_processing",
        "Linear Systems": "linear_systems",
        "Electromagnetics": "electromagnetics",
        "Communications": "communications",
        "Computer Systems": "computer_systems",
        "Probability & Statistics": "probability_statistics",
        "Electrical Materials": "properties_of_electrical_materials",
        "Computer Networks": "computer_networks",
        "Software Development": "software_development",
        "Engineering Economics": "engineering_economics",
        "Ethics": "ethics",
    }

    # Reused native filtered (dynamic) deck names, one per track. Get-or-create
    # by name and rebuild each time so the session re-pulls current cards.
    FE_TRACK_DECK_NAMES = {
        "durable": "FE Track: Durable",
        "cram": "FE Track: Cram",
    }

    _FE_DASH_STYLE = """
<style>
html,body{background:#0b1220!important;margin:0!important;padding:0!important;}
.fe-dash *{box-sizing:border-box;}
.fe-dash{--copper:#E0A45C;--bg:#0b1220;--panel:#141f36;--panel2:#0f1830;--line:#26344f;
  --text:#eaf1fc;--muted:#93a2bd;--grid:rgba(130,160,210,.05);
  font-family:system-ui,"Segoe UI",Roboto,-apple-system,sans-serif;color:var(--text);
  max-width:1080px;margin:0 auto;padding:34px 26px 60px;text-align:left;
  -webkit-font-smoothing:antialiased;}
.night_mode .fe-dash,.nightMode .fe-dash{}
/* HERO */
.fe-hero{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:20px;
  padding:34px 36px 32px;margin-bottom:22px;
  background:radial-gradient(120% 140% at 100% 0%, rgba(224,164,92,.10), transparent 55%),
    linear-gradient(180deg, var(--panel), var(--panel2));
  background-image:radial-gradient(120% 140% at 100% 0%, rgba(224,164,92,.12), transparent 55%),
    linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,26px 26px,26px 26px;box-shadow:0 24px 60px -30px rgba(0,0,0,.7);}
.fe-hero::before{content:"";position:absolute;top:0;left:0;width:70px;height:4px;background:var(--copper);border-bottom-right-radius:4px;}
.fe-eyebrow{font:600 11px/1 ui-monospace,Consolas,monospace;letter-spacing:.22em;text-transform:uppercase;color:var(--copper);}
.fe-title{font:700 clamp(28px,3vw,44px)/1.08 system-ui;letter-spacing:-.02em;margin:16px 0 0;color:var(--text);}
.fe-title-num{font-family:ui-monospace,Consolas,monospace;color:var(--copper);}
.fe-lede{font:400 15px/1.55 system-ui;color:var(--muted);max-width:660px;margin:14px 0 0;}
.fe-hero-actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px;}
.fe-btn{display:inline-flex;align-items:center;gap:6px;cursor:pointer;text-decoration:none;
  font:600 15px/1 system-ui;padding:14px 22px;border-radius:12px;transition:transform .08s ease,filter .15s ease;}
.fe-btn:hover{transform:translateY(-1px);}
.fe-btn-primary{background:linear-gradient(180deg,#ecb877,var(--copper));color:#2a1b08;
  box-shadow:0 10px 26px -10px rgba(224,164,92,.6);}
.fe-btn-primary:hover{filter:brightness(1.05);}
.fe-btn-ghost{background:transparent;color:var(--text);border:1px solid var(--line);}
.fe-btn-ghost:hover{border-color:var(--copper);color:var(--copper);}
.fe-btn-badge{font:600 12px/1 ui-monospace,Consolas,monospace;padding:4px 8px;border-radius:999px;
  background:rgba(0,0,0,.16);}
/* MEMORY PANEL (reuses fe-mem-*) */
.fe-dash .fe-mem{--fe-copper:var(--copper);--fe-panel:var(--panel);--fe-line:var(--line);
  --fe-text:var(--text);--fe-muted:var(--muted);--fe-band:rgba(224,164,92,.18);
  max-width:none;margin:0 0 26px;padding:20px 24px;position:relative;overflow:hidden;text-align:left;
  background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:0 18px 44px -28px rgba(0,0,0,.7);}
.fe-dash .fe-mem::before{content:"";position:absolute;top:0;left:0;width:52px;height:3px;background:var(--copper);border-bottom-right-radius:3px;}
.fe-mem-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}
.fe-mem-chip{font:600 10px/1 system-ui;letter-spacing:.16em;text-transform:uppercase;color:var(--copper);
  padding:6px 11px;border-radius:999px;background:color-mix(in srgb,var(--copper) 16%,transparent);
  border:1px solid color-mix(in srgb,var(--copper) 36%,transparent);display:inline-flex;align-items:center;gap:7px;}
.fe-mem-chip::before{content:"";width:6px;height:6px;border-radius:2px;background:var(--copper);}
.fe-mem-meta{font:600 10px/1 ui-monospace,Consolas,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);}
.fe-mem-val{display:flex;align-items:baseline;gap:12px;}
.fe-mem-pct{font:700 44px/1 ui-monospace,Consolas,monospace;color:var(--copper);}
.fe-mem-cap{font:600 12px/1 system-ui;letter-spacing:.04em;color:var(--muted);text-transform:uppercase;}
.fe-mem-range{font:600 15px/1 ui-monospace,Consolas,monospace;color:var(--muted);}
.fe-mem-bar{position:relative;height:9px;margin:15px 0 10px;border-radius:6px;background:color-mix(in srgb,var(--muted) 22%,transparent);}
.fe-mem-band{position:absolute;top:0;bottom:0;background:var(--fe-band);border-radius:6px;border:1px solid color-mix(in srgb,var(--copper) 40%,transparent);}
.fe-mem-mark{position:absolute;top:-3px;width:3px;height:15px;border-radius:2px;background:var(--copper);box-shadow:0 0 8px 1px var(--copper);transform:translateX(-50%);}
.fe-mem-note{font:400 13px/1.5 system-ui;color:var(--muted);margin-top:6px;}
.fe-mem-note b{color:var(--text);font-weight:600;}
/* TOPIC GRID */
.fe-sec-label{display:flex;align-items:baseline;justify-content:space-between;margin:6px 4px 14px;}
.fe-sec-label>span:first-child{font:700 15px/1 system-ui;letter-spacing:.01em;color:var(--text);display:inline-flex;align-items:center;}
.fe-sec-note{font:600 11px/1 ui-monospace,Consolas,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);}
.fe-sec-right{display:inline-flex;align-items:center;gap:14px;flex:none;}
.fe-track-btn{display:inline-flex;align-items:center;gap:6px;cursor:pointer;text-decoration:none;white-space:nowrap;
  font:600 11px/1 ui-monospace,Consolas,monospace;letter-spacing:.1em;text-transform:uppercase;
  color:var(--copper);background:transparent;padding:8px 13px;border-radius:999px;
  border:1px solid color-mix(in srgb,var(--copper) 55%,transparent);
  transition:background .15s ease,color .15s ease,border-color .15s ease,transform .08s ease;}
.fe-track-btn::before{content:"";width:6px;height:6px;border-radius:2px;background:currentColor;flex:none;}
.fe-track-btn:hover{background:var(--copper);border-color:var(--copper);color:#2a1b08;transform:translateY(-1px);}
.fe-sec-dot{width:9px;height:9px;border-radius:3px;display:inline-block;margin-right:9px;flex:none;}
.fe-sec-dot--durable{background:#E0A45C;box-shadow:0 0 0 4px rgba(224,164,92,.18);}
.fe-sec-dot--cram{background:#7FB2F0;box-shadow:0 0 0 4px rgba(127,178,240,.18);}
.fe-sec-desc{font:400 13px/1.5 system-ui;color:var(--muted);margin:-8px 4px 16px;max-width:660px;}
.fe-sec-group{margin-bottom:30px;}
.fe-sec-group:last-child{margin-bottom:0;}
.fe-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:14px;}
.fe-tile{--chip:var(--copper);display:block;cursor:pointer;text-decoration:none;position:relative;overflow:hidden;
  background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px 15px;
  transition:transform .08s ease,border-color .15s ease,box-shadow .15s ease;}
.fe-tile:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--chip) 60%,var(--line));
  box-shadow:0 16px 34px -22px rgba(0,0,0,.8);}
.fe-tile::before{content:"";position:absolute;top:0;left:0;width:34px;height:3px;background:var(--chip);border-bottom-right-radius:3px;}
.fe-tile-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:11px;}
.fe-tile-dot{width:9px;height:9px;border-radius:3px;background:var(--chip);box-shadow:0 0 0 4px color-mix(in srgb,var(--chip) 20%,transparent);}
.fe-tile-q{font:600 10px/1 ui-monospace,Consolas,monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);}
.fe-tile-name{font:650 16px/1.25 system-ui;font-weight:650;color:var(--text);letter-spacing:-.01em;}
.fe-tile-meta{display:flex;align-items:center;justify-content:space-between;margin-top:12px;}
.fe-tile-count{font:600 13px/1 ui-monospace,Consolas,monospace;color:var(--chip);}
.fe-tile-due{font:500 12px/1 system-ui;color:var(--muted);}
.fe-tile-studied{margin-top:11px;padding-top:10px;
  border-top:1px solid color-mix(in srgb,var(--chip) 22%,transparent);
  font:600 12px/1 system-ui;color:var(--chip);display:flex;align-items:center;gap:7px;}
.fe-tile-studied::before{content:"";width:6px;height:6px;border-radius:50%;
  background:var(--chip);box-shadow:0 0 6px 1px var(--chip);flex:none;}
.fe-tile-track{margin-top:11px;display:flex;justify-content:flex-end;}
.fe-tile-move{cursor:pointer;user-select:none;font:600 10px/1 ui-monospace,Consolas,monospace;
  letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  border:1px solid var(--line);border-radius:999px;padding:5px 10px;
  transition:color .15s ease,border-color .15s ease,background .15s ease;}
.fe-tile-move:hover{color:var(--copper);border-color:color-mix(in srgb,var(--copper) 60%,transparent);
  background:color-mix(in srgb,var(--copper) 12%,transparent);}
.fe-tile-move::before{content:"\\21C4  ";opacity:.8;}
</style>"""

    def _fe_dashboard_html(self) -> str:
        col = self.mw.col
        parent_name = "FE Electrical and Computer"
        try:
            total = len(col.find_cards('"tag:fe::*"'))
        except Exception:
            return ""
        if not total:
            return ""
        parent_node = None
        for ch in getattr(self._render_data.tree, "children", []):
            if ch.name == parent_name:
                parent_node = ch
                break
        if parent_node is None:
            return ""
        parent_new = parent_node.new_count
        parent_due = parent_node.review_count + parent_node.learn_count
        subs = {ch.name: ch for ch in parent_node.children}

        policy = self._fe_track_policy(col)
        durable_tiles = ""
        cram_tiles = ""
        covered = 0
        for disp, accent, qrange in self.FE_TOPICS:
            node = subs.get(disp)
            if not node:
                continue
            covered += 1
            key = self.FE_TAG_KEYS.get(disp)
            try:
                cnt = len(col.find_cards(f'"deck:{parent_name}::{disp}"'))
            except Exception:
                cnt = 0
            # Cards of this topic reviewed today. Keyed on the fe:: tag rather
            # than the deck so it stays correct no matter how the card was
            # reached -- "Study all" on the parent, the topic deck directly, or
            # a filtered track deck (where the card's deck is temporarily the
            # filtered one but its tag is unchanged). rated:1 = last day/rollover.
            studied = 0
            if key:
                try:
                    studied = len(col.find_cards(f'"tag:fe::{key}" rated:1'))
                except Exception:
                    studied = 0
            new = node.new_count
            due = node.review_count + node.learn_count
            due_txt = f" &middot; {due} due" if due else ""
            studied_html = (
                f'<div class="fe-tile-studied">{studied} studied today</div>'
                if studied
                else ""
            )
            pol = policy.get(disp, "cram")
            move_html = (
                f'<span class="fe-tile-move" title="Move this section between tracks '
                f'(split \u2192 durable \u2192 cram)" onclick="event.stopPropagation();'
                f"event.preventDefault();return pycmd('fesetpolicy:{key}')\">{pol}</span>"
                if key
                else ""
            )
            tile = f"""<a class="fe-tile" style="--chip:{accent};" onclick="return pycmd('open:{node.deck_id}')">
  <div class="fe-tile-top"><span class="fe-tile-dot"></span><span class="fe-tile-q">{qrange} Q</span></div>
  <div class="fe-tile-name">{html.escape(disp)}</div>
  <div class="fe-tile-meta"><span class="fe-tile-count">{cnt} cards</span><span class="fe-tile-due">{new} new{due_txt}</span></div>
  {studied_html}
  <div class="fe-tile-track">{move_html}</div>
</a>"""
            if pol == "cram":
                cram_tiles += tile
            else:
                durable_tiles += tile

        parent_badge = f"{parent_new} new" + (f" &middot; {parent_due} due" if parent_due else "")
        hero = f"""<header class="fe-hero">
  <div class="fe-eyebrow">FE &middot; Electrical &amp; Computer &middot; study fork &middot; AI-free</div>
  <h1 class="fe-title"><span class="fe-title-num">{total}</span> verified cards, one exam.</h1>
  <p class="fe-lede">Every card is a checked FE fact or worked problem, tagged to one of the {covered} NCEES knowledge areas. Drill the whole exam at once, or target a single area &mdash; laid out in points-at-stake order (exam weight first).</p>
  <div class="fe-hero-actions">
    <a class="fe-btn fe-btn-primary" onclick="return pycmd('festudy:{parent_node.deck_id}')">Study all&nbsp;&nbsp;<span class="fe-btn-badge">{parent_badge}</span></a>
    <a class="fe-btn fe-btn-ghost" onclick="return pycmd('open:{parent_node.deck_id}')">Deck overview</a>
  </div>
</header>"""

        try:
            mem = self._fe_mem_panel(col._backend.memory_score(search=""))
        except Exception:
            mem = ""

        groups = ""
        if durable_tiles:
            groups += f"""<div class="fe-sec-group">
  <div class="fe-sec-label"><span><span class="fe-sec-dot fe-sec-dot--durable"></span>Durable &middot; learn for keeps</span><span class="fe-sec-right"><span class="fe-sec-note">reschedules &middot; builds long-term memory</span><a class="fe-track-btn" onclick="return pycmd('festrack:durable')">Study durable track</a></span></div>
  <div class="fe-sec-desc">Field areas worth deep, lasting practice.</div>
  <div class="fe-grid">{durable_tiles}</div>
</div>"""
        if cram_tiles:
            groups += f"""<div class="fe-sec-group">
  <div class="fe-sec-label"><span><span class="fe-sec-dot fe-sec-dot--cram"></span>Cram &middot; peak for test day</span><span class="fe-sec-right"><span class="fe-sec-note">test-day drill &middot; won't reschedule</span><a class="fe-track-btn" onclick="return pycmd('festrack:cram')">Study cram track</a></span></div>
  <div class="fe-sec-desc">Outside your field &mdash; drilled to peak on exam day, decay after.</div>
  <div class="fe-grid">{cram_tiles}</div>
</div>"""
        grid = f'<section class="fe-topics">{groups}</section>'

        return self._FE_DASH_STYLE + f'<div class="fe-dash">{hero}{mem}{grid}</div>'

    def _fe_mem_panel(self, score: Any) -> str:
        if not getattr(score, "shown", False):
            return f"""
<div class="fe-mem">
  <div class="fe-mem-top"><span class="fe-mem-chip">Memory &middot; hidden</span>
    <span class="fe-mem-meta">give-up rule</span></div>
  <div class="fe-mem-note"><b>Not enough data yet.</b> {score.graded_reviews}/{score.min_reviews_required}
    graded reviews &middot; {score.topics_covered}/{score.min_topics_required} topics. The score stays hidden until
    the pre-registered threshold is met, so it never overstates what it knows.</div>
</div>"""
        pct = round(score.point_estimate * 100)
        low = round(score.range_low * 100)
        high = round(score.range_high * 100)
        cov = round(score.coverage * 100)
        band_left = max(0, min(100, low))
        band_right = max(0, min(100, 100 - high))
        return f"""
<div class="fe-mem">
  <div class="fe-mem-top"><span class="fe-mem-chip">Memory</span>
    <span class="fe-mem-meta">recall now</span></div>
  <div class="fe-mem-val"><span class="fe-mem-pct">{pct}%</span>
    <span class="fe-mem-range" style="margin-left:auto;">likely {low}&ndash;{high}%</span></div>
  <div class="fe-mem-bar"><div class="fe-mem-band" style="left:{band_left}%;right:{band_right}%;"></div>
    <div class="fe-mem-mark" style="left:{pct}%;"></div></div>
  <div class="fe-mem-note">Based on <b>{cov}%</b> of studied material &middot; <b>{score.graded_reviews}</b> reviews
    &middot; <b>{score.topics_covered}</b> topics.<br>{html.escape(score.main_reason)}</div>
</div>"""

    def _renderDeckTree(self, top: DeckTreeNode) -> str:
        buf = """
<tr><th colspan=5 align=start>{}</th>
<th class=count>{}</th>
<th class=count>{}</th>
<th class=count>{}</th>
<th class=optscol></th></tr>""".format(
            tr.decks_deck(),
            tr.actions_new(),
            tr.decks_learn_header(),
            tr.decks_review_header(),
        )
        buf += self._topLevelDragRow()

        ctx = RenderDeckNodeContext(current_deck_id=self._render_data.current_deck_id)

        for child in top.children:
            buf += self._render_deck_node(child, ctx)

        return buf

    def _render_deck_node(self, node: DeckTreeNode, ctx: RenderDeckNodeContext) -> str:
        if node.collapsed:
            prefix = "+"
        else:
            prefix = "−"

        def indent() -> str:
            return "&nbsp;" * 6 * (node.level - 1)

        if node.deck_id == ctx.current_deck_id:
            klass = "deck current"
        else:
            klass = "deck"

        buf = (
            "<tr class='%s' id='%d' onclick='if(event.shiftKey) return pycmd(\"select:%d\")'>"
            % (
                klass,
                node.deck_id,
                node.deck_id,
            )
        )
        # deck link
        if node.children:
            collapse = (
                "<a class=collapse href=# onclick='return pycmd(\"collapse:%d\")'>%s</a>"
                % (node.deck_id, prefix)
            )
        else:
            collapse = "<span class=collapse></span>"
        if node.filtered:
            extraclass = "filtered"
        else:
            extraclass = ""
        buf += """

        <td class=decktd colspan=5>%s%s<a class="deck %s"
        href=# onclick="return pycmd('open:%d')">%s</a></td>""" % (
            indent(),
            collapse,
            extraclass,
            node.deck_id,
            html.escape(node.name),
        )

        # due counts
        def nonzeroColour(cnt: int, klass: str) -> str:
            if not cnt:
                klass = "zero-count"
            return f'<span class="{klass}">{cnt}</span>'

        review = nonzeroColour(node.review_count, "review-count")
        learn = nonzeroColour(node.learn_count, "learn-count")

        buf += ("<td align=end>%s</td>" * 3) % (
            nonzeroColour(node.new_count, "new-count"),
            learn,
            review,
        )
        # options
        buf += (
            "<td align=center class=opts><a onclick='return pycmd(\"opts:%d\");'>"
            "<img src='/_anki/imgs/gears.svg' class=gears></a></td></tr>" % node.deck_id
        )
        # children
        if not node.collapsed:
            for child in node.children:
                buf += self._render_deck_node(child, ctx)
        return buf

    def _topLevelDragRow(self) -> str:
        return "<tr class='top-level-drag-row'><td colspan='6'>&nbsp;</td></tr>"

    # Options
    ##########################################################################

    def _showOptions(self, did: str) -> None:
        m = QMenu(self.mw)
        a = m.addAction(tr.actions_rename())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._rename(DeckId(int(did))))
        a = m.addAction(tr.actions_options())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._options(DeckId(int(did))))
        a = m.addAction(tr.actions_export())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._export(DeckId(int(did))))
        a = m.addAction(tr.actions_delete())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._delete(DeckId(int(did))))
        gui_hooks.deck_browser_will_show_options_menu(m, int(did))
        m.popup(QCursor.pos())

    def _export(self, did: DeckId) -> None:
        self.mw.onExport(did=did)

    def _rename(self, did: DeckId) -> None:
        def prompt(name: str) -> None:
            new_name = getOnlyText(
                tr.decks_new_deck_name(), default=name, title=tr.actions_rename()
            )
            if not new_name or new_name == name:
                return
            else:
                rename_deck(
                    parent=self.mw, deck_id=did, new_name=new_name
                ).run_in_background()

        QueryOp(
            parent=self.mw, op=lambda col: col.decks.name(did), success=prompt
        ).run_in_background()

    def _options(self, did: DeckId) -> None:
        display_options_for_deck_id(did)

    def _collapse(self, did: DeckId) -> None:
        node = self.mw.col.decks.find_deck_in_tree(self._render_data.tree, did)
        if node:
            node.collapsed = not node.collapsed
            set_deck_collapsed(
                parent=self.mw,
                deck_id=did,
                collapsed=node.collapsed,
                scope=DeckCollapseScope.REVIEWER,
            ).run_in_background()
            self._renderPage(reuse=True)

    def _handle_drag_and_drop(self, source: DeckId, target: DeckId) -> None:
        reparent_decks(
            parent=self.mw, deck_ids=[source], new_parent=target
        ).run_in_background()

    def _delete(self, did: DeckId) -> None:
        deck = self.mw.col.decks.find_deck_in_tree(self._render_data.tree, did)
        assert deck is not None
        deck_name = deck.name
        remove_decks(
            parent=self.mw, deck_ids=[did], deck_name=deck_name
        ).run_in_background()

    # Top buttons
    ######################################################################

    drawLinks = [
        ["", "shared", tr.decks_get_shared()],
        ["", "create", tr.decks_create_deck()],
        ["Ctrl+Shift+I", "import", tr.decks_import_file()],
    ]

    def _drawButtons(self) -> None:
        buf = ""
        drawLinks = deepcopy(self.drawLinks)
        for b in drawLinks:
            if b[0]:
                b[0] = tr.actions_shortcut_key(val=shortcut(b[0]))
            buf += """
<button title='%s' onclick='pycmd(\"%s\");'>%s</button>""" % tuple(b)
        self.bottom.draw(
            buf=buf,
            link_handler=self._linkHandler,
            web_context=DeckBrowserBottomBar(self),
        )

    def _onShared(self) -> None:
        openLink(f"{aqt.appShared}decks/")

    def _on_create(self) -> None:
        if op := add_deck_dialog(
            parent=self.mw, default_text=self.mw.col.decks.current()["name"]
        ):
            op.run_in_background()

    ######################################################################

    def _v1_upgrade_message(self, required: bool) -> str:
        if not required:
            return ""

        update_required = tr.scheduling_update_required().replace("V2", "v3")

        return f"""
<center>
<div class=callout>
    <div>
      {update_required}
    </div>
    <div>
      <button onclick='pycmd("v2upgrade")'>
        {tr.scheduling_update_button()}
      </button>
      <button onclick='pycmd("v2upgradeinfo")'>
        {tr.scheduling_update_more_info_button()}
      </button>
    </div>
</div>
</center>
"""

    def _confirm_upgrade(self) -> None:
        if self.mw.col.sched_ver() == 1:
            self.mw.col.mod_schema(check=True)
            self.mw.col.upgrade_to_v2_scheduler()
        self.mw.col.set_v3_scheduler(True)

        showInfo(tr.scheduling_update_done())
        self.refresh()
