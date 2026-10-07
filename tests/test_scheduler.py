# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""scheduler.py on a temporary collection with an invented clock: counts, sessions, the
learning steps, review intervals, custom study and the interval texts."""

import unittest

from tests import ROOT  # noqa: F401
from tests.support import Clock, add_basic, temporary_collection
from retain import fsrs
from retain.deck_config import DEFAULT_ID, LEECH_TAG, DeckConfig
from retain.fsrs import AGAIN, EASY, GOOD, HARD
from retain.scheduler import LIMITS_KEY, Scheduler, Session, describe_interval, format_interval

DAY = 86400
MINUTE = 60


class SchedulerCase(unittest.TestCase):
    """A collection with a Spanish deck, a scheduler and a clock at noon."""

    def setUp(self):
        self.collection = self.enterContext(temporary_collection())
        self.clock = Clock()
        self.scheduler = Scheduler(self.collection)
        self.deck = self.collection.add_deck('Spanish')

    @property
    def now(self):
        return self.clock.now

    def today(self):
        return self.collection.today(self.clock.now)

    def add_cards(self, count, deck=None, tags=(), prefix='word'):
        """`count` new Basic cards in `deck` (the Spanish one by default)."""
        cards = []
        for index in range(count):
            note = add_basic(self.collection, deck or self.deck, f'{prefix} {index}',
                             f'meaning {index}', tags)
            cards.append(self.collection.cards_of_note(note.id)[0])
        return cards

    def configure(self, **values):
        """Change the default preset's settings."""
        config = self.collection.deck_config(DEFAULT_ID)
        for key, value in values.items():
            setattr(config, key, value)
        self.collection.update_deck_config(config)
        return config

    def make_review(self, card, interval=10, stability=10.0, difficulty=5.0, lapses=0,
                    due_in=0, card_id=None):
        """Turn `card` into a review card last seen `interval` days ago, due in `due_in` days;
        `card_id` pins the id (the fuzz seed), for a deterministic interval."""
        if card_id is not None:
            self.collection.db.execute('UPDATE cards SET id = ? WHERE id = ?',
                                       (card_id, card.id))
            card.id = card_id
        self.collection.db.execute(
            'UPDATE cards SET state = ?, due = ?, interval = ?, stability = ?, difficulty = ?, '
            'last_review = ?, reps = 3, lapses = ?, left = 0 WHERE id = ?',
            ('review', self.today() + due_in, float(interval), stability, difficulty,
             int(self.now - interval * DAY), lapses, card.id))
        return self.collection.card(card.id)

    def answer(self, card, rating, **kwargs):
        return self.scheduler.answer(card, rating, now=self.now, **kwargs)

    def walk(self, session, rating=EASY):
        """Answer every card the session shows with `rating`; the cards shown."""
        shown = []
        while True:
            card = session.next_card(now=self.now)
            if card is None or len(shown) > 50:
                return shown
            shown.append(card)
            after = self.answer(card, rating, cram=session.cram)
            session.answered(after, rating)


class CountsTest(SchedulerCase):

    def test_counts_for_a_fresh_deck_are_limited_by_new_per_day(self):
        self.add_cards(25)
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (20, 0, 0))
        self.configure(new_per_day=5)
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (5, 0, 0))
        self.assertEqual(self.scheduler.counts(1, now=self.now), (0, 0, 0))
        self.assertEqual(self.scheduler.counts(424242, now=self.now), (0, 0, 0))

    def test_counts_tell_learning_and_due_apart(self):
        learning, due, later, new = self.add_cards(4)
        self.answer(learning, GOOD)
        self.make_review(due)
        self.make_review(later, due_in=3)
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (1, 1, 1))

    def test_tree_fills_the_counts_with_parents_covering_children(self):
        languages = self.collection.add_deck('Languages')
        spanish = self.collection.add_deck('Languages::Spanish')
        french = self.collection.add_deck('Languages::French')
        self.add_cards(3, deck=spanish)
        self.add_cards(2, deck=french)
        self.make_review(self.add_cards(1, deck=languages)[0])
        tree = self.scheduler.tree(now=self.now)
        by_name = {node.deck.name: node for root in tree for node in root.walk()}
        self.assertEqual((by_name['Languages'].new, by_name['Languages'].due), (5, 1))
        self.assertEqual(by_name['Languages'].total, 6)
        self.assertEqual(by_name['Languages::Spanish'].new, 3)
        self.assertEqual(by_name['Languages::French'].new, 2)
        self.assertEqual(by_name['Default'].total, 0)
        self.assertEqual([node.deck.basename for node in by_name['Languages'].children],
                         ['French', 'Spanish'])


