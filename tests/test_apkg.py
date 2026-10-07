# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""apkg.py: Anki packages written and read back, hand-built legacy and latest packages,
text files. Every package is invented and built here; only two text files are fixtures."""

import datetime
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from unittest import mock

from tests import ROOT
from retain import apkg, days

FIXTURES = ROOT / 'tests' / 'fixtures'

try:
    from compression import zstd
except ImportError:  # pragma: no cover
    zstd = None

BASIC_ID = 1600000000001
CLOZE_ID = 1600000000002
TOP_DECK = 1600000001000
SUB_DECK = 1600000002000
CREATED = int(days.day_start(days.day_number(datetime.datetime(2026, 1, 10, 12).timestamp())))
DAY0 = days.day_number(CREATED)
LEARNING_DUE = int(datetime.datetime(2026, 1, 12, 10, 30).timestamp())


def _invented():
    """Two note types, two nested decks, three notes, cards in every state, two reviews."""
    notetypes = [
        apkg.NoteType(BASIC_ID, 'Basic', 'standard',
                      [{'name': 'Front', 'description': 'the prompt'},
                       {'name': 'Back', 'description': ''}],
                      [{'name': 'Card 1', 'qfmt': '{{Front}}',
                        'afmt': '{{FrontSide}}<hr id=answer>{{Back}}'},
                       {'name': 'Card 2', 'qfmt': '{{Back}}',
                        'afmt': '{{FrontSide}}<hr id=answer>{{Front}}'}],
                      '.card { font-size: 20px; }', 0),
        apkg.NoteType(CLOZE_ID, 'Cloze', 'cloze',
                      [{'name': 'Text', 'description': ''},
                       {'name': 'Back Extra', 'description': ''}],
                      [{'name': 'Cloze', 'qfmt': '{{cloze:Text}}',
                        'afmt': '{{cloze:Text}}<br>{{Back Extra}}'}],
                      '.cloze { font-weight: bold; }', 0),
    ]
    decks = [
        apkg.Deck(TOP_DECK, 'Invented', 'made up for the tests'),
        apkg.Deck(SUB_DECK, 'Invented::Birds', ''),
    ]
    notes = [
        apkg.Note(1600000010000, 'abcdefghij', BASIC_ID,
                  ['heron <img src="heron.png">', 'a wading bird'], ['birds', 'marked'],
                  created=1600000010, modified=1600000020, marked=True),
        apkg.Note(1600000011000, 'klmnopqrst', CLOZE_ID,
                  ['The {{c1::coot}} is black', ''], [], created=1600000011,
                  modified=1600000011),
        apkg.Note(1600000012000, 'uvwxyz0123', BASIC_ID, ['rook', 'a crow'], ['birds'],
                  created=1600000012, modified=1600000012),
    ]
    cards = [
        apkg.Card(1600000020000, 1600000010000, SUB_DECK, 0, 'new', 3, created=1600000010,
                  modified=1600000010),
        apkg.Card(1600000021000, 1600000010000, SUB_DECK, 1, 'review', DAY0 + 5, 12.4,
                  10.1234, 5.2, reps=4, lapses=1, last_review=1600000500, flag=2,
                  created=1600000010, modified=1600000600),
        apkg.Card(1600000022000, 1600000011000, TOP_DECK, 0, 'learning', LEARNING_DUE, 0,
                  reps=1, left=2, suspended=1, created=1600000011, modified=1600000011),
        apkg.Card(1600000023000, 1600000012000, TOP_DECK, 0, 'relearning', LEARNING_DUE + 60,
                  1, 3.5, 7.1, reps=6, lapses=2, left=1, flag=7, created=1600000012,
                  modified=1600000012),
        apkg.Card(1600000024000, 1600000012000, TOP_DECK, 1, 'review', DAY0 + 1, 2.0,
                  reps=2, buried=DAY0, created=1600000012, modified=1600000012),
    ]
    reviews = [
        apkg.Review(1600000300000, 1600000021000, 3, 'learning', 0, 1 / 144, 0, 0, 4200,
                    'review'),
        apkg.Review(1600000500000, 1600000021000, 4, 'review', 1, 12, 0, 0, 3100, 'review'),
        apkg.Review(1600000600000, 1600000021000, 0, 'review', 0, 0, 0, 0, 0, 'manual'),
    ]
    return notetypes, decks, notes, cards, reviews


# A tiny protobuf writer, for the latest format's blobs.

def _varint(number):
    out = bytearray()
    while True:
        byte = number & 0x7f
        number >>= 7
        if number:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _pb_int(field, number):
    return _varint(field << 3) + _varint(number)


def _pb_bytes(field, data):
    return _varint(field << 3 | 2) + _varint(len(data)) + data


def _pb_str(field, text):
    return _pb_bytes(field, text.encode('utf-8'))


SCHEMA_11_MINIMAL = """
CREATE TABLE col (id integer primary key, crt integer, mod integer, scm integer, ver integer,
    dty integer, usn integer, ls integer, conf text, models text, decks text, dconf text,
    tags text);
