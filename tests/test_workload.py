# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""workload.py: the fuzz bounds, the load balancer and its easy days, and the simulator."""

import random
import unittest

from tests import ROOT  # noqa: F401
from retain import days, fsrs, workload
from retain.workload import MINIMUM, NORMAL, REDUCED

MONDAY = 739_000 - (739_000 - 1) % 7  # an invented Monday's day number
assert days.weekday(MONDAY) == 0


def flat(count):
    return lambda _target: count


class FuzzBoundsTest(unittest.TestCase):

    def test_matches_ankis_constrained_bounds(self):
        # Anki's fuzz.rs tests: the lowest and highest days of each range.
        cases = [
            ((1.0, 1, 1000), (1, 1)), ((2.49, 1, 1000), (2, 2)), ((2.5, 1, 1000), (2, 4)),
            ((7.0, 1, 1000), (5, 9)), ((17.0, 1, 1000), (14, 20)), ((37.0, 1, 1000), (33, 41)),
            ((2.0, 2, 1000), (2, 2)), ((2.0, 3, 1000), (3, 4)), ((2.0, 3, 3), (3, 3)),
            ((19.9, 3, 1000), (17, 23)), ((100.0, 101, 1000), (101, 108)),
            ((100.0, 1, 99), (92, 99)), ((100.0, 97, 103), (97, 103)),
        ]
        for arguments, bounds in cases:
            self.assertEqual(workload.fuzz_bounds(*arguments), bounds, arguments)

    def test_agrees_with_the_plain_fuzz_range(self):
        for interval in (3, 5, 8, 15, 30, 60, 90):
            self.assertEqual(workload.fuzz_bounds(interval), fsrs.fuzz_range(interval))


class BalancerTest(unittest.TestCase):

    def test_picks_the_lighter_days(self):
        # Days 9 and 11 of 8..12 hold almost nothing; the rest are crowded.
        load = {8: 40, 9: 1, 10: 40, 11: 1, 12: 40}
        picks = [workload.balanced_interval(10, seed, load.get, MONDAY) for seed in range(200)]
        light = sum(pick in (9, 11) for pick in picks)
        self.assertGreater(light, 190)

    def test_never_leaves_the_fuzz_range(self):
        rng = random.Random(4)
        for _case in range(300):
            interval = rng.uniform(1, 90)
            minimum = rng.choice((1, 1, int(interval) + 1))
            load = {day: rng.randint(0, 30) for day in range(100)}
            low, high = workload.fuzz_bounds(interval, minimum, 36500)
            pick = workload.balanced_interval(interval, rng.random(), load.get, MONDAY,
                                              [NORMAL, REDUCED, MINIMUM] * 2 + [NORMAL],
                                              minimum=minimum, sibling_days=[low + 1])
            self.assertTrue(low <= pick <= high, (interval, minimum, pick))

    def test_respects_the_maximum_interval(self):
        pick = workload.balanced_interval(40, 'seed', flat(3), MONDAY, maximum=38)
        self.assertLessEqual(pick, 38)

    def test_is_deterministic_for_a_seed(self):
        load = {day: (day * 7) % 13 for day in range(100)}
        for seed in ('12:3', 'other', 99):
            first = workload.balanced_interval(30, seed, load.get, MONDAY)
            self.assertEqual(workload.balanced_interval(30, seed, load.get, MONDAY), first)
        picks = {workload.balanced_interval(30, seed, flat(5), MONDAY) for seed in range(50)}
        self.assertGreater(len(picks), 1)  # different seeds spread over the range

    def test_short_and_long_intervals(self):
        self.assertEqual(workload.balanced_interval(1, 's', flat(9), MONDAY), 1)
        self.assertEqual(workload.balanced_interval(2.2, 's', flat(9), MONDAY), 2)
        self.assertIsNone(workload.balanced_interval(91, 's', flat(9), MONDAY))
        self.assertIsNone(workload.balanced_interval(20, 's', flat(9), MONDAY, minimum=95))

    def test_prefers_nearer_days_when_the_load_is_even(self):
        picks = [workload.balanced_interval(30, seed, flat(10), MONDAY) for seed in range(400)]
        low, high = workload.fuzz_bounds(30)
        self.assertGreater(sum(pick < 30 for pick in picks), sum(pick > 30 for pick in picks))
        self.assertTrue(all(low <= pick <= high for pick in picks))

    def test_an_empty_day_is_preferred(self):
        load = {7: 0, 5: 4, 6: 4, 8: 4, 9: 4}
        picks = [workload.balanced_interval(7, seed, lambda d: load.get(d, 4), MONDAY)
                 for seed in range(200)]
        self.assertGreater(picks.count(7), 150)

    def test_avoids_a_siblings_day(self):
        picks = [workload.balanced_interval(10, seed, flat(5), MONDAY, sibling_days=[10])
                 for seed in range(300)]
        self.assertLess(picks.count(10), 3)
        self.assertEqual(workload.sibling_modifiers([10], 8, 12),
                         [0.4, 0.2, 0.000001, 0.2, 0.4])
        # Two siblings multiply (Anki's example: days 2 and 8).
        self.assertEqual([round(m, 2) for m in workload.sibling_modifiers([2, 8], 0, 10)],
                         [0.4, 0.2, 0.0, 0.2, 0.32, 0.36, 0.32, 0.2, 0.0, 0.2, 0.4])


