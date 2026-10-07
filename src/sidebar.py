# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The sidebar: Today, Browse and Statistics, then the decks as a tree, each with what is
due today.

    controller = SidebarController(window, adw_sidebar, scheduler)
    controller.select('deck:123'); controller.has_key(key); controller.key_at(index)
    controller.refresh_now()

Each Adw.SidebarItem's suffix is a CountsLabel: the new, learning and due counts in the
accent, warning and success colours (style.css), hidden when all are zero. Adw.Sidebar
cannot nest, so a subdeck's title is indented with em spaces; a collapsed parent (the deck's
collapsed flag, kept in the collection) hides its children. The deck section's menu model is
window.blp's deck_menu: the sidebar's setup-menu signal says which item it opens on, and the
window's deck actions act on that deck. The counts refresh, a moment later, whenever the
collection reports a change to cards, decks, notes, presets or the config.
"""

import logging
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk

log = logging.getLogger(__name__)

DESTINATIONS = (
    ('today', 'Today', 'weather-clear-symbolic'),
    ('browse', 'Browse', 'edit-find-symbolic'),
    ('stats', 'Statistics', 'x-office-spreadsheet-symbolic'),
)
INDENT = ' '  # an em space per level
REFRESH_DELAY_MS = 150


class CountsLabel(Gtk.Box):
    """Three small numbers: new, learning, due, each in its colour."""

    def __init__(self):
        super().__init__(spacing=8)
        self.add_css_class('deck-counts')
        self._labels = {}
        for name in ('new', 'learning', 'due'):
            label = Gtk.Label(visible=False)
            label.add_css_class('numeric')
            label.add_css_class('caption')
            label.add_css_class(f'count-{name}')
            self.append(label)
            self._labels[name] = label

    def set_counts(self, new, learning, due):
        for name, value in (('new', new), ('learning', learning), ('due', due)):
            label = self._labels[name]
            label.set_text(str(value))
            label.set_visible(value > 0)
        shown = new + learning + due > 0
        self.set_visible(shown)
        self.set_tooltip_text(
            _('{new} new, {learning} learning, {due} to review').format(
                new=new, learning=learning, due=due) if shown else None)


class SidebarController:

    def __init__(self, window, sidebar, scheduler):
        self.window = window
        self.sidebar = sidebar
        self.scheduler = scheduler
        self.collection = scheduler.collection
        self._keys = []  # the key of each item, in order
        self._items = {}  # key -> Adw.SidebarItem
        self._refresh_pending = None
        self._selecting = False
        self._build_destinations()
        self._deck_section = Adw.SidebarSection(title=_('Decks'))
        self._deck_section.set_menu_model(_deck_menu())
        self.sidebar.append(self._deck_section)
        self._build_decks()
        self.sidebar.connect('activated', self._on_activated)
        self.sidebar.connect('notify::selected', self._on_selected)
        self.sidebar.connect('setup-menu', self._on_setup_menu)
        self.collection.connect('changed', self._on_changed)

    # -- building ----------------------------------------------------------------------------

    def _build_destinations(self):
        section = Adw.SidebarSection()
        for key, title, icon in DESTINATIONS:
            item = Adw.SidebarItem(title=_(title), icon_name=icon)
            section.append(item)
            self._keys.append(key)
            self._items[key] = item
        self.sidebar.append(section)

    def _build_decks(self):
        """(Re)fill the deck section from the scheduler's tree."""
        for key in [key for key in self._keys if key.startswith('deck:')]:
            self._keys.remove(key)
            self._deck_section.remove(self._items.pop(key))
        hidden_under = []
        roots = self.scheduler.tree()
        for root in roots:
            for node in root.walk():
                if _is_empty_default(node, roots):
                    continue
                hidden_under = [level for level in hidden_under if level < node.level]
                deck = node.deck
                key = f'deck:{deck.id}'
                item = Adw.SidebarItem(title=INDENT * deck.level + deck.basename)
                item.set_icon_name('folder-symbolic' if node.children
                                   else 'view-list-bullet-symbolic')
                item.set_tooltip(deck.name)
                counts = CountsLabel()
                counts.set_counts(node.new, node.learning, node.due)
                item.set_suffix(counts)
                item.set_visible(not hidden_under)
                self._deck_section.append(item)
                self._keys.append(key)
                self._items[key] = item
                if deck.collapsed and node.children:
                    hidden_under.append(node.level)

    # -- selection ---------------------------------------------------------------------------

    def has_key(self, key):
        return key in self._items

    def key_at(self, index):
        return self._keys[index] if 0 <= index < len(self._keys) else None

    def key_of(self, item):
        return next((key for key, found in self._items.items() if found is item), None)

    def index_of(self, key):
        return self._keys.index(key) if key in self._keys else -1

    def select(self, key):
        index = self.index_of(key)
        if index < 0 or self.sidebar.get_selected() == index:
            return
        self._selecting = True
        try:
            self.sidebar.set_selected(index)
        finally:
            self._selecting = False

    def _on_selected(self, *_args):
        if self._selecting:
            return
        key = self.key_at(self.sidebar.get_selected())
        if key is not None:
            self.window.show_root(key)

    def _on_activated(self, _sidebar, index):
        key = self.key_at(index)
        if key is not None:
            self.window.show_root(key)

    def _on_setup_menu(self, _sidebar, item):
        key = self.key_of(item) if item is not None else None
        self.window.set_menu_deck(int(key[5:]) if key and key.startswith('deck:') else None)

    # -- refreshing ----------------------------------------------------------------------------

    def _on_changed(self, _collection, kind):
        if kind in ('cards', 'decks', 'configs', 'config', 'notes'):
            if self._refresh_pending is None:
                self._refresh_pending = GLib.timeout_add(REFRESH_DELAY_MS, self._refresh)

    def _refresh(self):
        self._refresh_pending = None
        selected = self.key_at(self.sidebar.get_selected())
        self._selecting = True
        try:
            self._build_decks()
            if selected in self._items:
                self.sidebar.set_selected(self.index_of(selected))
        finally:
            self._selecting = False
        return GLib.SOURCE_REMOVE

    def refresh_now(self):
        if self._refresh_pending is not None:
            GLib.source_remove(self._refresh_pending)
        self._refresh()


def _is_empty_default(node, roots):
    """Whether this is the Default deck, empty and alone, which there is no reason to show
    beside real decks (Anki hides it too)."""
    return (node.deck.name == 'Default' and len(roots) > 1 and not node.children
            and node.total == 0 and not _has_cards(node.deck.id))


def _has_cards(deck_id):
    from .pages import app

    return app().collection.deck_card_count(deck_id) > 0


def _deck_menu():
    """The deck section's context menu (the window's win.deck-* actions)."""
    menu = Gio.Menu()
    first = Gio.Menu()
    first.append(_('_Study'), 'win.deck-study')
    first.append(_('_Add Cards'), 'win.deck-add')
    menu.append_section(None, first)
    second = Gio.Menu()
    second.append(_('_Rename…'), 'win.deck-rename')
    second.append(_('_Options…'), 'win.deck-options')
    second.append(_('_Export…'), 'win.deck-export')
    menu.append_section(None, second)
    third = Gio.Menu()
    third.append(_('_Delete…'), 'win.deck-delete')
    menu.append_section(None, third)
    return menu