CREATE TABLE notes (id integer primary key, guid text, mid integer, mod integer, usn integer,
    tags text, flds text, sfld integer, csum integer, flags integer, data text);
CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer,
    mod integer, usn integer, type integer, queue integer, due integer, ivl integer,
    factor integer, reps integer, lapses integer, left integer, odue integer, odid integer,
    flags integer, data text);
CREATE TABLE revlog (id integer primary key, cid integer, usn integer, ease integer,
    ivl integer, lastIvl integer, factor integer, time integer, type integer);
"""


class PackageTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='retain-apkg-test-')
        self.addCleanup(self._cleanup)
        self.packages = []

    def _cleanup(self):
        for package in self.packages:
            package.close()
        for name in os.listdir(self.directory):
            os.unlink(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def path(self, name):
        return os.path.join(self.directory, name)

    def open(self, path):
        package = apkg.read_package(path)
        self.packages.append(package)
        return package


class WriteAndReadBackTest(PackageTest):

    def setUp(self):
        super().setUp()
        self.notetypes, self.decks, self.notes, self.cards, self.reviews = _invented()
        self.media = {'heron.png': self.path('heron.png')}
        with open(self.media['heron.png'], 'wb') as file:
            file.write(b'\x89PNG not really')
        self.apkg = self.path('invented.apkg')
        apkg.write_package(self.apkg, self.notetypes, self.decks, self.notes, self.cards,
                           self.reviews, self.media, created=CREATED)
        self.package = self.open(self.apkg)

    def test_it_is_a_legacy1_package_with_a_schema_11_collection(self):
        with zipfile.ZipFile(self.apkg) as archive:
            self.assertEqual(sorted(archive.namelist()), ['0', 'collection.anki2', 'media'])
            self.assertEqual(json.loads(archive.read('media')), {'0': 'heron.png'})
            archive.extract('collection.anki2', self.directory)
        db = sqlite3.connect(self.path('collection.anki2'))
        self.addCleanup(db.close)
        ver, crt, conf, decks, dconf = db.execute(
            'SELECT ver, crt, conf, decks, dconf FROM col').fetchone()
        self.assertEqual((ver, crt), (11, CREATED))
        self.assertEqual(json.loads(conf)['curModel'], BASIC_ID)
        self.assertEqual(json.loads(conf)['nextPos'], 4)
        self.assertEqual(json.loads(decks)['1']['name'], 'Default')
        self.assertEqual(json.loads(dconf)['1']['new']['delays'], [1, 10])
        self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master "
                                                "WHERE type = 'index'")}
        self.assertIn('ix_cards_sched', indexes)
        self.assertIn('ix_notes_csum', indexes)
        self.assertEqual(self.package.format, 'legacy1')
        self.assertEqual(self.package.created, CREATED)

    def test_notetypes_round_trip(self):
        self.assertEqual(self.package.notetypes, self.notetypes)

    def test_decks_round_trip_with_default_added(self):
        by_name = {deck.name: deck for deck in self.package.decks}
        self.assertEqual(set(by_name), {'Default', 'Invented', 'Invented::Birds'})
        self.assertEqual(by_name['Invented'].original_id, TOP_DECK)
        self.assertEqual(by_name['Invented'].description, 'made up for the tests')
        self.assertEqual(by_name['Invented::Birds'].original_id, SUB_DECK)
        self.assertEqual(by_name['Default'].original_id, 1)
        self.assertEqual(by_name['Invented'].config['id'], 1)

    def test_a_missing_parent_deck_is_written(self):
        apkg.write_package(self.path('orphan.apkg'), [], [apkg.Deck(None, 'Top::Mid::Leaf')],
                           [], [], [], {})
        with self.open(self.path('orphan.apkg')) as package:
            self.assertEqual(sorted(deck.name for deck in package.decks),
                             ['Default', 'Top', 'Top::Mid', 'Top::Mid::Leaf'])

    def test_notes_round_trip(self):
        self.assertEqual(self.package.notes, self.notes)
        self.assertTrue(self.package.notes[0].marked)
        self.assertIn('marked', self.package.notes[0].tags)

    def test_the_sort_field_is_stripped_and_checksummed(self):
        with zipfile.ZipFile(self.apkg) as archive:
            archive.extract('collection.anki2', self.directory)
        db = sqlite3.connect(self.path('collection.anki2'))
        self.addCleanup(db.close)
        sfld, csum, tags = db.execute(
            'SELECT sfld, csum, tags FROM notes WHERE id = 1600000010000').fetchone()
        self.assertEqual(sfld, 'heron')
        self.assertEqual(csum, apkg.checksum('heron'))
        self.assertEqual(tags, ' birds marked ')

    def test_every_state_round_trips(self):
        states = [card.state for card in self.package.cards]
        self.assertEqual(states, ['new', 'review', 'learning', 'relearning', 'review'])

    def test_a_review_card_due_is_a_day_number_again(self):
        card = self.package.cards[1]
        self.assertEqual(card.due, DAY0 + 5)
        self.assertEqual(card.interval, 12)
        with zipfile.ZipFile(self.apkg) as archive:
            archive.extract('collection.anki2', self.directory)
        db = sqlite3.connect(self.path('collection.anki2'))
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT type, queue, due, ivl, factor FROM cards '
                                    'WHERE id = 1600000021000').fetchone(), (2, 2, 5, 12, 2500))

    def test_learning_cards_keep_their_seconds_and_steps(self):
        learning, relearning = self.package.cards[2], self.package.cards[3]
        self.assertEqual(learning.due, LEARNING_DUE)
        self.assertEqual(learning.left, 2)
        self.assertEqual(relearning.due, LEARNING_DUE + 60)
        self.assertEqual(relearning.left, 1)
        self.assertEqual(relearning.lapses, 2)

    def test_a_new_card_due_is_its_position(self):
        self.assertEqual(self.package.cards[0].due, 3)
        self.assertEqual(self.package.cards[0].interval, 0)

    def test_suspended_flags_counts_and_memory_round_trip(self):
        cards = self.package.cards
        self.assertEqual([card.suspended for card in cards], [0, 0, 1, 0, 0])
        self.assertEqual([card.flag for card in cards], [0, 2, 0, 7, 0])
        self.assertEqual((cards[1].reps, cards[1].lapses), (4, 1))
        self.assertEqual((cards[1].stability, cards[1].difficulty), (10.1234, 5.2))
        self.assertEqual((cards[3].stability, cards[3].difficulty), (3.5, 7.1))
        self.assertEqual((cards[0].stability, cards[0].difficulty), (0, 0))
        self.assertEqual(cards[1].last_review, 1600000500)
        self.assertEqual([card.original_id for card in cards],
                         [card.original_id for card in self.cards])
        self.assertEqual([(card.note_id, card.deck_id, card.ord) for card in cards],
                         [(card.note_id, card.deck_id, card.ord) for card in self.cards])

    def test_a_buried_card_comes_back_unburied(self):
        self.assertEqual(self.package.cards[4].buried, 0)
        self.assertEqual(self.package.cards[4].state, 'review')
        self.assertEqual(self.package.cards[4].due, DAY0 + 1)

    def test_reviews_round_trip(self):
        reviews = self.package.reviews
        self.assertEqual([r.id_ms for r in reviews], [r.id_ms for r in self.reviews])
        self.assertEqual([r.rating for r in reviews], [3, 4, 0])
        self.assertEqual([r.state for r in reviews], ['learning', 'review', 'review'])
        self.assertEqual([r.kind for r in reviews], ['review', 'review', 'manual'])
        self.assertAlmostEqual(reviews[0].scheduled_days, 1 / 144)
        self.assertEqual(reviews[1].scheduled_days, 12)
        self.assertEqual(reviews[1].elapsed_days, 1)
        self.assertEqual([r.duration_ms for r in reviews], [4200, 3100, 0])
        self.assertTrue(all(r.card_id == 1600000021000 for r in reviews))

    def test_media_round_trips(self):
        self.assertEqual(self.package.media_names(), ['heron.png'])
        self.assertEqual(self.package.read_media('heron.png'), b'\x89PNG not really')
        with self.assertRaises(apkg.PackageError):
            self.package.read_media('missing.png')

    def test_without_scheduling_every_card_is_new_and_there_is_no_revlog(self):
        path = self.path('plain.apkg')
        apkg.write_package(path, self.notetypes, self.decks, self.notes, self.cards,
                           self.reviews, {}, with_scheduling=False)
        with self.open(path) as package:
            self.assertEqual([card.state for card in package.cards], ['new'] * 5)
            self.assertEqual([card.due for card in package.cards], [0, 1, 2, 3, 4])
            self.assertEqual([card.suspended for card in package.cards], [0] * 5)
            self.assertEqual([card.reps for card in package.cards], [0] * 5)
            self.assertEqual(package.reviews, [])
            self.assertEqual(package.media_names(), [])

    def test_our_own_ids_become_millisecond_ids_and_references_follow(self):
        notetypes = [apkg.NoteType(7, 'Basic', 'standard', [{'name': 'Front'}],
                                   [{'name': 'Card 1', 'qfmt': '{{Front}}', 'afmt': ''}])]
        decks = [apkg.Deck(3, 'Mine')]
        notes = [apkg.Note(11, 'guidguidgu', 7, ['one'], [], created=1700000000),
                 apkg.Note(12, 'guidguidgv', 7, ['two'], [], created=1700000000)]
        cards = [apkg.Card(21, 11, 3, 0, 'review', DAY0 + 2, 3, created=1700000000),
                 apkg.Card(22, 12, 3, 0, 'new', 0, created=1700000000)]
        reviews = [apkg.Review(1700000000000, 21, 3, 'review', 1, 3)]
        path = self.path('ours.apkg')
        apkg.write_package(path, notetypes, decks, notes, cards, reviews, {}, created=CREATED)
        with self.open(path) as package:
            note_ids = {note.original_id for note in package.notes}
            self.assertEqual(len(note_ids), 2)
            self.assertTrue(all(nid >= 1700000000000 for nid in note_ids))
            self.assertEqual({card.note_id for card in package.cards}, note_ids)
            deck_id = package.cards[0].deck_id
            self.assertEqual([d.name for d in package.decks if d.original_id == deck_id],
                             ['Mine'])
            self.assertEqual(package.cards[0].note_id, package.notes[0].original_id)
            self.assertEqual(package.notes[0].notetype_id, package.notetypes[0].original_id)
            self.assertEqual(package.reviews[0].card_id, package.cards[0].original_id)

    def test_a_dangling_reference_is_an_error(self):
        notes = [apkg.Note(None, 'guidguidgu', 999, ['one'], [])]
        with self.assertRaises(apkg.PackageError):
            apkg.write_package(self.path('bad.apkg'), [], [], notes, [], [], {})
        with self.assertRaises(apkg.PackageError):
            apkg.write_package(self.path('bad.apkg'), [], [], [], [], [],
                               {'gone.png': self.path('gone.png')})


class Legacy2Test(PackageTest):
    """A hand-built package the way Anki 2.1.50+ writes a legacy one: collection.anki21 with
    the data, a dummy collection.anki2, a `meta` saying version 2."""

    HOME = 1600000001000
    FILTERED = 1600000003000

    def setUp(self):
        super().setUp()
        db_path = self.path('collection.anki21')
        db = sqlite3.connect(db_path)
        db.executescript(SCHEMA_11_MINIMAL)
        models = {str(BASIC_ID): {
            'id': BASIC_ID, 'name': 'Basic', 'type': 0, 'sortf': 1,
            'flds': [{'name': 'Front', 'ord': 0}, {'name': 'Back', 'ord': 1}],
            'tmpls': [{'name': 'Card 1', 'ord': 0, 'qfmt': '{{Front}}', 'afmt': '{{Back}}'}],
            'css': ''}}
        decks = {
            str(self.HOME): {'id': self.HOME, 'name': 'Home', 'desc': 'home', 'conf': 1,
                             'dyn': 0},
            str(self.FILTERED): {'id': self.FILTERED, 'name': 'Cram', 'desc': '', 'dyn': 1},
        }
        dconf = {'1': {'id': 1, 'name': 'Default', 'new': {'perDay': 15}}}
        db.execute('INSERT INTO col VALUES (1, ?, 0, 0, 11, 0, 0, 0, ?, ?, ?, ?, ?)',
                   (CREATED, '{}', json.dumps(models), json.dumps(decks), json.dumps(dconf),
                    '{}'))
        db.execute('INSERT INTO notes VALUES (?, ?, ?, ?, -1, ?, ?, ?, 0, 0, ?)',
                   (1600000010000, 'guidguidgu', BASIC_ID, 1600000010, ' Marked ',
                    'front\x1fback', 'back', ''))
        cards = [
            # In the filtered deck: a review card whose home due is day 4.
            (1, 1600000010000, self.FILTERED, 0, 0, -1, 2, 2, 123, 30, 2500, 3, 0, 0, 4,
             self.HOME, 1, '{"s": 20.5, "d": 4.25}'),
            # A day-learn card due two days after day 0, with 2 steps left of 3 total.
            (2, 1600000010000, self.HOME, 1, 0, -1, 1, 3, 2, -600, 2500, 1, 0, 2002, 0, 0,
             0, ''),
            # A user-buried review card, due on day 1.
            (3, 1600000010000, self.HOME, 2, 0, -1, 2, -3, 1, 1, 2500, 1, 0, 0, 0, 0, 0,
             '{}'),
            # A suspended relearning card whose due is a Unix time.
            (4, 1600000010000, self.HOME, 3, 0, -1, 3, -1, LEARNING_DUE, 0, 2500, 5, 2,
             1001, 0, 0, 10, ''),
        ]
        db.executemany('INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
                       '?, ?, ?, ?)', cards)
        revlog = [
            (1600000100000, 1, -1, 3, -600, 0, 2500, 5000, 0),
            (1600000200000, 1, -1, 2, 30, 10, 2500, 6000, 1),
            (1600000250000, 1, -1, 3, 1, 1, 0, 1000, 3),
            (1600000300000, 1, -1, 0, 0, 0, 0, 0, 4),
        ]
        db.executemany('INSERT INTO revlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', revlog)
        db.commit()
        db.close()
        self.apkg = self.path('legacy2.apkg')
        with zipfile.ZipFile(self.apkg, 'w') as archive:
            archive.writestr('meta', _pb_int(1, 2))
            archive.write(db_path, 'collection.anki21')
            archive.writestr('collection.anki2', b'dummy: upgrade your Anki')
            archive.writestr('media', '{}')
        self.package = self.open(self.apkg)

    def test_the_anki21_file_is_read(self):
        self.assertEqual(self.package.format, 'legacy2')
        self.assertEqual([n.name for n in self.package.notetypes], ['Basic'])
        self.assertEqual(self.package.notetypes[0].sort_field, 1)
        self.assertEqual(self.package.notetypes[0].fields[1], {'name': 'Back',
                                                               'description': ''})
        self.assertEqual(len(self.package.notes), 1)
        self.assertEqual(self.package.notes[0].tags, ['Marked'])
        self.assertTrue(self.package.notes[0].marked)

    def test_a_filtered_deck_is_dropped_and_its_cards_go_home(self):
        self.assertEqual([deck.name for deck in self.package.decks], ['Home'])
        self.assertEqual(self.package.decks[0].config['new']['perDay'], 15)
        card = self.package.cards[0]
        self.assertEqual(card.deck_id, self.HOME)
        self.assertEqual(card.state, 'review')
        self.assertEqual(card.due, DAY0 + 4)
        self.assertEqual((card.stability, card.difficulty), (20.5, 4.25))
        self.assertEqual(card.flag, 1)
        self.assertEqual(card.last_review, 1600000250)

    def test_a_day_learn_card_is_due_at_its_day_start(self):
        card = self.package.cards[1]
        self.assertEqual(card.state, 'learning')
        self.assertEqual(card.due, int(days.day_start(DAY0 + 2)))
        self.assertEqual(card.left, 2)
        self.assertAlmostEqual(card.interval, 600 / 86400)

    def test_buried_and_suspended_cards(self):
        buried, suspended = self.package.cards[2], self.package.cards[3]
        self.assertEqual((buried.state, buried.due, buried.buried, buried.suspended),
                         ('review', DAY0 + 1, 0, 0))
        self.assertEqual((suspended.state, suspended.due, suspended.suspended),
                         ('relearning', LEARNING_DUE, 1))
        self.assertEqual(suspended.left, 1)
        self.assertEqual(suspended.flag, 2)

    def test_revlog_conversion(self):
        reviews = self.package.reviews
        self.assertEqual([r.kind for r in reviews], ['review', 'review', 'cram', 'manual'])
        self.assertEqual([r.state for r in reviews],
                         ['learning', 'review', 'review', 'review'])
        self.assertEqual([r.rating for r in reviews], [3, 2, 3, 0])
        self.assertAlmostEqual(reviews[0].scheduled_days, 600 / 86400)
        self.assertEqual((reviews[1].scheduled_days, reviews[1].elapsed_days), (30, 10))
        self.assertEqual(reviews[1].duration_ms, 6000)

    def test_without_meta_anki21_still_wins(self):
        path = self.path('nometa.apkg')
        with zipfile.ZipFile(self.apkg) as source, zipfile.ZipFile(path, 'w') as target:
            for name in source.namelist():
                if name != 'meta':
                    target.writestr(name, source.read(name))
        with self.open(path) as package:
            self.assertEqual(package.format, 'legacy2')
            self.assertEqual(len(package.cards), 4)


@unittest.skipIf(zstd is None, 'no zstd module')
class LatestTest(PackageTest):
    """A hand-built package the way Anki 2.1.50+ writes one by default: meta version 3, a
    zstd-compressed schema 18 collection, protobuf note types, decks and media index."""

    DECK = 1600000001000

    def setUp(self):
        super().setUp()
        db_path = self.path('collection.anki21b.sqlite')
        db = sqlite3.connect(db_path)
        db.executescript(SCHEMA_11_MINIMAL + """