class LearningStepsTest(SchedulerCase):

    def setUp(self):
        super().setUp()
        self.card = self.add_cards(1)[0]

    def test_good_on_a_new_card_is_due_in_ten_minutes(self):
        after = self.answer(self.card, GOOD)
        self.assertEqual((after.state, after.left), ('learning', 1))
        self.assertEqual(after.due, int(self.now + 10 * MINUTE))
        self.assertEqual(after.reps, 1)
        self.assertEqual(after.last_review, int(self.now))
        self.assertGreater(after.stability, 0)
        stored = self.collection.card(self.card.id)
        self.assertEqual(stored, after)
        review = self.collection.reviews_of(self.card.id)[0]
        self.assertEqual((review['rating'], review['state'], review['kind']),
                         (GOOD, 'new', 'review'))

    def test_hard_on_a_new_card_waits_the_average_of_the_first_steps(self):
        after = self.answer(self.card, HARD)
        self.assertEqual(after.state, 'learning')
        self.assertEqual(after.due - self.now, 5.5 * MINUTE)
        self.assertEqual(after.left, 2)

    def test_again_on_a_new_card_waits_the_first_step(self):
        after = self.answer(self.card, AGAIN)
        self.assertEqual(after.state, 'learning')
        self.assertEqual(after.due - self.now, 1 * MINUTE)
        self.assertEqual(after.left, 2)

    def test_easy_on_a_new_card_graduates_at_once(self):
        after = self.answer(self.card, EASY)
        self.assertEqual(after.state, 'review')
        self.assertGreaterEqual(after.interval, 1)
        self.assertEqual(after.due, self.today() + int(after.interval))
        self.assertEqual(after.left, 0)

    def test_good_on_the_last_step_graduates(self):
        first = self.answer(self.card, GOOD)
        self.clock.advance(minutes=10)
        after = self.answer(first, GOOD)
        self.assertEqual(after.state, 'review')
        self.assertGreaterEqual(after.interval, 1)
        self.assertEqual(after.due, self.today() + int(after.interval))
        self.assertEqual(after.reps, 2)
        self.assertEqual(self.collection.card(self.card.id), after)
        reviews = self.collection.reviews_of(self.card.id)
        self.assertEqual([review['state'] for review in reviews], ['new', 'learning'])
        self.assertEqual(reviews[1]['scheduled_days'], after.interval)
        self.assertEqual(reviews[1]['stability'], after.stability)

    def test_again_in_learning_restarts_the_steps(self):
        first = self.answer(self.card, GOOD)
        self.clock.advance(minutes=10)
        after = self.answer(first, AGAIN)
        self.assertEqual((after.state, after.left), ('learning', 2))
        self.assertEqual(after.due, int(self.now + 1 * MINUTE))

    def test_a_single_step_hard_waits_one_and_a_half_steps(self):
        self.configure(learning_steps=[4])
        after = self.answer(self.card, HARD)
        self.assertEqual(after.due - self.now, 6 * MINUTE)
        after = self.answer(self.collection.card(self.card.id), GOOD)
        self.assertEqual(after.state, 'review')

    def test_no_learning_steps_graduate_at_once(self):
        self.configure(learning_steps=[])
        after = self.answer(self.card, GOOD)
        self.assertEqual(after.state, 'review')
        self.assertGreaterEqual(after.interval, 1)


