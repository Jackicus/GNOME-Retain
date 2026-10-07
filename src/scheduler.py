# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What to study next, and what an answer does to a card.

    scheduler = Scheduler(collection, learn_ahead_minutes=20)
    new, learning, due = scheduler.counts(deck_id)         # the deck and its subdecks
    session = scheduler.session(deck_id)                   # or custom_session(...)
    card = session.next_card()                             # None when the day is done
    labels = scheduler.preview(card)                       # {rating: interval text}
    after = scheduler.answer(card, rating, duration_ms=ms) # records it (undoable)
    session.answered(after)

The memory model is fsrs.py; the per-deck settings (limits, steps, retention) deck_config.py.
A new card goes through the deck's learning steps (minutes apart), then graduates to a
review card due in the number of days FSRS gives for the deck's desired retention, with
Anki's fuzz so cards learned together drift apart. A review card answered Again lapses into
the relearning steps and comes back with a shorter interval; Hard, Good and Easy give it a
longer one. A card answered within a day of its last review updates its memory with FSRS's
short-term rule. `counts()` and a session cap new and due cards by the deck's daily limits,
each deck's own for its cards and the studied deck's for the whole subtree; cards answered
earlier today count towards them. Custom sessions (custom_session) study beyond the limits,
cards forgotten lately, cards due soon, every card of a tag or deck (a cram, which records
nothing on the cards), or the cards of a search.

