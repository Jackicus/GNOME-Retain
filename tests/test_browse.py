# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Browse page (pages/browse.py): what a search shows, the modes, the sorting, the
selection's actions and the empty states, over a temporary collection and a stand-in
application (the page reaches pages.app() for the collection and the toasts)."""

import contextlib
import time
import unittest

from tests import ROOT  # noqa: F401  (registers src/ as retain)
from tests.gtk import SCHEMA_ID, pump, requires_gtk, wait_for
from tests.support import add_basic, add_cloze, temporary_collection

_stand_ins = {}


def classes():
    """The stand-in application and window, made once GTK is known to work."""
    if _stand_ins:
        return _stand_ins
    from gi.repository import Adw, Gio

    from retain.scheduler import Scheduler

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.github.jackicus.Retain.BrowseTest',
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.settings = Gio.Settings.new(SCHEMA_ID)
            self.collection = None
            self.scheduler = None
            self.toasts = []
            self.reported = []

        def use(self, collection):
            self.collection = collection
            self.scheduler = Scheduler(collection)
            self.toasts = []
            self.reported = []

        def toast(self, text, undo=False, timeout=0):
            self.toasts.append((text, undo))

        def report(self, error, context=None):
            self.reported.append(error)

        def undo(self):
            return self.collection.undo() is not None

    class Window(Adw.Window):
        def __init__(self):
            super().__init__(default_width=1000, default_height=700)
            self.navigation_view = Adw.NavigationView()
            self.root_page = Adw.NavigationPage(title='Root', child=Adw.Bin())
            self.navigation_view.add(self.root_page)
            self.set_content(self.navigation_view)
            self.dialog_open = False

        def set_dialog_open(self, is_open):
            self.dialog_open = is_open

    _stand_ins.update(App=App, Window=Window)
    return _stand_ins


@requires_gtk
class HelpersTest(unittest.TestCase):
    """The page's text helpers (the module needs the gresource to import, as it holds the
    template class)."""

    def test_due_text(self):
        from retain.pages.browse import due_text

        today = 1000
        base = {'suspended': 0, 'buried': 0, 'state': 'review', 'due': today}
        self.assertEqual(due_text(base, today), 'today')
        self.assertEqual(due_text({**base, 'due': today + 3}, today), 'in 3 days')
        self.assertEqual(due_text({**base, 'due': today - 60}, today), '2 months ago')
        self.assertEqual(due_text({**base, 'state': 'new', 'due': 12}, today), 'new #12')
        self.assertEqual(due_text({**base, 'state': 'learning', 'due': 5}, today), 'learning')
        self.assertEqual(due_text({**base, 'suspended': 1}, today), '(suspended)')
        self.assertEqual(due_text({**base, 'buried': today}, today), '(buried)')
        self.assertEqual(due_text({**base, 'buried': today - 1}, today), 'today')

    def test_notes_only_keeps_the_first_card_of_each_note(self):
        from retain.pages.browse import notes_only

        rows = [{'note_id': 1, 'id': 10}, {'note_id': 2, 'id': 20}, {'note_id': 1, 'id': 11}]
        self.assertEqual([row['id'] for row in notes_only(rows)], [10, 20])

    def test_with_term_appends_once(self):
        from retain.pages.browse import with_term

        self.assertEqual(with_term('', 'is:due'), 'is:due')
        self.assertEqual(with_term('deck:Spanish ', 'is:due'), 'deck:Spanish is:due')
        self.assertEqual(with_term('deck:Spanish is:due', 'is:due'), 'deck:Spanish is:due')


@requires_gtk
class BrowsePageTest(unittest.TestCase):
    """A presented stand-in window; each test gets its own collection and page."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        stand_ins = classes()
        cls.app = stand_ins['App']()
        cls.app.set_default()
        cls.gtk_settings = Gtk.Settings.get_default()
        cls.animations = cls.gtk_settings.props.gtk_enable_animations
        cls.gtk_settings.props.gtk_enable_animations = False
        cls.window = stand_ins['Window']()
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
        self.app.set_default()
        self._stack = contextlib.ExitStack()
        self.collection = self._stack.enter_context(temporary_collection())
        self.app.use(self.collection)
        self.page = None

    def tearDown(self):
        self.window.set_focus(None)
        pump()
        self.window.navigation_view.replace([self.window.root_page])
        if self.page is not None:
            self.window.navigation_view.remove(self.page)
        pump()
        self._stack.close()

    # -- helpers ---------------------------------------------------------------------------

    def fill(self):
        """Three Spanish cards, two Geography ones; the ids of the Spanish notes."""
        spanish = self.collection.add_deck('Spanish')
        geography = self.collection.add_deck('Geography')
        notes = [add_basic(self.collection, spanish, front, back, tags=['verbs'])
                 for front, back in (('hablar', 'to speak'), ('comer', 'to eat'),
                                     ('vivir', 'to live'))]
        add_basic(self.collection, geography, 'Capital of Nowhere', 'Nowhere City')
        add_basic(self.collection, geography, 'Longest river', 'The Invented')
        return [note.id for note in notes]

    def show(self):
        from retain.pages.browse import BrowsePage

        self.page = BrowsePage()
        self.window.navigation_view.add(self.page)
        self.window.navigation_view.replace([self.page])
        self.assertTrue(wait_for(self.page.get_mapped), 'the page was not shown')
        return self.page

    def rows(self):
        store = self.page.selection.get_model()
        return [store.get_item(i) for i in range(store.get_n_items())]

    def search(self, query):
        self.page.search(query)
        pump()
        return self.rows()

    # -- tests -----------------------------------------------------------------------------

    def test_lists_every_card(self):
        self.fill()
        page = self.show()
        rows = self.search('')
        self.assertEqual(len(rows), 5)
        self.assertEqual(page.count_label.get_label(), '5 cards')
        self.assertEqual(page.stack.get_visible_child_name(), 'table')
        self.assertEqual(sorted(row.sort_field for row in rows),
                         ['Capital of Nowhere', 'Longest river', 'comer', 'hablar', 'vivir'])
        self.assertTrue(all(row.due.startswith('new #') for row in rows))
        self.assertEqual(rows[0].card, 'Card 1')

    def test_search_filters(self):
        self.fill()
        self.show()
        rows = self.search('deck:Spanish')
        self.assertEqual([row.deck for row in rows], ['Spanish'] * 3)
        self.assertEqual([row.sort_field for row in rows], ['comer', 'hablar', 'vivir'])
        self.assertEqual(self.collection.get('browse_query'), 'deck:Spanish')

    def test_bad_query_shows_the_banner(self):
        self.fill()
        page = self.show()
        self.search('deck:Spanish is:bogus')
        self.assertTrue(page.error_banner.get_revealed())
        self.assertIn('is:bogus', page.error_banner.get_title())
        self.search('deck:Spanish')
        self.assertFalse(page.error_banner.get_revealed())

    def test_suspending_the_selection(self):
        note_ids = self.fill()
        page = self.show()
        self.search('deck:Spanish')
        page.selection.select_all()
        pump()
        self.assertTrue(page.selection_bar.get_revealed())
        self.assertEqual(page.selection_label.get_label(), '3 selected')
        self.assertEqual(page.suspend_button.get_label(), 'Suspend')
        page.on_toggle_suspend()
        for note_id in note_ids:
            for card in self.collection.cards_of_note(note_id):
                self.assertTrue(card.suspended)
        self.assertEqual(self.app.toasts, [('Suspended 3 cards', True)])
        # The collection's change runs the query again, keeping the selection.
        self.assertTrue(wait_for(lambda: all(row.suspended for row in self.rows())))
        self.assertEqual(len(page.selected_rows()), 3)
        self.assertEqual(page.suspend_button.get_label(), 'Unsuspend')
        self.assertEqual(self.rows()[0].due, '(suspended)')
        page.on_toggle_suspend()
        self.assertFalse(self.collection.cards_of_note(note_ids[0])[0].suspended)
        self.assertEqual(self.app.toasts[-1], ('Unsuspended 3 cards', True))

    def test_notes_mode_collapses_siblings(self):
        deck = self.collection.add_deck('Spanish')
        add_cloze(self.collection, deck, 'Yo {{c1::hablo}} y tú {{c2::hablas}}')
        add_basic(self.collection, deck, 'comer', 'to eat')
        page = self.show()
        rows = self.search('')
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(row.card for row in rows), ['Card 1', 'Cloze 1', 'Cloze 2'])
        page.mode_group.set_active_name('notes')
        pump()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(page.count_label.get_label(), '2 notes')
        self.assertEqual(sorted(row.card for row in rows), ['Basic', 'Cloze'])
        page.mode_group.set_active_name('cards')
        pump()
        self.assertEqual(len(self.rows()), 3)

    def test_sorting_by_a_column(self):
        from gi.repository import Gtk

        self.fill()
        page = self.show()
        self.search('')
        page.column_view.sort_by_column(page.deck_column, Gtk.SortType.DESCENDING)
        pump()
        self.assertEqual([row.deck for row in self.rows()],
                         ['Spanish', 'Spanish', 'Spanish', 'Geography', 'Geography'])
        page.column_view.sort_by_column(page.deck_column, Gtk.SortType.ASCENDING)
        pump()
        self.assertEqual([row.deck for row in self.rows()][:2], ['Geography', 'Geography'])
        page.column_view.sort_by_column(page.sort_column, Gtk.SortType.DESCENDING)
        pump()
        self.assertEqual(self.rows()[0].sort_field, 'vivir')

    def test_empty_states(self):
        page = self.show()
        self.search('')
        self.assertEqual(page.stack.get_visible_child_name(), 'no-cards')
        self.fill()
        self.search('nothingmatchesthis')
        self.assertEqual(page.stack.get_visible_child_name(), 'no-results')
        self.assertEqual(page.count_label.get_label(), '0 cards')
        self.search('')
        self.assertEqual(page.stack.get_visible_child_name(), 'table')

    def test_the_query_is_restored(self):
        self.fill()
        self.collection.set('browse_query', 'tag:verbs')
        page = self.show()
        self.assertEqual(page.search_entry.get_text(), 'tag:verbs')
        self.assertTrue(wait_for(lambda: len(self.rows()) == 3))

    def test_deleting_and_marking(self):
        note_ids = self.fill()
        page = self.show()
        self.search('deck:Spanish')
        page.selection.select_item(0, True)
        pump()
        page.on_toggle_mark()
        self.assertEqual(self.app.toasts[-1], ('Marked 1 note', True))
        self.assertTrue(wait_for(lambda: self.rows()[0].marked))
        page.on_delete()
        self.assertEqual(self.app.toasts[-1], ('Deleted 1 note', True))
        self.assertTrue(wait_for(lambda: len(self.rows()) == 2))
        self.assertEqual(len(self.collection.find_notes('deck:Spanish')), 2)
        self.assertTrue(self.app.undo())
        self.assertTrue(wait_for(lambda: len(self.rows()) == 3))
        self.assertEqual(len(note_ids), 3)


if __name__ == '__main__':
    unittest.main()