CREATE TABLE notetypes (id integer primary key, name text, mtime_secs integer, usn integer,
    config blob);
CREATE TABLE fields (ntid integer, ord integer, name text, config blob,
    PRIMARY KEY (ntid, ord));
CREATE TABLE templates (ntid integer, ord integer, name text, mtime_secs integer,
    usn integer, config blob, PRIMARY KEY (ntid, ord));
CREATE TABLE decks (id integer primary key, name text, mtime_secs integer, usn integer,
    common blob, kind blob);
""")
        db.execute('INSERT INTO col VALUES (1, ?, 0, 0, 18, 0, 0, 0, ?, ?, ?, ?, ?)',
                   (CREATED, '{}', '', '', '', ''))
        # Basic: kind normal, sort field 1, css; Image Occlusion: cloze with the IO fields.
        db.execute('INSERT INTO notetypes VALUES (?, ?, 0, 0, ?)',
                   (BASIC_ID, 'Basic', _pb_int(1, 0) + _pb_int(2, 1) + _pb_str(3, '.card {}')))
        db.execute('INSERT INTO notetypes VALUES (?, ?, 0, 0, ?)',
                   (CLOZE_ID, 'Image Occlusion', _pb_int(1, 1) + _pb_int(2, 0) +
                    _pb_str(3, '') + _pb_int(9, 6)))
        db.executemany('INSERT INTO fields VALUES (?, ?, ?, ?)', [
            (BASIC_ID, 0, 'Front', _pb_str(5, 'the question')),
            (BASIC_ID, 1, 'Back', b''),
            (CLOZE_ID, 0, 'Occlusion', b''), (CLOZE_ID, 1, 'Image', b''),
            (CLOZE_ID, 2, 'Header', b''), (CLOZE_ID, 3, 'Back Extra', b''),
            (CLOZE_ID, 4, 'Comments', b''),
        ])
        db.executemany('INSERT INTO templates VALUES (?, ?, ?, 0, 0, ?)', [
            (BASIC_ID, 0, 'Card 1', _pb_str(1, '{{Front}}') + _pb_str(2, '{{Back}}')),
            (CLOZE_ID, 0, 'Image Occlusion',
             _pb_str(1, '{{#Header}}{{Header}}{{/Header}}{{Image}}') + _pb_str(2, '{{Image}}')),
        ])
        normal = _pb_bytes(1, _pb_int(1, 1) + _pb_str(4, 'a nested deck'))
        filtered = _pb_bytes(2, _pb_int(1, 1))
        db.executemany('INSERT INTO decks VALUES (?, ?, 0, 0, ?, ?)', [
            (1, 'Default', b'', _pb_bytes(1, _pb_int(1, 1))),
            (self.DECK, 'Parent\x1fChild', b'', normal),
            (1600000003000, 'Filtered', b'', filtered),
        ])
        db.execute('INSERT INTO notes VALUES (?, ?, ?, ?, -1, ?, ?, ?, 0, 0, ?)',
                   (1600000010000, 'guidguidgu', BASIC_ID, 1600000010, ' tag ',
                    'crow <img src="crow.png">\x1fblack', 'crow', ''))
        db.execute('INSERT INTO cards VALUES (?, ?, ?, ?, ?, -1, ?, ?, ?, ?, 2500, 2, 0, 0, '
                   '0, 0, 0, ?)',
                   (1, 1600000010000, self.DECK, 0, 0, 2, 2, 9, 15, '{"pos":1,"s":8.5,"d":6}'))
        db.commit()
        db.close()
        with open(db_path, 'rb') as file:
            collection = zstd.compress(file.read())
        png = b'\x89PNG crow'
        entry = _pb_str(1, 'crow.png') + _pb_int(2, len(png)) + _pb_bytes(
            3, hashlib.sha1(png).digest())
        self.apkg = self.path('latest.apkg')
        with zipfile.ZipFile(self.apkg, 'w') as archive:
            archive.writestr('meta', _pb_int(1, 3))
            archive.writestr('collection.anki21b', collection)
            archive.writestr('collection.anki2', b'dummy')
            archive.writestr('media', zstd.compress(_pb_bytes(1, entry)))
            archive.writestr('0', zstd.compress(png))
        self.package = self.open(self.apkg)

    def test_the_format_and_the_notetypes_come_from_the_tables(self):
        self.assertEqual(self.package.format, 'latest')
        basic, occlusion = self.package.notetypes
        self.assertEqual((basic.name, basic.kind, basic.sort_field, basic.css),
                         ('Basic', 'standard', 1, '.card {}'))
        self.assertEqual(basic.fields, [{'name': 'Front', 'description': 'the question'},
                                        {'name': 'Back', 'description': ''}])
        self.assertEqual(basic.templates, [{'name': 'Card 1', 'qfmt': '{{Front}}',
                                            'afmt': '{{Back}}'}])
        self.assertEqual(occlusion.kind, 'occlusion')
        self.assertEqual([f['name'] for f in occlusion.fields][:2], ['Occlusion', 'Image'])
        self.assertEqual(occlusion.templates[0]['afmt'], '{{Image}}')

    def test_decks_use_double_colons_and_filtered_ones_are_dropped(self):
        self.assertEqual([(d.original_id, d.name, d.description, d.config)
                          for d in self.package.decks],
                         [(1, 'Default', '', None), (self.DECK, 'Parent::Child',
                                                     'a nested deck', None)])

    def test_cards_and_notes_read_as_in_legacy(self):
        card = self.package.cards[0]
        self.assertEqual((card.state, card.due, card.interval), ('review', DAY0 + 9, 15))
        self.assertEqual((card.stability, card.difficulty), (8.5, 6))
        self.assertEqual(self.package.notes[0].fields, ['crow <img src="crow.png">', 'black'])

    def test_media_is_decompressed(self):
        self.assertEqual(self.package.media_names(), ['crow.png'])
        self.assertEqual(self.package.media, {'crow.png': '0'})
        self.assertEqual(self.package.read_media('crow.png'), b'\x89PNG crow')

    def test_without_zstd_the_message_says_to_export_legacy(self):
        with mock.patch.object(apkg, '_decompress', None):
            with self.assertRaises(apkg.PackageError) as caught:
                apkg.read_package(self.apkg)
        self.assertIn('Support older Anki versions', str(caught.exception))


class UnreadableTest(PackageTest):

    def test_a_file_that_is_not_a_zip(self):
        path = self.path('notes.apkg')
        with open(path, 'wb') as file:
            file.write(b'this is plain text, not a package')
        with self.assertRaises(apkg.PackageError):
            apkg.read_package(path)
        with self.assertRaises(apkg.PackageError):
            apkg.read_package(self.path('absent.apkg'))

    def test_a_zip_without_a_collection(self):
        path = self.path('empty.apkg')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('media', '{}')
        with self.assertRaises(apkg.PackageError):
            apkg.read_package(path)

    def test_a_collection_that_is_not_a_database(self):
        path = self.path('broken.apkg')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('collection.anki2', b'not sqlite at all')
        with self.assertRaises(apkg.PackageError):
            apkg.read_package(path)


class TextRowsTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='retain-text-test-')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for name in os.listdir(self.directory):
            os.unlink(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def write(self, name, text):
        path = os.path.join(self.directory, name)
        with open(path, 'w', encoding='utf-8', newline='') as file:
            file.write(text)
        return path

    def test_anki_header_lines_in_a_tab_file(self):
        rows, info = apkg.read_text_rows(str(FIXTURES / 'anki-headers.txt'))
        self.assertEqual(info['separator'], '\t')
        self.assertTrue(info['html'])
        self.assertEqual((info['tags_column'], info['notetype_column'], info['deck_column']),
                         (2, 3, 4))
        self.assertEqual(info['columns'], ['Front', 'Back', 'Tags', 'Notetype', 'Deck'])
        self.assertEqual(rows, [
            ['abacus', '<b>an old counting frame</b>', 'objects tools', 'Basic',
             'Invented::Things'],
            ['balafon', 'a wooden xylophone', 'instruments', 'Basic', 'Invented::Sounds'],
        ])

    def test_a_comma_file_with_quoted_fields_and_newlines(self):
        rows, info = apkg.read_text_rows(str(FIXTURES / 'quoted.csv'))
        self.assertEqual(info['separator'], ',')
        self.assertFalse(info['html'])
        self.assertIsNone(info['tags_column'])
        self.assertIsNone(info['columns'])
        self.assertEqual(rows, [
            ['word', 'meaning', 'tags'],
            ['coracle', 'a small, round boat', 'boats'],
            ['glyph', 'a carved sign\nor symbol', 'writing marks'],
            ['#hash', 'a sign that is not a comment', 'signs'],
        ])

    def test_a_semicolon_file_is_sniffed(self):
        path = self.write('semi.txt', 'eins;one\nzwei;two\n# not a row\ndrei;three\n\n')
        rows, info = apkg.read_text_rows(path)
        self.assertEqual(info['separator'], ';')
        self.assertEqual(rows, [['eins', 'one'], ['zwei', 'two'], ['drei', 'three']])

    def test_a_tab_file_without_headers_and_a_pipe_file(self):
        path = self.write('plain.tsv', 'uno\tone, the first\ndos\ttwo\n')
        rows, info = apkg.read_text_rows(path)
        self.assertEqual(info['separator'], '\t')
        self.assertEqual(rows, [['uno', 'one, the first'], ['dos', 'two']])
        path = self.write('pipes.txt', '#separator:pipe\n#deck:Numbers\n#tags:a b\n'
                                       'tres|three\ncuatro|four\n')
        rows, info = apkg.read_text_rows(path)
        self.assertEqual(info['separator'], '|')
        self.assertEqual(info['deck'], 'Numbers')
        self.assertEqual(info['tags'], ['a', 'b'])
        self.assertEqual(rows, [['tres', 'three'], ['cuatro', 'four']])

    def test_html_false_in_the_header_is_honoured(self):
        path = self.write('nohtml.txt', '#html:false\na<br>b\tc\n')
        rows, info = apkg.read_text_rows(path)
        self.assertFalse(info['html'])
        self.assertEqual(rows, [['a<br>b', 'c']])

    def test_a_file_that_is_not_text(self):
        path = os.path.join(self.directory, 'binary.txt')
        with open(path, 'wb') as file:
            file.write(b'\xff\xfe\x00bad')
        with self.assertRaises(apkg.PackageError):
            apkg.read_text_rows(path)


class HelpersTest(unittest.TestCase):

    def test_guid_is_ten_characters_and_fresh_each_time(self):
        guids = {apkg.guid() for _ in range(50)}
        self.assertEqual(len(guids), 50)
        for value in guids:
            self.assertEqual(len(value), 10)
            self.assertTrue(value.isascii() and value.isprintable() and ' ' not in value)

    def test_checksum_is_the_first_eight_hex_digits_of_sha1(self):
        expected = int(hashlib.sha1(b'hello').hexdigest()[:8], 16)
        self.assertEqual(apkg.checksum('hello'), expected)
        self.assertEqual(apkg.strip_html('<b>hello</b> &amp; <img src="x.png">'), 'hello &')

    def test_the_protobuf_reader(self):
        message = _pb_int(1, 300) + _pb_str(3, 'css') + _pb_bytes(8, _pb_int(1, 1))
        fields = apkg._proto(message)
        self.assertEqual(fields[1], [300])
        self.assertEqual(apkg._proto_str(fields, 3), 'css')
        self.assertEqual(apkg._proto_int(apkg._proto(fields[8][0]), 1), 1)
        self.assertEqual(apkg._proto_str(fields, 2, 'none'), 'none')
        with self.assertRaises(apkg.PackageError):
            apkg._proto(b'\x0a\x10short')


if __name__ == '__main__':
    unittest.main()
