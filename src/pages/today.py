# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Today page, the home screen: the day over the whole collection, then the decks.

    page = TodayPage()
    page.refresh()                      # from the collection (also on map and on `changed`)

The hero card sums the scheduler's tree (every root deck's new, learning and due counts,
capped by the decks' limits) and adds today's progress from stats.Stats.today_summary(); with
nothing due anywhere it says All Caught Up. Under it, one DeckRow per deck of the tree
(indented by level, with its counts and a Study button); a row opens the deck's page
through window.show_root('deck:ID'). A collection without decks or cards shows a status page
offering Add Cards and Import. The page listens to the collection only while mapped, and
refreshes a moment after a change, once, however many changes came.
"""

import datetime
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gtk

from ..stats import Stats
from . import app

REFRESH_DELAY_MS = 150
INDENT = 18  # pixels per level of a subdeck's row
CHANGE_KINDS = ('cards', 'decks', 'configs', 'config', 'notes')


class DeckRow(Adw.ActionRow):
    """A deck in a boxed list: the basename, its counts in words, and Study when there is
    something to study. `deck_id` names the deck; activating the row opens its page."""

    def __init__(self, node, level, on_study):
        super().__init__(title=node.deck.basename, activatable=True, use_markup=False)
        self.deck_id = node.deck.id
        self.set_tooltip_text(node.deck.name)
        if level:
            self.add_prefix(Gtk.Box(width_request=INDENT * level))
        self.set_subtitle(counts_text(node.new, node.learning, node.due))
        self.study_button = Gtk.Button(icon_name='media-playback-start-symbolic',
                                       valign=Gtk.Align.CENTER, visible=node.total > 0)
        self.study_button.add_css_class('flat')
        self.study_button.set_tooltip_text(_('Study'))
        self.study_button.connect('clicked', lambda *_args: on_study(self.deck_id))
        self.add_suffix(self.study_button)
        self.add_suffix(Gtk.Image(icon_name='go-next-symbolic', accessible_role='presentation'))


def counts_text(new, learning, due):
    """'3 new · 1 learning · 12 due', or 'Nothing due'."""
    if new + learning + due == 0:
        return _('Nothing due')
    parts = []
    if new:
        parts.append(ngettext('{n} new', '{n} new', new).format(n=new))
    if learning:
        parts.append(ngettext('{n} learning', '{n} learning', learning).format(n=learning))
    if due:
        parts.append(ngettext('{n} due', '{n} due', due).format(n=due))
    return ' · '.join(parts)


def fill_deck_list(list_box, nodes, on_study, base_level=0):
    """Replace a Gtk.ListBox's rows with DeckRows for `nodes` (a list of DeckNodes whose
    children are walked), indented from `base_level`."""
    list_box.remove_all()
    for root in nodes:
        for node in root.walk():
            list_box.append(DeckRow(node, node.level - base_level, on_study))


def hidden_default(node, roots):
    """Whether this is the Default deck, empty and alone beside real decks, which the sidebar
    hides too (Anki does as well)."""
    return (node.deck.name == 'Default' and len(roots) > 1 and not node.children
            and node.total == 0 and app().collection.deck_card_count(node.deck.id) == 0)


def progress_text(summary):
    """Today's progress in a line: '12 cards in 4 minutes · 92% correct · 7-day streak'."""
    parts = []
    reviews = summary['reviews']
    if reviews:
        minutes = max(1, round(summary['minutes']))
        cards = ngettext('{n} card', '{n} cards', reviews).format(n=reviews)
        parts.append(ngettext('{cards} in {m} minute', '{cards} in {m} minutes',
                              minutes).format(cards=cards, m=minutes))
        if summary['correct_percent'] is not None:
            parts.append(_('{percent}% correct').format(percent=round(summary['correct_percent'])))
    else:
        parts.append(_('Nothing studied yet today'))
    streak = summary['streak_days']
    if streak:
        parts.append(ngettext('{n}-day streak', '{n}-day streak', streak).format(n=streak))
    return ' · '.join(parts)


@Gtk.Template(resource_path='/io/github/jackicus/Retain/today.ui')
class TodayPage(Adw.NavigationPage):
    __gtype_name__ = 'RetainTodayPage'

    stack = Gtk.Template.Child()
    date_label = Gtk.Template.Child()
    hero_stack = Gtk.Template.Child()
    due_label = Gtk.Template.Child()
    new_label = Gtk.Template.Child()
    learning_label = Gtk.Template.Child()
    progress_label = Gtk.Template.Child()
    decks_list = Gtk.Template.Child()
    empty_page = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self._changed_handler = None
        self._refresh_pending = None
        self.decks_list.connect('row-activated', self._on_row_activated)
        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)
        Gtk.Widget.set_focusable(self, False)

    # -- lifetime ----------------------------------------------------------------------------

    def _on_map(self, *_args):
        collection = app().collection
        if self._changed_handler is None:
            self._changed_handler = collection.connect('changed', self._on_changed)
        self.refresh()

    def _on_unmap(self, *_args):
        if self._changed_handler is not None:
            app().collection.disconnect(self._changed_handler)
            self._changed_handler = None
        if self._refresh_pending is not None:
            GLib.source_remove(self._refresh_pending)
            self._refresh_pending = None

    def _on_changed(self, _collection, kind):
        if kind in CHANGE_KINDS and self._refresh_pending is None:
            self._refresh_pending = GLib.timeout_add(REFRESH_DELAY_MS, self._refresh_later)

    def _refresh_later(self):
        self._refresh_pending = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    # -- content -----------------------------------------------------------------------------

    def refresh(self):
        application = app()
        collection = application.collection
        roots = application.scheduler.tree()
        decks = collection.decks()
        if collection.card_count() == 0 and all(deck.name == 'Default' for deck in decks):
            self.stack.set_visible_child_name('empty')
            return
        self.stack.set_visible_child_name('decks')
        self.date_label.set_text(self._date_text(collection))
        new = sum(node.new for node in roots)
        learning = sum(node.learning for node in roots)
        due = sum(node.due for node in roots)
        self.new_label.set_text(str(new))
        self.learning_label.set_text(str(learning))
        self.due_label.set_text(str(due))
        self.hero_stack.set_visible_child_name('counts' if new + learning + due else 'done')
        self.progress_label.set_text(progress_text(Stats(collection).today_summary()))
        shown = [root for root in roots if not hidden_default(root, roots)]
        fill_deck_list(self.decks_list, shown, self._study)

    @staticmethod
    def _date_text(collection):
        day = datetime.date.fromtimestamp(collection.day_start(collection.today()))
        # Translators: the date under "Today" (strftime): weekday, day and month.
        return day.strftime(_('%A, %-d %B'))

    def _study(self, deck_id):
        window = self.get_root()
        if window is not None and hasattr(window, 'study'):
            window.study(deck_id)

    def _on_row_activated(self, _list, row):
        window = self.get_root()
        if window is not None and hasattr(window, 'show_root'):
            window.show_root(f'deck:{row.deck_id}')
