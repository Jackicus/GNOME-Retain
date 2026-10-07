# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Fits the 21 FSRS-6 parameters to a review history, in pure Python.

A deck's Optimize button runs `suggest()` in a thread: it turns the deck's revlog rows into
one history per card (the list of (rating, delta_days) a card was answered with, a fresh
history each time a card is learned from new), checks there is enough data, and fits the
parameters to minimise the mean binary log loss of predicting each review a day or more after
the previous one: the FSRS memory state is replayed through the history and the loss of the
predicted retrievability R against whether the card was recalled (a rating above Again) is
summed. Same-day reviews update the memory but are not predicted.

The fit is deterministic and needs no torch: it starts from the deck's current parameters
(or the defaults), pretrains the initial stabilities w0…w3 from the outcome of each card's
first interval by first rating, then runs a coordinate descent over all 21 parameters with
an adaptive step per parameter (a tenth of the parameter's range to start, halved when
neither direction helps, grown by half when one does) for a few sweeps, until a sweep gains
less than 1e-4. The result is the best set seen, including the start, normalised to the
bounds. `progress(fraction, loss)` is called after each sweep and `should_stop()` is polled
between parameters, so the thread can report and be cancelled.

The replay inlines the formulas of fsrs.next_memory() for speed (a loss over 10 000 reviews
takes about 10 ms); tests/test_optimizer.py checks it against the model on random inputs.

    MINIMUM_REVIEWS                      the loss-bearing reviews an optimisation needs
    NotEnoughData(count)                 raised by suggest() below it
    items_from_revlog(rows, day_start_hour=4) -> [[(rating, delta_days), …], …]
    log_loss(params, histories) -> float
    optimize(histories, initial=None, iterations=None, progress=None, should_stop=None)
        -> 21 parameters
    evaluate(params, histories) -> {'log_loss', 'rmse_bins', 'reviews'}
    suggest(collection_rows, current=None, day_start_hour=4, progress=None, should_stop=None)
        -> (params, before_loss, after_loss)
