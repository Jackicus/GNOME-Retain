# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

import math
import random
import unittest

from tests import ROOT  # noqa: F401
from retain import days, fsrs, optimizer
from retain.fsrs import AGAIN, EASY, GOOD, HARD

DEFAULTS = fsrs.DEFAULT_PARAMETERS

# The "true" model the synthetic reviews are drawn from: initial stabilities under half the
# defaults, a slower stability growth (w8) and a steeper forgetting curve (w20).
TRUE = list(DEFAULTS)
for _index in range(4):
    TRUE[_index] = DEFAULTS[_index] * 0.4
TRUE[8] = 1.0
TRUE[20] = 0.3

FIRST_RATINGS = (AGAIN, HARD, GOOD, GOOD, GOOD, GOOD, EASY, EASY)
RECALL_RATINGS = (HARD, GOOD, GOOD, GOOD, GOOD, EASY)


def within_bounds(params):
    return all(low <= value <= high
               for value, low, high in zip(params, fsrs.LOWER_BOUNDS, fsrs.UPPER_BOUNDS,
                                           strict=True))


def simulate(params, cards=300, reviews=8, seed=7):
    """Histories drawn from `params`: each card's recall at each review is a coin weighted by
    the true retrievability, the intervals scattered around the 90 % one so the forgetting
    curve is sampled at several points, and a lapse is followed by a same-day relearning
    step."""
    rng = random.Random(seed)
    histories = []
    for _ in range(cards):
        rating = rng.choice(FIRST_RATINGS)
        memory = fsrs.initial_memory(params, rating)
        history = [(rating, 0)]
        for _ in range(reviews):
            interval = fsrs.next_interval(params, memory.stability, 0.9)
            delta = max(1, round(interval * rng.choice((0.3, 0.6, 1.0, 1.5, 2.5))))
            recalled = rng.random() < fsrs.retrievability(params, memory.stability, delta)
            rating = rng.choice(RECALL_RATINGS) if recalled else AGAIN
            history.append((rating, delta))
            memory = fsrs.next_memory(params, memory, rating, delta)
            if rating == AGAIN:
                history.append((GOOD, 0))
                memory = fsrs.next_memory(params, memory, GOOD, 0)
        histories.append(history)
    return histories


def reference_predictions(params, histories):
    """(R, recalled) for each loss-bearing review, through fsrs' own functions."""
    predictions = []
    for history in histories:
        memory = fsrs.initial_memory(params, history[0][0])
        for rating, delta in history[1:]:
            if delta >= 1:
                recall = fsrs.retrievability(params, memory.stability, delta)
                predictions.append((recall, 1 if rating > AGAIN else 0))
            memory = fsrs.next_memory(params, memory, rating, delta)
    return predictions


def random_parameters(rng):
    return [rng.uniform(low, high)
            for low, high in zip(fsrs.LOWER_BOUNDS, fsrs.UPPER_BOUNDS, strict=True)]


def row(ms, card_id, rating, state='review', kind='review'):
    return {'id': ms, 'card_id': card_id, 'rating': rating, 'state': state, 'kind': kind}


def rows_from_histories(histories, day_start_hour=4):
    """Revlog rows that items_from_revlog() turns back into `histories`, one card each."""
    rows = []
    first_day = days.today(day_start_hour) - 400
    for card_id, history in enumerate(histories, start=1):
        day = first_day
        for index, (rating, delta) in enumerate(history):
            day += delta
            moment = days.day_start(day, day_start_hour) + 3600 + index * 60
            state = 'new' if index == 0 else ('relearning' if delta == 0 else 'review')
            rows.append(row(int(moment * 1000), card_id, rating, state))
    rows.sort(key=lambda entry: entry['id'])
    return rows


