# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""pages/stats.py over a temporary collection with a few days of invented reviews (the
scheduler on a Clock, as test_stats.py): the page fills, follows the deck and the range,
describes its charts, and refreshes when the collection changes."""

import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from tests.gtk import requires_gtk, wait_for
from tests.support import Clock, add_basic, temporary_collection

AGAIN, GOOD, EASY = 1, 3, 4


@requires_gtk
class StatsPageTest(unittest.TestCase):
    """Deck Alpha (cards A, B) and deck Beta (card X) over four days: 8 reviews in all,
    7 of Alpha's, 3 today."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        from retain.pages.stats import StatsPage
        from retain.scheduler import Scheduler

        cls.Gtk = Gtk
        cls.StatsPage = StatsPage
        cls.Scheduler = Scheduler

    def setUp(self):
        self.clock = Clock()
        patcher = mock.patch('time.time', lambda: self.clock.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        context = temporary_collection()
        self.collection = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        scheduler = self.Scheduler(self.collection)
        self.alpha = self.collection.add_deck('Alpha')
        self.beta = self.collection.add_deck('Beta')
        a = self.collection.cards_of_note(add_basic(self.collection, self.alpha, 'a', '1').id)[0]
        b = self.collection.cards_of_note(add_basic(self.collection, self.alpha, 'b', '2').id)[0]
        x = self.collection.cards_of_note(add_basic(self.collection, self.beta, 'x', '9').id)[0]

        def answer(card, rating, seconds):
            scheduler.answer(self.collection.card(card.id), rating, now=self.clock.now,
                             duration_ms=seconds * 1000)

        answer(a, GOOD, 3)
        answer(b, AGAIN, 5)
        answer(x, GOOD, 2)
        self.clock.advance(days=1)
        answer(a, GOOD, 4)
        answer(b, GOOD, 2)
        self.clock.advance(days=2)
        answer(b, EASY, 1)
        answer(a, AGAIN, 6)
        answer(a, GOOD, 1)

    def make_page(self):
        page = self.StatsPage(collection=self.collection)
        page.refresh()
        return page

    def test_the_page_fills_from_the_collection(self):
        page = self.make_page()
        self.assertEqual(page.get_tag(), 'stats')
        self.assertEqual(page.get_title(), 'Statistics')
        self.assertIsNone(page.deck_id)
        self.assertEqual(page.tile_studied.value.get_text(), '3')
        self.assertEqual(page.tile_streak.value.get_text(), '1')
        self.assertEqual(page.total_tiles['cards'].value.get_text(), '3')
        self.assertEqual(page.total_tiles['reviews'].value.get_text(), '8')
        self.assertIn('3 days studied', page.heatmap_caption.get_text())
        self.assertIn('longest streak 2 days', page.heatmap_caption.get_text())
        self.assertEqual(page.donut.centre, '3')
        self.assertEqual(len(page.hourly_chart.bars), 24)
        self.assertEqual(len(page.reviews_chart.bars), 30)
        self.assertEqual(page.deck_chooser.get_model().get_n_items(),
                         len(self.collection.decks()) + 1)

    def test_every_chart_has_an_accessible_description(self):
        page = self.make_page()
        for chart in (page.heatmap, page.due_chart, page.reviews_chart, page.donut,
                      page.intervals_chart, page.difficulty_chart, page.hourly_chart):
            self.assertTrue(chart.description, type(chart).__name__)
        self.assertIn('8 in all', page.heatmap.description)
        self.assertIn('3 days', page.heatmap.description)
        self.assertTrue(page.button_bars['learning'][0].description)

    def test_switching_the_deck_requeries_and_is_remembered(self):
        from retain.pages import stats as module
        from retain.stats import Stats

        page = self.make_page()
        with mock.patch.object(module, 'Stats', wraps=Stats) as spy:
            page.deck_chooser.set_selected(1)  # Alpha, the first deck after All Decks
            self.assertEqual(page.deck_id, self.alpha.id)
            self.assertEqual(spy.call_args.args[1], self.alpha.id)
        self.assertEqual(self.collection.get('stats_deck'), self.alpha.id)
        self.assertEqual(page.last_stats.deck_id, self.alpha.id)
        self.assertEqual(page.total_tiles['reviews'].value.get_text(), '7')
        self.assertEqual(page.total_tiles['cards'].value.get_text(), '2')
        again = self.make_page()  # a new page starts on the remembered deck
        self.assertEqual(again.deck_id, self.alpha.id)
        self.assertEqual(again.deck_chooser.get_selected(), 1)

    def test_switching_the_range_requeries_and_groups_by_week(self):
        from retain.pages import stats as module
        from retain.stats import Stats

        page = self.make_page()
        self.assertEqual(page.range_days(), 30)
        with mock.patch.object(module, 'Stats', wraps=Stats) as spy:
            page.range_group.set_active_name('year')
            self.assertEqual(spy.call_count, 1)
        self.assertEqual(page.range_days(), 365)
        self.assertEqual(len(page.reviews_chart.bars), 53)
        self.assertEqual(len(page.due_chart.bars), 53)
        self.assertIn('the last year', page.reviews_caption.get_text())
        page.range_group.set_active_name('all')
        self.assertIsNone(page.range_days())
        self.assertGreaterEqual(len(page.reviews_chart.bars), 30)

    def test_a_collection_change_refreshes_the_mapped_page(self):
        page = self.StatsPage(collection=self.collection)
        window = self.Gtk.Window(default_width=800, default_height=600)
        window.set_child(page)
        window.present()
        self.addCleanup(window.destroy)
        self.assertTrue(wait_for(page.get_mapped))
        first = page.last_stats
        self.assertIsNotNone(first)  # mapping refreshed
        self.collection.add_deck('Gamma')
        self.assertEqual(page.deck_chooser.get_model().get_n_items(),
                         len(self.collection.decks()) + 1)
        self.assertTrue(wait_for(lambda: page.last_stats is not first))
        self.assertEqual(page.total_tiles['cards'].value.get_text(), '3')