class ReviewTest(SchedulerCase):

    def setUp(self):
        super().setUp()
        self.card = self.add_cards(1)[0]

    def test_again_on_a_review_card_relearns(self):
        card = self.make_review(self.card, stability=10.0)
        after = self.answer(card, AGAIN)
        self.assertEqual((after.state, after.left, after.lapses), ('relearning', 1, 1))
        self.assertEqual(after.due, int(self.now + 10 * MINUTE))
        self.assertLess(after.stability, 10.0)
        self.assertGreaterEqual(after.interval, 1)
        self.assertEqual(self.collection.reviews_of(card.id)[0]['state'], 'review')

    def test_good_on_relearning_graduates(self):
        card = self.make_review(self.card, stability=10.0)
        lapsed = self.answer(card, AGAIN)
        self.clock.advance(minutes=10)
        after = self.answer(lapsed, GOOD)
        self.assertEqual((after.state, after.left, after.lapses), ('review', 0, 1))
        self.assertGreaterEqual(after.interval, 1)
        self.assertEqual(after.due, self.today() + int(after.interval))

    def test_hard_good_and_easy_intervals_are_in_order_and_grow(self):
        intervals = {}
        for rating in (HARD, GOOD, EASY):
            card = self.make_review(self.card, interval=10, stability=10.0, card_id=1000)
            intervals[rating] = self.answer(card, rating).interval
            self.collection.undo()
        self.assertLessEqual(intervals[HARD], intervals[GOOD])
        self.assertLessEqual(intervals[GOOD], intervals[EASY])
        self.assertGreaterEqual(intervals[HARD], 10)
        self.assertGreaterEqual(intervals[GOOD], 11)
        self.assertGreater(intervals[EASY], intervals[HARD])

    def test_fuzz_keeps_hard_below_good(self):
        # The fuzz is seeded the same for every rating, so the order of the raw intervals
        # (Hard, Good, Easy, a day apart at least) survives it.
        intervals = {}
        for rating in (HARD, GOOD, EASY):
            card = self.make_review(self.card, interval=1, stability=30.0, card_id=1000)
            intervals[rating] = self.answer(card, rating).interval
            self.collection.undo()
        self.assertLessEqual(intervals[HARD], intervals[GOOD])
        self.assertLessEqual(intervals[GOOD], intervals[EASY])

    def test_good_never_shortens_a_remembered_cards_interval(self):
        card = self.make_review(self.card, interval=60, stability=3.0, card_id=1000)
        after = self.answer(card, GOOD)
        self.assertGreaterEqual(after.interval, 61)
        card = self.make_review(self.card, interval=60, stability=3.0, card_id=1000)
        after = self.answer(card, HARD)
        self.assertGreaterEqual(after.interval, 60)

    def test_the_maximum_interval_caps_a_review(self):
        self.configure(maximum_interval=5)
        card = self.make_review(self.card, interval=3, stability=100.0, card_id=1000)
        after = self.answer(card, EASY)
        self.assertEqual(after.interval, 5)

    def test_a_leech_is_tagged_and_suspended_as_the_preset_says(self):
        card = self.make_review(self.card, lapses=7)
        self.answer(card, AGAIN)
        self.assertEqual(self.collection.note(card.note_id).tags, [LEECH_TAG])
        self.assertEqual(self.collection.card(card.id).suspended, 0)
        self.collection.undo()
        self.configure(leech_action='suspend')
        self.answer(card, AGAIN)
        self.assertEqual(self.collection.card(card.id).suspended, 1)
        self.assertEqual(self.collection.note(card.note_id).tags, [LEECH_TAG])

    def test_an_answer_is_one_undo_step(self):
        card = self.make_review(self.card)
        self.answer(card, GOOD)
        self.assertEqual(self.collection.undo_label, 'Good')
        self.assertEqual(self.collection.undo(), 'Good')
        self.assertEqual(self.collection.card(card.id), card)
        self.assertEqual(self.collection.reviews_of(card.id), [])


