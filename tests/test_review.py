# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The review page (pages/review.py) over a temporary collection, with a stand-in
application as the default one (what pages.app() finds): the question shows, Show Answer
reveals the buttons with the scheduler's intervals, an answer advances, the end of the queue
shows the finished state, an undone answer comes back, two-button mode hides Hard and Easy,
the counts follow, and the card actions go through the collection. The card view renders
without WebKit here (its fallback label), so each test stays quick."""

import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import SCHEMA_ID, pump, requires_gtk
from tests.support import add_basic, temporary_collection

from gi.repository import Gio, GLib

from retain import notetypes
from retain.fsrs import AGAIN, GOOD, HARD
from retain.scheduler import Scheduler

SETTINGS_KEYS = ('two-button-mode', 'show-intervals', 'show-remaining', 'auto-play-audio',
                 'card-text-scale')


class StandInApp(Gio.Application):
    """What the page asks of the application: the collection, the scheduler, the settings
    (the memory backend's), toasts and reports (recorded)."""

    def __init__(self, collection):
        super().__init__(application_id='io.github.jackicus.Retain.ReviewTest',
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.collection = collection
        self.scheduler = Scheduler(collection)
        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.toasts = []
        self.reported = []

    def toast(self, text, undo=False, timeout=0):
        self.toasts.append((text, undo))

    def report(self, error, context=None):
        self.reported.append(error)

    def undo(self):
        return self.collection.undo()


@requires_gtk
class ReviewPageTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from retain.widgets import card_view

        cls._webkit = card_view.WebKit
        card_view.WebKit = None  # the fallback renderer: no web process in the tests

    @classmethod
    def tearDownClass(cls):
        from retain.widgets import card_view

        card_view.WebKit = cls._webkit

    def setUp(self):
        self.collection = self.enterContext(temporary_collection())
        self.app = StandInApp(self.collection)
        self.app.set_default()
        self.app.settings.set_boolean('auto-play-audio', False)
        for key in SETTINGS_KEYS:
            self.addCleanup(self.app.settings.reset, key)
        self.deck = self.collection.add_deck('Invented Deck')
        self.notes = [add_basic(self.collection, self.deck, word, meaning)
                      for word, meaning in (('uno', 'one'), ('dos', 'two'), ('tres', 'three'))]

    def page(self, deck=None):
        from retain.pages.review import ReviewPage

        page = ReviewPage((deck or self.deck).id)
        self.addCleanup(page.run_dispose)
        return page

    def card_text(self, page):
        return page.card_view._label.get_text()

    def test_question_shows(self):
        page = self.page()
        self.assertEqual(page.deck_id, self.deck.id)
        self.assertEqual(page.get_title(), 'Invented Deck')
        self.assertEqual(page.side, 'question')
        self.assertEqual(page.content_stack.get_visible_child_name(), 'study')
        self.assertEqual(page.answer_stack.get_visible_child_name(), 'show')
        self.assertEqual(self.card_text(page), 'uno')
        self.assertFalse(page.type_entry.get_visible())
        self.assertFalse(page.answer(GOOD))  # not before the answer shows

    def test_show_answer_reveals_the_buttons_with_intervals(self):
        page = self.page()
        expected = self.app.scheduler.preview(page.card)
        page.show_answer()
        self.assertEqual(page.side, 'answer')
        self.assertEqual(page.answer_stack.get_visible_child_name(), 'rate')
        self.assertIn('one', self.card_text(page))
        for rating, label in page._intervals.items():
            self.assertTrue(label.get_visible())
            self.assertEqual(label.get_text(), expected[rating])
        self.assertTrue(page.good_button.has_css_class('suggested-action'))
        self.assertFalse(page.again_button.has_css_class('suggested-action'))

    def test_intervals_hide_with_the_setting(self):
        self.app.settings.set_boolean('show-intervals', False)
        page = self.page()
        page.show_answer()
        self.assertFalse(page.good_interval.get_visible())

    def test_good_answers_and_advances(self):
        page = self.page()
        first = page.card.id
        page.show_answer()
        self.assertTrue(page.answer(GOOD))
        self.assertEqual(len(self.collection.reviews_of(first)), 1)
        self.assertEqual(page.side, 'question')
        self.assertNotEqual(page.card.id, first)
        self.assertEqual(self.card_text(page), 'dos')
        self.assertEqual(page.answer_stack.get_visible_child_name(), 'show')

    def test_keys(self):
        from gi.repository import Gdk

        page = self.page()
        first = page.card.id
        self.assertEqual(page._on_key(None, Gdk.KEY_space, 0, 0), Gdk.EVENT_STOP)
        self.assertEqual(page.side, 'answer')
        self.assertEqual(page._on_key(None, Gdk.KEY_KP_3, 0, 0), Gdk.EVENT_STOP)
        self.assertNotEqual(page.card.id, first)
        self.assertEqual(self.collection.reviews_of(first)[0]['rating'], GOOD)
        self.assertEqual(page._on_key(None, Gdk.KEY_x, 0, 0), Gdk.EVENT_PROPAGATE)
        # Return answers Good once the answer shows; Ctrl+1 flags.
        page.run_key('show-answer-alt')
        self.assertEqual(page._on_key(None, Gdk.KEY_1, 0, Gdk.ModifierType.CONTROL_MASK),
                         Gdk.EVENT_STOP)
        self.assertEqual(self.collection.card(page.card.id).flag, 1)
        self.assertEqual(page.side, 'answer')

    def test_finished_state(self):
        page = self.page(self.collection.add_deck('Empty Deck'))
        self.assertIsNone(page.card)
        self.assertIsNone(page.side)
        self.assertEqual(page.content_stack.get_visible_child_name(), 'finished')
        self.assertFalse(page.toolbar_view.get_reveal_bottom_bars())
        self.assertFalse(page._actions['rate'].get_enabled())

    def test_the_queue_runs_out(self):
        page = self.page()
        for _ in range(3):
            page.show_answer()
            page.answer(4)  # Easy graduates each new card at once
        self.assertIsNone(page.card)
        self.assertEqual(page.content_stack.get_visible_child_name(), 'finished')

    def test_undone_brings_the_card_back(self):
        page = self.page()
        first = page.card.id
        page.show_answer()
        page.answer(GOOD)
        self.assertNotEqual(page.card.id, first)
        label = self.app.undo()
        page.undone(label)
        self.assertEqual(page.card.id, first)
        self.assertEqual(page.side, 'question')
        self.assertEqual(self.collection.reviews_of(first), [])
        self.assertEqual(page.answer_stack.get_visible_child_name(), 'show')

    def test_undone_of_something_else_keeps_the_card(self):
        page = self.page()
        first = page.card.id
        page.run_key('flag-2')
        self.assertEqual(self.collection.card(first).flag, 2)
        page.undone(self.app.undo())
        self.assertEqual(page.card.id, first)
        self.assertEqual(page.card.flag, 0)

    def test_two_button_mode(self):
        self.app.settings.set_boolean('two-button-mode', True)
        page = self.page()
        self.assertFalse(page.hard_button.get_visible())
        self.assertFalse(page.easy_button.get_visible())
        self.assertTrue(page.again_button.get_visible())
        self.assertTrue(page.good_button.get_visible())
        first = page.card.id
        page.show_answer()
        self.assertTrue(page.run_key('hard'))  # still reachable from the keyboard
        self.assertEqual(self.collection.reviews_of(first)[0]['rating'], HARD)

    def test_counts_follow(self):
        page = self.page()
        labels = page.counts._labels
        self.assertEqual([labels[k].get_text() for k in ('new', 'learning', 'due')],
                         ['3', '0', '0'])
        self.assertIn('<u>', labels['new'].get_label())  # the kind shown is underlined
        page.show_answer()
        page.answer(GOOD)
        self.assertEqual(labels['new'].get_text(), '2')
        self.assertEqual(labels['learning'].get_text(), '1')

    def test_counts_hide_with_the_setting(self):
        page = self.page()
        self.assertTrue(page.counts.get_visible())
        self.app.settings.set_boolean('show-remaining', False)
        pump()
        self.assertFalse(page.counts.get_visible())

    def test_suspend_moves_on_with_an_undo_toast(self):
        page = self.page()
        first = page.card.id
        self.assertTrue(page.run_key('suspend'))
        self.assertEqual(self.app.toasts[-1], ('Card suspended', True))
        self.assertEqual(self.collection.card(first).suspended, 1)
        self.assertNotEqual(page.card.id, first)
        page.undone(self.app.undo())
        self.assertEqual(page.card.id, first)

    def test_bury_and_delete(self):
        page = self.page()
        first = page.card
        page.run_key('bury')
        self.assertTrue(self.collection.card(first.id).buried)
        self.assertNotEqual(page.card.id, first.id)
        second = page.card
        page.run_key('delete')
        self.assertIsNone(self.collection.note(second.note_id))
        self.assertEqual(self.app.toasts[-1], ('Note deleted', True))
        self.assertEqual(self.card_text(page), 'tres')

    def test_mark_and_flag(self):
        page = self.page()
        self.assertTrue(page.run_key('mark'))
        self.assertEqual(self.collection.note(page.note.id).marked, 1)
        self.assertTrue(page._actions['mark'].get_state().get_boolean())
        page.run_key('mark')
        self.assertEqual(self.collection.note(page.note.id).marked, 0)
        page._actions['flag'].activate(GLib.Variant('i', 3))
        self.assertEqual(self.collection.card(page.card.id).flag, 3)
        self.assertTrue(page.flag_button.has_css_class('accent'))
        page.run_key('flag-3')  # the same flag again removes it
        self.assertEqual(self.collection.card(page.card.id).flag, 0)
        self.assertFalse(page.flag_button.has_css_class('accent'))

    def test_typed_answer(self):
        deck = self.collection.add_deck('Typed Deck')
        typed = self.collection.notetype_by_name(notetypes.stock('basic-typed').name)
        self.collection.add_note(typed.id, deck.id, ['cuatro', 'four'])
        page = self.page(deck)
        self.assertTrue(page.type_entry.get_visible())
        page.type_entry.set_text('fuor')
        page._on_entry_activate(page.type_entry)
        self.assertEqual(page.side, 'answer')
        self.assertFalse(page.type_entry.get_visible())
        self.assertIn('four', self.card_text(page))
        self.assertIn('f-uor', self.card_text(page))  # the missing letter's place

    def test_leech_toast(self):
        deck = self.collection.add_deck('Leech Deck')
        note = add_basic(self.collection, deck, 'cinco', 'five')
        card = self.collection.cards_of_note(note.id)[0]
        today = self.collection.today()
        self.collection.db.execute(
            'UPDATE cards SET state = ?, due = ?, interval = ?, stability = ?, difficulty = ?, '
            'lapses = ?, reps = ?, last_review = ? WHERE id = ?',
            ('review', today, 5, 5.0, 6.0, 7, 10, self.collection.day_start(today) - 86400 * 5,
             card.id))
        page = self.page(deck)
        self.assertEqual(page.card.id, card.id)
        page.show_answer()
        page.answer(AGAIN)
        self.assertIn(('Card is a leech', False), self.app.toasts)
        self.assertIn('leech', self.collection.note(note.id).tags)

    def test_card_info_rows(self):
        from retain.dialogs import card_info

        page = self.page()
        page.show_answer()
        page.answer(GOOD)
        card = self.collection.card(self.collection.cards_of_note(self.notes[0].id)[0].id)
        rows = dict(card_info.rows(self.app, card))
        self.assertEqual(rows['Reviews'], '1')
        self.assertEqual(rows['Deck'], 'Invented Deck')
        self.assertEqual(rows['Card ID'], str(card.id))
        self.assertIn('Stability', rows)
        self.assertEqual(len(card_info.history(self.app, card)), 1)
        self.assertEqual(card_info.history(self.app, card)[0][1], 'Good')
        dialog = card_info.build(self.app, card)
        self.assertEqual(dialog.get_title(), 'Card Information')


if __name__ == '__main__':
    unittest.main()
