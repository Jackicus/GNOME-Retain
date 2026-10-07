# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The queries behind the Statistics page, for one deck with its subdecks or the whole
collection.

    stats = Stats(collection, deck_id=None, now=None)   # deck_id None: everything
    stats.today_summary()        {'reviews', 'minutes', 'again', 'hard', 'good', 'easy',
                                  'new_introduced', 'relearned', 'correct_percent',
                                  'streak_days'}
    stats.due_forecast(30)       [(day_offset, review_count)], offset 0 with the overdue
    stats.review_heatmap(365)    {day_number: reviews}, days without one left out
    stats.reviews_per_day(30)    [(day_number, {'new', 'learning', 'review', 'relearning'})]
    stats.card_counts()          {'new', 'learning', 'young', 'mature', 'suspended', 'buried',
                                  'total'}
    stats.interval_histogram()   [(low_days, high_days or None, cards)] of the review cards
    stats.difficulty_histogram() [(low, high, cards)], nine buckets from 1 to 10
    stats.retention(30)          {'passed', 'failed', 'percent', 'young': {…}, 'mature': {…}}
    stats.memorized()            {'cards', 'total_reviewed', 'average_retention'}
    stats.answer_buttons(30)     {'learning': {1: n, 2: n, 3: n, 4: n}, 'young': …, 'mature': …}
    stats.added_per_day(30)      [(day_number, cards)]
    stats.hourly_breakdown()     [(hour, reviews, correct_percent)] for the 24 hours
    stats.totals()               {'cards', 'notes', 'reviews', 'days_studied', 'days_total',
                                  'minutes_total', 'average_per_day'}
    cumulative(pairs)            the pairs with running totals of their second element

A deck's statistics cover the cards now in it and its subdecks, and the reviews of those
cards (wherever the cards were when reviewed). Manual revlog rows (a reset, a set due date)
are never reviews; crams count as reviews where a rating is all that matters (the heatmap,
the answer buttons) but not towards retention. A period of `days_back` days ends today and
includes it. Percentages are 0 to 100, None when there is nothing to divide by.

The before-interval of a review is not logged (`scheduled_days` is the interval it gave),
so a review card counts as mature when `elapsed_days`, the days since its previous review,
is 21 or more. Retrievability is the scheduler's (`Scheduler.retrievability()`), computed
here with each deck's parameters looked up once.