class SyntheticOptimizationTest(unittest.TestCase):
    """One optimisation of a synthetic deck, shared by the tests that look at its result."""

    @classmethod
    def setUpClass(cls):
        cls.histories = simulate(TRUE)
        cls.progress = []
        cls.params = optimizer.optimize(
            cls.histories, progress=lambda fraction, loss: cls.progress.append((fraction, loss)))

    def test_the_fit_beats_the_defaults(self):
        before = optimizer.log_loss(DEFAULTS, self.histories)
        after = optimizer.log_loss(self.params, self.histories)
        self.assertLess(after, before)
        # And it gets most of the way to the model the reviews were drawn from.
        truth = optimizer.log_loss(TRUE, self.histories)
        self.assertLess(after - truth, (before - truth) * 0.5)

    def test_the_fit_is_within_bounds(self):
        self.assertEqual(len(self.params), 21)
        self.assertTrue(within_bounds(self.params))
        self.assertEqual(fsrs.normalize_parameters(self.params), self.params)

    def test_pretraining_recovers_the_initial_stabilities(self):
        pretrained = optimizer.pretrain(DEFAULTS, self.histories)
        for index in (0, 2, 3):          # Again, Good and Easy: plenty of first reviews
            self.assertLess(abs(math.log(pretrained[index] / TRUE[index])),
                            abs(math.log(DEFAULTS[index] / TRUE[index])))
        self.assertEqual(pretrained[4:], DEFAULTS[4:])

    def test_the_fit_is_deterministic(self):
        again = optimizer.optimize(self.histories)
        self.assertEqual(again, self.params)

    def test_progress_reports_increasing_fractions_and_falling_loss(self):
        self.assertGreaterEqual(len(self.progress), 1)
        fractions = [fraction for fraction, _ in self.progress]
        self.assertEqual(fractions, sorted(set(fractions)))
        self.assertTrue(all(0 < fraction <= 1 for fraction in fractions))
        losses = [loss for _, loss in self.progress]
        self.assertEqual(losses, sorted(losses, reverse=True))
        self.assertAlmostEqual(losses[-1], optimizer.log_loss(self.params, self.histories))

    def test_evaluate_reports_the_fit(self):
        report = optimizer.evaluate(self.params, self.histories)
        self.assertEqual(set(report), {'log_loss', 'rmse_bins', 'reviews'})
        self.assertEqual(report['reviews'], optimizer.loss_bearing_reviews(self.histories))
        self.assertAlmostEqual(report['log_loss'],
                               optimizer.log_loss(self.params, self.histories))
        self.assertTrue(0 <= report['rmse_bins'] <= 1)
        # The fitted model is better calibrated than the defaults.
        self.assertLess(report['rmse_bins'],
                        optimizer.evaluate(DEFAULTS, self.histories)['rmse_bins'])


class OptimizeControlTest(unittest.TestCase):

    def setUp(self):
        self.histories = simulate(TRUE, cards=120, reviews=5, seed=3)

    def test_should_stop_ends_the_search_early(self):
        progress = []
        calls = []

        def should_stop():
            calls.append(True)
            return len(calls) > 5

        params = optimizer.optimize(self.histories, progress=progress.append,
                                    should_stop=should_stop)
        self.assertEqual(progress, [])
        self.assertLessEqual(len(calls), 7)
        self.assertTrue(within_bounds(params))
        # The few steps taken never made the parameters worse than the start.
        self.assertLessEqual(optimizer.log_loss(params, self.histories),
                             optimizer.log_loss(DEFAULTS, self.histories))

    def test_stopping_at_once_returns_the_start(self):
        params = optimizer.optimize(self.histories, initial=TRUE, should_stop=lambda: True)
        self.assertEqual(params, fsrs.normalize_parameters(TRUE))

    def test_iterations_bound_the_sweeps(self):
        progress = []
        optimizer.optimize(self.histories, iterations=2,
                           progress=lambda fraction, loss: progress.append(fraction))
        self.assertLessEqual(len(progress), 2)
        self.assertEqual(progress[0], 0.5)

    def test_the_start_can_be_an_older_parameter_set(self):
        initial = DEFAULTS[:19]           # an FSRS-5 set is upgraded, not refused
        params = optimizer.optimize(self.histories, initial=initial, iterations=1)
        self.assertEqual(len(params), 21)
        self.assertTrue(within_bounds(params))

    def test_no_histories_gives_the_normalised_start(self):
        self.assertEqual(optimizer.optimize([]), DEFAULTS)
        self.assertEqual(optimizer.log_loss(DEFAULTS, []), 0.0)
        self.assertEqual(optimizer.evaluate(DEFAULTS, []),
                         {'log_loss': 0.0, 'rmse_bins': 0.0, 'reviews': 0})


