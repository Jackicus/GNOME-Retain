# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""search.py against an in-memory collection of invented notes, cards and reviews."""

import datetime
import json
import sqlite3
import unittest

from tests import ROOT  # noqa: F401
from retain import days, schema, search

DAY = 86400
NOW = datetime.datetime(2026, 6, 15, 12, 0).timestamp()
TODAY = days.day_number(NOW)
OLD = NOW - 10 * DAY           # ten days ago, noon
LAST_MONTH = NOW - 30 * DAY

NOTETYPES = [
    # id, name, kind, fields, templates
    (1, 'Basic', 'standard', ['Front', 'Back'], ['Card 1']),
    (2, 'Basic (and reversed card)', 'standard', ['Front', 'Back'], ['Card 1', 'Card 2']),
    (3, 'Cloze', 'cloze', ['Text', 'Extra'], ['Cloze']),
]
DECKS = [(1, 'Languages'), (2, 'Languages::Spanish'), (3, 'Science')]
NOTES = [
    # id, notetype, fields, tags, marked, created, modified
    (1, 1, 'perro\x1fdog', ' animal spanish ', 0, OLD, OLD),
    (2, 1, 'gato\x1fcat', ' animal spanish::vocab ', 1, OLD, NOW - DAY),
    (3, 2, 'Hola\x1fhello there', ' ', 0, OLD, OLD),
    (4, 1, 'a_b 100% *star*\x1f', ' misc ', 0, LAST_MONTH, LAST_MONTH),
    (5, 3, 'The {{c1::hotdog}} stand\x1ffast food', ' food ', 0, NOW - 3600, NOW - 3600),
    (6, 1, 'Dog\x1fcanine (a-b)', ' animal ', 0, OLD, OLD),
]
CARDS = [
    # id, note, deck, ord, state, due, interval, stability, difficulty, reps, lapses, left,
    # flag, suspended, buried, created
    (1, 1, 2, 0, 'review', TODAY - 1, 10, 12.5, 6.0, 5, 1, 0, 1, 0, 0, OLD),
    (2, 2, 2, 0, 'review', TODAY + 1, 3, 3.0, 4.0, 2, 0, 0, 0, 0, 0, OLD),
    (3, 3, 1, 0, 'new', 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, OLD),
    (4, 3, 1, 1, 'learning', NOW - 60, 0, 0, 0, 1, 0, 1, 0, 0, 0, OLD),
    (5, 5, 3, 0, 'new', 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, NOW - 3600),
    (6, 6, 3, 0, 'review', TODAY + 5, 20, 40.0, 8.0, 8, 3, 0, 7, 0, TODAY, OLD),
    (7, 4, 3, 0, 'relearning', NOW + 600, 1, 1.0, 5.0, 4, 2, 1, 0, 0, 0, LAST_MONTH),
]


def at(day_offset, seconds):
    """Revlog id: milliseconds, `seconds` into the day `day_offset` days ago."""
    return int((days.day_start(TODAY - day_offset) + seconds) * 1000)


REVLOG = [
    # id, card, rating, state, kind
    (at(0, 3600), 1, 3, 'review', 'review'),
    (at(0, 7200), 4, 1, 'new', 'review'),
    (at(2, 3600), 2, 1, 'review', 'review'),
    (at(2, 7200), 2, 3, 'relearning', 'review'),
    (at(10, 3600), 1, 3, 'new', 'review'),
    (at(1, 3600), 6, 0, 'review', 'manual'),
]