class PreviewTest(SchedulerCase):

    def test_preview_gives_four_labels_that_match_answer(self):
        card = self.add_cards(1)[0]
        labels = self.scheduler.preview(card, now=self.now)
        self.assertEqual(sorted(labels), [AGAIN, HARD, GOOD, EASY])
        self.assertEqual((labels[AGAIN], labels[HARD], labels[GOOD]), ('1m', '6m', '10m'))
        self.assertTrue(labels[EASY].endswith('d'))
        after = self.answer(card, GOOD)
        self.assertEqual(format_interval(after.due - self.now), labels[GOOD])
        self.collection.undo()
        after = self.answer(card, EASY)
        self.assertEqual(format_interval(after.interval * DAY, True), labels[EASY])

    def test_preview_of_a_review_card(self):
        card = self.make_review(self.add_cards(1)[0], interval=10, stability=10.0, card_id=1000)
        labels = self.scheduler.preview(card, now=self.now)
        self.assertEqual(labels[AGAIN], '10m')
        for rating in (HARD, GOOD, EASY):
            after = self.answer(card, rating)
            self.assertEqual(format_interval(after.interval * DAY, True), labels[rating])
            self.collection.undo()

    def test_retrievability_is_none_for_new_and_ninety_percent_at_the_interval(self):
        card = self.add_cards(1)[0]
        self.assertIsNone(self.scheduler.retrievability(card, now=self.now))
        card = self.make_review(card, interval=10, stability=10.0)
        self.assertAlmostEqual(self.scheduler.retrievability(card, now=self.now), 0.9)
        self.assertAlmostEqual(self.scheduler.retrievability(card, now=card.last_review), 1.0)
        self.clock.advance(days=30)
        self.assertLess(self.scheduler.retrievability(card, now=self.now), 0.8)
        self.assertEqual(
            self.scheduler.retrievability(card, now=self.now),
            fsrs.retrievability(fsrs.DEFAULT_PARAMETERS, 10.0, 40.0))