Day numbers are days.py's; a session reads the clock through `now`, which tests pass.
"""

import math
import random
import time
from gettext import gettext as _
from gettext import ngettext

from . import days, fsrs
from .fsrs import AGAIN, EASY, GOOD, HARD

RATINGS = (AGAIN, HARD, GOOD, EASY)
MINUTE = 60
LIMITS_KEY = 'limits'  # the collection config key of today's extra limits


class Scheduler:

    def __init__(self, collection, learn_ahead_minutes=20):
        self.collection = collection
        self.learn_ahead_minutes = learn_ahead_minutes

    # -- counts ----------------------------------------------------------------------------

    def counts(self, deck_id, now=None):
        """(new, learning, due) for a deck and its subdecks today, within the limits."""
        return self._counts(self.collection.deck_and_children(deck_id), deck_id, now)

    def tree(self, now=None):
        """The deck tree (collection.deck_tree()) with each node's counts filled in, a
        parent's covering its children."""
        roots = self.collection.deck_tree()
        for root in roots:
            for node in root.walk():
                node.new, node.learning, node.due = self.counts(node.deck.id, now)
        return roots

    def _counts(self, deck_ids, top_id, now=None):
        now = now or time.time()
        today = self.collection.today(now)
        if not deck_ids:
            return 0, 0, 0
        limits = self._limits(deck_ids, top_id, now)
        new = 0
        due = 0
        for deck_id in deck_ids:
            available_new = self._count(
                'state = ? AND suspended = 0 AND buried = 0 AND deck_id = ?', ('new', deck_id))
            new += min(available_new, limits['new'][deck_id])
            available_due = self._count(
                'state = ? AND due <= ? AND suspended = 0 AND buried = 0 AND deck_id = ?',
                ('review', today, deck_id))
            due += min(available_due, limits['review'][deck_id])
        new = min(new, limits['new_total'])
        due = min(due, limits['review_total'])
        marks = ','.join('?' * len(deck_ids))
        learning = self._count(
            f'state IN (?, ?) AND due < ? AND suspended = 0 AND buried = 0 '
            f'AND deck_id IN ({marks})',
            ['learning', 'relearning', self.collection.day_start(today + 1)] + list(deck_ids))
        return new, learning, due

    def _count(self, where, params):
        return self.collection.db.execute(f'SELECT COUNT(*) FROM cards WHERE {where}',
                                          list(params)).fetchone()[0]

    def _limits(self, deck_ids, top_id, now):
        """What is left of today's limits: per deck for its own cards, and for the subtree
        as a whole (the studied deck's, plus any extra given today)."""
        today = self.collection.today(now)
        start_ms = int(self.collection.day_start(today) * 1000)
        introduced = {}
        reviewed = {}
        for deck_id in deck_ids:
            introduced[deck_id] = self._done_today(deck_id, start_ms, ('new',))
            reviewed[deck_id] = self._done_today(deck_id, start_ms, ('review', 'relearning'))
        configs = {deck_id: self.collection.config_for_deck(deck_id) for deck_id in deck_ids}
        extra = self._extra(top_id, today)
        top = configs[top_id] if top_id in configs else configs[deck_ids[0]]
        # Extra cards asked for today (custom study) raise the studied deck's own limit too.
        return {
            'new': {deck_id: max(0, configs[deck_id].new_per_day - introduced[deck_id]
                                + (extra['new'] if deck_id == top_id else 0))
                    for deck_id in deck_ids},
            'review': {deck_id: max(0, configs[deck_id].reviews_per_day - reviewed[deck_id]
                                   + (extra['review'] if deck_id == top_id else 0))
                       for deck_id in deck_ids},
            'new_total': max(0, top.new_per_day + extra['new'] - sum(introduced.values())),
            'review_total': max(0, top.reviews_per_day + extra['review']
                                - sum(reviewed.values())),
        }

    def _done_today(self, deck_id, start_ms, states):
        """Reviews of the given before-states logged today for the cards now in the deck."""
        marks = ','.join('?' * len(states))
        return self.collection.db.execute(
            f'SELECT COUNT(*) FROM revlog JOIN cards ON cards.id = revlog.card_id '
            f'WHERE revlog.id >= ? AND revlog.kind = ? AND revlog.state IN ({marks}) '
            f'AND cards.deck_id = ?',
            [start_ms, 'review', *states, deck_id]).fetchone()[0]

    def _extra(self, deck_id, today):
        limits = self.collection.get(LIMITS_KEY, {}) or {}
        entry = limits.get(str(deck_id)) or {}
        if entry.get('day') != today:
            return {'new': 0, 'review': 0}
        return {'new': int(entry.get('new', 0)), 'review': int(entry.get('review', 0))}

    def extend_limits(self, deck_id, new=0, review=0, now=None):
        """Raise today's limits of a deck by so many new cards and reviews (custom study)."""
        today = self.collection.today(now)
        limits = self.collection.get(LIMITS_KEY, {}) or {}
        entry = limits.get(str(deck_id)) or {}
        if entry.get('day') != today:
            entry = {'day': today, 'new': 0, 'review': 0}
        entry['new'] += int(new)
        entry['review'] += int(review)
        limits[str(deck_id)] = entry
        with self.collection.undoable(_('Study More')):
            if self.collection._open is not None:
                old = self.collection.db.execute(
                    'SELECT value FROM config WHERE key = ?', (LIMITS_KEY,)).fetchone()
                self.collection._open['config'].setdefault(LIMITS_KEY,
                                                           old['value'] if old else None)
            self.collection.set(LIMITS_KEY, limits)

    # -- sessions ----------------------------------------------------------------------------

    def session(self, deck_id):
        """Today's study of a deck and its subdecks."""
        return Session(self, deck_id, self.collection.deck_and_children(deck_id))

    def custom_session(self, deck_id, kind, amount=None, now=None):
        """A study beyond the day's queue: `kind` is 'forgotten' (cards answered Again in
        the last `amount` days), 'ahead' (review cards due within `amount` days), 'all'
        (every card of the deck, a cram), 'tag' (every card with the tag `amount`, a cram),
        'search' (the cards a search `amount` finds, a cram), 'new' (the next `amount` new
        cards, beyond the limit) or 'review' (`amount` more due cards)."""
        now = now or time.time()
        deck_ids = self.collection.deck_and_children(deck_id)
        if kind in ('new', 'review'):
            self.extend_limits(deck_id, new=amount if kind == 'new' else 0,
                               review=amount if kind == 'review' else 0, now=now)
            return Session(self, deck_id, deck_ids)
        today = self.collection.today(now)
        marks = ','.join('?' * len(deck_ids))
        base = f'suspended = 0 AND deck_id IN ({marks})'
        if kind == 'forgotten':
            since_ms = int(self.collection.day_start(today - int(amount) + 1) * 1000)
            rows = self.collection.db.execute(
                f'SELECT DISTINCT cards.id FROM cards JOIN revlog ON revlog.card_id = cards.id '
                f'WHERE revlog.rating = 1 AND revlog.id >= ? AND {base} ORDER BY cards.id',
                [since_ms, *deck_ids])
            return Session(self, deck_id, deck_ids, cards=[row[0] for row in rows])
        if kind == 'ahead':
            rows = self.collection.db.execute(
                f'SELECT id FROM cards WHERE state = ? AND due <= ? AND {base} ORDER BY due',
                ['review', today + int(amount), *deck_ids])
            return Session(self, deck_id, deck_ids, cards=[row[0] for row in rows])
        if kind == 'all':
            rows = self.collection.db.execute(
                f'SELECT id FROM cards WHERE {base} ORDER BY due', deck_ids)
            cards = [row[0] for row in rows]
        elif kind == 'tag':
            cards = self.collection.find_cards(f'tag:"{amount}" deck:"{self._deck_name(deck_id)}"')
        elif kind == 'search':
            cards = self.collection.find_cards(str(amount))
        else:
            raise ValueError(f'unknown custom study: {kind}')
        return Session(self, deck_id, deck_ids, cards=cards, cram=True)

    def _deck_name(self, deck_id):
        deck = self.collection.deck(deck_id)
        return deck.name if deck else ''

    # -- answering ------------------------------------------------------------------------

    def preview(self, card, now=None):
        """The interval each rating would give, as short text: {1: '<1m', 2: '10m', 3: '1d',
        4: '4d'}."""
        now = now or time.time()
        outcomes = {rating: self._compute(card, rating, now) for rating in RATINGS}
        return {rating: format_interval(self._wait(outcome, now), outcome.state == 'review')
                for rating, outcome in outcomes.items()}

    def _wait(self, after, now):
        """Seconds until a computed card comes back."""
        if after.state == 'review':
            return after.interval * days.SECONDS_PER_DAY
        return max(0.0, after.due - now)

    def answer(self, card, rating, now=None, duration_ms=0, cram=False):
        """Apply a rating to a card and record it; the card as it is now. With `cram` the
        card is left as it was and the review logged as a cram."""
        now = now or time.time()
        config = self.collection.config_for_deck(card.deck_id)
        if cram:
            after = card.copy()
            self.collection.record_answer(card, after, rating, now, duration_ms, kind='cram')
            return after
        after = self._compute(card, rating, now)
        self.collection.record_answer(
            card, after, rating, now, duration_ms, kind='review',
            bury_siblings=config.bury_siblings and after.state != 'new',
            leech_threshold=config.leech_threshold, leech_action=config.leech_action)
        return after

    def _compute(self, card, rating, now):
        """The card after `rating`, not stored."""
        config = self.collection.config_for_deck(card.deck_id)
        params = config.parameters()
        after = card.copy()
        elapsed = (now - card.last_review) / days.SECONDS_PER_DAY if card.last_review else 0.0
        if card.state == 'new' or not card.stability:
            memory = fsrs.initial_memory(params, rating)
        else:
            memory = fsrs.next_memory(params, fsrs.Memory(card.stability, card.difficulty),
                                      rating, max(0.0, elapsed))
        after.stability, after.difficulty = memory.stability, memory.difficulty
        after.last_review = int(now)
        after.buried = 0
        if card.state in ('new', 'learning'):
            steps = list(config.learning_steps)
            left = len(steps) if card.state == 'new' else min(card.left, len(steps))
            self._step(after, rating, steps, left, 'learning', now, config, params)
        elif card.state == 'relearning':
            steps = list(config.relearning_steps)
            left = min(card.left, len(steps))
            self._step(after, rating, steps, left, 'relearning', now, config, params)
        else:  # review
            if rating == AGAIN:
                after.lapses = card.lapses + 1
                steps = list(config.relearning_steps)
                if steps:
                    after.state = 'relearning'
                    after.left = len(steps)
                    after.due = int(now + steps[0] * MINUTE)
                    after.interval = self._interval(after, rating, config, params, card)
                else:
                    self._graduate(after, rating, config, params, card)
            else:
                self._graduate(after, rating, config, params, card)
        return after

    def _step(self, after, rating, steps, left, state, now, config, params):
        """A learning or relearning card through its steps (Anki's rules)."""
        if not steps:
            self._graduate(after, rating, config, params, after)
            return
        index = max(0, len(steps) - max(left, 1))
        if rating == AGAIN:
            after.state = state
            after.left = len(steps)
            after.due = int(now + steps[0] * MINUTE)
        elif rating == HARD:
            after.state = state
            after.left = max(left, 1)
            if len(steps) == 1:
                delay = steps[0] * 1.5
            elif index == 0:
                delay = (steps[0] + steps[1]) / 2
            else:
                delay = steps[index]
            after.due = int(now + delay * MINUTE)
        elif rating == GOOD:
            remaining = max(left, 1) - 1
            if remaining <= 0:
                self._graduate(after, rating, config, params, after)
                return
            after.state = state
            after.left = remaining
            after.due = int(now + steps[len(steps) - remaining] * MINUTE)
        else:
            self._graduate(after, rating, config, params, after)

    def _graduate(self, after, rating, config, params, before):
        """Make a card a review card due in the interval FSRS gives, fuzzed."""
        after.state = 'review'
        after.left = 0
        after.interval = float(self._interval(after, rating, config, params, before))
        after.due = self.collection.today(after.last_review) + int(after.interval)

    def _interval(self, after, rating, config, params, before):
        """The next interval in days: FSRS's for the stability, kept in order (Hard shorter
        than Good, Good than Easy, each by a day at least), fuzzed the same way for every
        rating (so the order holds), never shorter than a remembered review card's last one."""
        interval = self._raw_interval(after.stability, config, params)
        if before.state == 'review' and rating != AGAIN:
            if before.interval and rating != HARD:
                interval = max(interval, int(before.interval) + 1)
            elif before.interval:
                interval = max(interval, max(1, int(before.interval)))
            # Each rating's interval is longer than the one below it.
            memory = fsrs.Memory(before.stability, before.difficulty)
            elapsed = max(0.0, (after.last_review - before.last_review) / days.SECONDS_PER_DAY) \
                if before.last_review else 0.0
            for lower in range(HARD, rating):
                lower_memory = fsrs.next_memory(params, memory, lower, elapsed)
                lower_interval = self._raw_interval(lower_memory.stability, config, params)
                if lower == HARD and before.interval:
                    lower_interval = max(lower_interval, max(1, int(before.interval)))
                interval = max(interval, lower_interval + 1)
        seed = f'{after.id}:{after.reps + 1}'
        minimum = int(before.interval) + 1 if (before.state == 'review' and rating == GOOD
                                              and before.interval) else 1
        return fsrs.fuzzed_interval(interval, seed, config.maximum_interval, minimum=minimum)

    def _raw_interval(self, stability, config, params):
        return fsrs.next_interval(params, stability, config.desired_retention,
                                  config.maximum_interval)

    def retrievability(self, card, now=None):
        """How likely the card is remembered now (None for a card never reviewed)."""
        if not card.stability or not card.last_review:
            return None
        now = now or time.time()
        config = self.collection.config_for_deck(card.deck_id)
        elapsed = max(0.0, (now - card.last_review) / days.SECONDS_PER_DAY)
        return fsrs.retrievability(config.parameters(), card.stability, elapsed)


