# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

import math
import unittest

from tests import ROOT  # noqa: F401
from retain import fsrs
from retain.fsrs import AGAIN, EASY, GOOD, HARD, Memory

P = fsrs.DEFAULT_PARAMETERS

# Published defaults of the two older versions normalize_parameters() upgrades.
FSRS_4_5 = [0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474, 0.1367,
            1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755]
FSRS_5 = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
          1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621]


def within_bounds(params):
    return all(low <= value <= high
               for value, low, high in zip(params, fsrs.LOWER_BOUNDS, fsrs.UPPER_BOUNDS,
                                           strict=True))


class ParametersTest(unittest.TestCase):

    def test_defaults_are_21_values_within_bounds(self):
        self.assertEqual(len(P), 21)
        self.assertEqual(len(fsrs.LOWER_BOUNDS), 21)
        self.assertEqual(len(fsrs.UPPER_BOUNDS), 21)
        self.assertTrue(within_bounds(P))
        self.assertEqual(fsrs.normalize_parameters(P), P)
        self.assertEqual(fsrs.normalize_parameters(tuple(P)), P)

    def test_curve_constants_come_from_w20(self):
        self.assertEqual(fsrs.decay(P), -P[20])
        self.assertAlmostEqual(fsrs.factor(P), 0.9 ** (1 / -P[20]) - 1)

    def test_an_fsrs_4_5_set_is_upgraded(self):
        upgraded = fsrs.normalize_parameters(FSRS_4_5)
        self.assertEqual(len(upgraded), 21)
        self.assertTrue(within_bounds(upgraded))
        self.assertEqual(upgraded[:4], FSRS_4_5[:4])
        self.assertAlmostEqual(upgraded[4], 5.1618 + 2 * 1.2298)
        self.assertAlmostEqual(upgraded[5], math.log(3 * 1.2298 + 1) / 3)
        self.assertAlmostEqual(upgraded[6], 0.8975 + 0.5)
        self.assertEqual(upgraded[7:17], FSRS_4_5[7:17])
        self.assertEqual(upgraded[17:], [0.0, 0.0, 0.0, 0.5])

    def test_an_fsrs_5_set_is_upgraded(self):
        upgraded = fsrs.normalize_parameters(FSRS_5)
        self.assertEqual(len(upgraded), 21)
        self.assertTrue(within_bounds(upgraded))
        self.assertEqual(upgraded[:19], FSRS_5)
        self.assertEqual(upgraded[19:], [0.0, 0.5])

    def test_out_of_bounds_values_are_clamped(self):
        params = list(P)
        params[0] = 0.0
        params[4] = 50.0
        params[20] = -1.0
        normalized = fsrs.normalize_parameters(params)
        self.assertEqual(normalized[0], fsrs.LOWER_BOUNDS[0])
        self.assertEqual(normalized[4], fsrs.UPPER_BOUNDS[4])
        self.assertEqual(normalized[20], fsrs.LOWER_BOUNDS[20])
        self.assertTrue(within_bounds(normalized))

    def test_bad_sets_raise(self):
        for length in (0, 1, 16, 18, 20, 22):
            with self.subTest(length=length):
                with self.assertRaises(ValueError):
                    fsrs.normalize_parameters([1.0] * length)
        for bad in (['x'] * 21, [None] * 21, [math.nan] * 21, P[:20] + [[0.1]]):
            with self.subTest(bad=bad[-1]):
                with self.assertRaises(ValueError):
                    fsrs.normalize_parameters(bad)


