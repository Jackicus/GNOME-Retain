# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The window: a sidebar of destinations and decks, and the page it opens.

    window = Window(application=app)
    window.show_root('deck:123')         # a sidebar key: today, browse, stats, deck:ID
    window.push(page)                     # a pushed page (Study, Card Information)
    window.study(deck_id, session=None)   # the review page for a deck or a custom session
    window.current_deck_id()              # the deck the page shown belongs to, or None
    window.add_toast(toast)

The sidebar is sidebar.py's SidebarController over the window's Adw.Sidebar; choosing an
item replaces the navigation view's stack with that destination's root page, which is made
on the first visit and kept (pages/__init__.py's PAGES). The deck actions (win.deck-*) act on
`win.deck-target`, the deck the sidebar's context menu was opened on, else the page's deck.
Keyed window actions are disabled while a dialog is open over the window, so a dialog's
entry gets its keys.
"""

import logging
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from . import pages
from .sidebar import SidebarController

log = logging.getLogger(__name__)

ROOT_KEYS = ('today', 'browse', 'stats')


@Gtk.Template(resource_path='/io/github/jackicus/Retain/window.ui')
class Window(Adw.ApplicationWindow):
    __gtype_name__ = 'RetainWindow'

    toast_overlay = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    sidebar = Gtk.Template.Child()
    navigation_view = Gtk.Template.Child()
    content_page = Gtk.Template.Child()
    primary_menu_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        app = self.get_application()
        self.app = app
        self.settings = app.settings
        self._roots = {}  # sidebar key -> root page
        self._current_key = None
        self._menu_deck_id = None  # the deck the sidebar's menu was opened on
        self._restore_size()
        self._add_actions()
        self.sidebar_controller = SidebarController(self, self.sidebar, app.scheduler)
        self.navigation_view.connect('notify::visible-page', self._on_visible_page)
        app.collection.connect('changed', self._on_collection_changed)
        self.connect('close-request', self._on_close_request)
        last = self.settings.get_string('last-page')
        self.show_root(last if self.sidebar_controller.has_key(last) else 'today')

    # -- size and closing ------------------------------------------------------------------

    def _restore_size(self):
        self.set_default_size(self.settings.get_int('window-width'),
                              self.settings.get_int('window-height'))
        if self.settings.get_boolean('window-maximized'):
            self.maximize()

    def _on_close_request(self, *_args):
        if not self.is_maximized():
            width, height = self.get_default_size()
            self.settings.set_int('window-width', width)
            self.settings.set_int('window-height', height)
        self.settings.set_boolean('window-maximized', self.is_maximized())
        if self._current_key:
            self.settings.set_string('last-page', self._current_key)
        return False

    # -- actions -----------------------------------------------------------------------------

    def _add_actions(self):
        self._keyed = []
        for name, callback, keyed in (
                ('search', self.on_search, True), ('today', lambda *a: self.show_root('today'),
                                                   True),
                ('browse', lambda *a: self.show_root('browse'), True),
                ('stats', lambda *a: self.show_root('stats'), True),
                ('study', self.on_study, True), ('back', self.on_back, True),
                ('toggle-sidebar', self.on_toggle_sidebar, True),
                ('deck-study', self.on_deck_study, False), ('deck-add', self.on_deck_add, False),
                ('deck-rename', self.on_deck_rename, False),
                ('deck-options', self.on_deck_options, False),
                ('deck-export', self.on_deck_export, False),
                ('deck-delete', self.on_deck_delete, False)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
            if keyed:
                self._keyed.append(action)

    def set_dialog_open(self, is_open):
        """Keyed window actions step aside while a dialog is open over the window."""
        for action in self._keyed:
            action.set_enabled(not is_open)

    def on_search(self, *_args):
        self.show_root('browse')
        page = self._roots.get('browse')
        if page is not None and hasattr(page, 'focus_search'):
            page.focus_search()

    def on_study(self, *_args):
        deck_id = self.current_deck_id()
        if deck_id is not None:
            self.study(deck_id)

    def on_back(self, *_args):
        if self.navigation_view.get_visible_page() is not self._roots.get(self._current_key):
            self.navigation_view.pop()
        elif self.split_view.get_collapsed() and self.split_view.get_show_content():
            self.split_view.set_show_content(False)

    def on_toggle_sidebar(self, *_args):
        if self.split_view.get_collapsed():
            self.split_view.set_show_content(not self.split_view.get_show_content())
        else:
            self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

    def set_menu_deck(self, deck_id):
        """The deck the sidebar's context menu acts on (None once it closes)."""
        self._menu_deck_id = deck_id

    def _target_deck(self):
        return self._menu_deck_id if self._menu_deck_id is not None else self.current_deck_id()

    def on_deck_study(self, *_args):
        deck_id = self._target_deck()
        if deck_id is not None:
            self.study(deck_id)

    def on_deck_add(self, *_args):
        from .dialogs import add_edit

        add_edit.present_add(self.app, self, deck_id=self._target_deck())

    def on_deck_rename(self, *_args):
        from .dialogs import deck_name

        deck_id = self._target_deck()
        if deck_id is not None:
            deck_name.present_rename(self.app, self, deck_id)

    def on_deck_options(self, *_args):
        from .dialogs import deck_options

        deck_id = self._target_deck()
        if deck_id is not None:
            deck_options.present(self.app, self, deck_id)

    def on_deck_export(self, *_args):
        from .dialogs import import_export

        import_export.present_export(self.app, self, deck_id=self._target_deck())

    def on_deck_delete(self, *_args):
        from .dialogs import deck_name

        deck_id = self._target_deck()
        if deck_id is not None:
            deck_name.confirm_delete(self.app, self, deck_id)

    # -- pages -------------------------------------------------------------------------------

    def show_root(self, key):
        """Show a destination's root page (made on first visit) and select it in the sidebar."""
        if not self.sidebar_controller.has_key(key):
            key = 'today'
        page = self._roots.get(key)
        if page is None:
            page = pages.make_root(key)
            if page is None:
                return
            self._roots[key] = page
        self._current_key = key
        self.navigation_view.replace([page])
        self.sidebar_controller.select(key)
        if self.split_view.get_collapsed():
            self.split_view.set_show_content(True)

    def push(self, page):
        self.navigation_view.push(page)

    def pop(self):
        self.navigation_view.pop()

    def study(self, deck_id, session=None):
        """Push the review page for a deck's queue, or for a custom session."""
        from .pages.review import ReviewPage

        page = ReviewPage(deck_id, session=session)
        self.push(page)
        return page

    def current_deck_id(self):
        """The deck of the page shown (a deck page, a review page), else the sidebar's
        selected deck, else None."""
        page = self.navigation_view.get_visible_page()
        deck_id = getattr(page, 'deck_id', None)
        if deck_id is not None:
            return deck_id
        if self._current_key and self._current_key.startswith('deck:'):
            return int(self._current_key[5:])
        return None

    def forget_deck(self, deck_id):
        """A deleted deck's root page goes; Today takes its place when it was shown."""
        key = f'deck:{deck_id}'
        if key in self._roots:
            del self._roots[key]
        if self._current_key == key:
            self.show_root('today')

    def _on_visible_page(self, *_args):
        page = self.navigation_view.get_visible_page()
        if page is not None:
            self.content_page.set_title(page.get_title() or _('Retain'))

    def _on_collection_changed(self, _collection, kind):
        if kind == 'decks':
            # A deck that is gone has no page; the sidebar follows on its own.
            existing = {deck.id for deck in self.app.collection.decks()}
            for key in list(self._roots):
                if key.startswith('deck:') and int(key[5:]) not in existing:
                    self.forget_deck(int(key[5:]))

    def undone(self, label):
        """An undo happened: the review page, if shown, takes its card back."""
        page = self.navigation_view.get_visible_page()
        if hasattr(page, 'undone'):
            page.undone(label)

    # -- messages ----------------------------------------------------------------------------

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)

    def announce(self, text):
        """Say something to a screen reader."""
        self.toast_overlay.announce(text, Gtk.AccessibleAnnouncementPriority.MEDIUM)


def later(callback, *args):
    """Run a callback from the main loop's idle (the next frame)."""
    GLib.idle_add(lambda: (callback(*args), GLib.SOURCE_REMOVE)[1])


GObject.type_ensure(Window)
