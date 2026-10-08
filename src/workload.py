# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The daily workload: load balancing, easy days, and a simulation of the reviews to come.

    low, high = fuzz_bounds(interval, minimum, maximum)        # Anki's constrained bounds
    day = balanced_interval(interval, seed, load, today, easy_days, minimum, maximum,
                            sibling_days)                      # None: fuzz as usual
    result = simulate(cards, params, retention, new_cards=…, …) # Simulation, or None
    cards, new_cards = snapshot(collection, deck_ids, now)     # simulate()'s input
    preset_decks(collection, config_id)                        # the decks on a preset

Load balancing is Anki's (24.11 and later, rslib/src/scheduler/states/load_balancer.rs).
When a review interval is set, the fuzz range around it is the set of days the card may go
to. Instead of picking one uniformly, each day is weighed by the reviews already due on it
among the decks of the card's preset and by its distance, and one is drawn by that weight
with the card's fuzz seed (so the answer buttons' preview and the answer agree):

    weight = (1 / reviews) ** 2.15 * (1 / days) ** 3 * sibling * easy_day

A light day is far likelier than a busy one, and a near day than a far one (without the
second factor the balancer drifts cards late). A day with nothing due counts as half a
review: Anki gives it the full weight whatever its other factors, which lets an empty
Minimum day or a sibling's day take cards. Intervals over MAX_BALANCED_INTERVAL days are not
balanced (the fuzz spreads them enough), and under 2.5 days the range is a single day. With
bury siblings on, the day a sibling is due weighs almost nothing and the five days either
side of it progressively less (SIBLING_FACTORS), so a note's cards do not come up together.

Easy days: each weekday of a preset (Monday first) is NORMAL (1.0), REDUCED (0.5) or MINIMUM
(0.0), Anki's easyDaysPercentages. A Minimum day weighs almost nothing (not zero, so a range
of Minimum days still balances); a Reduced day counts as Normal while its reviews, doubled,
stay at or under the average load of the range's other days by their factors, and as Minimum
above that (easy_day_modifiers). Easy days act through the balancer, so only with it on.

simulate() runs a preset's cards forward, day by day, with FSRS as the memory model (the
fsrs-rs simulator Anki's workload estimate uses, simplified): each due card is recalled with
its retrievability and then rated Hard, Good or Easy in fsrs-rs's default proportions, or
forgotten, relearned and rated Again; new cards come in at the daily limit with fsrs-rs's
first-rating proportions and pass the learning steps with Good; due cards over the review
limit wait for the next day; intervals are FSRS's for the desired retention, balanced as
above when the preset balances. Past SAMPLE_LIMIT cards a random sample stands in for the
whole, its counts scaled up. The result is the reviews of each day, the cards expected to
be remembered at the end, and the cards studied by then.
"""

import heapq
import math
import random
from collections import namedtuple

from . import days, fsrs
from .fsrs import AGAIN, EASY, GOOD, HARD

NORMAL, REDUCED, MINIMUM = 1.0, 0.5, 0.0
EASY_DAYS = (MINIMUM, REDUCED, NORMAL)
# What each kind of day weighs; a Minimum day is not zero, so all-Minimum ranges still work.
DAY_LOAD = {NORMAL: 1.0, REDUCED: 0.5, MINIMUM: 0.0001}

MAX_BALANCED_INTERVAL = 90
BALANCE_DAYS = int(MAX_BALANCED_INTERVAL * 1.1)  # the days ahead whose load is counted
SIBLING_STEPS = range(-5, 6)
SIBLING_FACTORS = (1.0, 0.8, 0.6, 0.4, 0.2, 0.000001, 0.2, 0.4, 0.6, 0.8, 1.0)
COUNT_EXPONENT = 2.15
DISTANCE_EXPONENT = 3
EMPTY_DAY = 0.5  # the reviews a day with none due counts as

# fsrs-rs's simulator defaults: how a recalled card is rated (Hard, Good, Easy) and how a
# new card is first rated (Again, Hard, Good, Easy).
REVIEW_RATINGS = (HARD, GOOD, EASY)
REVIEW_WEIGHTS = (0.224, 0.631, 0.145)
FIRST_WEIGHTS = (0.24, 0.094, 0.495, 0.171)
SIMULATED_DAYS = 365
SAMPLE_LIMIT = 2500

# The reviews of each day, the cards expected to be remembered at the end, and the cards
# studied by then.
Simulation = namedtuple('Simulation', 'reviews memorized cards')


# -- load balancing ----------------------------------------------------------------------------

def _round(value):
    """Half away from zero, as Rust's f32::round (Python's round() goes to even)."""
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def fuzz_delta(interval):
    """The days of fuzz either side of `interval`: none under 2.5 days."""
    if interval < 2.5:
        return 0.0
    delta = 1.0
    for start, end, factor in fsrs.FUZZ_RANGES:
        delta += factor * max(min(interval, end) - start, 0)
    return delta


def fuzz_bounds(interval, minimum=1, maximum=fsrs.MAXIMUM_INTERVAL):
    """The (low, high) days the fuzz may turn `interval` into, within `minimum` and
    `maximum`; at least two days wide when that is allowed and over two days (Anki's
    constrained_fuzz_bounds)."""
    minimum = min(minimum, maximum)
    interval = min(max(interval, minimum), maximum)
    delta = fuzz_delta(interval)
    low = min(max(_round(interval - delta), minimum), maximum)
    high = min(max(_round(interval + delta), minimum), maximum)
    if high == low and 2 < high < maximum:
        high = low + 1
    return int(low), int(high)


def easy_day(value):
    """A stored easy-day value as NORMAL, REDUCED or MINIMUM (Anki reads any value other
    than 1 and 0 as Reduced)."""
    value = float(value)
    if value >= 1.0:
        return NORMAL
    if value <= 0.0:
        return MINIMUM
    return REDUCED


def normalize_easy_days(values):
    """Seven easy-day values, Monday first; the defaults (every day Normal) for anything
    that is not seven numbers."""
    try:
        values = [easy_day(value) for value in values or ()]
    except (TypeError, ValueError):
        values = []
    return values if len(values) == 7 else [NORMAL] * 7


def easy_day_modifiers(easy_days, weekdays, counts):
    """The easy-day factor of each day of a range, from its weekday and the reviews due on
    it: Normal 1, Minimum almost 0, Reduced one or the other by the range's load."""
    kinds = [easy_days[weekday] for weekday in weekdays]
    total_reviews = sum(counts)
    total_load = sum(DAY_LOAD[kind] for kind in kinds)
    modifiers = []
    for kind, count in zip(kinds, counts, strict=True):
        if kind == REDUCED:
            other_load = total_load - DAY_LOAD[REDUCED]
            threshold = (total_reviews - count) / other_load if other_load > 0 else math.inf
            kind = MINIMUM if count / DAY_LOAD[REDUCED] > threshold else NORMAL
        modifiers.append(DAY_LOAD[kind])
    return modifiers


