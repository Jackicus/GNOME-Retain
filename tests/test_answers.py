# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""answers.py: which field answers a card, typed-answer grading, and distractors."""

from tests import ROOT  # noqa: F401  (registers src/ as retain)

import random
import unittest

from tests.support import add_basic, temporary_collection

from retain import answers
from retain.collection import Card
from retain.fsrs import AGAIN, GOOD, HARD
from retain.notetypes import NoteType


def vocabulary_type(collection):
    notetype = NoteType(
        name='Vocabulary', kind='standard',
        fields=[{'name': 'Expression'}, {'name': 'Meaning'}, {'name': 'Part of speech'}],
        templates=[{'name': 'Recognition', 'qfmt': '{{kanji:Expression}}',
                    'afmt': '{{furigana:Expression}}<hr id=answer>{{Meaning}} '
                            '{{Part of speech}}'}])
    return collection.add_notetype(notetype)


class ExpectedAnswerTests(unittest.TestCase):

    def test_the_field_the_answer_shows_and_the_question_does_not(self):
        with temporary_collection() as collection:
            deck = collection.add_deck('Japanese')
            note = add_basic(collection, deck, '猫', 'cat')
            basic = collection.notetype(note.notetype_id)
            self.assertEqual(answers.expected_answer(basic, note, 0), ('Back', 'cat'))
            reversed_type = collection.notetype_by_name('Basic (and reversed card)')
            note2 = collection.add_note(reversed_type.id, deck.id, ['犬', 'dog'])
            self.assertEqual(answers.expected_answer(reversed_type, note2, 1), ('Front', '犬'))
            vocabulary = vocabulary_type(collection)
            note3 = collection.add_note(vocabulary.id, deck.id, ['鳥[とり]', 'bird', 'noun'])
            self.assertEqual(answers.expected_answer(vocabulary, note3, 0), ('Meaning', 'bird'))

    def test_cloze_long_and_empty_answers_are_flipped(self):
        with temporary_collection() as collection:
            deck = collection.add_deck('Japanese')
            cloze = collection.notetype_by_name('Cloze')
            note = collection.add_note(cloze.id, deck.id, ['{{c1::は}}', ''])
            self.assertIsNone(answers.expected_answer(cloze, note, 0))
            long_note = add_basic(collection, deck, 'essay', 'word ' * 30)
            basic = collection.notetype(long_note.notetype_id)
            self.assertIsNone(answers.expected_answer(basic, long_note, 0))
            empty = add_basic(collection, deck, 'nothing', '')
            self.assertIsNone(answers.expected_answer(basic, empty, 0))


class TypedTests(unittest.TestCase):

    def grade(self, expected, typed):
        return answers.grade_typed(expected, typed)

    def test_any_alternative_ignoring_case_punctuation_and_articles(self):
        for typed in ('tall', 'High', ' expensive. ', 'TALL!'):
            self.assertEqual(self.grade('tall; high; expensive', typed).rating, GOOD, typed)
        self.assertEqual(self.grade('to go home; return', 'go home').kind, 'exact')
        self.assertEqual(self.grade('cat', 'a cat').kind, 'exact')
        self.assertEqual(self.grade('(green) tea', 'tea').kind, 'exact')
        self.assertEqual(self.grade("~ o'clock", 'oclock').kind, 'exact')

    def test_a_typo_is_a_near_miss(self):
        verdict = self.grade('expensive', 'expensiev')
        self.assertEqual(tuple(verdict), (HARD, 'close', 'expensive'))
        self.assertEqual(self.grade('cat', 'cot').rating, AGAIN)  # short words get no slack
        self.assertEqual(self.grade('café', 'cafe').rating, HARD)  # accents only

    def test_wrong_and_empty(self):
        self.assertEqual(self.grade('tall; high', 'short'), (AGAIN, 'wrong', 'tall; high'))
        self.assertEqual(self.grade('cat', '').rating, AGAIN)

    def test_japanese_kanji_or_kana_and_no_typo_slack(self):
        self.assertEqual(self.grade('猫[ねこ]', 'ねこ').rating, GOOD)
        self.assertEqual(self.grade('猫[ねこ]', '猫').rating, GOOD)
        self.assertEqual(self.grade('<ruby>猫<rt>ねこ</rt></ruby>', 'ネコ').rating, GOOD)
        self.assertEqual(self.grade('食[た]べる', 'たべる').rating, GOOD)
        self.assertEqual(self.grade('ねこ', 'ねご').rating, AGAIN)  # one kana is another word
        self.assertEqual(self.grade('ｶﾀｶﾅ', 'かたかな').rating, GOOD)  # half-width folds

    def test_damerau_levenshtein(self):
        self.assertEqual(answers.damerau_levenshtein('expensive', 'expensiev'), 1)
        self.assertEqual(answers.damerau_levenshtein('kitten', 'sitting'), 3)
        self.assertEqual(answers.damerau_levenshtein('', 'abc'), 3)


class ChoiceTests(unittest.TestCase):

    def test_a_right_pick_is_hard_once_the_card_is_a_review_card(self):
        card = Card(state='review')
        self.assertEqual(answers.choice_rating(True, card), HARD)
        card.state = 'learning'
        self.assertEqual(answers.choice_rating(True, card), GOOD)
        card.state = 'new'
        self.assertEqual(answers.choice_rating(False, card), AGAIN)

    def test_distractors_are_alike_and_never_also_right(self):
        with temporary_collection() as collection:
            deck = collection.add_deck('Japanese')
            vocabulary = vocabulary_type(collection)
            rows = [('高[たか]い', 'tall; high; expensive', 'adjective'),
                    ('上[うえ]', 'above; high', 'noun'),        # shares "high": never offered
                    ('安[やす]い', 'cheap', 'adjective'),
                    ('低[ひく]い', 'low; short', 'adjective'),
                    ('長[なが]い', 'long', 'adjective'),
                    ('食[た]べる', 'to eat', 'verb'),
                    ('飲[の]む', 'to drink', 'verb'),
                    ('犬[いぬ]', 'dog', 'noun'),
                    ('猫[ねこ]', 'cat', 'noun')]
            notes = [collection.add_note(vocabulary.id, deck.id, list(row)) for row in rows]
            found = answers.distractors(collection, notes[0], vocabulary, 'Meaning', [deck.id],
                                        rng=random.Random(1))
            self.assertEqual(len(found), 3)
            self.assertNotIn('above; high', found)
            self.assertEqual(set(found), {'cheap', 'low; short', 'long'})  # the adjectives
            verb = answers.distractors(collection, notes[5], vocabulary, 'Meaning', [deck.id],
                                       rng=random.Random(1))
            self.assertIn('to drink', verb)

    def test_fewer_distractors_in_a_small_deck(self):
        with temporary_collection() as collection:
            deck = collection.add_deck('Tiny')
            first = add_basic(collection, deck, 'one', 'un')
            add_basic(collection, deck, 'two', 'deux')
            basic = collection.notetype(first.notetype_id)
            self.assertEqual(answers.distractors(collection, first, basic, 'Back', [deck.id]),
                             ['deux'])


if __name__ == '__main__':
    unittest.main()
