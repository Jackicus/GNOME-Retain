# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Today page, and the stand-ins the deck page and deck dialog tests share.

    with DeckPagesTestCase: self.app (the default application, over a temporary collection
    with Spanish, Spanish::Verbs and Empty), self.window (records study(), show_root(),
    forget_deck(), add_toast() and set_dialog_open())

The stand-in application is an Adw.Application that is never run: it holds the collection,
the scheduler and the settings, and records toasts and reports. The window is a presented
Adw.Window with a navigation view a test shows a page in.
"""

import shutil
import tempfile
import time
import unittest

from tests import ROOT  # noqa: F401  (registers src/ as retain)
from tests.gtk import SCHEMA_ID, pump, requires_gtk, wait_for
from tests.support import add_basic

_stand_ins = {}


def classes():
    """The stand-in classes, made once GTK is known to work."""
    if _stand_ins:
        return _stand_ins
    from gi.repository import Adw, Gio, Gtk

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.github.jackicus.Retain.DeckPagesTest',
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.settings = Gio.Settings.new(SCHEMA_ID)
            self.collection = None
            self.scheduler = None
            self.toasts = []  # (text, undo)
            self.reported = []

        def reset(self):
            self.toasts = []
            self.reported = []

        def toast(self, text, undo=False, timeout=0):
            self.toasts.append((text, undo))

        def report(self, error, context=None):
            self.reported.append(error)

        def undo(self):
            return self.collection.undo()

    class Window(Adw.Window):
        def __init__(self):
            super().__init__(default_width=900, default_height=700)
            self.navigation_view = Adw.NavigationView()
            self.root_page = Adw.NavigationPage(title='Root', child=Gtk.Box())
            self.navigation_view.add(self.root_page)
            self.set_content(self.navigation_view)
            self.reset()

        def reset(self):
            self.studied = []  # (deck_id, session)
            self.shown = []  # show_root keys
            self.forgotten = []
            self.toasts = []
            self.dialog_open = []

        def study(self, deck_id, session=None):
            self.studied.append((deck_id, session))

        def show_root(self, key):
            self.shown.append(key)

        def forget_deck(self, deck_id):
            self.forgotten.append(deck_id)

        def add_toast(self, toast):
            self.toasts.append(toast)

        def set_dialog_open(self, is_open):
            self.dialog_open.append(is_open)

    _stand_ins.update(App=App, Window=Window)
    return _stand_ins


def stand_in_app():
    """The stand-in application, made once and made the default one (pages.app())."""
    stand_ins = classes()
    if 'app' not in stand_ins:
        stand_ins['app'] = stand_ins['App']()
    stand_ins['app'].set_default()
    return stand_ins['app']


def find_all(widget, cls):
    """Every widget of class cls under widget (itself included), depth first."""
    found = [widget] if isinstance(widget, cls) else []
    child = widget.get_first_child()
    while child is not None:
        found.extend(find_all(child, cls))
        child = child.get_next_sibling()
    return found


@requires_gtk
class DeckPagesTestCase(unittest.TestCase):
    """A presented stand-in window and application over a fresh temporary collection with
    a few invented decks: Spanish (three new cards), Spanish::Verbs (two, tagged verbs)
    and Empty (no cards)."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        cls.app = stand_in_app()
        cls.gtk_settings = Gtk.Settings.get_default()
        cls.animations = cls.gtk_settings.props.gtk_enable_animations
        cls.gtk_settings.props.gtk_enable_animations = False
        cls.window = classes()['Window']()
        cls.window.present()
        deadline = time.monotonic() + 2
        while not cls.window.get_mapped() and time.monotonic() < deadline:
            pump(20)
            time.sleep(0.005)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()
        cls.gtk_settings.props.gtk_enable_animations = cls.animations
        del cls.window

    def setUp(self):
        from retain.collection import Collection
        from retain.scheduler import Scheduler

        self.app.set_default()
        self.app.reset()
        self.window.reset()
        self.directory = tempfile.mkdtemp(prefix='retain-test-')
        self.collection = Collection(f'{self.directory}/collection.sqlite', backups=False)
        self.app.collection = self.collection
        self.app.scheduler = Scheduler(self.collection)
        self.populate()
        self._roots = []
        self._dialogs = []

    def populate(self):
        self.spanish = self.collection.add_deck('Spanish')
        self.verbs = self.collection.add_deck('Spanish::Verbs')
        self.empty = self.collection.add_deck('Empty')
        for front, back in (('hola', 'hello'), ('adiós', 'goodbye'), ('gracias', 'thanks')):
            add_basic(self.collection, self.spanish, front, back)
        for front, back in (('hablar', 'to speak'), ('comer', 'to eat')):
            add_basic(self.collection, self.verbs, front, back, tags=['verbs'])
        self.collection.clear_undo()

    def tearDown(self):
        for dialog in self._dialogs:
            dialog.force_close()
        self.window.set_focus(None)
        pump()
        self.window.navigation_view.replace([self.window.root_page])
        for page in self._roots:
            self.window.navigation_view.remove(page)
        pump()
        self.collection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def show(self, page):
        """Show a page as the navigation view's root and wait for it to map."""
        self.window.navigation_view.add(page)
        self._roots.append(page)
        self.window.navigation_view.replace([page])
        self.assertTrue(wait_for(page.get_mapped), f'{type(page).__name__} not shown')
        return page

    def keep(self, dialog):
        """A dialog to close at the end of the test."""
        self._dialogs.append(dialog)
        pump()
        return dialog