class ReplayTest(unittest.TestCase):

    def test_the_replay_matches_the_model_on_random_inputs(self):
        rng = random.Random(11)
        for _ in range(40):
            params = random_parameters(rng)
            histories = []
            for _ in range(10):
                history = [(rng.choice(fsrs.RATINGS), 0)]
                history += [(rng.choice(fsrs.RATINGS), rng.choice((0, 0, 1, 2, 3, 7, 30, 400)))
                            for _ in range(rng.randrange(1, 12))]
                histories.append(history)
            expected = reference_predictions(params, histories)
            predictions = []
            total, count = optimizer._replay(params, histories, predictions)
            self.assertEqual(count, len(expected))
            self.assertEqual([recalled for _, recalled in predictions],
                             [recalled for _, recalled in expected])
            for (recall, _), (reference, _) in zip(predictions, expected, strict=True):
                self.assertAlmostEqual(recall, reference, places=12)
            loss = -sum(math.log(min(max(recall, 1e-6), 1 - 1e-6)) if recalled
                        else math.log(1 - min(max(recall, 1e-6), 1 - 1e-6))
                        for recall, recalled in expected)
            self.assertAlmostEqual(total, loss, places=9)

    def test_a_perfect_predictor_has_no_loss_and_a_coin_ln_2(self):
        # Stability at the ceiling: R stays at 1 for a day, so recalls are free.
        certain = list(DEFAULTS)
        certain[2] = fsrs.STABILITY_MAX
        histories = [[(GOOD, 0), (GOOD, 1)] for _ in range(20)]
        self.assertAlmostEqual(optimizer.log_loss(certain, histories), 0.0, places=3)
        # R = 0.5 exactly: the delta at which the default curve halves.
        stability = DEFAULTS[2]
        delta = fsrs.next_interval(DEFAULTS, stability, 0.5)
        halving = list(DEFAULTS)
        halving[2] = stability * delta / (stability / fsrs.factor(DEFAULTS)
                                          * (0.5 ** (1 / fsrs.decay(DEFAULTS)) - 1))
        histories = [[(GOOD, 0), (GOOD, delta)], [(GOOD, 0), (AGAIN, delta)]]
        self.assertAlmostEqual(optimizer.log_loss(halving, histories), math.log(2), places=9)

    def test_same_day_reviews_add_no_loss(self):
        histories = [[(GOOD, 0), (AGAIN, 0), (GOOD, 0)]]
        self.assertEqual(optimizer.loss_bearing_reviews(histories), 0)
        self.assertEqual(optimizer.log_loss(DEFAULTS, histories), 0.0)
        histories = [[(GOOD, 0), (GOOD, 0), (GOOD, 3)]]
        self.assertEqual(optimizer.loss_bearing_reviews(histories), 1)
        self.assertGreater(optimizer.log_loss(DEFAULTS, histories), 0.0)

    def test_loss_is_fast(self):
        import time
        histories = simulate(DEFAULTS, cards=1250, reviews=7, seed=1)
        reviews = sum(len(history) for history in histories)
        self.assertGreater(reviews, 10000)
        start = time.perf_counter()
        optimizer.log_loss(DEFAULTS, histories)
        self.assertLess(time.perf_counter() - start, 0.5)


