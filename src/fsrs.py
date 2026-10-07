# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""FSRS-6, the memory model the scheduler asks how well a card is remembered and when to show
it again.

FSRS (Free Spaced Repetition Scheduler) describes a card's memory with two numbers: stability,
the number of days until the probability of recalling the card falls to 90 %, and difficulty,
a number from 1 to 10 that says how much slower than average that stability grows. The
probability of recall, the retrievability R, is a power-law forgetting curve of the days t
since the last review: R = (1 + factor * t / S) ** decay, with decay = -w20 and factor chosen so
that R is exactly 0.9 at t = S. The next interval is the t at which R falls to the deck's
desired retention; the scheduler adds Anki's fuzz to it so that cards learned together drift
apart. An answer updates the state: a review a day or more after the last one updates
stability from R and the rating (recall grows it, more so for Easy and less for Hard; a lapse
shrinks it); a review on the same day (learning steps, relearning) scales stability by a
short-term factor; difficulty moves with the rating and reverts a little towards the
difficulty of an Easy first rating. The 21 parameters w0…w20 are fitted to a collection's
review history (optimizer.py) or left at the defaults.

    def next_memory(w, (S, D), rating, elapsed_days):
        if elapsed_days < 1:                        # same-day review
            sinc = e ** (w17 * (rating - 3 + w18)) * S ** -w19
            S' = S * (max(sinc, 1) if rating >= Hard else sinc)
        else:
            R = retrievability(S, elapsed_days)
            if rating == Again:
                S' = min(w11 * D ** -w12 * ((S + 1) ** w13 - 1) * e ** ((1 - R) * w14),
                         S / e ** (w17 * w18))
            else:
                S' = S * (1 + e ** w8 * (11 - D) * S ** -w9 * (e ** ((1 - R) * w10) - 1)
                          * (w15 if rating == Hard else 1) * (w16 if rating == Easy else 1))
        D' = D - w6 * (rating - 3) * (10 - D) / 9
        D' = w7 * D0(Easy) + (1 - w7) * D'            # mean reversion
        return clamp(S', 0.001, 36500), clamp(D', 1, 10)

The formulas follow the open-spaced-repetition FSRS-6 algorithm as published in its reference
implementations, py-fsrs and fsrs-rs (MIT licensed); this is an independent implementation
in the same shape, so parameters fitted by either (or by Anki) are usable here as they are.
Parameter sets from FSRS-4.5 (17 numbers) and FSRS-5 (19) are upgraded by
`normalize_parameters()` the way those implementations do.

    DEFAULT_PARAMETERS, LOWER_BOUNDS, UPPER_BOUNDS   21 floats each
    AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4             the ratings
    STABILITY_MIN, STABILITY_MAX                     0.001 and 36500 days
    Memory(stability, difficulty)                    a card's state
    normalize_parameters(params) -> list             21 floats within bounds, or ValueError
    decay(params), factor(params)                    the forgetting curve's constants
    retrievability(params, stability, elapsed_days) -> float
    next_interval(params, stability, desired_retention, maximum_interval=36500) -> int days
    initial_memory(params, rating) -> Memory         after a card's first rating
    next_memory(params, memory, rating, elapsed_days) -> Memory
    fuzz_range(interval, maximum_interval=36500) -> (low, high)
    fuzzed_interval(interval, seed, maximum_interval=36500, minimum=1) -> int days
    memorized(params, cards) -> float                expected cards remembered now
"""

import math
import random
from collections import namedtuple

DEFAULT_PARAMETERS = [
    0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796,
    1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542,
]

LOWER_BOUNDS = [
    0.001, 0.001, 0.001, 0.001, 1.0, 0.001, 0.001, 0.001, 0.0, 0.0, 0.001,
    0.001, 0.001, 0.001, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.1,
]

UPPER_BOUNDS = [
    100.0, 100.0, 100.0, 100.0, 10.0, 4.0, 4.0, 0.75, 4.5, 0.8, 3.5,
    5.0, 0.25, 0.9, 4.0, 1.0, 6.0, 2.0, 2.0, 0.8, 0.8,
]

PARAMETER_COUNT = 21

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RATINGS = (AGAIN, HARD, GOOD, EASY)

STABILITY_MIN = 0.001
STABILITY_MAX = 36500.0
DIFFICULTY_MIN = 1.0
DIFFICULTY_MAX = 10.0

RETENTION_MIN = 0.0001
RETENTION_MAX = 0.9999

MAXIMUM_INTERVAL = 36500

# Anki's fuzz: (start, end, factor) ranges; the fuzz delta grows by factor * the days of the
# interval that fall in the range, and intervals under 2.5 days are not fuzzed at all.
FUZZ_RANGES = ((2.5, 7.0, 0.15), (7.0, 20.0, 0.10), (20.0, math.inf, 0.05))

Memory = namedtuple('Memory', 'stability difficulty')


def _clamp(value, low, high):
    return min(max(value, low), high)


def _check_rating(rating):
    if rating not in RATINGS:
        raise ValueError(f'rating must be 1 to 4, not {rating!r}')


def normalize_parameters(params):
    """Return `params` as 21 floats, each within its bounds.

    Accepts an FSRS-4.5 set (17 numbers) or an FSRS-5 set (19), upgraded as the reference
    implementations upgrade them, or an FSRS-6 set (21). Raises ValueError for any other
    length or for entries that are not numbers.
    """
    try:
        values = [float(value) for value in params]
    except (TypeError, ValueError) as error:
        raise ValueError(f'FSRS parameters must be numbers: {params!r}') from error
    if any(math.isnan(value) for value in values):
        raise ValueError(f'FSRS parameters must be numbers: {params!r}')
    if len(values) == 17:
        values[4] += 2 * values[5]
        values[5] = math.log(3 * values[5] + 1) / 3
        values[6] += 0.5
        values += [0.0, 0.0, 0.0, 0.5]
    elif len(values) == 19:
        values += [0.0, 0.5]
    elif len(values) != PARAMETER_COUNT:
        raise ValueError(f'FSRS needs 17, 19 or 21 parameters, not {len(values)}')
    return [_clamp(value, low, high)
            for value, low, high in zip(values, LOWER_BOUNDS, UPPER_BOUNDS, strict=True)]


def decay(params):
    """The forgetting curve's exponent, -w20."""
    return -params[20]