def sibling_modifiers(sibling_days, low, high):
    """The sibling factor of each day from `low` to `high`: tiny on a day a sibling is due,
    rising over the five days either side of it."""
    modifiers = [1.0] * (high - low + 1)
    for day in set(sibling_days):
        for step, factor in zip(SIBLING_STEPS, SIBLING_FACTORS, strict=True):
            index = day + step - low
            if 0 <= index < len(modifiers):
                modifiers[index] *= factor
    return modifiers


def day_weight(count, interval):
    """How likely a day is picked for its reviews due and its distance in days."""
    count = count if count > 0 else EMPTY_DAY
    return (1 / count) ** COUNT_EXPONENT * (1 / interval) ** DISTANCE_EXPONENT


def balanced_interval(interval, seed, load, today, easy_days=None, minimum=1,
                      maximum=fsrs.MAXIMUM_INTERVAL, sibling_days=()):
    """The interval in days, within fuzz_bounds(interval, minimum, maximum), that keeps the
    daily load even; None for an interval too long to balance (the caller fuzzes it).

    `load(days)` is the reviews due that many days from `today` (a day number, whose
    weekdays easy_days are read by), `seed` the card's fuzz seed (or a random.Random),
    `easy_days` seven values Monday first (None: every day Normal), `sibling_days` the days
    from today a sibling is due on."""
    minimum = max(1, minimum)
    if int(interval) > MAX_BALANCED_INTERVAL or minimum > MAX_BALANCED_INTERVAL:
        return None
    low, high = fuzz_bounds(interval, minimum, maximum)
    if low == high:
        return low
    targets = range(low, high + 1)
    counts = [load(target) for target in targets]
    if easy_days is None:
        easy = [1.0] * len(targets)
    else:
        easy = easy_day_modifiers(normalize_easy_days(easy_days),
                                  [days.weekday(today + target) for target in targets], counts)
    siblings = sibling_modifiers(sibling_days, low, high)
    weights = [day_weight(count, target) * sibling * easy_factor
               for target, count, sibling, easy_factor
               in zip(targets, counts, siblings, easy, strict=True)]
    rng = seed if isinstance(seed, random.Random) else random.Random(seed)
    return rng.choices(targets, weights)[0]


# -- the collection ----------------------------------------------------------------------------

def preset_decks(collection, config_id):
    """The ids of the decks on a preset (a deck whose preset is gone is on the default)."""
    from .deck_config import DEFAULT_ID  # here: deck_config imports this module

    known = {row[0] for row in collection.db.execute('SELECT id FROM deck_configs')}
    return [row[0] for row in collection.db.execute('SELECT id, config_id FROM decks')
            if (row[1] if row[1] in known else DEFAULT_ID) == config_id]