class SessionTest(SchedulerCase):

    def test_a_learning_card_due_now_comes_first(self):
        first, _second = self.add_cards(2)
        learning = self.answer(first, GOOD)
        self.clock.advance(minutes=10)
        session = self.scheduler.session(self.deck.id)
        self.assertEqual(session.next_card(now=self.now).id, learning.id)
        self.assertEqual(session.counts(now=self.now), (1, 1, 0))

    def test_learn_ahead_shows_a_card_due_soon_but_not_in_an_hour(self):
        card = self.add_cards(1)[0]
        self.answer(card, GOOD)
        session = self.scheduler.session(self.deck.id)
        self.assertEqual(session.next_card(now=self.now).id, card.id)
        self.collection.db.execute('UPDATE cards SET due = ? WHERE id = ?',
                                   (int(self.now + 60 * MINUTE), card.id))
        self.assertIsNone(session.next_card(now=self.now))
        self.clock.advance(minutes=45)
        self.assertEqual(session.next_card(now=self.now).id, card.id)
        self.assertIsNone(Scheduler(self.collection, learn_ahead_minutes=5)
                          .session(self.deck.id).next_card(now=self.now))

    def test_new_cards_stop_at_the_daily_limit_and_extend_limits_adds_more(self):
        self.configure(new_per_day=2)
        cards = self.add_cards(3)
        session = self.scheduler.session(self.deck.id)
        shown = self.walk(session)
        self.assertEqual([card.id for card in shown], [cards[0].id, cards[1].id])
        self.assertEqual(session.counts(now=self.now), (0, 0, 0))
        self.assertEqual(session.answered_count, 2)
        self.scheduler.extend_limits(self.deck.id, new=1, now=self.now)
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (1, 0, 0))
        more = self.walk(session)
        self.assertEqual([card.id for card in more], [cards[2].id])
        self.assertIsNone(session.next_card(now=self.now))

    def test_custom_session_new_studies_beyond_the_limit(self):
        self.configure(new_per_day=1)
        cards = self.add_cards(3)
        self.walk(self.scheduler.session(self.deck.id))
        self.assertIsNone(self.scheduler.session(self.deck.id).next_card(now=self.now))
        session = self.scheduler.custom_session(self.deck.id, 'new', 2, now=self.now)
        self.assertFalse(session.custom)
        self.assertEqual([card.id for card in self.walk(session)],
                         [cards[1].id, cards[2].id])

    def test_extend_limits_is_undoable(self):
        self.configure(new_per_day=1)
        self.add_cards(3)
        kinds = []
        self.collection.connect('changed', lambda _collection, kind: kinds.append(kind))
        self.scheduler.extend_limits(self.deck.id, new=2, now=self.now)
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (3, 0, 0))
        self.assertEqual(kinds, ['config'])
        self.assertEqual(self.collection.undo(), 'Study More')
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (1, 0, 0))
        self.assertIsNone(self.collection.get(LIMITS_KEY))

    def test_the_new_limit_is_fresh_the_next_day(self):
        # The revlog rows are stamped with the session's `now`, so the day turns with it.
        self.configure(new_per_day=2)
        self.add_cards(3)
        self.walk(self.scheduler.session(self.deck.id))
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (0, 0, 0))
        self.clock.advance(days=1)
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (1, 0, 0))

    def test_reviews_stop_at_the_daily_limit(self):
        self.configure(reviews_per_day=2)
        cards = [self.make_review(card) for card in self.add_cards(3)]
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (0, 0, 2))
        session = self.scheduler.session(self.deck.id)
        shown = self.walk(session, GOOD)
        self.assertEqual(len(shown), 2)
        self.assertEqual(session.counts(now=self.now), (0, 0, 0))
        self.assertIsNone(session.next_card(now=self.now))
        left = {card.id for card in cards} - {card.id for card in shown}
        more = self.scheduler.custom_session(self.deck.id, 'review', 1, now=self.now)
        self.assertEqual({card.id for card in self.walk(more, GOOD)}, left)

    def test_a_parent_session_includes_subdeck_cards_with_their_own_limits(self):
        languages = self.collection.add_deck('Languages')
        spanish = self.collection.add_deck('Languages::Spanish')
        slow = self.collection.add_deck_config(DeckConfig(name='Slow', new_per_day=1))
        self.collection.set_deck_config(spanish.id, slow.id)
        in_parent = self.add_cards(2, deck=languages, prefix='parent')
        in_child = self.add_cards(3, deck=spanish, prefix='child')
        self.assertEqual(self.scheduler.counts(languages.id, now=self.now), (3, 0, 0))
        self.assertEqual(self.scheduler.counts(spanish.id, now=self.now), (1, 0, 0))
        shown = self.walk(self.scheduler.session(languages.id))
        self.assertEqual(len(shown), 3)
        child_ids = {card.id for card in in_child}
        self.assertEqual(len([card for card in shown if card.id in child_ids]), 1)
        self.assertEqual({card.id for card in shown if card.id not in child_ids},
                         {card.id for card in in_parent})

    def test_new_mix_before_and_after(self):
        new, review = self.add_cards(2)
        self.make_review(review)
        self.configure(new_mix='before')
        self.assertEqual(self.scheduler.session(self.deck.id).next_card(now=self.now).id, new.id)
        self.configure(new_mix='after')
        self.assertEqual(self.scheduler.session(self.deck.id).next_card(now=self.now).id,
                         review.id)
        self.configure(new_mix='mix')
        self.assertIn(self.scheduler.session(self.deck.id).next_card(now=self.now).id,
                      (new.id, review.id))

    def test_siblings_are_buried_for_the_day(self):
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        note = self.collection.add_note(reversed_type.id, self.deck.id, ['agua', 'water'])
        first, second = self.collection.cards_of_note(note.id)
        session = self.scheduler.session(self.deck.id)
        self.assertEqual(session.next_card(now=self.now).id, first.id)
        after = self.answer(first, GOOD)
        session.answered(after, GOOD)
        self.assertEqual(self.collection.card(second.id).buried, self.today())
        self.assertEqual(session.next_card(now=self.now).id, first.id)
        self.assertEqual(session.counts(now=self.now), (0, 1, 0))

    def test_siblings_stay_when_the_preset_says_so(self):
        self.configure(bury_siblings=False)
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        note = self.collection.add_note(reversed_type.id, self.deck.id, ['agua', 'water'])
        first, second = self.collection.cards_of_note(note.id)
        session = self.scheduler.session(self.deck.id)
        session.answered(self.answer(session.next_card(now=self.now), GOOD), GOOD)
        self.assertEqual(self.collection.card(second.id).buried, 0)
        self.assertEqual(session.next_card(now=self.now).id, second.id)

    def test_suspended_and_buried_cards_never_come_up(self):
        suspended, buried, due = self.add_cards(3)
        self.collection.suspend([suspended.id])
        self.collection.bury([buried.id])
        self.make_review(due)
        self.collection.suspend([due.id])
        self.assertEqual(self.scheduler.counts(self.deck.id, now=self.now), (0, 0, 0))
        self.assertIsNone(self.scheduler.session(self.deck.id).next_card(now=self.now))
        self.collection.unsuspend([due.id])
        self.assertEqual(self.scheduler.session(self.deck.id).next_card(now=self.now).id, due.id)
        self.assertEqual(self.scheduler.custom_session(self.deck.id, 'all', now=self.now)
                         .counts(), (0, 0, 2))

    def test_the_review_queue_does_not_repeat_the_card_just_shown(self):
        first, second = (self.make_review(card) for card in self.add_cards(2))
        session = self.scheduler.session(self.deck.id)
        shown = session.next_card(now=self.now)
        self.assertIsNotNone(shown)
        again = self.answer(shown, AGAIN)
        session.answered(again, AGAIN)
        self.assertNotEqual(session.next_card(now=self.now).id, shown.id)