class Session:
    """One sitting: the cards to show, in order. Without `cards` it is the day's queue of
    the decks (learning cards due, then reviews and new cards mixed as the deck asks,
    then learning cards due soon); with `cards` it is that list, each shown until it is
    answered with something other than Again (a cram asks again at the end)."""

    def __init__(self, scheduler, deck_id, deck_ids, cards=None, cram=False):
        self.scheduler = scheduler
        self.collection = scheduler.collection
        self.deck_id = deck_id
        self.deck_ids = list(deck_ids)
        self.cram = cram
        self.custom = cards is not None
        self._queue = list(cards or [])
        self._shown = None  # the card id shown last, not repeated at once when avoidable
        self._mix_counter = 0.0
        self.answered_count = 0
        self.started = time.time()

    def counts(self, now=None):
        """(new, learning, due) still to do."""
        if self.custom:
            return 0, 0, len(self._queue)
        return self.scheduler._counts(self.deck_ids, self.deck_id, now)

    def next_card(self, now=None):
        now = now or time.time()
        if self.custom:
            return self._next_custom()
        card = self._next_due(now)
        self._shown = card.id if card else None
        return card

    def _next_custom(self):
        if not self._queue:
            return None
        card = self.collection.card(self._queue[0])
        if card is None:
            self._queue.pop(0)
            return self._next_custom()
        return card

    def answered(self, card, rating=None):
        """Tell the session a card was answered (a custom session drops it from its queue,
        unless it was Again, which puts it last)."""
        self.answered_count += 1
        if self.custom and self._queue and self._queue[0] == card.id:
            self._queue.pop(0)
            if rating == AGAIN:
                self._queue.append(card.id)

    def put_back(self, card):
        """An undone answer: the card returns to the front of a custom queue."""
        self.answered_count = max(0, self.answered_count - 1)
        if self.custom:
            if card.id in self._queue:
                self._queue.remove(card.id)
            self._queue.insert(0, card.id)

    def _next_due(self, now):
        marks = ','.join('?' * len(self.deck_ids))
        db = self.collection.db
        today = self.collection.today(now)
        row = db.execute(
            f'SELECT id FROM cards WHERE state IN (?, ?) AND due <= ? AND suspended = 0 '
            f'AND buried = 0 AND deck_id IN ({marks}) ORDER BY due LIMIT 1',
            ['learning', 'relearning', int(now), *self.deck_ids]).fetchone()
        if row:
            return self.collection.card(row['id'])
        limits = self.scheduler._limits(self.deck_ids, self.deck_id, now)
        review = self._pick_review(limits, today)
        new = self._pick_new(limits)
        if review is None and new is None:
            ahead = int(now + self.scheduler.learn_ahead_minutes * MINUTE)
            row = db.execute(
                f'SELECT id FROM cards WHERE state IN (?, ?) AND due <= ? AND suspended = 0 '
                f'AND buried = 0 AND deck_id IN ({marks}) ORDER BY due LIMIT 1',
                ['learning', 'relearning', ahead, *self.deck_ids]).fetchone()
            return self.collection.card(row['id']) if row else None
        if review is None or new is None:
            return review or new
        config = self.collection.config_for_deck(self.deck_id)
        if config.new_mix == 'before':
            return new
        if config.new_mix == 'after':
            return review
        new_left, _learning, due_left = self.counts(now)
        self._mix_counter += new_left / max(1, new_left + due_left)
        if self._mix_counter >= 1:
            self._mix_counter -= 1
            return new
        return review

    def _pick_review(self, limits, today):
        if limits['review_total'] <= 0:
            return None
        decks = [deck_id for deck_id in self.deck_ids if limits['review'][deck_id] > 0]
        if not decks:
            return None
        marks = ','.join('?' * len(decks))
        rows = self.collection.db.execute(
            f'SELECT id FROM cards WHERE state = ? AND due <= ? AND suspended = 0 '
            f'AND buried = 0 AND deck_id IN ({marks}) ORDER BY due LIMIT 50',
            ['review', today, *decks]).fetchall()
        if not rows:
            return None
        ids = [row['id'] for row in rows]
        if len(ids) > 1 and ids[0] == self._shown:
            ids = ids[1:] + ids[:1]
        choice = random.Random(f'{today}:{len(ids)}:{self.answered_count}').choice(ids[:10])
        return self.collection.card(choice)

    def _pick_new(self, limits):
        if limits['new_total'] <= 0:
            return None
        decks = [deck_id for deck_id in self.deck_ids if limits['new'][deck_id] > 0]
        if not decks:
            return None
        config = self.collection.config_for_deck(self.deck_id)
        order = 'RANDOM()' if config.new_order == 'random' else 'due, id'
        marks = ','.join('?' * len(decks))
        row = self.collection.db.execute(
            f'SELECT id FROM cards WHERE state = ? AND suspended = 0 AND buried = 0 '
            f'AND deck_id IN ({marks}) ORDER BY {order} LIMIT 1', ['new', *decks]).fetchone()
        return self.collection.card(row['id']) if row else None