class CurveTest(unittest.TestCase):

    def test_retrievability_is_90_percent_at_stability(self):
        for stability in (0.5, 1, 7.3, 30, 365):
            with self.subTest(stability=stability):
                self.assertAlmostEqual(fsrs.retrievability(P, stability, stability), 0.9)
        self.assertEqual(fsrs.retrievability(P, 10, 0), 1.0)
        self.assertEqual(fsrs.retrievability(P, 10, -5), 1.0)

    def test_retrievability_decreases_with_time(self):
        values = [fsrs.retrievability(P, 10, t) for t in (0, 1, 5, 10, 20, 100, 1000)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertGreater(values[-1], 0)
        # Zero stability is read as the minimum, not a division by zero.
        self.assertLess(fsrs.retrievability(P, 0, 1), 0.5)

    def test_next_interval_at_90_percent_is_the_stability(self):
        for stability in (1, 3.7, 10, 100.2, 365, 2000.4):
            with self.subTest(stability=stability):
                self.assertEqual(fsrs.next_interval(P, stability, 0.9), round(stability))
        self.assertEqual(fsrs.next_interval(P, 0.2, 0.9), 1)
        self.assertEqual(fsrs.next_interval(P, 1000, 0.9, maximum_interval=365), 365)

    def test_next_interval_is_monotone(self):
        by_stability = [fsrs.next_interval(P, s, 0.9) for s in (1, 2, 5, 10, 50, 500)]
        self.assertEqual(by_stability, sorted(by_stability))
        self.assertEqual(len(set(by_stability)), len(by_stability))
        by_retention = [fsrs.next_interval(P, 30, r) for r in (0.7, 0.8, 0.85, 0.9, 0.95, 0.99)]
        self.assertEqual(by_retention, sorted(by_retention, reverse=True))
        self.assertEqual(len(set(by_retention)), len(by_retention))
        self.assertGreater(fsrs.next_interval(P, 30, 0.8), 30)
        self.assertLess(fsrs.next_interval(P, 30, 0.95), 30)
        # Hand-computed from the curve: the t at which R(t) == r.
        expected = 30 / fsrs.factor(P) * (0.85 ** (1 / fsrs.decay(P)) - 1)
        self.assertEqual(fsrs.next_interval(P, 30, 0.85), round(expected))

    def test_retention_is_clamped(self):
        self.assertEqual(fsrs.next_interval(P, 10, 1.0), fsrs.next_interval(P, 10, 0.9999))
        self.assertEqual(fsrs.next_interval(P, 10, 0.0), fsrs.next_interval(P, 10, 0.0001))
        self.assertEqual(fsrs.next_interval(P, 10, 0.9999), 1)


class InitialMemoryTest(unittest.TestCase):

    def test_first_rating_sets_stability_and_difficulty(self):
        memories = [fsrs.initial_memory(P, rating) for rating in (AGAIN, HARD, GOOD, EASY)]
        for rating, memory in zip((AGAIN, HARD, GOOD, EASY), memories, strict=True):
            with self.subTest(rating=rating):
                self.assertEqual(memory.stability, P[rating - 1])
                expected = min(max(P[4] - math.e ** (P[5] * (rating - 1)) + 1, 1), 10)
                self.assertAlmostEqual(memory.difficulty, expected)
                self.assertTrue(1 <= memory.difficulty <= 10)
        stabilities = [memory.stability for memory in memories]
        self.assertEqual(stabilities, sorted(stabilities))
        difficulties = [memory.difficulty for memory in memories]
        self.assertEqual(difficulties, sorted(difficulties, reverse=True))
        self.assertIsInstance(memories[0], Memory)

    def test_bad_ratings_raise(self):
        for rating in (0, 5, None):
            with self.subTest(rating=rating):
                with self.assertRaises(ValueError):
                    fsrs.initial_memory(P, rating)
                with self.assertRaises(ValueError):
                    fsrs.next_memory(P, Memory(10, 5), rating, 1)


class NextMemoryTest(unittest.TestCase):

    def test_good_at_stability_grows_stability(self):
        before = fsrs.initial_memory(P, GOOD)
        after = fsrs.next_memory(P, before, GOOD, before.stability)
        self.assertGreater(after.stability, before.stability)
        # Hand-computed: R is 0.9 at t == S, then the recall formula.
        s, d = before
        expected = s * (1 + math.e ** P[8] * (11 - d) * s ** -P[9]
                        * (math.e ** ((1 - 0.9) * P[10]) - 1))
        self.assertAlmostEqual(after.stability, expected, places=6)
        # Good leaves difficulty where it was, but for the mean reversion towards D0(Easy).
        easy_d0 = P[4] - math.e ** (P[5] * 3) + 1
        self.assertAlmostEqual(after.difficulty, P[7] * easy_d0 + (1 - P[7]) * d)

    def test_again_shrinks_stability_and_raises_difficulty(self):
        before = Memory(30.0, 5.0)
        after = fsrs.next_memory(P, before, AGAIN, 30)
        self.assertLess(after.stability, before.stability)
        self.assertGreater(after.difficulty, before.difficulty)
        s, d = before
        r = fsrs.retrievability(P, s, 30)
        formula = (P[11] * d ** -P[12] * ((s + 1) ** P[13] - 1)
                   * math.e ** ((1 - r) * P[14]))
        self.assertAlmostEqual(after.stability, min(formula, s / math.e ** (P[17] * P[18])))
        stepped = d + (-P[6] * (AGAIN - 3)) * (10 - d) / 9
        easy_d0 = P[4] - math.e ** (P[5] * 3) + 1
        self.assertAlmostEqual(after.difficulty, P[7] * easy_d0 + (1 - P[7]) * stepped)

    def test_ratings_order_the_outcome(self):
        before = Memory(10.0, 5.0)
        outcomes = [fsrs.next_memory(P, before, rating, 10) for rating in (HARD, GOOD, EASY)]
        stabilities = [memory.stability for memory in outcomes]
        self.assertEqual(stabilities, sorted(stabilities))
        self.assertGreater(stabilities[0], before.stability)
        difficulties = [memory.difficulty for memory in outcomes]
        self.assertEqual(difficulties, sorted(difficulties, reverse=True))
        self.assertGreater(difficulties[0], before.difficulty)
        self.assertLess(difficulties[2], before.difficulty)
        # The lapse is the only path down.
        self.assertLess(fsrs.next_memory(P, before, AGAIN, 10).stability, before.stability)

    def test_a_late_review_grows_stability_more(self):
        before = Memory(10.0, 5.0)
        on_time = fsrs.next_memory(P, before, GOOD, 10).stability
        late = fsrs.next_memory(P, before, GOOD, 40).stability
        early = fsrs.next_memory(P, before, GOOD, 2).stability
        self.assertGreater(late, on_time)
        self.assertGreater(on_time, early)

    def test_same_day_good_never_lowers_stability(self):
        for stability in (0.01, 0.5, 1, 10, 100, 5000):
            for rating in (HARD, GOOD, EASY):
                with self.subTest(stability=stability, rating=rating):
                    after = fsrs.next_memory(P, Memory(stability, 5.0), rating, 0)
                    self.assertGreaterEqual(after.stability, stability)
        after = fsrs.next_memory(P, Memory(1.0, 5.0), EASY, 0.5)
        self.assertGreater(after.stability, 1.0)

    def test_same_day_again_can_lower_stability(self):
        before = Memory(10.0, 5.0)
        after = fsrs.next_memory(P, before, AGAIN, 0)
        self.assertLess(after.stability, before.stability)
        expected = 10.0 * math.e ** (P[17] * (AGAIN - 3 + P[18])) * 10.0 ** -P[19]
        self.assertAlmostEqual(after.stability, expected)
        self.assertGreater(after.difficulty, before.difficulty)

    def test_forgetting_is_capped(self):
        cap = 1 / math.e ** (P[17] * P[18])
        for stability, difficulty, elapsed in ((1, 1, 1), (10, 5, 100), (365, 9, 365)):
            with self.subTest(stability=stability):
                after = fsrs.next_memory(P, Memory(stability, difficulty), AGAIN, elapsed)
                self.assertLessEqual(after.stability, stability * cap)
        # Parameters that would make a lapse keep most of the stability hit the cap.
        params = list(P)
        params[11], params[13], params[14] = 5.0, 0.9, 4.0
        after = fsrs.next_memory(params, Memory(1.0, 1.0), AGAIN, 1000)
        self.assertAlmostEqual(after.stability, 1.0 * cap)

    def test_results_stay_within_range(self):
        huge = fsrs.next_memory(P, Memory(fsrs.STABILITY_MAX, 1.0), EASY, 36500)
        self.assertEqual(huge.stability, fsrs.STABILITY_MAX)
        self.assertGreaterEqual(huge.difficulty, 1.0)
        tiny = fsrs.next_memory(P, Memory(0.0, 10.0), AGAIN, 0)
        self.assertGreaterEqual(tiny.stability, fsrs.STABILITY_MIN)
        self.assertLessEqual(tiny.difficulty, 10.0)
        hardest = fsrs.next_memory(P, Memory(10.0, 10.0), AGAIN, 10)
        self.assertLessEqual(hardest.difficulty, 10.0)
        easiest = fsrs.next_memory(P, Memory(10.0, 1.0), EASY, 10)
        self.assertGreaterEqual(easiest.difficulty, 1.0)


class FuzzTest(unittest.TestCase):

    def test_short_intervals_are_not_fuzzed(self):
        self.assertEqual(fsrs.fuzz_range(1), (1, 1))
        self.assertEqual(fsrs.fuzz_range(2), (2, 2))

    def test_fuzz_range_grows_with_the_interval(self):
        # 10 days: delta = 1 + 0.15 * 4.5 + 0.10 * 3 = 1.975.
        low, high = fsrs.fuzz_range(10)
        self.assertEqual((low, high), (8, 12))
        self.assertTrue(low <= 10 <= high)
        self.assertGreaterEqual(low, 2)
        # 3 days: delta = 1.075, so 2 to 4.
        self.assertEqual(fsrs.fuzz_range(3), (2, 4))
        # 100 days: delta = 1 + 0.675 + 1.3 + 0.05 * 80 = 6.975.
        self.assertEqual(fsrs.fuzz_range(100), (93, 107))
        spans = [high - low for low, high in (fsrs.fuzz_range(d) for d in (5, 10, 50, 500))]
        self.assertEqual(spans, sorted(spans))

    def test_fuzz_range_respects_the_maximum(self):
        self.assertEqual(fsrs.fuzz_range(100, maximum_interval=95), (93, 95))
        self.assertEqual(fsrs.fuzz_range(100, maximum_interval=90), (90, 90))

    def test_fuzzed_interval_is_deterministic_and_in_range(self):
        self.assertEqual(fsrs.fuzzed_interval(10, 42), fsrs.fuzzed_interval(10, 42))
        values = {fsrs.fuzzed_interval(10, seed) for seed in range(200)}
        self.assertTrue(values <= {8, 9, 10, 11, 12})
        self.assertGreater(len(values), 1)
        for value in values:
            self.assertIsInstance(value, int)
        self.assertEqual({fsrs.fuzzed_interval(1, seed) for seed in range(20)}, {1})
        self.assertEqual(fsrs.fuzzed_interval(10, 'a card'), fsrs.fuzzed_interval(10, 'a card'))
        for seed in range(50):
            self.assertLessEqual(fsrs.fuzzed_interval(100, seed, maximum_interval=95), 95)

    def test_fuzzed_interval_respects_the_minimum(self):
        self.assertEqual({fsrs.fuzzed_interval(10, seed, minimum=12) for seed in range(50)},
                         {12})
        self.assertEqual(fsrs.fuzzed_interval(10, 7, minimum=20), 20)
        for seed in range(50):
            self.assertGreaterEqual(fsrs.fuzzed_interval(10, seed, minimum=11), 11)
        self.assertEqual(fsrs.fuzzed_interval(2, 3, minimum=3), 3)


class MemorizedTest(unittest.TestCase):

    def test_memorized_sums_retrievability(self):
        self.assertEqual(fsrs.memorized(P, []), 0)
        self.assertEqual(fsrs.memorized(P, [(10, 0)]), 1.0)
        self.assertAlmostEqual(fsrs.memorized(P, [(10, 10), (20, 20), (5, 5)]), 2.7)
        cards = ((stability, 3) for stability in (1, 2, 4))
        expected = sum(fsrs.retrievability(P, stability, 3) for stability in (1, 2, 4))
        self.assertAlmostEqual(fsrs.memorized(P, cards), expected)


if __name__ == '__main__':
    unittest.main()