class CustomSessionTest(SchedulerCase):

    def test_all_is_a_cram_that_changes_nothing(self):
        first, second = self.add_cards(2)
        session = self.scheduler.custom_session(self.deck.id, 'all', now=self.now)
        self.assertTrue(session.custom)
        self.assertTrue(session.cram)
        self.assertEqual(session.counts(), (0, 0, 2))
        card = session.next_card(now=self.now)
        self.assertEqual(card.id, first.id)
        after = self.answer(card, AGAIN, cram=True)
        session.answered(after, AGAIN)
        stored = self.collection.card(first.id)
        self.assertEqual((stored.state, stored.reps, stored.due), ('new', 0, first.due))
        review = self.collection.reviews_of(first.id)[0]
        self.assertEqual((review['kind'], review['rating']), ('cram', AGAIN))
        self.assertEqual(session.next_card(now=self.now).id, second.id)
        session.answered(second, GOOD)
        self.assertEqual(session.next_card(now=self.now).id, first.id)
        session.answered(first, GOOD)
        self.assertIsNone(session.next_card(now=self.now))
        self.assertEqual(session.answered_count, 3)
        self.assertEqual(self.collection.undo_label, 'Again')

    def test_forgotten_finds_cards_lapsed_in_the_last_days(self):
        lapsed, remembered, old = (self.make_review(card) for card in self.add_cards(3))
        self.answer(lapsed, AGAIN)
        self.answer(remembered, GOOD)
        self.collection.db.execute(
            'INSERT INTO revlog (id, card_id, rating, state, kind) VALUES (?, ?, ?, ?, ?)',
            (int((self.now - 10 * DAY) * 1000), old.id, AGAIN, 'review', 'review'))
        session = self.scheduler.custom_session(self.deck.id, 'forgotten', 7, now=self.now)
        self.assertTrue(session.custom)
        self.assertFalse(session.cram)
        self.assertEqual(session.counts(), (0, 0, 1))
        self.assertEqual(session.next_card(now=self.now).id, lapsed.id)
        month = self.scheduler.custom_session(self.deck.id, 'forgotten', 30, now=self.now)
        self.assertEqual(month.counts(), (0, 0, 2))

    def test_ahead_finds_cards_due_within_days(self):
        soon, later, learning = self.add_cards(3)
        self.make_review(soon, due_in=3)
        self.make_review(later, due_in=8)
        self.answer(learning, GOOD)
        session = self.scheduler.custom_session(self.deck.id, 'ahead', 5, now=self.now)
        self.assertEqual(session.counts(), (0, 0, 1))
        self.assertEqual(session.next_card(now=self.now).id, soon.id)
        wide = self.scheduler.custom_session(self.deck.id, 'ahead', 10, now=self.now)
        self.assertEqual(wide.counts(), (0, 0, 2))
        self.assertEqual(wide.next_card(now=self.now).id, soon.id)
        wide.answered(soon)
        self.assertEqual(wide.next_card(now=self.now).id, later.id)

    def test_tag_crams_the_cards_of_a_tag_in_the_deck(self):
        verbs = self.add_cards(2, tags=['verbs'])
        self.add_cards(1, tags=['nouns'])
        elsewhere = self.collection.add_deck('Science')
        self.add_cards(1, deck=elsewhere, tags=['verbs'], prefix='science')
        session = self.scheduler.custom_session(self.deck.id, 'tag', 'verbs', now=self.now)
        self.assertTrue(session.cram)
        self.assertEqual(session.counts(), (0, 0, 2))
        self.assertEqual({card.id for card in self.walk(session, GOOD)},
                         {card.id for card in verbs})

    def test_search_crams_what_a_search_finds(self):
        cards = self.add_cards(3)
        session = self.scheduler.custom_session(self.deck.id, 'search', 'word 1', now=self.now)
        self.assertEqual(session.counts(), (0, 0, 1))
        self.assertEqual(session.next_card(now=self.now).id, cards[1].id)
        with self.assertRaises(ValueError):
            self.scheduler.custom_session(self.deck.id, 'unknown', now=self.now)

    def test_answered_and_put_back_on_a_custom_queue(self):
        first, second, third = self.add_cards(3)
        session = Session(self.scheduler, self.deck.id, [self.deck.id],
                          cards=[first.id, second.id, third.id])
        self.assertEqual(session.next_card(now=self.now).id, first.id)
        session.answered(first, GOOD)
        self.assertEqual(session.answered_count, 1)
        self.assertEqual(session.next_card(now=self.now).id, second.id)
        session.put_back(first)
        self.assertEqual(session.answered_count, 0)
        self.assertEqual(session.next_card(now=self.now).id, first.id)
        session.put_back(first)
        self.assertEqual(session.counts(), (0, 0, 3))
        session.answered(third, GOOD)  # not the card at the front: the queue is unchanged
        self.assertEqual(session.counts(), (0, 0, 3))
        self.collection.remove_cards([second.id])
        session.answered(first, GOOD)
        self.assertEqual(session.next_card(now=self.now).id, third.id)
        session.put_back(self.collection.card(third.id))
        self.assertEqual(session.counts(), (0, 0, 1))


class IntervalTextTest(unittest.TestCase):

    def test_format_interval(self):
        self.assertEqual(format_interval(30), '<1m')
        self.assertEqual(format_interval(600), '10m')
        self.assertEqual(format_interval(5400), '1.5h')
        self.assertEqual(format_interval(DAY), '1d')
        self.assertEqual(format_interval(3 * DAY), '3d')
        self.assertEqual(format_interval(75 * DAY), '2.5mo')
        self.assertEqual(format_interval(438 * DAY), '1.2y')
        self.assertEqual(format_interval(3650 * DAY), '10y')
        self.assertEqual(format_interval(2 * DAY, True), '2d')

    def test_describe_interval(self):
        self.assertEqual(describe_interval(0.5), 'today')
        self.assertEqual(describe_interval(1), '1 day')
        self.assertEqual(describe_interval(3), '3 days')
        self.assertEqual(describe_interval(60), '2 months')
        self.assertEqual(describe_interval(45), '1.5 months')
        self.assertEqual(describe_interval(730), '2 years')


if __name__ == '__main__':
    unittest.main()