"""

import logging
import math

from . import days, fsrs
from .fsrs import (
    DIFFICULTY_MAX, DIFFICULTY_MIN, LOWER_BOUNDS, PARAMETER_COUNT, STABILITY_MAX, STABILITY_MIN,
    UPPER_BOUNDS,
)

log = logging.getLogger(__name__)

MINIMUM_REVIEWS = 400
DEFAULT_SWEEPS = 8
SWEEP_TOLERANCE = 1e-4
INITIAL_STEP_FRACTION = 0.1
STEP_GROWTH = 1.5
STEP_SHRINK = 0.5
PRETRAIN_MINIMUM = 20           # first-interval outcomes a rating needs to be pretrained
PRETRAIN_PRIOR = 1.0            # the weight (in reviews) pulling a pretrained S to its start
RMSE_BINS = 20

# Retrievability is clamped to this range before the loss so a wrong but certain prediction
# costs ln(1e6), not infinity.
EPSILON = 1e-6

# The kinds and ratings of revlog rows that say nothing about memory.
SKIPPED_KINDS = frozenset(('cram', 'manual'))


class NotEnoughData(Exception):
    """The revlog holds too few loss-bearing reviews to fit parameters to; `count` says how
    many it holds."""

    def __init__(self, count):
        super().__init__(f'{count} reviews, {MINIMUM_REVIEWS} needed')
        self.count = count


def items_from_revlog(rows, day_start_hour=days.DEFAULT_DAY_START_HOUR):
    """The review histories in revlog `rows` (dicts or sqlite rows with id, card_id, rating,
    state and kind, in id order), one list of (rating, delta_days) per learning of a card.

    Cram and manual rows and rating 0 are skipped; a review in the new state starts a fresh
    history for its card; delta_days is the whole days since the previous review of the
    history (0 for the first review and for a review on the same day); a history with fewer
    than two reviews is dropped.
    """
    histories = []
    open_histories = {}             # card_id -> (history, day number of its last review)

    def close(card_id):
        history, _ = open_histories.pop(card_id)
        if len(history) >= 2:
            histories.append(history)

    for row in rows:
        rating = row['rating']
        if rating == 0 or row['kind'] in SKIPPED_KINDS:
            continue
        card_id = row['card_id']
        day = days.day_number(row['id'] / 1000, day_start_hour)
        if row['state'] == 'new' or card_id not in open_histories:
            if card_id in open_histories:
                close(card_id)
            open_histories[card_id] = ([(rating, 0)], day)
        else:
            history, last_day = open_histories[card_id]
            history.append((rating, max(day - last_day, 0)))
            open_histories[card_id] = (history, day)
    for card_id in list(open_histories):
        close(card_id)
    return histories


def _replay(params, histories, predictions=None):
    """Replay `histories` through the memory model with `params`: the (summed log loss,
    count) of the reviews a day or more after the previous one, appending each (R, recalled)
    to `predictions` when a list is given.

    This mirrors fsrs.initial_memory() and fsrs.next_memory() in one tight loop, the
    parameters unpacked into locals.
    """
    (w0, w1, w2, w3, w4, w5, w6, w7, w8, w9, w10, w11, w12, w13, w14, w15, w16, w17, w18,
     w19, w20) = params
    exp = math.exp
    ln = math.log
    decay = -w20
    factor = 0.9 ** (1 / decay) - 1
    initial_stability = (
        min(max(w0, STABILITY_MIN), STABILITY_MAX), min(max(w1, STABILITY_MIN), STABILITY_MAX),
        min(max(w2, STABILITY_MIN), STABILITY_MAX), min(max(w3, STABILITY_MIN), STABILITY_MAX))
    easy_difficulty = w4 - exp(w5 * 3) + 1          # D0(Easy), the mean reversion target
    reversion = w7 * easy_difficulty
    keep = 1 - w7
    exp_w8 = exp(w8)
    forget_ceiling = exp(w17 * w18)
    low = EPSILON
    high = 1 - EPSILON
    total = 0.0
    count = 0
    for history in histories:
        rating, _ = history[0]
        stability = initial_stability[rating - 1]
        difficulty = w4 - exp(w5 * (rating - 1)) + 1
        difficulty = min(max(difficulty, DIFFICULTY_MIN), DIFFICULTY_MAX)
        for rating, delta in history[1:]:
            if delta < 1:
                increase = exp(w17 * (rating - 3 + w18)) * stability ** -w19
                if rating >= 2 and increase < 1:
                    increase = 1
                new_stability = stability * increase
            else:
                recall = (1 + factor * delta / stability) ** decay
                clamped = low if recall < low else (high if recall > high else recall)
                if rating > 1:
                    total -= ln(clamped)
                    if predictions is not None:
                        predictions.append((recall, 1))
                else:
                    total -= ln(1 - clamped)
                    if predictions is not None:
                        predictions.append((recall, 0))
                count += 1
                if rating == 1:
                    new_stability = (w11 * difficulty ** -w12 * ((stability + 1) ** w13 - 1)
                                     * exp((1 - recall) * w14))
                    ceiling = stability / forget_ceiling
                    if new_stability > ceiling:
                        new_stability = ceiling
                else:
                    growth = (exp_w8 * (11 - difficulty) * stability ** -w9
                              * (exp((1 - recall) * w10) - 1))
                    if rating == 2:
                        growth *= w15
                    elif rating == 4:
                        growth *= w16
                    new_stability = stability * (1 + growth)
            new_difficulty = difficulty - w6 * (rating - 3) * (10 - difficulty) / 9
            new_difficulty = reversion + keep * new_difficulty
            stability = min(max(new_stability, STABILITY_MIN), STABILITY_MAX)
            difficulty = min(max(new_difficulty, DIFFICULTY_MIN), DIFFICULTY_MAX)
    return total, count


def log_loss(params, histories):
    """The mean binary log loss of predicting the reviews of `histories` that came a day or
    more after the previous review (0.0 when there are none)."""
    total, count = _replay(params, histories)
    return total / count if count else 0.0


def loss_bearing_reviews(histories):
    """The number of reviews in `histories` the loss is taken over."""
    return sum(1 for history in histories for _, delta in history[1:] if delta >= 1)


def _first_interval_outcomes(histories):
    """By first rating, the outcome of each history's first interval: {rating: {delta:
    [reviews, recalled]}}, the delta being that of the first review a day or more after the
    first one (same-day reviews in between are skipped)."""
    outcomes = {rating: {} for rating in fsrs.RATINGS}
    for history in histories:
        first_rating = history[0][0]
        for rating, delta in history[1:]:
            if delta >= 1:
                counts = outcomes[first_rating].setdefault(delta, [0, 0])
                counts[0] += 1
                if rating > 1:
                    counts[1] += 1
                break
    return outcomes


def _first_interval_loss(params, stability, groups, start):
    """The log loss of a single `stability` against `groups` ({delta: [reviews, recalled]}),
    plus a weak pull towards the `start` stability."""
    total = 0.0
    for delta, (reviews, recalled) in groups.items():
        recall = fsrs.retrievability(params, stability, delta)
        recall = min(max(recall, EPSILON), 1 - EPSILON)
        total -= recalled * math.log(recall) + (reviews - recalled) * math.log(1 - recall)
    return total + PRETRAIN_PRIOR * (math.log(stability) - math.log(start)) ** 2


def _pretrain_stability(params, groups, start, low, high):
    """The stability in [low, high] that best predicts `groups`: a grid over the range (in
    log space) and then a golden-section search around the best point."""
    log_low = math.log(low)
    log_high = math.log(high)
    grid = 64

    def loss_at(position):
        return _first_interval_loss(params, math.exp(position), groups, start)

    positions = [log_low + (log_high - log_low) * index / grid for index in range(grid + 1)]
    losses = [loss_at(position) for position in positions]
    best = min(range(len(positions)), key=losses.__getitem__)
    left = positions[max(best - 1, 0)]
    right = positions[min(best + 1, grid)]
    ratio = (math.sqrt(5) - 1) / 2
    inner_left = right - ratio * (right - left)
    inner_right = left + ratio * (right - left)
    loss_left = loss_at(inner_left)
    loss_right = loss_at(inner_right)
    for _ in range(40):
        if loss_left < loss_right:
            right, inner_right, loss_right = inner_right, inner_left, loss_left
            inner_left = right - ratio * (right - left)
            loss_left = loss_at(inner_left)
        else:
            left, inner_left, loss_left = inner_left, inner_right, loss_right
            inner_right = left + ratio * (right - left)
            loss_right = loss_at(inner_right)
    return math.exp((left + right) / 2)


def pretrain(params, histories):
    """`params` with w0…w3 refitted to the outcomes of the first intervals, for each first
    rating with at least PRETRAIN_MINIMUM of them."""
    params = list(params)
    outcomes = _first_interval_outcomes(histories)
    for rating in fsrs.RATINGS:
        groups = outcomes[rating]
        if sum(reviews for reviews, _ in groups.values()) < PRETRAIN_MINIMUM:
            continue
        index = rating - 1
        params[index] = _pretrain_stability(params, groups, params[index], LOWER_BOUNDS[index],
                                            UPPER_BOUNDS[index])
    return params


def optimize(histories, initial=None, iterations=None, progress=None, should_stop=None):
    """The 21 parameters, within bounds, that best predict `histories`.

    Starts from `initial` (the defaults when None), pretrains w0…w3, then runs `iterations`
    sweeps of coordinate descent (DEFAULT_SWEEPS when None), stopping early when a sweep
    gains less than SWEEP_TOLERANCE. `progress(fraction, loss)` is called after each sweep;
    `should_stop()` is polled between parameters and ends the search with the best set so
    far. Deterministic.
    """
    start = fsrs.normalize_parameters(
        fsrs.DEFAULT_PARAMETERS if initial is None else initial)
    best = list(start)
    best_loss = log_loss(best, histories)
    if not histories or (should_stop is not None and should_stop()):
        return fsrs.normalize_parameters(best)

    current = pretrain(start, histories)
    current_loss = log_loss(current, histories)
    if current_loss < best_loss:
        best, best_loss = list(current), current_loss
    log.debug('optimizer: start %.4f, pretrained %.4f', log_loss(start, histories),
              current_loss)

    sweeps = DEFAULT_SWEEPS if iterations is None else max(int(iterations), 0)
    steps = [(high - low) * INITIAL_STEP_FRACTION
             for low, high in zip(LOWER_BOUNDS, UPPER_BOUNDS, strict=True)]
    stopped = False
    for sweep in range(sweeps):
        sweep_start = current_loss
        for index in range(PARAMETER_COUNT):
            if should_stop is not None and should_stop():
                stopped = True
                break
            low, high = LOWER_BOUNDS[index], UPPER_BOUNDS[index]
            improved = False
            for direction in (1, -1):
                value = min(max(current[index] + direction * steps[index], low), high)
                if value == current[index]:
                    continue
                candidate = list(current)
                candidate[index] = value
                candidate_loss = log_loss(candidate, histories)
                if candidate_loss < current_loss:
                    current, current_loss = candidate, candidate_loss
                    improved = True
                    break
            if improved:
                steps[index] = min(steps[index] * STEP_GROWTH, high - low)
            else:
                steps[index] *= STEP_SHRINK
        if current_loss < best_loss:
            best, best_loss = list(current), current_loss
        if stopped:
            break
        if progress is not None:
            progress((sweep + 1) / sweeps, best_loss)
        if sweep_start - current_loss < SWEEP_TOLERANCE:
            break
    log.debug('optimizer: best %.4f after %d sweeps%s', best_loss, sweep + 1 if sweeps else 0,
              ' (stopped)' if stopped else '')
    return fsrs.normalize_parameters(best)


def evaluate(params, histories):
    """How well `params` predict `histories`: the mean log loss, the RMSE (bins) of
    predicted against observed recall over RMSE_BINS bins of predicted retrievability
    weighted by the reviews in each, and the number of reviews predicted."""
    params = fsrs.normalize_parameters(params)
    predictions = []
    total, count = _replay(params, histories, predictions)
    if not count:
        return {'log_loss': 0.0, 'rmse_bins': 0.0, 'reviews': 0}
    bins = [[0, 0.0, 0] for _ in range(RMSE_BINS)]     # reviews, predicted sum, recalled
    for recall, recalled in predictions:
        bin_ = bins[min(int(recall * RMSE_BINS), RMSE_BINS - 1)]
        bin_[0] += 1
        bin_[1] += recall
        bin_[2] += recalled
    squares = sum(reviews * (predicted / reviews - recalled / reviews) ** 2
                  for reviews, predicted, recalled in bins if reviews)
    return {'log_loss': total / count, 'rmse_bins': math.sqrt(squares / count),
            'reviews': count}


def suggest(collection_rows, current=None, day_start_hour=days.DEFAULT_DAY_START_HOUR,
            progress=None, should_stop=None):
    """Fit parameters to a deck's revlog rows: (params, the loss of `current` or the
    defaults, the loss of params). Raises NotEnoughData when fewer than MINIMUM_REVIEWS
    reviews bear a loss."""
    histories = items_from_revlog(collection_rows, day_start_hour)
    count = loss_bearing_reviews(histories)
    if count < MINIMUM_REVIEWS:
        raise NotEnoughData(count)
    start = fsrs.normalize_parameters(
        fsrs.DEFAULT_PARAMETERS if current is None else current)
    before = log_loss(start, histories)
    params = optimize(histories, initial=start, progress=progress, should_stop=should_stop)
    return params, before, log_loss(params, histories)