class TodayPageTest(DeckPagesTestCase):

    def page(self):
        from retain.pages.today import TodayPage

        return self.show(TodayPage())

    def rows(self, page):
        from retain.pages.today import DeckRow

        return find_all(page.decks_list, DeckRow)

    def test_lists_the_decks_with_their_counts(self):
        page = self.page()
        rows = self.rows(page)
        # In name order; Default, empty beside real decks, is hidden; Verbs sits under
        # Spanish.
        self.assertEqual([row.get_title() for row in rows], ['Empty', 'Spanish', 'Verbs'])
        self.assertEqual([row.deck_id for row in rows],
                         [self.empty.id, self.spanish.id, self.verbs.id])
        self.assertEqual(rows[0].get_subtitle(), 'Nothing due')
        self.assertEqual(rows[1].get_subtitle(), '5 new')
        self.assertEqual(rows[2].get_subtitle(), '2 new')
        self.assertFalse(rows[0].study_button.get_visible())
        self.assertTrue(rows[1].study_button.get_visible())
        self.assertEqual(page.stack.get_visible_child_name(), 'decks')
        self.assertEqual(page.hero_stack.get_visible_child_name(), 'counts')
        self.assertEqual(page.new_label.get_text(), '5')
        self.assertEqual(page.due_label.get_text(), '0')
        self.assertIn('Nothing studied yet today', page.progress_label.get_text())
        self.assertTrue(page.date_label.get_text())

    def test_study_button_studies_the_deck(self):
        page = self.page()
        self.rows(page)[1].study_button.emit('clicked')
        self.assertEqual(self.window.studied, [(self.spanish.id, None)])

    def test_a_row_opens_the_deck_page(self):
        page = self.page()
        row = self.rows(page)[2]
        page.decks_list.emit('row-activated', row)
        self.assertEqual(self.window.shown, [f'deck:{self.verbs.id}'])

    def test_all_caught_up_when_nothing_is_due(self):
        self.collection.suspend(self.collection.find_cards('deck:Spanish'))
        page = self.page()
        self.assertEqual(page.hero_stack.get_visible_child_name(), 'done')
        self.assertEqual(self.rows(page)[1].get_subtitle(), 'Nothing due')

    def test_empty_collection_offers_add_and_import(self):
        self.collection.remove_deck(self.spanish.id)
        self.collection.remove_deck(self.empty.id)
        page = self.page()
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        self.assertEqual(page.empty_page.get_title(), 'No Decks Yet')

    def test_refreshes_after_a_change_while_shown(self):
        page = self.page()
        self.collection.add_deck('Geography')
        self.assertTrue(wait_for(
            lambda: 'Geography' in [row.get_title() for row in self.rows(page)]))

    def test_counts_text(self):
        from retain.pages.today import counts_text, progress_text

        self.assertEqual(counts_text(3, 1, 12), '3 new · 1 learning · 12 due')
        self.assertEqual(counts_text(0, 0, 4), '4 due')
        self.assertEqual(counts_text(0, 0, 0), 'Nothing due')
        summary = {'reviews': 12, 'minutes': 4.2, 'correct_percent': 91.7, 'streak_days': 7}
        self.assertEqual(progress_text(summary),
                         '12 cards in 4 minutes · 92% correct · 7-day streak')
        summary = {'reviews': 0, 'minutes': 0, 'correct_percent': None, 'streak_days': 0}
        self.assertEqual(progress_text(summary), 'Nothing studied yet today')


if __name__ == '__main__':
    unittest.main()