def build_collection():
    connection = sqlite3.connect(':memory:')
    connection.executescript(schema.SCHEMA)
    search.register_functions(connection)
    for id_, name, kind, fields, templates in NOTETYPES:
        connection.execute(
            'INSERT INTO notetypes (id, name, kind, fields, templates, modified) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (id_, name, kind, json.dumps([{'name': f, 'description': ''} for f in fields]),
             json.dumps([{'name': t, 'qfmt': '', 'afmt': ''} for t in templates]), OLD))
    for id_, name in DECKS:
        connection.execute('INSERT INTO decks (id, name, modified) VALUES (?, ?, ?)',
                           (id_, name, OLD))
    for id_, notetype, fields, tags, marked, created, modified in NOTES:
        connection.execute(
            'INSERT INTO notes (id, guid, notetype_id, fields, tags, marked, created, modified) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (id_, f'guid{id_}', notetype, fields, tags, marked, created, modified))
    for row in CARDS:
        connection.execute(
            'INSERT INTO cards (id, note_id, deck_id, ord, state, due, interval, stability, '
            'difficulty, reps, lapses, left, flag, suspended, buried, created, modified) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (*row, row[-1]))
    for id_, card, rating, state, kind in REVLOG:
        connection.execute(
            'INSERT INTO revlog (id, card_id, rating, state, kind) VALUES (?, ?, ?, ?, ?)',
            (id_, card, rating, state, kind))
    return connection


class SearchTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.connection = build_collection()

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def cards(self, query):
        clause, params = search.compile(query, TODAY, now=NOW)
        rows = self.connection.execute(
            f'SELECT cards.id FROM {search.SQL_FROM} WHERE {clause} ORDER BY cards.id', params)
        return [row[0] for row in rows]

    def assertCards(self, query, expected):
        self.assertEqual(self.cards(query), expected, query)

    def assertError(self, query, fragment=None):
        with self.assertRaises(search.SearchError, msg=query) as caught:
            search.compile(query, TODAY, now=NOW)
        if fragment is not None:
            self.assertIn(fragment, str(caught.exception))

    # Plain terms

    def test_an_empty_query_matches_everything(self):
        self.assertEqual(search.compile('', TODAY), ('1', []))
        self.assertEqual(search.compile('  \t ', TODAY), ('1', []))
        self.assertCards('', [1, 2, 3, 4, 5, 6, 7])

    def test_a_bare_term_matches_anywhere_in_the_fields_ignoring_case(self):
        self.assertCards('dog', [1, 5, 6])
        self.assertCards('DOG', [1, 5, 6])
        self.assertCards('cat', [2])

    def test_params_are_a_list(self):
        clause, params = search.compile('dog', TODAY)
        self.assertIsInstance(params, list)
        self.assertEqual(params, ['%dog%'])

    def test_wildcards(self):
        self.assertCards('per*o', [1])
        self.assertCards('p_rro', [1])
        self.assertCards('h_l*', [3, 4])
        self.assertCards('hotd*g', [5])
        self.assertCards('a_b', [6, 7])

    def test_escaped_wildcards_and_like_metacharacters_are_literal(self):
        self.assertCards(r'a\_b', [7])
        self.assertCards(r'\*star\*', [7])
        self.assertCards('100%', [7])
        self.assertCards('100%*', [7])
        self.assertCards(r'\\', [])

    def test_a_quoted_phrase(self):
        self.assertCards('"fast food"', [5])
        self.assertCards('"food fast"', [])
        self.assertCards('food fast', [5])
        self.assertCards('"canine dog"', [])
        self.assertCards('canine dog', [6])

    def test_quoted_operators_are_terms(self):
        self.assertCards('"or"', [])
        self.assertCards('"-dog"', [])
        self.assertEqual(search.tokenize('"or" -x'), ['"or"', '-', 'x'])

    # Boolean structure

    def test_negation(self):
        self.assertCards('-dog', [2, 3, 4, 7])
        self.assertCards('tag:animal -tag:spanish', [6])

    def test_or_and_and(self):
        self.assertCards('dog or gato', [1, 2, 5, 6])
        self.assertCards('dog OR gato', [1, 2, 5, 6])
        self.assertCards('dog and tag:animal', [1, 6])
        self.assertCards('dog tag:animal', [1, 6])

    def test_and_binds_tighter_than_or(self):
        self.assertCards('gato or dog tag:animal', [1, 2, 6])

    def test_parentheses(self):
        self.assertCards('(gato or hola) tag:animal', [2])
        self.assertCards('-(dog or gato)', [3, 4, 7])
        self.assertCards('((dog))', [1, 5, 6])

    def test_tokenize(self):
        self.assertEqual(
            search.tokenize('deck:"My Deck" -tag:x (a or b) "c d" e\\ f'),
            ['deck:"My Deck"', '-', 'tag:x', '(', 'a', 'or', 'b', ')', '"c d"', 'e\\ f'])
        self.assertEqual(search.tokenize(''), [])

    def test_structural_errors(self):
        self.assertError('"unbalanced', 'quotes')
        self.assertError('(a or b', 'parentheses')
        self.assertError('a)', 'parentheses')
        self.assertError('()', 'parentheses')
        self.assertError('a or')
        self.assertError('or a')
        self.assertError('-')

    # deck:

    def test_deck_includes_subdecks_and_ignores_case(self):
        self.assertCards('deck:Languages', [1, 2, 3, 4])
        self.assertCards('deck:languages::spanish', [1, 2])
        self.assertCards('"deck:Languages::Spanish"', [1, 2])
        self.assertCards('deck:"Languages::Spanish"', [1, 2])
        self.assertCards('-deck:Science', [1, 2, 3, 4])
        self.assertCards('deck:Nowhere', [])

    def test_deck_wildcards(self):
        self.assertCards('deck:Lang*', [1, 2, 3, 4])
        self.assertCards('deck:*spanish', [1, 2])

    def test_deck_current_and_filtered_are_not_supported(self):
        self.assertError('deck:current', 'deck:current')
        self.assertError('deck:filtered')

    # tag:

    def test_tag_matches_whole_tags_and_children(self):
        self.assertCards('tag:animal', [1, 2, 6])
        self.assertCards('tag:Spanish', [1, 2])
        self.assertCards('tag:spanish::vocab', [2])
        self.assertCards('tag:anim', [])
        self.assertCards('tag:anim*', [1, 2, 6])

    def test_tag_none_and_tag_marked(self):
        self.assertCards('tag:none', [3, 4])
        self.assertCards('-tag:none', [1, 2, 5, 6, 7])
        self.assertCards('tag:marked', [2])

    def test_normalize_tag(self):
        self.assertEqual(search.normalize_tag('  my  big tag '), 'my_big_tag')
        self.assertEqual(search.normalize_tag('plain'), 'plain')
        self.assertEqual(search.normalize_tag('   '), '')

    # is:

    def test_is_states(self):
        self.assertCards('is:new', [3, 5])
        self.assertCards('is:learn', [4, 7])
        self.assertCards('is:review', [1, 2, 6, 7])
        self.assertCards('is:suspended', [5])
        self.assertCards('is:buried', [6])
        self.assertCards('is:marked', [2])
        self.assertCards('-is:suspended', [1, 2, 3, 4, 6, 7])

    def test_is_due(self):
        self.assertCards('is:due', [1, 4])
        self.assertCards('is:due is:review', [1])

    def test_is_unknown(self):
        self.assertError('is:xyz', 'Unknown search: is:xyz')

    # flag:

    def test_flag(self):
        self.assertCards('flag:1', [1])
        self.assertCards('flag:7', [6])
        self.assertCards('flag:0', [2, 4, 5, 7])
        self.assertCards('-flag:0', [1, 3, 6])
        self.assertError('flag:8')
        self.assertError('flag:red')

    # prop:

    def test_prop_interval(self):
        self.assertCards('prop:ivl>=10', [1, 6])
        self.assertCards('prop:ivl<10', [2, 3, 4, 5, 7])
        self.assertCards('prop:ivl=20', [6])
        self.assertCards('prop:ivl!=0', [1, 2, 6, 7])

    def test_prop_due_is_relative_to_today(self):
        self.assertCards('prop:due=1', [2])
        self.assertCards('prop:due<=0', [1])
        self.assertCards('prop:due=-1', [1])
        self.assertCards('prop:due>0', [2, 6])

    def test_prop_counts_and_memory_state(self):
        self.assertCards('prop:reps=5', [1])
        self.assertCards('prop:reps>4', [1, 6])
        self.assertCards('prop:lapses>1', [6, 7])
        self.assertCards('prop:s>30', [6])
        self.assertCards('prop:s<=3.0', [2, 3, 4, 5, 7])
        self.assertCards('prop:d>5', [1, 6])
        self.assertCards('prop:pos=1', [5])
        self.assertCards('prop:pos<1', [3])

    def test_prop_errors(self):
        self.assertError('prop:ease>2', 'prop:ease')
        self.assertError('prop:xyz>1', 'Unknown search: prop:xyz')
        self.assertError('prop:ivl>abc')
        self.assertError('prop:ivl')
        self.assertError('prop:due=1.5')

    # Days

    def test_rated(self):
        self.assertCards('rated:1', [1, 4])
        self.assertCards('rated:2', [1, 4])
        self.assertCards('rated:3', [1, 2, 4])
        self.assertCards('rated:3:1', [2, 4])
        self.assertCards('rated:3:3', [1, 2])
        self.assertCards('rated:1:3', [1])
        self.assertError('rated:0')
        self.assertError('rated:1:5')
        self.assertError('rated:x')

    def test_resched(self):
        self.assertCards('resched:1', [])
        self.assertCards('resched:2', [6])

    def test_introduced(self):
        self.assertCards('introduced:1', [4])
        self.assertCards('introduced:3', [2, 4])
        self.assertCards('introduced:12', [1, 2, 4])

    def test_added(self):
        self.assertCards('added:1', [5])
        self.assertCards('added:10', [5])
        self.assertCards('added:11', [1, 2, 3, 4, 5, 6])
        self.assertCards('-added:11', [7])

    def test_edited(self):
        self.assertCards('edited:1', [5])
        self.assertCards('edited:2', [2, 5])

    def test_day_cutoffs_use_the_day_start_hour(self):
        clause, params = search.compile('added:1', TODAY, now=NOW)
        self.assertEqual(params, [days.day_start(TODAY)])
        clause, params = search.compile('added:1', TODAY, now=NOW, day_start_hour=0)
        self.assertEqual(params, [days.day_start(TODAY, 0)])
        clause, params = search.compile('rated:2', TODAY, now=NOW)
        self.assertEqual(params, [int(days.day_start(TODAY - 1) * 1000)])

    # Ids

    def test_ids(self):
        self.assertCards('nid:1,3', [1, 3, 4])
        self.assertCards('nid:2', [2])
        self.assertCards('cid:2,5', [2, 5])
        self.assertCards('did:3', [5, 6, 7])
        self.assertCards('mid:2', [3, 4])
        self.assertCards('mid:1,3', [1, 2, 5, 6, 7])

    def test_id_errors(self):
        self.assertError('nid:abc')
        self.assertError('cid:')
        self.assertError('did:1,x')

    # note: and card:

    def test_note_type(self):
        self.assertCards('note:Basic', [1, 2, 6, 7])
        self.assertCards('note:basic', [1, 2, 6, 7])
        self.assertCards('note:basic*', [1, 2, 3, 4, 6, 7])
        self.assertCards('note:Cloze', [5])
        self.assertCards('"note:Basic (and reversed card)"', [3, 4])

    def test_card_by_number(self):
        self.assertCards('card:1', [1, 2, 3, 5, 6, 7])
        self.assertCards('card:2', [4])
        self.assertCards('card:3', [])

    def test_card_by_template_name(self):
        self.assertCards('"card:Card 2"', [4])
        self.assertCards('card:"card 1"', [1, 2, 3, 6, 7])
        self.assertCards('card:cloze', [5])
        self.assertCards('card:card*', [1, 2, 3, 4, 6, 7])

    # field:

    def test_field_is_an_exact_match_unless_wildcarded(self):
        self.assertCards('front:gato', [2])
        self.assertCards('front:Gato', [2])
        self.assertCards('front:gat', [])
        self.assertCards('front:gat*', [2])
        self.assertCards('"back:hello there"', [3, 4])
        self.assertCards('extra:"fast food"', [5])
        self.assertCards('text:*hotdog*', [5])

    def test_field_name_wildcards_and_unknown_fields(self):
        self.assertCards('fr*:gato', [2])
        self.assertCards('nosuch:gato', [])

    def test_field_empty_and_non_empty(self):
        self.assertCards('back:', [7])
        self.assertCards('back:*', [1, 2, 3, 4, 6])
        self.assertCards('extra:*', [5])

    def test_field_regex(self):
        self.assertCards('front:re:^g.t', [2])
        self.assertCards('front:re:^G', [2])
        self.assertCards('back:re:dog$', [1])
        self.assertError('front:re:[')

    # re:, nc:, w:

    def test_regex(self):
        self.assertCards('re:^per', [1])
        self.assertCards('re:d.g', [1, 5, 6])
        self.assertCards('re:PERRO', [1])
        self.assertCards(r're:\x1fdog$', [1])
        self.assertCards('"re:fast food"', [5])
        self.assertError('re:[', 'Invalid regular expression')

    def test_no_combining_is_a_plain_term(self):
        self.assertCards('nc:gato', [2])
        self.assertCards('nc:g_to', [2])

    def test_whole_word(self):
        self.assertCards('w:dog', [1, 6])
        self.assertCards('w:DOG', [1, 6])
        self.assertCards('w:d*g', [1, 6])
        self.assertCards('w:hot', [])

    # Unsupported and unknown

    def test_dupe_is_not_supported(self):
        self.assertError('dupe:1,text', 'Not supported')

    def test_a_null_safe_regexp_function(self):
        self.assertEqual(self.connection.execute('SELECT regexp(?, NULL)', ('a',)).fetchone(),
                         (0,))
        self.assertEqual(self.connection.execute("SELECT fieldn('a\x1fb', 1)").fetchone(),
                         ('b',))
        self.assertEqual(self.connection.execute("SELECT fieldn('a', 4)").fetchone(), ('',))
        self.assertEqual(self.connection.execute("SELECT fold('ÀB')").fetchone(), ('àb',))

    def test_combined_queries(self):
        self.assertCards('deck:Science -is:suspended', [6, 7])
        self.assertCards('(is:new or is:learn) -deck:Science', [3, 4])
        self.assertCards('tag:animal prop:ivl>=10 -flag:7', [1])
        self.assertCards('"deck:Languages::Spanish" (dog or cat)', [1, 2])


if __name__ == '__main__':
    unittest.main()