class RevlogTest(unittest.TestCase):

    def setUp(self):
        self.day = days.today() - 30
        self.base = days.day_start(self.day)

    def at(self, day_offset, hour=1.0):
        """A revlog id (ms) `hour` hours into day `day_offset` days after the base day."""
        return int((self.base + day_offset * 86400 + hour * 3600) * 1000)

    def test_histories_are_built_per_card_with_whole_day_deltas(self):
        rows = [
            row(self.at(0), 1, GOOD, 'new'),
            row(self.at(0, 1.5), 1, GOOD, 'learning'),          # same day: delta 0
            row(self.at(3), 1, AGAIN),                           # three days later
            row(self.at(3, 1.2), 1, GOOD, 'relearning'),
            row(self.at(10), 1, EASY),
            row(self.at(1), 2, AGAIN, 'new'),
            row(self.at(2), 2, GOOD),
        ]
        rows.sort(key=lambda entry: entry['id'])
        self.assertEqual(optimizer.items_from_revlog(rows), [
            [(GOOD, 0), (GOOD, 0), (AGAIN, 3), (GOOD, 0), (EASY, 7)],
            [(AGAIN, 0), (GOOD, 1)],
        ])

    def test_cram_manual_and_rating_0_rows_are_skipped(self):
        rows = [
            row(self.at(0), 1, GOOD, 'new'),
            row(self.at(1), 1, GOOD, kind='cram'),
            row(self.at(2), 1, 0, kind='manual'),
            row(self.at(3), 1, 0),
            row(self.at(4), 1, GOOD),
        ]
        self.assertEqual(optimizer.items_from_revlog(rows), [[(GOOD, 0), (GOOD, 4)]])

    def test_a_new_state_review_starts_a_fresh_history(self):
        rows = [
            row(self.at(0), 1, GOOD, 'new'),
            row(self.at(2), 1, GOOD),
            row(self.at(5), 1, HARD, 'new'),          # forgotten and learned again
            row(self.at(6), 1, GOOD),
            row(self.at(9), 1, GOOD),
        ]
        self.assertEqual(optimizer.items_from_revlog(rows),
                         [[(GOOD, 0), (GOOD, 2)], [(HARD, 0), (GOOD, 1), (GOOD, 3)]])

    def test_single_review_histories_are_dropped(self):
        rows = [
            row(self.at(0), 1, GOOD, 'new'),
            row(self.at(0, 2), 2, GOOD, 'new'),
            row(self.at(1), 2, GOOD),
            row(self.at(2), 2, GOOD, 'new'),          # a fresh start with nothing after it
            row(self.at(3), 3, GOOD),                 # no new review seen: a history of its own
            row(self.at(4), 3, GOOD),
        ]
        self.assertEqual(optimizer.items_from_revlog(rows),
                         [[(GOOD, 0), (GOOD, 1)], [(GOOD, 0), (GOOD, 1)]])

    def test_the_day_turns_at_the_day_start_hour(self):
        rows = [
            row(self.at(0, 18), 1, GOOD, 'new'),      # 10 p.m. (the base is 4 a.m.)
            row(self.at(0, 23), 1, GOOD),             # 3 a.m. next date: same day at hour 4
        ]
        self.assertEqual(optimizer.items_from_revlog(rows, day_start_hour=4),
                         [[(GOOD, 0), (GOOD, 0)]])
        self.assertEqual(optimizer.items_from_revlog(rows, day_start_hour=0),
                         [[(GOOD, 0), (GOOD, 1)]])

    def test_sqlite_rows_work_too(self):
        import sqlite3
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE revlog (id, card_id, rating, state, kind)')
        connection.executemany('INSERT INTO revlog VALUES (?, ?, ?, ?, ?)', [
            (self.at(0), 1, GOOD, 'new', 'review'), (self.at(2), 1, GOOD, 'review', 'review')])
        rows = connection.execute('SELECT * FROM revlog ORDER BY id').fetchall()
        connection.close()
        self.assertEqual(optimizer.items_from_revlog(rows), [[(GOOD, 0), (GOOD, 2)]])


class SuggestTest(unittest.TestCase):

    def test_too_few_reviews_raise_not_enough_data(self):
        histories = simulate(TRUE, cards=30, reviews=4, seed=2)
        count = optimizer.loss_bearing_reviews(histories)
        self.assertLess(count, optimizer.MINIMUM_REVIEWS)
        with self.assertRaises(optimizer.NotEnoughData) as caught:
            optimizer.suggest(rows_from_histories(histories))
        self.assertEqual(caught.exception.count, count)
        with self.assertRaises(optimizer.NotEnoughData):
            optimizer.suggest([])

    def test_suggest_fits_a_deck(self):
        histories = simulate(TRUE, cards=150, reviews=5, seed=5)
        rows = rows_from_histories(histories)
        self.assertEqual(optimizer.items_from_revlog(rows), histories)
        progress = []
        params, before, after = optimizer.suggest(
            rows, current=DEFAULTS, progress=lambda fraction, loss: progress.append(fraction))
        self.assertTrue(within_bounds(params))
        self.assertAlmostEqual(before, optimizer.log_loss(DEFAULTS, histories))
        self.assertAlmostEqual(after, optimizer.log_loss(params, histories))
        self.assertLess(after, before)
        self.assertGreaterEqual(len(progress), 1)


if __name__ == '__main__':
    unittest.main()