def format_interval(seconds, is_days=False):
    """An interval as the answer buttons show it: '<1m', '10m', '1d', '3.5mo', '1.2y'."""
    if seconds < MINUTE:
        return _('<1m')
    if seconds < 3600:
        return _('{n}m').format(n=int(round(seconds / MINUTE)))
    if seconds < days.SECONDS_PER_DAY and not is_days:
        hours = seconds / 3600
        return _('{n}h').format(n=_short(hours))
    day_count = seconds / days.SECONDS_PER_DAY
    if day_count < 30:
        return _('{n}d').format(n=_short(day_count))
    if day_count < 365:
        return _('{n}mo').format(n=_short(day_count / 30))
    return _('{n}y').format(n=_short(day_count / 365))


def describe_interval(day_count):
    """An interval in words, for the browser and card info: 'in 3 days', '2 months'."""
    if day_count < 1:
        return _('today')
    if day_count < 30:
        count = int(round(day_count))
        return ngettext('{n} day', '{n} days', count).format(n=count)
    if day_count < 365:
        return _('{n} months').format(n=_short(day_count / 30))
    return _('{n} years').format(n=_short(day_count / 365))


def _short(number):
    """A number with one decimal unless it is whole or large: 1, 1.5, 12."""
    if number >= 10 or math.isclose(number, round(number), abs_tol=0.05):
        return str(int(round(number)))
    return f'{number:.1f}'