class EasyDaysTest(unittest.TestCase):

    def test_values_normalize_as_anki_reads_them(self):
        self.assertEqual(workload.easy_day(1.0), NORMAL)
        self.assertEqual(workload.easy_day(0), MINIMUM)
        self.assertEqual(workload.easy_day(0.5), REDUCED)
        self.assertEqual(workload.easy_day(0.3), REDUCED)
        self.assertEqual(workload.normalize_easy_days([1, 1, 1, 1, 1, 0.5, 0]),
                         [NORMAL] * 5 + [REDUCED, MINIMUM])
        self.assertEqual(workload.normalize_easy_days([]), [NORMAL] * 7)
        self.assertEqual(workload.normalize_easy_days(None), [NORMAL] * 7)
        self.assertEqual(workload.normalize_easy_days([1, 0]), [NORMAL] * 7)
        self.assertEqual(workload.normalize_easy_days(['x'] * 7), [NORMAL] * 7)

    def test_a_minimum_day_is_avoided(self):
        # Interval 9 from a Monday: 7..11 days ahead, Monday to Friday of the next week.
        easy = [MINIMUM, NORMAL, NORMAL, NORMAL, NORMAL, NORMAL, NORMAL]
        low, high = workload.fuzz_bounds(9)
        self.assertEqual((low, high), (7, 11))
        picks = [workload.balanced_interval(9, seed, flat(5), MONDAY, easy)
                 for seed in range(300)]
        self.assertNotIn(7, picks)  # day 7 is a Monday
        empty = [workload.balanced_interval(9, seed, flat(0), MONDAY, easy)
                 for seed in range(300)]
        self.assertNotIn(7, empty)  # even when every day is empty

    def test_every_day_minimum_still_balances(self):
        load = {7: 9, 8: 9, 9: 1, 10: 9, 11: 9}
        picks = [workload.balanced_interval(9, seed, load.get, MONDAY, [MINIMUM] * 7)
                 for seed in range(100)]
        self.assertGreater(picks.count(9), 90)

    def test_a_reduced_day_takes_cards_until_it_holds_half_the_load(self):
        weekdays = [0, 1, 2]
        easy = [REDUCED, NORMAL, NORMAL, NORMAL, NORMAL, NORMAL, NORMAL]
        # Monday has 4 against 10 on the others: under half, still open.
        self.assertEqual(workload.easy_day_modifiers(easy, weekdays, [4, 10, 10]),
                         [1.0, 1.0, 1.0])
        # 6 is over half of 10: closed.
        self.assertEqual(workload.easy_day_modifiers(easy, weekdays, [6, 10, 10]),
                         [0.0001, 1.0, 1.0])
        self.assertEqual(workload.easy_day_modifiers([REDUCED] * 7, [3], [5]), [1.0])

    def test_reduced_days_end_up_lighter(self):
        easy = [NORMAL] * 5 + [REDUCED, MINIMUM]
        load = [0] * 200
        rng = random.Random(1)
        for day in range(150):  # cards answered every day, each balanced over what is due
            for _card in range(20):
                interval = rng.uniform(5, 30)
                pick = workload.balanced_interval(
                    interval, rng, lambda target, day=day: load[day + target],
                    MONDAY + day, easy)
                load[day + pick] += 1
        week = [0] * 7
        for day in range(60, 150):
            week[days.weekday(MONDAY + day)] += load[day]
        weekday = sum(week[:5]) / 5
        self.assertLess(week[5], weekday * 0.75)  # Saturday, reduced
        self.assertLess(week[6], weekday * 0.1)  # Sunday, minimum


