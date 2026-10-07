# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""stats.py, over reviews the scheduler logged on invented days.

The collection stamps ids with the clock, so the Clock stands in for `time.time` while the
collection and its reviews are made; the Stats then read the same clock through `now`.
"""

import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from retain import stats
from retain.scheduler import Scheduler
from retain.stats import Stats
from tests.support import Clock, add_basic, temporary_collection

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4


class StatsTestCase(unittest.TestCase):
    """A collection and scheduler on the Clock."""

    def setUp(self):
        self.clock = Clock()
        patcher = mock.patch('time.time', lambda: self.clock.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        context = temporary_collection()
        self.collection = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.scheduler = Scheduler(self.collection)
        self.today = self.collection.today(self.clock.now)

    def answer(self, card_id, rating, duration_ms=0):
        card = self.collection.card(card_id)
        return self.scheduler.answer(card, rating, now=self.clock.now, duration_ms=duration_ms)

    def card_of(self, note):
        return self.collection.cards_of_note(note.id)[0].id

    def stats(self, deck=None, now=None):
        return Stats(self.collection, deck.id if deck else None,
                     now=self.clock.now if now is None else now)


class ScenarioTest(StatsTestCase):
    """Deck Alpha with cards A, B, C and deck Beta with card X, over four days:

        day 0   A new Good (3 s), B new Again (5 s), X new Good (2 s)
        day 1   A learning Good (4 s, graduates), B learning Good (2 s)
        day 2   nothing
        day 3   B learning Easy (1 s, graduates), A review Again (6 s, lapses),
                A relearning Good (1.5 s, graduates); note D added to Alpha

    So Alpha has 7 reviews (5 of them not Again) in 22.5 s over 3 of 4 days."""

    def setUp(self):
        super().setUp()
        self.alpha = self.collection.add_deck('Alpha')
        self.beta = self.collection.add_deck('Beta')
        self.a = self.card_of(add_basic(self.collection, self.alpha, 'a', '1'))
        self.b = self.card_of(add_basic(self.collection, self.alpha, 'b', '2'))
        self.c = self.card_of(add_basic(self.collection, self.alpha, 'c', '3'))
        self.x = self.card_of(add_basic(self.collection, self.beta, 'x', '9'))
        self.day0 = self.clock.now
        self.answer(self.a, GOOD, 3000)
        self.answer(self.b, AGAIN, 5000)
        self.answer(self.x, GOOD, 2000)
        self.clock.advance(days=1)
        self.day1 = self.clock.now
        self.answer(self.a, GOOD, 4000)
        self.answer(self.b, GOOD, 2000)
        self.clock.advance(days=2)
        self.answer(self.b, EASY, 1000)
        self.answer(self.a, AGAIN, 6000)
        self.answer(self.a, GOOD, 1500)
        self.d = self.card_of(add_basic(self.collection, self.alpha, 'd', '4'))
        self.today = self.collection.today(self.clock.now)

    # -- today ----------------------------------------------------------------------------

    def test_today_summary_counts_the_deck_s_reviews_today(self):
        summary = self.stats(self.alpha).today_summary()
        self.assertEqual(summary['reviews'], 3)
        self.assertAlmostEqual(summary['minutes'], 8500 / 60000)
        self.assertEqual((summary['again'], summary['hard'], summary['good'], summary['easy']),
                         (1, 0, 1, 1))
        self.assertEqual(summary['new_introduced'], 0)
        self.assertEqual(summary['relearned'], 1)
        self.assertAlmostEqual(summary['correct_percent'], 200 / 3)
        self.assertEqual(summary['streak_days'], 1)

    def test_today_summary_on_the_first_day(self):
        summary = self.stats(self.alpha, now=self.day0).today_summary()
        self.assertEqual(summary['reviews'], 2)
        self.assertEqual(summary['new_introduced'], 2)
        self.assertEqual(summary['again'], 1)
        self.assertAlmostEqual(summary['correct_percent'], 50.0)
        self.assertAlmostEqual(summary['minutes'], 8000 / 60000)
        self.assertEqual(self.stats(now=self.day0).today_summary()['reviews'], 3)

    def test_streak_counts_consecutive_days_back_from_today_or_yesterday(self):
        self.assertEqual(self.stats(self.alpha, now=self.day1).today_summary()['streak_days'], 2)
        day2 = self.day1 + 86400  # nothing today: the streak is the one ending yesterday
        self.assertEqual(self.stats(self.alpha, now=day2).today_summary()['streak_days'], 2)
        day4 = self.clock.now + 86400  # only yesterday studied
        self.assertEqual(self.stats(self.alpha, now=day4).today_summary()['streak_days'], 1)
        day5 = self.clock.now + 2 * 86400  # neither today nor yesterday: no streak
        self.assertEqual(self.stats(self.alpha, now=day5).today_summary()['streak_days'], 0)
        self.assertEqual(self.stats(self.beta).today_summary()['streak_days'], 0)
        self.assertEqual(self.stats(self.beta, now=self.day1).today_summary()['streak_days'], 1)

    # -- the past -------------------------------------------------------------------------

    def test_heatmap_omits_days_without_reviews(self):
        heatmap = self.stats(self.alpha).review_heatmap()
        self.assertEqual(heatmap, {self.today - 3: 2, self.today - 2: 2, self.today: 3})
        self.assertEqual(self.stats(self.alpha).review_heatmap(days_back=2), {self.today: 3})
        self.assertEqual(self.stats().review_heatmap()[self.today - 3], 3)

    def test_reviews_per_day_by_before_state_with_zero_days(self):
        rows = self.stats(self.alpha).reviews_per_day(days_back=4)
        zero = {'new': 0, 'learning': 0, 'review': 0, 'relearning': 0}
        self.assertEqual(rows, [
            (self.today - 3, {**zero, 'new': 2}),
            (self.today - 2, {**zero, 'learning': 2}),
            (self.today - 1, zero),
            (self.today, {**zero, 'learning': 1, 'review': 1, 'relearning': 1}),
        ])

    def test_answer_buttons_by_learning_young_and_mature(self):
        buttons = self.stats(self.alpha).answer_buttons()
        self.assertEqual(buttons['learning'], {1: 1, 2: 0, 3: 4, 4: 1})
        self.assertEqual(buttons['young'], {1: 1, 2: 0, 3: 0, 4: 0})
        self.assertEqual(buttons['mature'], {1: 0, 2: 0, 3: 0, 4: 0})

    def test_retention_counts_review_state_reviews_only(self):
        retention = self.stats(self.alpha).retention()
        self.assertEqual((retention['passed'], retention['failed']), (0, 1))
        self.assertEqual(retention['percent'], 0.0)
        self.assertEqual(retention['young']['failed'], 1)
        self.assertIsNone(retention['mature']['percent'])

    def test_retention_splits_mature_by_elapsed_days(self):
        self.collection.db.execute(
            'UPDATE revlog SET elapsed_days = 40 WHERE card_id = ? AND state = ?',
            (self.a, 'review'))
        self.collection.db.execute(
            'UPDATE cards SET last_review = ? WHERE id = ?', (int(self.clock.now - 86400), self.b))
        self.answer(self.b, GOOD, 1000)  # a review-state pass, one day on
        retention = self.stats(self.alpha).retention()
        self.assertEqual(retention['mature'], {'passed': 0, 'failed': 1, 'percent': 0.0})
        self.assertEqual(retention['young'], {'passed': 1, 'failed': 0, 'percent': 100.0})
        self.assertAlmostEqual(retention['percent'], 50.0)

    def test_hourly_breakdown_has_24_entries_at_the_local_hour(self):
        hours = self.stats(self.alpha).hourly_breakdown()
        self.assertEqual(len(hours), 24)
        self.assertEqual([hour for hour, _n, _p in hours], list(range(24)))
        busy = [entry for entry in hours if entry[1]]
        self.assertEqual(len(busy), 1)
        hour, reviews, percent = busy[0]
        self.assertEqual(hour, 12)
        self.assertEqual(reviews, 7)
        self.assertAlmostEqual(percent, 500 / 7)
        self.assertEqual(hours[0], (0, 0, None))

    def test_totals(self):
        totals = self.stats(self.alpha).totals()
        self.assertEqual(totals['cards'], 4)
        self.assertEqual(totals['notes'], 4)
        self.assertEqual(totals['reviews'], 7)
        self.assertEqual(totals['days_studied'], 3)
        self.assertEqual(totals['days_total'], 4)
        self.assertAlmostEqual(totals['minutes_total'], 22500 / 60000)
        self.assertAlmostEqual(totals['average_per_day'], 7 / 3)

    def test_added_per_day(self):
        added = self.stats(self.alpha).added_per_day(days_back=4)
        self.assertEqual(added, [(self.today - 3, 3), (self.today - 2, 0), (self.today - 1, 0),
                                 (self.today, 1)])
        self.assertEqual(self.stats().added_per_day(days_back=4)[0], (self.today - 3, 4))

    # -- the cards ------------------------------------------------------------------------

    def test_card_counts_after_the_scenario(self):
        counts = self.stats(self.alpha).card_counts()
        self.assertEqual(counts, {'new': 2, 'learning': 0, 'young': 2, 'mature': 0,
                                  'suspended': 0, 'buried': 0, 'total': 4})
        self.assertEqual(self.stats(self.beta).card_counts()['learning'], 1)

    def test_card_counts_separate_suspended_buried_and_mature(self):
        self.collection.suspend([self.c])
        self.collection.bury([self.d])
        self.collection.db.execute('UPDATE cards SET interval = 21 WHERE id = ?', (self.a,))
        counts = self.stats(self.alpha).card_counts()
        self.assertEqual(counts, {'new': 0, 'learning': 0, 'young': 1, 'mature': 1,
                                  'suspended': 1, 'buried': 1, 'total': 4})

    def test_memorized_sums_the_scheduler_s_retrievability(self):
        expected = sum(self.scheduler.retrievability(self.collection.card(card_id),
                                                     now=self.clock.now)
                       for card_id in (self.a, self.b))
        memorized = self.stats(self.alpha).memorized()
        self.assertEqual(memorized['total_reviewed'], 2)
        self.assertAlmostEqual(memorized['cards'], expected)
        self.assertAlmostEqual(memorized['average_retention'], expected / 2 * 100)
        self.assertGreater(memorized['cards'], 0)
        self.assertLessEqual(memorized['cards'], 2)
        self.assertEqual(self.stats().memorized()['total_reviewed'], 3)

    # -- scoping --------------------------------------------------------------------------

    def test_another_deck_s_reviews_are_excluded(self):
        self.assertEqual(self.stats(self.beta).totals()['reviews'], 1)
        self.assertEqual(self.stats(self.alpha).totals()['reviews'], 7)
        self.assertEqual(self.stats().totals()['reviews'], 8)
        self.assertEqual(self.stats().totals()['notes'], 5)
        self.assertEqual(self.stats(self.beta).answer_buttons()['learning'][GOOD], 1)

    def test_a_subdeck_s_cards_count_towards_its_parent(self):
        sub = self.collection.add_deck('Alpha::Sub')
        card = self.card_of(add_basic(self.collection, sub, 's', '0'))
        self.answer(card, GOOD, 500)
        self.assertEqual(self.stats(self.alpha).totals()['reviews'], 8)
        self.assertEqual(self.stats(sub).totals()['reviews'], 1)
        self.assertEqual(self.stats(self.alpha).today_summary()['new_introduced'], 1)
        self.assertEqual(self.stats(self.alpha).card_counts()['total'], 5)

    def test_a_card_moved_to_another_deck_takes_its_reviews_along(self):
        self.collection.set_deck([self.x], self.alpha.id)
        self.assertEqual(self.stats(self.alpha).totals()['reviews'], 8)
        self.assertEqual(self.stats(self.beta).totals()['reviews'], 0)

    def test_manual_revlog_rows_are_not_reviews(self):
        self.collection.forget([self.b])
        self.collection.set_due([self.c], 1, now=self.clock.now)
        self.assertEqual(self.collection.db.execute(
            "SELECT COUNT(*) FROM revlog WHERE kind = 'manual'").fetchone()[0], 2)
        self.assertEqual(self.stats(self.alpha).today_summary()['reviews'], 3)
        self.assertEqual(self.stats(self.alpha).totals()['reviews'], 7)
        self.assertEqual(self.stats(self.alpha).review_heatmap()[self.today], 3)
        self.assertEqual(self.stats(self.alpha).hourly_breakdown()[12][1], 7)

    def test_a_missing_deck_has_nothing(self):
        missing = Stats(self.collection, 123456, now=self.clock.now)
        self.assertEqual(missing.totals()['reviews'], 0)
        self.assertEqual(missing.card_counts()['total'], 0)
        self.assertEqual(missing.today_summary()['streak_days'], 0)


class CardStateTest(StatsTestCase):
    """The card-based charts, over rows written directly."""

    def setUp(self):
        super().setUp()
        self.deck = self.collection.add_deck('Gamma')
        self.cards = [self.card_of(add_basic(self.collection, self.deck, f'q{i}', 'a'))
                      for i in range(6)]

    def set_card(self, card_id, **columns):
        assignment = ', '.join(f'{name} = ?' for name in columns)
        self.collection.db.execute(f'UPDATE cards SET {assignment} WHERE id = ?',
                                   [*columns.values(), card_id])

    def test_due_forecast_puts_the_overdue_on_day_zero(self):
        today = self.today
        self.set_card(self.cards[0], state='review', due=today - 5)
        self.set_card(self.cards[1], state='review', due=today)
        self.set_card(self.cards[2], state='review', due=today + 2)
        self.set_card(self.cards[3], state='review', due=today + 2, suspended=1)
        self.set_card(self.cards[4], state='review', due=today + 3, buried=today)
        self.set_card(self.cards[5], state='learning', due=int(self.clock.now) + 60)
        forecast = self.stats(self.deck).due_forecast(days_ahead=3)
        self.assertEqual(forecast, [(0, 2), (1, 0), (2, 1), (3, 0)])
        self.assertEqual(stats.cumulative(forecast), [(0, 2), (1, 2), (2, 3), (3, 3)])
        self.assertEqual(self.stats(self.deck).due_forecast(days_ahead=0), [(0, 2)])

    def test_interval_histogram_default_buckets(self):
        for card_id, interval in zip(self.cards, (1, 5, 7, 30, 400, 0.5), strict=True):
            self.set_card(card_id, state='review', interval=interval)
        self.set_card(self.cards[5], state='learning')  # not a review card
        histogram = self.stats(self.deck).interval_histogram()
        self.assertEqual(histogram, [(0, 1, 0), (1, 7, 2), (7, 30, 1), (30, 90, 1),
                                     (90, 365, 0), (365, None, 1)])

    def test_interval_histogram_custom_buckets(self):
        for card_id, interval in zip(self.cards, (1, 5, 7, 30, 400, 2), strict=True):
            self.set_card(card_id, state='review', interval=interval)
        histogram = self.stats(self.deck).interval_histogram(buckets=[(0, 10), (10, None)])
        self.assertEqual(histogram, [(0, 10, 4), (10, None, 2)])

    def test_difficulty_histogram(self):
        for card_id, difficulty in zip(self.cards[:4], (1.0, 2.5, 9.99, 10.0), strict=True):
            self.set_card(card_id, stability=3.0, difficulty=difficulty)
        self.set_card(self.cards[4], stability=0, difficulty=5.0)  # no memory state yet
        histogram = self.stats(self.deck).difficulty_histogram()
        self.assertEqual(len(histogram), 9)
        self.assertEqual(histogram[0], (1, 2, 1))
        self.assertEqual(histogram[1], (2, 3, 1))
        self.assertEqual(histogram[8], (9, 10, 2))
        self.assertEqual(sum(count for _low, _high, count in histogram), 4)


class EmptyCollectionTest(StatsTestCase):

    def test_every_method_answers_with_nothing(self):
        empty = self.stats()
        summary = empty.today_summary()
        self.assertEqual(summary['reviews'], 0)
        self.assertEqual(summary['minutes'], 0)
        self.assertIsNone(summary['correct_percent'])
        self.assertEqual(summary['streak_days'], 0)
        self.assertEqual(empty.due_forecast(2), [(0, 0), (1, 0), (2, 0)])
        self.assertEqual(empty.review_heatmap(), {})
        self.assertEqual(len(empty.reviews_per_day(7)), 7)
        self.assertEqual(empty.card_counts()['total'], 0)
        self.assertEqual([c for _l, _h, c in empty.interval_histogram()], [0] * 6)
        self.assertEqual([c for _l, _h, c in empty.difficulty_histogram()], [0] * 9)
        retention = empty.retention()
        self.assertEqual((retention['passed'], retention['failed']), (0, 0))
        self.assertIsNone(retention['percent'])
        memorized = empty.memorized()
        self.assertEqual(memorized, {'cards': 0.0, 'total_reviewed': 0,
                                     'average_retention': None})
        self.assertEqual(empty.answer_buttons()['mature'], {1: 0, 2: 0, 3: 0, 4: 0})
        self.assertEqual(empty.added_per_day(3), [(self.today - 2, 0), (self.today - 1, 0),
                                                  (self.today, 0)])
        self.assertEqual(empty.hourly_breakdown()[5], (5, 0, None))
        totals = empty.totals()
        self.assertEqual(totals['reviews'], 0)
        self.assertEqual(totals['days_studied'], 0)
        self.assertEqual(totals['days_total'], 0)
        self.assertEqual(totals['average_per_day'], 0.0)

    def test_now_defaults_to_the_clock(self):
        self.assertEqual(Stats(self.collection).today, self.today)


class CumulativeTest(unittest.TestCase):

    def test_running_totals(self):
        self.assertEqual(stats.cumulative([(0, 3), (1, 0), (2, 2)]), [(0, 3), (1, 3), (2, 5)])

    def test_empty(self):
        self.assertEqual(stats.cumulative([]), [])


if __name__ == '__main__':
    unittest.main()