def factor(params):
    """The forgetting curve's scale: makes retrievability 0.9 when elapsed_days == stability."""
    return 0.9 ** (1 / decay(params)) - 1


def retrievability(params, stability, elapsed_days):
    """The probability of recalling a card `elapsed_days` after a review left it at `stability`."""
    stability = max(stability, STABILITY_MIN)
    elapsed_days = max(elapsed_days, 0)
    return (1 + factor(params) * elapsed_days / stability) ** decay(params)


def next_interval(params, stability, desired_retention, maximum_interval=MAXIMUM_INTERVAL):
    """The days until retrievability falls to `desired_retention`: 1 to `maximum_interval`."""
    stability = max(stability, STABILITY_MIN)
    retention = _clamp(desired_retention, RETENTION_MIN, RETENTION_MAX)
    interval = stability / factor(params) * (retention ** (1 / decay(params)) - 1)
    return int(_clamp(round(interval), 1, maximum_interval))


def _initial_difficulty(params, rating):
    return params[4] - math.exp(params[5] * (rating - 1)) + 1


def initial_memory(params, rating):
    """The memory state after a card's first rating."""
    _check_rating(rating)
    stability = _clamp(params[rating - 1], STABILITY_MIN, STABILITY_MAX)
    difficulty = _clamp(_initial_difficulty(params, rating), DIFFICULTY_MIN, DIFFICULTY_MAX)
    return Memory(stability, difficulty)


def _next_difficulty(params, difficulty, rating):
    delta = -params[6] * (rating - 3)
    difficulty = difficulty + delta * (10 - difficulty) / 9
    difficulty = params[7] * _initial_difficulty(params, EASY) + (1 - params[7]) * difficulty
    return _clamp(difficulty, DIFFICULTY_MIN, DIFFICULTY_MAX)


def _short_term_stability(params, stability, rating):
    increase = math.exp(params[17] * (rating - 3 + params[18])) * stability ** -params[19]
    if rating >= HARD:
        increase = max(increase, 1)
    return stability * increase


def _forget_stability(params, stability, difficulty, retrievability_):
    new = (params[11] * difficulty ** -params[12] * ((stability + 1) ** params[13] - 1)
           * math.exp((1 - retrievability_) * params[14]))
    return min(new, stability / math.exp(params[17] * params[18]))


def _recall_stability(params, stability, difficulty, retrievability_, rating):
    hard = params[15] if rating == HARD else 1
    easy = params[16] if rating == EASY else 1
    return stability * (1 + math.exp(params[8]) * (11 - difficulty) * stability ** -params[9]
                        * (math.exp((1 - retrievability_) * params[10]) - 1) * hard * easy)


def next_memory(params, memory, rating, elapsed_days):
    """The memory state after rating a card `elapsed_days` after its last review.

    Under a day is a same-day review (a learning or relearning step), which only scales
    stability; a day or more goes through the forgetting curve.
    """
    _check_rating(rating)
    stability = _clamp(memory.stability, STABILITY_MIN, STABILITY_MAX)
    difficulty = _clamp(memory.difficulty, DIFFICULTY_MIN, DIFFICULTY_MAX)
    if elapsed_days < 1:
        new_stability = _short_term_stability(params, stability, rating)
    else:
        recall = retrievability(params, stability, elapsed_days)
        if rating == AGAIN:
            new_stability = _forget_stability(params, stability, difficulty, recall)
        else:
            new_stability = _recall_stability(params, stability, difficulty, recall, rating)
    return Memory(_clamp(new_stability, STABILITY_MIN, STABILITY_MAX),
                  _next_difficulty(params, difficulty, rating))


def fuzz_range(interval, maximum_interval=MAXIMUM_INTERVAL):
    """The (low, high) days Anki's fuzz may turn `interval` into; no fuzz under 2.5 days."""
    if interval < 2.5:
        return (interval, interval)
    delta = 1.0
    for start, end, fuzz_factor in FUZZ_RANGES:
        delta += fuzz_factor * max(min(interval, end) - start, 0)
    low = max(2, round(interval - delta))
    high = min(round(interval + delta), maximum_interval)
    return (min(low, high), high)


def fuzzed_interval(interval, seed, maximum_interval=MAXIMUM_INTERVAL, minimum=1):
    """`interval` fuzzed deterministically by `seed`: whole days, in fuzz_range() and never
    under `minimum` (the caller passes the previous interval plus one so a review always
    moves a card forward, as Anki does)."""
    minimum = min(minimum, maximum_interval)
    low, high = fuzz_range(interval, maximum_interval)
    fraction = random.Random(seed).random()
    value = min(math.floor(low + fraction * (1 + high - low)), high)
    return int(max(value, minimum))


def memorized(params, cards):
    """The expected number of cards remembered now: the sum of the retrievability of each
    (stability, elapsed_days) pair in `cards`."""
    return sum(retrievability(params, stability, elapsed) for stability, elapsed in cards)
