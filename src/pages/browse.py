# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Browse page: Anki's browser as a page. A search entry (search.py's syntax, with quick
filters from a menu), a Cards or Notes mode, and the cards found as a Gtk.ColumnView over
rows the collection builds (Collection.browse_rows), sorted by the collection as a header is
clicked, with a selection bar of actions at the bottom.

    page = BrowsePage()
    page.focus_search()            # Ctrl+F (win.search)
    page.search('deck:Spanish')    # types the query and runs it (screenshot.py)
    page.refresh()                 # runs the current query again (the collection changed)

The query runs on Enter and 300 ms after the last keystroke (the entry's own delay), is kept
in the collection's config ('browse_query') and restored when the page is made. The first
LIMIT rows are shown (a banner says when there were more); notes mode shows the first card's
row of each note. While the page is mapped, a change to the collection's cards or notes runs
the query again after 200 ms, keeping the selection by card (or note) id.

The actions on the selection are the page's `browse.*` action group (the selection bar, the
More menu and the rows' context menu share them; the table's keys come from
shortcuts.BROWSE): edit, change-deck, suspend, unsuspend, toggle-suspend, mark, unmark,
toggle-mark, flag(s), add-tags, remove-tags, reset, set-due, delete, and filter(s), which
appends a term to the query. Each change is one undo step of the collection and toasts with
Undo. A row shows its state: a suspended row is dimmed, a flagged card has a coloured dot
before its sort field (style.css's flag-1 to flag-7), a marked note a star.
"""

import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .. import shortcuts
from ..scheduler import describe_interval
from ..search import SearchError
from ..template import FIELD_SEPARATOR, strip_html
from . import app

log = logging.getLogger(__name__)

LIMIT = 5000
REFRESH_DELAY_MS = 200
CONFIG_KEY = 'browse_query'
INDENT = ' '  # an em space per deck level in the filter menu, as the sidebar indents

# The states and the flags of the quick filters: (label, term).
STATE_FILTERS = (
    (_('New'), 'is:new'), (_('Learning'), 'is:learn'), (_('Review'), 'is:review'),
    (_('Due'), 'is:due'), (_('Suspended'), 'is:suspended'), (_('Buried'), 'is:buried'),
)
FLAG_FILTERS = (
    (_('Red'), 'flag:1'), (_('Orange'), 'flag:2'), (_('Green'), 'flag:3'), (_('Blue'), 'flag:4'),
    (_('Pink'), 'flag:5'), (_('Turquoise'), 'flag:6'), (_('Purple'), 'flag:7'),
)

# A column -> the column of the collection's join that orders the rows (browse_rows's `order`).
ORDER_SQL = {
    'sort': 'notes.sort_field', 'card': 'cards.ord', 'notetype': 'notetypes.name',
    'deck': 'decks.name', 'due': 'cards.due', 'interval': 'cards.interval',
    'difficulty': 'cards.difficulty', 'reviews': 'cards.reps', 'lapses': 'cards.lapses',
    'tags': 'notes.tags',
}


class BrowseRow(GObject.Object):
    """One row of the table: a card with its note's and deck's details, as text ready to
    show (the cells only copy it)."""

    __gtype_name__ = 'RetainBrowseRow'

    def __init__(self, card_id, note_id, **texts):
        super().__init__()
        self.card_id = card_id
        self.note_id = note_id
        self.sort_field = texts['sort_field']
        self.card = texts['card']
        self.deck = texts['deck']
        self.due = texts['due']
        self.interval = texts['interval']
        self.difficulty = texts['difficulty']
        self.reviews = texts['reviews']
        self.lapses = texts['lapses']
        self.tags = texts['tags']
        self.flag = texts['flag']
        self.marked = texts['marked']
        self.suspended = texts['suspended']


def row_texts(row, today, notetype, notes_mode=False):
    """The texts of a browse_rows dict, for a BrowseRow: how due, interval and difficulty
    read, and the card's name (the template's, or "Cloze N"; the note type's in notes mode).
    `notetype` is the note's (None when it is gone)."""
    fields = row['note_fields'].split(FIELD_SEPARATOR)
    sort_index = min(notetype.sort_field or 0, len(fields) - 1) if notetype else 0
    sort_field = strip_html(fields[sort_index]) if fields else ''
    if notes_mode:
        card = row['notetype_name']
    elif notetype is None:
        card = ''
    elif notetype.kind in ('cloze', 'occlusion'):
        card = _('Cloze {n}').format(n=row['ord'] + 1)
    elif row['ord'] < len(notetype.templates):
        card = notetype.templates[row['ord']].get('name') or ''
    else:
        card = ''
    state = row['state']
    interval = row['interval']
    return {
        'sort_field': sort_field, 'card': card, 'deck': row['deck_name'],
        'due': due_text(row, today),
        'interval': describe_interval(interval) if state != 'new' and interval >= 1 else '—',
        'difficulty': f'{row["difficulty"]:.1f}' if row['difficulty'] else '—',
        'reviews': str(row['reps']), 'lapses': str(row['lapses']),
        'tags': ' '.join(row['tags'].split()), 'flag': row['flag'],
        'marked': bool(row['marked']), 'suspended': bool(row['suspended']),
    }


def due_text(row, today):
    """When a card is due, in words: "today", "in 3 days", "2 months ago", "new #12",
    "learning", "(suspended)", "(buried)"."""
    if row['suspended']:
        return _('(suspended)')
    if row['buried'] and row['buried'] >= today:
        return _('(buried)')
    state = row['state']
    if state == 'new':
        return _('new #{n}').format(n=row['due'])
    if state in ('learning', 'relearning'):
        return _('learning')
    delta = row['due'] - today
    if delta == 0:
        return _('today')
    if delta > 0:
        # Translators: when a card is due; {interval} is "3 days" or "2 months".
        return _('in {interval}').format(interval=describe_interval(delta))
    # Translators: how long a card has been overdue; {interval} is "3 days" or "2 months".
    return _('{interval} ago').format(interval=describe_interval(-delta))


def notes_only(rows):
    """The first row of each note, in the rows' order (notes mode)."""
    seen = set()
    kept = []
    for row in rows:
        if row['note_id'] not in seen:
            seen.add(row['note_id'])
            kept.append(row)
    return kept


def with_term(query, term):
    """The query with a quick filter's term appended (once)."""
    words = query.split()
    if term in words:
        return query
    return f'{query.strip()} {term}'.strip()


class _TextCell(Gtk.Inscription):
    """A cell of one line of text, ellipsized; `item` is the row bound (None when unbound),
    for the context menu to find under the pointer."""

    __gtype_name__ = 'RetainBrowseTextCell'

    def __init__(self, numeric=False):
        super().__init__(xalign=1.0 if numeric else 0.0, valign=Gtk.Align.CENTER,
                         text_overflow=Gtk.InscriptionOverflow.ELLIPSIZE_END)
        if numeric:
            self.add_css_class('numeric')
        self.item = None
        self.position = None


class _SortCell(Gtk.Box):
    """The sort field's cell: the flag's dot and the marked star before the text."""

    __gtype_name__ = 'RetainBrowseSortCell'

    def __init__(self):
        super().__init__(spacing=6, valign=Gtk.Align.CENTER)
        self.item = None
        self.position = None
        self._flag = 0
        self.dot = Gtk.Label(label='●', visible=False, css_classes=['browse-flag'],
                             accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.star = Gtk.Label(label='★', visible=False, css_classes=['browse-star'],
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.text = Gtk.Inscription(xalign=0.0, hexpand=True,
                                    text_overflow=Gtk.InscriptionOverflow.ELLIPSIZE_END)
        self.append(self.dot)
        self.append(self.star)
        self.append(self.text)

    def show(self, item):
        self.text.set_text(item.sort_field)
        if item.flag != self._flag:
            if self._flag:
                self.dot.remove_css_class(f'flag-{self._flag}')
            if item.flag:
                self.dot.add_css_class(f'flag-{item.flag}')
            self._flag = item.flag
        self.dot.set_visible(bool(item.flag))
        self.star.set_visible(item.marked)


@Gtk.Template(resource_path='/io/github/jackicus/Retain/browse.ui')
class BrowsePage(Adw.NavigationPage):
    __gtype_name__ = 'RetainBrowsePage'

    toolbar_view = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    mode_group = Gtk.Template.Child()
    filter_button = Gtk.Template.Child()
    count_label = Gtk.Template.Child()
    error_banner = Gtk.Template.Child()
    limit_banner = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    no_results_page = Gtk.Template.Child()
    column_view = Gtk.Template.Child()
    sort_column = Gtk.Template.Child()
    card_column = Gtk.Template.Child()
    deck_column = Gtk.Template.Child()
    due_column = Gtk.Template.Child()
    interval_column = Gtk.Template.Child()
    difficulty_column = Gtk.Template.Child()
    reviews_column = Gtk.Template.Child()
    lapses_column = Gtk.Template.Child()
    tags_column = Gtk.Template.Child()
    selection_bar = Gtk.Template.Child()
    selection_label = Gtk.Template.Child()
    suspend_button = Gtk.Template.Child()
    card_menu = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.collection = app().collection
        self._query = None  # the query last run (None before the first)
        self._rows = Gio.ListStore(item_type=BrowseRow)
        self._shown = []  # the BrowseRows in the store, in order
        self._changed_handler = None  # the collection's `changed`, while mapped
        self._refresh_source = None  # the pending debounced refresh
        self._menu_popover = None  # the rows' context menu, made on the first right click

        self._columns = {
            self.sort_column: 'sort', self.card_column: 'card', self.deck_column: 'deck',
            self.due_column: 'due', self.interval_column: 'interval',
            self.difficulty_column: 'difficulty', self.reviews_column: 'reviews',
            self.lapses_column: 'lapses', self.tags_column: 'tags',
        }
        # A sorter on each column makes its header clickable and keeps the arrow; the
        # sorting itself is the collection's (ORDER_SQL), re-querying on each click.
        for column in self._columns:
            column.set_sorter(Gtk.StringSorter())
        self.sort_column.set_factory(self._factory(self._setup_sort, self._bind_sort,
                                                   self._unbind))
        self.card_column.set_factory(self._text_factory('card'))
        self.deck_column.set_factory(self._text_factory('deck'))
        self.due_column.set_factory(self._text_factory('due'))
        self.interval_column.set_factory(self._text_factory('interval'))
        self.difficulty_column.set_factory(self._text_factory('difficulty', numeric=True))
        self.reviews_column.set_factory(self._text_factory('reviews', numeric=True))
        self.lapses_column.set_factory(self._text_factory('lapses', numeric=True))
        self.tags_column.set_factory(self._text_factory('tags'))
        row_factory = Gtk.SignalListItemFactory()
        row_factory.connect('bind', self._bind_row)
        self.column_view.set_row_factory(row_factory)

        self.selection = Gtk.MultiSelection(model=self._rows)
        self.column_view.set_model(self.selection)
        self.selection.connect('selection-changed', self._on_selection_changed)
        self.selection.connect('items-changed', self._on_selection_changed)
        self.column_view.sort_by_column(self.sort_column, Gtk.SortType.ASCENDING)
        self.column_view.get_sorter().connect('changed', self._on_sort_changed)

        self._add_actions()
        self._add_keys()
        self.filter_button.set_create_popup_func(self._fill_filter_menu)
        right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY,
                                       propagation_phase=Gtk.PropagationPhase.CAPTURE)
        right_click.connect('pressed', self._on_right_click)
        self.column_view.add_controller(right_click)
        down = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        down.connect('key-pressed', self._on_entry_key)
        self.search_entry.add_controller(down)

        self.search_entry.set_text(self.collection.get(CONFIG_KEY, '') or '')
        self._update_selection()

    # -- lifetime ----------------------------------------------------------------------------

    def do_map(self):
        Adw.NavigationPage.do_map(self)
        if self._changed_handler is None:
            self._changed_handler = self.collection.connect('changed',
                                                            self._on_collection_changed)
        self.refresh()

    def do_unmap(self):
        if self._changed_handler is not None:
            self.collection.disconnect(self._changed_handler)
            self._changed_handler = None
        self._cancel_refresh()
        Adw.NavigationPage.do_unmap(self)

    def _on_collection_changed(self, _collection, kind):
        if kind in ('cards', 'notes', 'decks'):
            self._cancel_refresh()
            self._refresh_source = GLib.timeout_add(REFRESH_DELAY_MS, self._refresh_timeout)

    def _refresh_timeout(self):
        self._refresh_source = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _cancel_refresh(self):
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = None

    # -- the search --------------------------------------------------------------------------

    def focus_search(self):
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def search(self, query):
        """Put a query in the entry and run it."""
        self.search_entry.set_text(query)
        self.search_entry.set_position(-1)
        self.refresh()

    @property
    def notes_mode(self):
        return self.mode_group.get_active_name() == 'notes'

    @property
    def order(self):
        """(the ORDER_SQL key of the sorted column, descending)."""
        sorter = self.column_view.get_sorter()
        key = self._columns.get(sorter.get_primary_sort_column(), 'sort')
        if key == 'card' and self.notes_mode:
            key = 'notetype'
        return key, sorter.get_primary_sort_order() == Gtk.SortType.DESCENDING

    @Gtk.Template.Callback()
    def on_search_changed(self, _entry):
        if self.search_entry.get_text().strip() != self._query:
            self.refresh()

    @Gtk.Template.Callback()
    def on_search_activate(self, _entry):
        self.refresh()

    @Gtk.Template.Callback()
    def on_stop_search(self, _entry):
        self.search_entry.set_text('')
        self.refresh()

    @Gtk.Template.Callback()
    def on_mode_changed(self, *_args):
        self.card_column.set_title(_('Note Type') if self.notes_mode else _('Card'))
        self.refresh()

    def _on_entry_key(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Down and self._shown:
            self.column_view.grab_focus()
            return True
        return False

    def _on_sort_changed(self, _sorter, _change):
        self.refresh()

    def refresh(self):
        """Run the entry's query and show its rows, keeping the selection where it can."""
        self._cancel_refresh()
        query = self.search_entry.get_text().strip()
        self._query = query
        if self.collection.get(CONFIG_KEY, '') != query:
            self.collection.set(CONFIG_KEY, query)
        if self.collection.card_count() == 0:
            self.error_banner.set_revealed(False)
            self.limit_banner.set_revealed(False)
            self.count_label.set_label('')
            self._show([])
            self.stack.set_visible_child_name('no-cards')
            return
        key, descending = self.order
        try:
            found = self.collection.browse_rows(query, order=ORDER_SQL[key],
                                                descending=descending, limit=LIMIT + 1)
        except SearchError as error:
            self.error_banner.set_title(str(error))
            self.error_banner.set_revealed(True)
            return
        self.error_banner.set_revealed(False)
        truncated = len(found) > LIMIT
        found = found[:LIMIT]
        if self.notes_mode:
            found = notes_only(found)
        self.limit_banner.set_revealed(truncated)
        self._show(self._make_rows(found))
        count = len(found)
        if self.notes_mode:
            text = ngettext('{n} note', '{n} notes', count)
            self.no_results_page.set_title(_('No Notes Found'))
        else:
            text = ngettext('{n} card', '{n} cards', count)
            self.no_results_page.set_title(_('No Cards Found'))
        self.count_label.set_label(text.format(n=f'{count:n}'))
        self.stack.set_visible_child_name('table' if count else 'no-results')

    def _make_rows(self, found):
        today = self.collection.today()
        notetypes = {notetype.id: notetype for notetype in self.collection.notetypes()}
        notes_mode = self.notes_mode
        return [BrowseRow(row['id'], row['note_id'],
                          **row_texts(row, today, notetypes.get(row['notetype_id']), notes_mode))
                for row in found]

    def _show(self, rows):
        """Replace the table's rows, selecting again those with the ids selected before."""
        keys = self._selected_keys()
        self._shown = rows
        self._rows.splice(0, self._rows.get_n_items(), rows)
        if keys:
            selected = Gtk.Bitset.new_empty()
            for position, row in enumerate(rows):
                if self._key(row) in keys:
                    selected.add(position)
            mask = Gtk.Bitset.new_range(0, len(rows)) if rows else Gtk.Bitset.new_empty()
            self.selection.set_selection(selected, mask)
        self._update_selection()

    def _key(self, row):
        return row.note_id if self.notes_mode else row.card_id

    # -- the cells ---------------------------------------------------------------------------

    def _factory(self, setup, bind, unbind):
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', setup)
        factory.connect('bind', bind)
        factory.connect('unbind', unbind)
        return factory

    def _text_factory(self, name, numeric=False):
        def setup(_factory, cell):
            cell.set_child(_TextCell(numeric=numeric))

        def bind(_factory, cell):
            item = cell.get_item()
            child = cell.get_child()
            child.item = item
            child.position = cell.get_position()
            child.set_text(getattr(item, name))
            _set_dimmed(child, item.suspended)

        return self._factory(setup, bind, self._unbind)

    def _setup_sort(self, _factory, cell):
        cell.set_child(_SortCell())

    def _bind_sort(self, _factory, cell):
        item = cell.get_item()
        child = cell.get_child()
        child.item = item
        child.position = cell.get_position()
        child.show(item)
        _set_dimmed(child, item.suspended)

    def _unbind(self, _factory, cell):
        child = cell.get_child()
        child.item = None
        child.position = None

    def _bind_row(self, _factory, row):
        item = row.get_item()
        row.set_accessible_label(f'{item.sort_field}, {item.deck}, {item.due}')

    # -- the selection -----------------------------------------------------------------------

    def _selected_positions(self):
        bitset = self.selection.get_selection()
        return [bitset.get_nth(i) for i in range(bitset.get_size())]

    def selected_rows(self):
        return [self._rows.get_item(position) for position in self._selected_positions()]

    def _selected_keys(self):
        return {self._key(row) for row in self.selected_rows()}

    def selected_card_ids(self):
        """The cards the actions act on: the selected rows' cards, or in notes mode every
        card of the selected notes."""
        rows = self.selected_rows()
        if not self.notes_mode:
            return [row.card_id for row in rows]
        return [card.id for row in rows for card in self.collection.cards_of_note(row.note_id)]

    def selected_note_ids(self):
        """The selected rows' notes, each once, in the rows' order."""
        return list(dict.fromkeys(row.note_id for row in self.selected_rows()))

    def _on_selection_changed(self, *_args):
        self._update_selection()

    def _update_selection(self):
        rows = self.selected_rows()
        count = len(rows)
        self.selection_bar.set_revealed(count > 0)
        self.selection_label.set_label(
            ngettext('{n} selected', '{n} selected', count).format(n=f'{count:n}'))
        any_suspended = any(row.suspended for row in rows)
        all_suspended = count > 0 and all(row.suspended for row in rows)
        any_marked = any(row.marked for row in rows)
        all_marked = count > 0 and all(row.marked for row in rows)
        self.suspend_button.set_label(_('Unsuspend') if all_suspended else _('Suspend'))
        for name in ('edit', 'change-deck', 'toggle-suspend', 'toggle-mark', 'flag',
                     'add-tags', 'remove-tags', 'reset', 'set-due', 'delete'):
            self._actions.lookup_action(name).set_enabled(count > 0)
        self._actions.lookup_action('suspend').set_enabled(count > 0 and not all_suspended)
        self._actions.lookup_action('unsuspend').set_enabled(any_suspended)
        self._actions.lookup_action('mark').set_enabled(count > 0 and not all_marked)
        self._actions.lookup_action('unmark').set_enabled(any_marked)

    # -- actions -----------------------------------------------------------------------------

    def _add_actions(self):
        self._actions = Gio.SimpleActionGroup()
        for name, callback in (
                ('edit', self.on_edit), ('change-deck', self.on_change_deck),
                ('suspend', self.on_suspend), ('unsuspend', self.on_unsuspend),
                ('toggle-suspend', self.on_toggle_suspend), ('mark', self.on_mark),
                ('unmark', self.on_unmark), ('toggle-mark', self.on_toggle_mark),
                ('add-tags', self.on_add_tags), ('remove-tags', self.on_remove_tags),
                ('reset', self.on_reset), ('set-due', self.on_set_due),
                ('delete', self.on_delete), ('select-all', self.on_select_all)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self._actions.add_action(action)
        for name, callback in (('flag', self.on_flag), ('filter', self.on_filter)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))
            action.connect('activate', callback)
            self._actions.add_action(action)
        self.insert_action_group('browse', self._actions)

    def _add_keys(self):
        """The table's keys (shortcuts.BROWSE): Return is the view's own activate."""
        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        for key, name in (('delete', 'delete'), ('suspend', 'toggle-suspend'),
                          ('mark', 'toggle-mark'), ('change-deck', 'change-deck'),
                          ('add-tags', 'add-tags'), ('select-all', 'select-all')):
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(shortcuts.BROWSE[key]),
                Gtk.NamedAction.new(f'browse.{name}')))
        self.column_view.add_controller(controller)

    @Gtk.Template.Callback()
    def on_row_activated(self, _view, position):
        row = self._rows.get_item(position)
        if row is not None:
            self._edit(row.note_id)

    def on_edit(self, *_args):
        rows = self.selected_rows()
        if rows:
            self._edit(rows[0].note_id)

    def _edit(self, note_id):
        from ..dialogs import add_edit

        add_edit.present_edit(app(), self.get_root(), note_id)

    def on_select_all(self, *_args):
        self.selection.select_all()

    def on_filter(self, _action, value):
        term = value.get_string()
        if term == 'clear':
            self.search('')
        else:
            self.search(with_term(self.search_entry.get_text(), term))

    def _fill_filter_menu(self, button):
        """The quick filters, built as the menu opens: the decks and tags are the
        collection's now."""
        menu = Gio.Menu()
        states = Gio.Menu()
        for label, term in STATE_FILTERS:
            states.append(label, f'browse.filter::{term}')
        menu.append_submenu(_('State'), states)
        flags = Gio.Menu()
        for label, term in FLAG_FILTERS:
            flags.append(label, f'browse.filter::{term}')
        menu.append_submenu(_('Flag'), flags)
        menu.append(_('Marked'), 'browse.filter::is:marked')
        decks = Gio.Menu()
        for root in self.collection.deck_tree():
            for node in root.walk():
                deck = node.deck
                item = Gio.MenuItem.new(INDENT * node.level + deck.basename, None)
                item.set_action_and_target_value(
                    'browse.filter', GLib.Variant('s', f'deck:"{deck.name}"'))
                decks.append_item(item)
        menu.append_submenu(_('Deck'), decks)
        tags = Gio.Menu()
        for tag in self.collection.all_tags():
            item = Gio.MenuItem.new(tag, None)
            item.set_action_and_target_value('browse.filter', GLib.Variant('s', f'tag:{tag}'))
            tags.append_item(item)
        if tags.get_n_items():
            menu.append_submenu(_('Tag'), tags)
        clear = Gio.Menu()
        clear.append(_('Clear'), 'browse.filter::clear')
        menu.append_section(None, clear)
        button.set_menu_model(menu)

    def _on_right_click(self, gesture, _n_press, x, y):
        """A row's context menu: the row under the pointer joins the selection (or replaces
        it when it was not selected), and the card menu opens there."""
        picked = self.column_view.pick(x, y, Gtk.PickFlags.DEFAULT)
        cell = _cell_under(picked)
        if cell is None or cell.position is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if not self.selection.is_selected(cell.position):
            self.selection.select_item(cell.position, True)
        if self._menu_popover is None:
            self._menu_popover = Gtk.PopoverMenu.new_from_model(self.card_menu)
            self._menu_popover.set_parent(self.column_view)
            self._menu_popover.set_has_arrow(False)
            self._menu_popover.set_halign(Gtk.Align.START)
        rectangle = Gdk.Rectangle()
        rectangle.x, rectangle.y, rectangle.width, rectangle.height = int(x), int(y), 1, 1
        self._menu_popover.set_pointing_to(rectangle)
        self._menu_popover.popup()

    # Each change is one undo step of the collection, toasted with Undo; the collection's
    # `changed` then runs the query again.

    def on_change_deck(self, *_args):
        card_ids = self.selected_card_ids()
        if not card_ids:
            return
        decks = self.collection.decks()
        names = Gtk.StringList.new([deck.name for deck in decks])
        dropdown = Gtk.DropDown(model=names, enable_search=True,
                                expression=Gtk.PropertyExpression.new(Gtk.StringObject, None,
                                                                      'string'))
        current = self.collection.card(card_ids[0])
        if current is not None:
            for index, deck in enumerate(decks):
                if deck.id == current.deck_id:
                    dropdown.set_selected(index)
        dialog = Adw.AlertDialog(heading=_('Change Deck'), extra_child=dropdown)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('move', _('_Move'))
        dialog.set_response_appearance('move', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('move')

        def on_response(_dialog, response):
            if response != 'move':
                return
            deck = decks[dropdown.get_selected()]
            self.collection.set_deck(card_ids, deck.id)
            self._toast(ngettext('Moved {n} card to {deck}', 'Moved {n} cards to {deck}',
                                 len(card_ids)).format(n=len(card_ids), deck=deck.name))

        self._present(dialog, on_response)

    def on_suspend(self, *_args):
        card_ids = self.selected_card_ids()
        if card_ids:
            self.collection.suspend(card_ids)
            self._toast(ngettext('Suspended {n} card', 'Suspended {n} cards',
                                 len(card_ids)).format(n=len(card_ids)))

    def on_unsuspend(self, *_args):
        card_ids = self.selected_card_ids()
        if card_ids:
            self.collection.unsuspend(card_ids)
            self._toast(ngettext('Unsuspended {n} card', 'Unsuspended {n} cards',
                                 len(card_ids)).format(n=len(card_ids)))

    def on_toggle_suspend(self, *_args):
        rows = self.selected_rows()
        if rows and all(row.suspended for row in rows):
            self.on_unsuspend()
        else:
            self.on_suspend()

    def on_mark(self, *_args):
        note_ids = self.selected_note_ids()
        if note_ids:
            self.collection.set_marked(note_ids, True)
            self._toast(ngettext('Marked {n} note', 'Marked {n} notes',
                                 len(note_ids)).format(n=len(note_ids)))

    def on_unmark(self, *_args):
        note_ids = self.selected_note_ids()
        if note_ids:
            self.collection.set_marked(note_ids, False)
            self._toast(ngettext('Unmarked {n} note', 'Unmarked {n} notes',
                                 len(note_ids)).format(n=len(note_ids)))

    def on_toggle_mark(self, *_args):
        rows = self.selected_rows()
        if rows and all(row.marked for row in rows):
            self.on_unmark()
        else:
            self.on_mark()

    def on_flag(self, _action, value):
        card_ids = self.selected_card_ids()
        flag = int(value.get_string())
        if not card_ids:
            return
        self.collection.set_flag(card_ids, flag)
        if flag:
            text = ngettext('Flagged {n} card', 'Flagged {n} cards', len(card_ids))
        else:
            text = ngettext('Removed the flag from {n} card',
                            'Removed the flag from {n} cards', len(card_ids))
        self._toast(text.format(n=len(card_ids)))

    def on_add_tags(self, *_args):
        self._tags_dialog(_('Add Tags'), _('_Add'), remove=False)

    def on_remove_tags(self, *_args):
        self._tags_dialog(_('Remove Tags'), _('_Remove'), remove=True)

    def _tags_dialog(self, heading, verb, remove):
        note_ids = self.selected_note_ids()
        if not note_ids:
            return
        entry = Gtk.Entry(placeholder_text=_('Tags, separated by spaces'),
                          activates_default=True)
        dialog = Adw.AlertDialog(heading=heading, extra_child=entry)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('apply', verb)
        dialog.set_response_appearance('apply', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('apply')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            tags = entry.get_text().split()
            if response != 'apply' or not tags:
                return
            if remove:
                self.collection.set_tags(note_ids, remove=tags)
                text = ngettext('Removed tags from {n} note', 'Removed tags from {n} notes',
                                len(note_ids))
            else:
                self.collection.set_tags(note_ids, add=tags)
                text = ngettext('Added tags to {n} note', 'Added tags to {n} notes',
                                len(note_ids))
            self._toast(text.format(n=len(note_ids)))

        self._present(dialog, on_response)
        entry.grab_focus()

    def on_reset(self, *_args):
        card_ids = self.selected_card_ids()
        if card_ids:
            self.collection.forget(card_ids)
            self._toast(ngettext('Reset {n} card', 'Reset {n} cards',
                                 len(card_ids)).format(n=len(card_ids)))

    def on_set_due(self, *_args):
        card_ids = self.selected_card_ids()
        if not card_ids:
            return
        spin = Gtk.SpinButton.new_with_range(0, 36500, 1)
        spin.set_activates_default(True)
        spin.set_halign(Gtk.Align.CENTER)
        dialog = Adw.AlertDialog(heading=_('Set Due Date'),
                                 body=_('Days from today (0 is today)'), extra_child=spin)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('set', _('_Set'))
        dialog.set_response_appearance('set', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('set')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if response != 'set':
                return
            days = spin.get_value_as_int()
            self.collection.set_due(card_ids, days)
            if days:
                text = ngettext('{n} card due in {days} days', '{n} cards due in {days} days',
                                len(card_ids))
            else:
                text = ngettext('{n} card due today', '{n} cards due today', len(card_ids))
            self._toast(text.format(n=len(card_ids), days=days))

        self._present(dialog, on_response)

    def on_delete(self, *_args):
        note_ids = self.selected_note_ids()
        if note_ids:
            self.collection.remove_notes(note_ids)
            self._toast(ngettext('Deleted {n} note', 'Deleted {n} notes',
                                 len(note_ids)).format(n=len(note_ids)))

    def _present(self, dialog, on_response):
        """Show an alert dialog over the window, whose keyed actions step aside meanwhile."""
        window = self.get_root()
        dialog.connect('response', on_response)
        if hasattr(window, 'set_dialog_open'):
            window.set_dialog_open(True)
            dialog.connect('closed', lambda *_args: window.set_dialog_open(False))
        dialog.present(window)

    def _toast(self, text):
        app().toast(text, undo=True)


def _set_dimmed(widget, dimmed):
    if dimmed:
        widget.add_css_class('dimmed')
    else:
        widget.remove_css_class('dimmed')


def _cell_under(widget):
    """The cell (a _TextCell or _SortCell) at or above a picked widget: the one picked, or
    the row's first when the pick landed between cells."""
    while widget is not None:
        if isinstance(widget, (_TextCell, _SortCell)):
            return widget
        if widget.get_css_name() == 'row':
            cell = widget.get_first_child()
            child = cell.get_first_child() if cell is not None else None
            return child if isinstance(child, (_TextCell, _SortCell)) else None
        widget = widget.get_parent()
    return None