class SimulatorTest(unittest.TestCase):

    def cards(self, count=400, seed=3):
        rng = random.Random(seed)
        cards = []
        for _card in range(count):
            stability = rng.uniform(1, 80)
            cards.append((stability, rng.uniform(3, 8), rng.randint(0, int(stability)),
                          -rng.randint(1, int(stability) + 1)))
        return cards

    def simulate(self, retention=0.9, **kwargs):
        kwargs.setdefault('today', MONDAY)
        return workload.simulate(self.cards(), fsrs.DEFAULT_PARAMETERS, retention, **kwargs)

    def test_a_year_of_reviews(self):
        result = self.simulate(new_cards=200, new_per_day=10)
        self.assertEqual(len(result.reviews), workload.SIMULATED_DAYS)
        self.assertGreater(sum(result.reviews), 0)
        self.assertEqual(round(result.cards), 600)
        self.assertGreater(result.memorized, 0)
        self.assertLess(result.memorized, result.cards)

    def test_higher_retention_costs_reviews_and_remembers_more(self):
        low = self.simulate(0.8, new_cards=100)
        high = self.simulate(0.95, new_cards=100)
        self.assertGreater(sum(high.reviews), sum(low.reviews) * 1.5)
        self.assertGreater(high.memorized, low.memorized)

    def test_is_deterministic(self):
        self.assertEqual(self.simulate(new_cards=50), self.simulate(new_cards=50))

    def test_the_review_limit_caps_each_day(self):
        result = self.simulate(reviews_per_day=10, horizon=60)
        self.assertLessEqual(max(result.reviews), 10)

    def test_easy_days_lighten_their_weekdays(self):
        easy = [NORMAL] * 6 + [MINIMUM]
        result = self.simulate(new_cards=300, easy_days=easy)
        sundays = [count for day, count in enumerate(result.reviews)
                   if days.weekday(MONDAY + day) == 6 and day > 30]
        others = [count for day, count in enumerate(result.reviews)
                  if days.weekday(MONDAY + day) != 6 and day > 30]
        self.assertLess(sum(sundays) / len(sundays), sum(others) / len(others) * 0.5)

    def test_no_cards_no_reviews(self):
        result = workload.simulate([], fsrs.DEFAULT_PARAMETERS, 0.9, horizon=10)
        self.assertEqual(result.reviews, [0] * 10)
        self.assertEqual(result.memorized, 0)

    def test_a_large_collection_is_sampled_and_scaled(self):
        cards = self.cards(count=workload.SAMPLE_LIMIT * 2)
        result = workload.simulate(cards, fsrs.DEFAULT_PARAMETERS, 0.9, horizon=30)
        self.assertAlmostEqual(result.cards, len(cards), delta=len(cards) * 0.05)

    def test_stops_when_asked(self):
        self.assertIsNone(self.simulate(should_stop=lambda: True))


if __name__ == '__main__':
    unittest.main()