The ids of the revlog are review times in milliseconds, so a day's reviews are the rows
with ids in [day_start(day), day_start(day + 1)) * 1000, which the primary key finds; a
window of days is fetched in one query and grouped by day in Python.
"""

import bisect
import time

from . import fsrs

MATURE_DAYS = 21
NOT_MANUAL = "revlog.kind != 'manual'"
DEFAULT_INTERVAL_BUCKETS = ((0, 1), (1, 7), (7, 30), (30, 90), (90, 365), (365, None))
SLOT_SECONDS = 900  # the quarter hour: no day starts inside one, in any time zone


class Stats:
    """See the module. `now` is Unix seconds (the clock's by default); today follows it."""

    def __init__(self, collection, deck_id=None, now=None):
        self.collection = collection
        self.deck_id = deck_id
        self.now = time.time() if now is None else now
        self.today = collection.today(self.now)
        if deck_id is None:
            self._deck_ids = None
        else:
            self._deck_ids = list(collection.deck_and_children(deck_id))

    # -- scoping ------------------------------------------------------------------------

    def _scope(self):
        """The SQL condition on `cards` for the decks covered, with its parameters."""
        if self._deck_ids is None:
            return '1', []
        if not self._deck_ids:
            return '0', []
        marks = ','.join('?' * len(self._deck_ids))
        return f'cards.deck_id IN ({marks})', list(self._deck_ids)

    def _review_source(self):
        """The FROM clause and condition that pick the covered cards' reviews."""
        scope, params = self._scope()
        if self._deck_ids is None:
            return 'revlog', '1', []
        return 'revlog JOIN cards ON cards.id = revlog.card_id', scope, params

    def _ms(self, day):
        """The millisecond a day begins."""
        return int(self.collection.day_start(day) * 1000)

    def _window(self, days_back):
        """(first_day, since_ms, until_ms) of the last `days_back` days, today included."""
        first = self.today - max(1, int(days_back)) + 1
        return first, self._ms(first), self._ms(self.today + 1)

    def _reviews(self, since_ms, until_ms, kinds=None):
        """The reviews in [since_ms, until_ms) as rows of (id, rating, state, kind,
        duration_ms, elapsed_days), oldest first; `kinds` restricts the kind."""
        source, scope, params = self._review_source()
        condition = f'revlog.id >= ? AND revlog.id < ? AND {NOT_MANUAL} AND {scope}'
        params = [since_ms, until_ms, *params]
        if kinds:
            marks = ','.join('?' * len(kinds))
            condition += f' AND revlog.kind IN ({marks})'
            params.extend(kinds)
        return self.collection.db.execute(
            f'SELECT revlog.id, revlog.rating, revlog.state, revlog.kind, revlog.duration_ms, '
            f'revlog.elapsed_days FROM {source} WHERE {condition} ORDER BY revlog.id',
            params).fetchall()

    def _reviewed_on(self, day):
        """Whether any review was logged on a day."""
        source, scope, params = self._review_source()
        row = self.collection.db.execute(
            f'SELECT 1 FROM {source} WHERE revlog.id >= ? AND revlog.id < ? AND {NOT_MANUAL} '
            f'AND {scope} LIMIT 1', [self._ms(day), self._ms(day + 1), *params]).fetchone()
        return row is not None

    def _streak(self):
        """Consecutive days with a review, ending today or (today being unstudied) yesterday."""
        day = self.today
        if not self._reviewed_on(day):
            day -= 1
        streak = 0
        while self._reviewed_on(day):
            streak += 1
            day -= 1
        return streak

    # -- today ----------------------------------------------------------------------------

    def today_summary(self):
        rows = self._reviews(self._ms(self.today), self._ms(self.today + 1))
        ratings = {rating: 0 for rating in (1, 2, 3, 4)}
        duration = 0
        introduced = 0
        relearned = 0
        for row in rows:
            ratings[row['rating']] = ratings.get(row['rating'], 0) + 1
            duration += row['duration_ms']
            if row['state'] == 'new':
                introduced += 1
            elif row['state'] == 'relearning':
                relearned += 1
        reviews = len(rows)
        return {
            'reviews': reviews,
            'minutes': duration / 60000,
            'again': ratings[1],
            'hard': ratings[2],
            'good': ratings[3],
            'easy': ratings[4],
            'new_introduced': introduced,
            'relearned': relearned,
            'correct_percent': _percent(reviews - ratings[1], reviews),
            'streak_days': self._streak(),
        }

    # -- the future -----------------------------------------------------------------------

    def due_forecast(self, days_ahead=30):
        """Review cards due on each of the next days: [(0, overdue and today), (1, n), …]."""
        scope, params = self._scope()
        days_ahead = max(0, int(days_ahead))
        counts = [0] * (days_ahead + 1)
        rows = self.collection.db.execute(
            f'SELECT due, COUNT(*) AS n FROM cards WHERE state = ? AND due <= ? '
            f'AND suspended = 0 AND buried = 0 AND {scope} GROUP BY due',
            ['review', self.today + days_ahead, *params])
        for row in rows:
            counts[max(0, row['due'] - self.today)] += row['n']
        return list(enumerate(counts))

    # -- the past -------------------------------------------------------------------------

    def review_heatmap(self, days_back=365):
        first, since, until = self._window(days_back)
        days = _DayMap(self.collection, first, self.today)
        counts = {}
        for row in self._reviews(since, until):
            day = days.day_of(row['id'])
            counts[day] = counts.get(day, 0) + 1
        return counts

    def reviews_per_day(self, days_back=30):
        first, since, until = self._window(days_back)
        days = _DayMap(self.collection, first, self.today)
        by_day = {day: {'new': 0, 'learning': 0, 'review': 0, 'relearning': 0}
                  for day in range(first, self.today + 1)}
        for row in self._reviews(since, until):
            counts = by_day[days.day_of(row['id'])]
            if row['state'] in counts:
                counts[row['state']] += 1
        return sorted(by_day.items())

    def added_per_day(self, days_back=30):
        first, since, until = self._window(days_back)
        days = _DayMap(self.collection, first, self.today)
        scope, params = self._scope()
        by_day = dict.fromkeys(range(first, self.today + 1), 0)
        rows = self.collection.db.execute(
            f'SELECT created, COUNT(*) AS n FROM cards WHERE created >= ? AND created < ? '
            f'AND {scope} GROUP BY created', [since // 1000, until // 1000, *params])
        for row in rows:
            by_day[days.day_of(row['created'] * 1000)] += row['n']
        return sorted(by_day.items())

    def retention(self, days_back=30):
        """How often review cards were remembered (true retention): Again fails, the rest
        pass; split into young and mature by the days since the previous review."""
        first, since, until = self._window(days_back)
        buckets = {'young': [0, 0], 'mature': [0, 0]}
        for row in self._reviews(since, until, kinds=['review']):
            if row['state'] != 'review':
                continue
            bucket = buckets['mature' if row['elapsed_days'] >= MATURE_DAYS else 'young']
            bucket[1 if row['rating'] == 1 else 0] += 1
        result = _retention_entry(sum(b[0] for b in buckets.values()),
                                  sum(b[1] for b in buckets.values()))
        for name, (passed, failed) in buckets.items():
            result[name] = _retention_entry(passed, failed)
        return result

    def answer_buttons(self, days_back=30):
        """How often each button was pressed, for learning cards (new, learning and
        relearning), young review cards and mature ones."""
        first, since, until = self._window(days_back)
        result = {group: {rating: 0 for rating in (1, 2, 3, 4)}
                  for group in ('learning', 'young', 'mature')}
        for row in self._reviews(since, until):
            if row['rating'] not in result['learning']:
                continue
            if row['state'] != 'review':
                group = 'learning'
            elif row['elapsed_days'] >= MATURE_DAYS:
                group = 'mature'
            else:
                group = 'young'
            result[group][row['rating']] += 1
        return result

    def hourly_breakdown(self):
        """Reviews by the local hour of the day they were made at, over all time."""
        source, scope, params = self._review_source()
        counts = {hour: [0, 0] for hour in range(24)}
        rows = self.collection.db.execute(
            f"SELECT CAST(strftime('%H', revlog.id / 1000, 'unixepoch', 'localtime') "
            f'AS INTEGER) AS hour, COUNT(*) AS n, SUM(revlog.rating != 1) AS correct '
            f'FROM {source} WHERE {NOT_MANUAL} AND {scope} GROUP BY hour', params)
        for row in rows:
            counts[row['hour']][0] += row['n']
            counts[row['hour']][1] += row['correct']
        return [(hour, n, _percent(correct, n)) for hour, (n, correct) in counts.items()]

    def totals(self):
        scope, params = self._scope()
        cards, notes = self.collection.db.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT note_id) FROM cards WHERE {scope}',
            params).fetchone()
        source, review_scope, review_params = self._review_source()
        slots = self.collection.db.execute(
            f'SELECT revlog.id / {SLOT_SECONDS * 1000} AS slot, COUNT(*) AS n, '
            f'SUM(revlog.duration_ms) AS duration FROM {source} '
            f'WHERE {NOT_MANUAL} AND {review_scope} GROUP BY slot', review_params).fetchall()
        reviews = sum(row['n'] for row in slots)
        duration = sum(row['duration'] for row in slots)
        days_studied = 0
        days_total = 0
        if slots:
            first_ms = slots[0]['slot'] * SLOT_SECONDS * 1000
            first_day = self.collection.day_number(first_ms / 1000)
            last_ms = slots[-1]['slot'] * SLOT_SECONDS * 1000
            last_day = max(self.today, self.collection.day_number(last_ms / 1000))
            days = _DayMap(self.collection, first_day, last_day)
            days_studied = len({days.day_of(row['slot'] * SLOT_SECONDS * 1000) for row in slots})
            days_total = self.today - first_day + 1
        return {
            'cards': cards,
            'notes': notes,
            'reviews': reviews,
            'days_studied': days_studied,
            'days_total': days_total,
            'minutes_total': duration / 60000,
            'average_per_day': reviews / days_studied if days_studied else 0.0,
        }

    # -- the cards ------------------------------------------------------------------------

    def card_counts(self):
        """Cards by kind: a suspended or buried card counts there and nowhere else."""
        scope, params = self._scope()
        counts = {'new': 0, 'learning': 0, 'young': 0, 'mature': 0, 'suspended': 0,
                  'buried': 0, 'total': 0}
        rows = self.collection.db.execute(
            f'SELECT state, suspended != 0 AS suspended, buried != 0 AS buried, '
            f'interval >= {MATURE_DAYS} AS mature, COUNT(*) AS n FROM cards WHERE {scope} '
            f'GROUP BY 1, 2, 3, 4', params)
        for row in rows:
            counts['total'] += row['n']
            if row['suspended']:
                counts['suspended'] += row['n']
            elif row['buried']:
                counts['buried'] += row['n']
            elif row['state'] == 'new':
                counts['new'] += row['n']
            elif row['state'] == 'review':
                counts['mature' if row['mature'] else 'young'] += row['n']
            else:
                counts['learning'] += row['n']
        return counts

    def interval_histogram(self, buckets=None):
        """Review cards by interval: [(low, high, cards)], low inclusive, high exclusive
        (None: open-ended)."""
        buckets = [tuple(bucket) for bucket in (buckets or DEFAULT_INTERVAL_BUCKETS)]
        counts = [0] * len(buckets)
        scope, params = self._scope()
        rows = self.collection.db.execute(
            f'SELECT interval, COUNT(*) AS n FROM cards WHERE state = ? AND {scope} '
            f'GROUP BY interval', ['review', *params])
        for row in rows:
            for index, (low, high) in enumerate(buckets):
                if row['interval'] >= low and (high is None or row['interval'] < high):
                    counts[index] += row['n']
                    break
        return [(low, high, count) for (low, high), count in zip(buckets, counts, strict=True)]

    def difficulty_histogram(self):
        """Cards with a memory state by difficulty: nine buckets, [1, 2) to [9, 10]."""
        scope, params = self._scope()
        counts = dict.fromkeys(range(1, 10), 0)
        rows = self.collection.db.execute(
            f'SELECT MIN(MAX(CAST(difficulty AS INTEGER), 1), 9) AS bucket, COUNT(*) AS n '
            f'FROM cards WHERE stability > 0 AND {scope} GROUP BY bucket', params)
        for row in rows:
            counts[row['bucket']] += row['n']
        return [(low, low + 1, counts[low]) for low in range(1, 10)]

    def memorized(self):
        """How many of the reviewed cards are remembered now, in expectation: the sum of
        their retrievability, and that as a percentage of them."""
        scope, params = self._scope()
        rows = self.collection.db.execute(
            f'SELECT deck_id, stability, last_review FROM cards '
            f'WHERE stability > 0 AND last_review > 0 AND {scope}', params)
        parameters = {}
        total = 0.0
        count = 0
        for row in rows:
            if row['deck_id'] not in parameters:
                parameters[row['deck_id']] = \
                    self.collection.config_for_deck(row['deck_id']).parameters()
            elapsed = max(0.0, (self.now - row['last_review']) / 86400)
            total += fsrs.retrievability(parameters[row['deck_id']], row['stability'], elapsed)
            count += 1
        return {
            'cards': total,
            'total_reviewed': count,
            'average_retention': _percent(total, count),
        }


class _DayMap:
    """Day numbers of millisecond ids within a range of days, by bisection over the
    moments the days begin."""

    def __init__(self, collection, first_day, last_day):
        self.first = first_day
        self.starts = [collection.day_start(day) * 1000 for day in range(first_day, last_day + 2)]

    def day_of(self, id_ms):
        index = bisect.bisect_right(self.starts, id_ms) - 1
        return self.first + min(max(index, 0), len(self.starts) - 2)


def cumulative(pairs):
    """The pairs with the second element replaced by its running total."""
    total = 0
    result = []
    for first, count in pairs:
        total += count
        result.append((first, total))
    return result


def _percent(part, whole):
    return part / whole * 100 if whole else None


def _retention_entry(passed, failed):
    return {'passed': passed, 'failed': failed, 'percent': _percent(passed, passed + failed)}