def snapshot(collection, deck_ids, now=None):
    """The decks' cards as simulate() takes them: ([(stability, difficulty, due, last), …],
    new cards), `due` and `last` (the last review) in days from today. Suspended cards are
    left out; a learning card counts as a review due today."""
    if not deck_ids:
        return [], 0
    today = collection.today(now)
    marks = ','.join('?' * len(deck_ids))
    cards = []
    new = 0
    rows = collection.db.execute(
        f'SELECT state, due, stability, difficulty, last_review FROM cards '
        f'WHERE suspended = 0 AND deck_id IN ({marks})', list(deck_ids))
    for state, due, stability, difficulty, last_review in rows:
        if state == 'new' or not stability or not last_review:
            new += state == 'new'
            continue
        last = collection.today(last_review) - today
        if state == 'review':
            due_in = due - today
        else:
            due_in = 0
        cards.append((float(stability), float(difficulty or 5.0), max(0, due_in), min(0, last)))
    return cards, new


# -- simulation ------------------------------------------------------------------------------

def simulate(cards, params, desired_retention, new_cards=0, new_per_day=20,
             reviews_per_day=9999, maximum_interval=fsrs.MAXIMUM_INTERVAL,
             learning_steps=2, relearning_steps=1, load_balancing=True, easy_days=None,
             today=0, horizon=SIMULATED_DAYS, seed=0, should_stop=None):
    """The reviews of each of the next `horizon` days and the cards remembered after them,
    as a Simulation; None when `should_stop()` turned true on the way.

    `cards` are (stability, difficulty, due, last) as snapshot() gives them, `new_cards` the
    number not yet studied, `learning_steps` and `relearning_steps` the number of steps;
    `today` is the day number the simulation starts on (for the easy days' weekdays). The
    same arguments give the same result."""
    rng = random.Random(seed)
    params = list(params)
    introduce = min(new_cards, new_per_day * horizon)
    total = len(cards) + introduce
    share = min(1.0, SAMPLE_LIMIT / total) if total else 1.0
    if share < 1.0:
        cards = [card for card in cards if rng.random() < share]
        new_cards = int(round(new_cards * share))
    stability = []
    difficulty = []
    last = []
    load = [0] * (horizon + MAX_BALANCED_INTERVAL * 2)
    queue = []
    for index, (s, d, due, seen) in enumerate(cards):
        stability.append(s)
        difficulty.append(d)
        last.append(seen)
        if due < horizon:
            heapq.heappush(queue, (due, index))
        if due < len(load):
            load[due] += 1
    balance_days = easy_days if load_balancing else None

    def schedule(index, memory, day):
        stability[index], difficulty[index] = memory.stability, memory.difficulty
        last[index] = day
        interval = fsrs.next_interval(params, memory.stability, desired_retention,
                                      maximum_interval)
        if load_balancing:
            def count(target):
                return load[day + target] if day + target < len(load) else 0

            interval = balanced_interval(interval, rng, count, today + day, balance_days,
                                         1, maximum_interval) or interval
        due = day + interval
        if due < len(load):
            load[due] += 1
        if due < horizon:
            heapq.heappush(queue, (due, index))

    reviews = []
    review_budget = 0.0
    new_budget = 0.0
    for day in range(horizon):
        if should_stop is not None and should_stop():
            return None
        review_budget += reviews_per_day * share
        allowed = int(review_budget)
        review_budget -= allowed
        done = 0
        while queue and queue[0][0] <= day:
            _due, index = heapq.heappop(queue)
            if done >= allowed:  # over the limit: due again tomorrow
                heapq.heappush(queue, (day + 1, index))
                if day + 1 < len(load):
                    load[day + 1] += 1
                continue
            done += 1
            memory = fsrs.Memory(stability[index], difficulty[index])
            elapsed = day - last[index]
            if rng.random() < fsrs.retrievability(params, memory.stability, elapsed):
                rating = rng.choices(REVIEW_RATINGS, REVIEW_WEIGHTS)[0]
                memory = fsrs.next_memory(params, memory, rating, elapsed)
            else:
                memory = fsrs.next_memory(params, memory, AGAIN, elapsed)
                for _step in range(relearning_steps):
                    memory = fsrs.next_memory(params, memory, GOOD, 0)
            schedule(index, memory, day)
        reviews.append(done / share)
        new_budget += new_per_day * share
        coming = min(int(new_budget), new_cards)
        new_budget -= int(new_budget)
        new_cards -= coming
        for _new in range(coming):
            rating = rng.choices(fsrs.RATINGS, FIRST_WEIGHTS)[0]
            memory = fsrs.initial_memory(params, rating)
            # Then Good through what is left of the steps (Again and Hard start them over).
            left = 0 if rating == EASY else learning_steps - (rating == GOOD)
            for _step in range(max(0, left)):
                memory = fsrs.next_memory(params, memory, GOOD, 0)
            stability.append(memory.stability)
            difficulty.append(memory.difficulty)
            last.append(day)
            schedule(len(stability) - 1, memory, day)
    memorized = sum(fsrs.retrievability(params, s, horizon - seen)
                    for s, seen in zip(stability, last, strict=True)) / share
    return Simulation(reviews, memorized, len(stability) / share)
