# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""importing.py: a collection exported with exporting.py and imported into a fresh one,
re-imports, hand-made packages (note type clashes, deck presets, missing media), text
files and the result's wording. Everything is invented and built here."""

import os
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from tests.support import Clock, add_basic, add_cloze, temporary_collection
from retain import apkg, exporting, importing
from retain.deck_config import DEFAULT_ID
from retain.fsrs import AGAIN, GOOD
from retain.importing import ImportResult, describe_result, rename_media_references
from retain.notetypes import NoteType
from retain.scheduler import Scheduler

PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(32))
OTHER_PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(32, 64))
OGG = b'OggS' + bytes(range(16))
TYPE_ID = 1600000000001
DECK_ID = 1600000001000
NOTE_ID = 1600000002000
CARD_ID = 1600000003000


def build_source(collection, clock):
    """Two nested decks, a custom note type, notes with media, a few answers.

    Returns the dict a test reads its expectations from."""
    languages = collection.add_deck('Languages')
    spanish = collection.add_deck('Languages::Spanish')
    spanish.description = 'Words and phrases'
    collection.update_deck(spanish)
    vocab = collection.add_notetype(NoteType(
        'Vocab', fields=[{'name': 'Word', 'description': ''},
                         {'name': 'Meaning', 'description': ''},
                         {'name': 'Picture', 'description': ''}],
        templates=[{'name': 'Card 1', 'qfmt': '{{Word}}<br>{{Picture}}',
                    'afmt': '{{FrontSide}}<hr id=answer>{{Meaning}}'}]))
    collection.media.add_bytes(PNG, 'cat.png')
    collection.media.add_bytes(OGG, 'meow.ogg')
    hola = add_basic(collection, spanish, 'hola', 'hello', ['greeting'])
    adios = add_basic(collection, languages, 'adiós', 'goodbye')
    collection.set_marked([adios.id], True)
    gato = collection.add_note(vocab.id, spanish.id,
                               ['gato [sound:meow.ogg]', 'cat', '<img src="cat.png">'], ['pets'])
    cloze = add_cloze(collection, spanish, 'Buenos {{c1::días}} y buenas {{c2::noches}}')
    scheduler = Scheduler(collection)
    hola_card = collection.cards_of_note(hola.id)[0]
    scheduler.answer(hola_card, GOOD, now=clock.now)
    clock.advance(minutes=11)
    hola_card = collection.card(hola_card.id)
    scheduler.answer(hola_card, GOOD, now=clock.now)
    clock.advance(minutes=1)
    adios_card = collection.cards_of_note(adios.id)[0]
    scheduler.answer(adios_card, AGAIN, now=clock.now)
    gato_card = collection.cards_of_note(gato.id)[0]
    collection.set_flag([gato_card.id], 3)
    cloze_cards = collection.cards_of_note(cloze.id)
    collection.suspend([cloze_cards[1].id])
    return {'languages': languages, 'spanish': spanish, 'vocab': vocab, 'hola': hola,
            'adios': adios, 'gato': gato, 'cloze': cloze}


class FakePackage(apkg.Package):
    """An apkg.Package built in memory, its media from a dict."""

    def __init__(self, media_bytes=None):
        super().__init__(None, 'invented.apkg', 'legacy1')
        self.media_bytes = dict(media_bytes or {})
        self.media = {name: name for name in self.media_bytes}

    def read_media(self, name):
        return self.media_bytes[name]


def basic_package(front='front', back='back', fields=None, media=None, guid='guid000001',
                  modified=1_700_000_000, config=None, deck_name='Imported deck', tags=()):
    """A package with one Basic-like note type, one deck, one note and its card."""
    package = FakePackage(media)
    package.created = 1_600_000_000
    field_names = fields or ['Front', 'Back']
    package.notetypes = [apkg.NoteType(
        TYPE_ID, 'Basic', 'standard', [{'name': name, 'description': ''} for name in field_names],
        [{'name': 'Card 1', 'qfmt': '{{Front}}', 'afmt': '{{FrontSide}}<hr id=answer>{{Back}}'}])]
    package.decks = [apkg.Deck(DECK_ID, deck_name, 'About this deck', config)]
    package.notes = [apkg.Note(NOTE_ID, guid, TYPE_ID, [front, back][:len(field_names)]
                               + [''] * (len(field_names) - 2), list(tags),
                               created=modified - 100, modified=modified)]
    package.cards = [apkg.Card(CARD_ID, NOTE_ID, DECK_ID, 0, due=3, created=modified - 100,
                               modified=modified)]
    return package


class RoundTripTest(unittest.TestCase):
    """A collection exported and imported into a fresh one."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='retain-import-')
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.source = self.enterContext(temporary_collection())
        self.clock = Clock()
        self.built = build_source(self.source, self.clock)
        self.package_path = os.path.join(self.directory, 'export.apkg')
        exporting.export_package(self.source, self.package_path)
        self.target = self.enterContext(temporary_collection())

    def import_into_target(self, **kwargs):
        return importing.import_package(self.target, self.package_path, **kwargs)

    def note_by_guid(self, collection, guid):
        row = collection.db.execute('SELECT id FROM notes WHERE guid = ?', (guid,)).fetchone()
        return collection.note(row['id']) if row else None

    def test_counts_of_a_first_import(self):
        result = self.import_into_target()
        self.assertEqual(result.notes_added, 4)
        self.assertEqual(result.cards_added, 5)
        self.assertEqual(result.notes_updated, 0)
        self.assertEqual(result.notes_skipped, 0)
        self.assertEqual(result.decks_added, 2)
        self.assertEqual(result.notetypes_added, 1)  # Vocab; Basic and Cloze match the stock
        self.assertEqual(result.media_added, 2)
        self.assertEqual(result.reviews_added, 3)
        self.assertEqual(result.problems, [])
        self.assertFalse(result.stopped)

    def test_decks_come_with_their_descriptions(self):
        self.import_into_target()
        names = [deck.name for deck in self.target.decks()]
        self.assertEqual(names, ['Default', 'Languages', 'Languages::Spanish'])
        self.assertEqual(self.target.deck_by_name('Languages::Spanish').description,
                         'Words and phrases')

    def test_note_types_match_stock_ones_and_the_custom_one_is_added(self):
        self.import_into_target()
        vocab = self.target.notetype_by_name('Vocab')
        self.assertEqual(vocab.field_names(), ['Word', 'Meaning', 'Picture'])
        self.assertEqual(vocab.original_id, self.built['vocab'].id)
        basic = self.target.notetype_by_name('Basic')
        hola = self.note_by_guid(self.target, self.built['hola'].guid)
        self.assertEqual(hola.notetype_id, basic.id)
        self.assertEqual(len(self.target.notetypes()), len(self.source.notetypes()))

    def test_notes_keep_fields_tags_mark_and_times(self):
        self.import_into_target()
        hola = self.note_by_guid(self.target, self.built['hola'].guid)
        self.assertEqual(hola.fields, ['hola', 'hello'])
        self.assertEqual(hola.tags, ['greeting'])
        self.assertEqual(hola.created, self.built['hola'].created)
        self.assertEqual(hola.modified, self.built['hola'].modified)
        self.assertEqual(hola.sort_field, 'hola')
        adios = self.note_by_guid(self.target, self.built['adios'].guid)
        self.assertTrue(adios.marked)
        self.assertEqual(adios.tags, [])
        self.assertEqual(self.target.deck(self.target.cards_of_note(adios.id)[0].deck_id).name,
                         'Languages')

    def test_cards_keep_their_scheduling(self):
        self.import_into_target()
        source_card = self.source.cards_of_note(self.built['hola'].id)[0]
        card = self.target.cards_of_note(
            self.note_by_guid(self.target, self.built['hola'].guid).id)[0]
        self.assertEqual(source_card.state, 'review')
        self.assertEqual(card.state, 'review')
        self.assertEqual(card.due, source_card.due)
        self.assertEqual(round(card.interval), round(source_card.interval))
        self.assertAlmostEqual(card.stability, source_card.stability, places=3)
        self.assertAlmostEqual(card.difficulty, source_card.difficulty, places=2)
        self.assertEqual(card.reps, 2)
        self.assertAlmostEqual(card.last_review, source_card.last_review, delta=1)
        self.assertEqual(self.target.deck(card.deck_id).name, 'Languages::Spanish')
        adios_source = self.source.cards_of_note(self.built['adios'].id)[0]
        adios = self.target.cards_of_note(
            self.note_by_guid(self.target, self.built['adios'].guid).id)[0]
        self.assertEqual(adios.state, 'learning')
        self.assertEqual(adios.due, adios_source.due)
        self.assertEqual(adios.left, adios_source.left)
        gato = self.target.cards_of_note(
            self.note_by_guid(self.target, self.built['gato'].guid).id)[0]
        self.assertEqual(gato.flag, 3)
        self.assertEqual(gato.state, 'new')
        cloze = self.target.cards_of_note(
            self.note_by_guid(self.target, self.built['cloze'].guid).id)
        self.assertEqual([card.ord for card in cloze], [0, 1])
        self.assertEqual([card.suspended for card in cloze], [0, 1])
        self.assertEqual([card.buried for card in cloze], [0, 0])

    def test_new_cards_keep_their_order_at_fresh_positions(self):
        self.import_into_target()
        new = self.target.db.execute(
            "SELECT due FROM cards WHERE state = 'new' ORDER BY due").fetchall()
        dues = [row['due'] for row in new]
        self.assertEqual(len(dues), 3)
        self.assertEqual(dues, sorted(dues))
        self.assertEqual(len(set(dues)), 3)
        self.assertGreater(min(dues), 0)
        self.assertEqual(self.target.get('next_position'), max(dues))

    def test_reviews_are_in_the_revlog(self):
        self.import_into_target()
        hola = self.target.cards_of_note(
            self.note_by_guid(self.target, self.built['hola'].guid).id)[0]
        reviews = self.target.reviews_of(hola.id)
        self.assertEqual([review['rating'] for review in reviews], [GOOD, GOOD])
        source_reviews = self.source.reviews_of(
            self.source.cards_of_note(self.built['hola'].id)[0].id)
        self.assertEqual([review['id'] for review in reviews],
                         [review['id'] for review in source_reviews])
        self.assertEqual(self.target.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0], 3)

    def test_media_bytes_arrive(self):
        self.import_into_target()
        self.assertEqual(self.target.media.path('cat.png').read_bytes(), PNG)
        self.assertEqual(self.target.media.path('meow.ogg').read_bytes(), OGG)
        gato = self.note_by_guid(self.target, self.built['gato'].guid)
        self.assertEqual(gato.fields, ['gato [sound:meow.ogg]', 'cat', '<img src="cat.png">'])

    def test_a_second_import_skips_everything(self):
        self.import_into_target()
        before = (self.target.note_count(), self.target.card_count(),
                  len(self.target.decks()), len(self.target.notetypes()),
                  self.target.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0],
                  self.target.media.names())
        result = self.import_into_target()
        self.assertEqual(result.notes_skipped, 4)
        self.assertEqual(result.notes_added, 0)
        self.assertEqual(result.notes_updated, 0)
        self.assertEqual(result.cards_added, 0)
        self.assertEqual(result.decks_added, 0)
        self.assertEqual(result.notetypes_added, 0)
        self.assertEqual(result.media_added, 0)
        self.assertEqual(result.reviews_added, 0)
        after = (self.target.note_count(), self.target.card_count(),
                 len(self.target.decks()), len(self.target.notetypes()),
                 self.target.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0],
                 self.target.media.names())
        self.assertEqual(before, after)

    def test_without_scheduling_every_card_is_new_and_no_review_comes(self):
        result = self.import_into_target(with_scheduling=False)
        self.assertEqual(result.reviews_added, 0)
        states = {row['state'] for row in self.target.db.execute('SELECT state FROM cards')}
        self.assertEqual(states, {'new'})
        self.assertEqual(self.target.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0], 0)
        self.assertEqual(self.target.db.execute(
            'SELECT SUM(reps) + SUM(lapses) + SUM(suspended) FROM cards').fetchone()[0], 0)
        gato = self.target.cards_of_note(
            self.note_by_guid(self.target, self.built['gato'].guid).id)[0]
        self.assertEqual(gato.flag, 3)
        dues = [row['due'] for row in self.target.db.execute('SELECT due FROM cards')]
        self.assertEqual(len(set(dues)), 5)

    def test_into_deck_prefixes_every_deck(self):
        result = self.import_into_target(into_deck='From Anki')
        names = [deck.name for deck in self.target.decks()]
        self.assertEqual(names, ['Default', 'From Anki', 'From Anki::Languages',
                                 'From Anki::Languages::Spanish'])
        self.assertEqual(result.decks_added, 2)
        hola = self.target.cards_of_note(
            self.note_by_guid(self.target, self.built['hola'].guid).id)[0]
        self.assertEqual(self.target.deck(hola.deck_id).name, 'From Anki::Languages::Spanish')

    def test_an_older_note_is_updated_and_its_cards_kept(self):
        self.import_into_target()
        hola = self.note_by_guid(self.target, self.built['hola'].guid)
        card_before = self.target.cards_of_note(hola.id)[0]
        package = basic_package('hola', 'hello there', guid=hola.guid,
                                modified=hola.modified + 100, tags=['edited'])
        result = importing.import_contents(self.target, package)
        self.assertEqual(result.notes_updated, 1)
        self.assertEqual(result.notes_added, 0)
        self.assertEqual(result.cards_added, 0)
        hola = self.target.note(hola.id)
        self.assertEqual(hola.fields, ['hola', 'hello there'])
        self.assertEqual(hola.tags, ['edited'])
        self.assertEqual(hola.modified, package.notes[0].modified)
        card_after = self.target.cards_of_note(hola.id)[0]
        self.assertEqual(card_after, card_before)

    def test_a_newer_note_in_the_collection_is_left_alone(self):
        self.import_into_target()
        hola = self.note_by_guid(self.target, self.built['hola'].guid)
        package = basic_package('hola', 'changed', guid=hola.guid, modified=hola.modified - 100)
        result = importing.import_contents(self.target, package)
        self.assertEqual(result.notes_skipped, 1)
        self.assertEqual(self.target.note(hola.id).fields, ['hola', 'hello'])

    def test_progress_reports_fractions_in_order(self):
        calls = []
        self.import_into_target(progress=lambda fraction, message: calls.append(
            (fraction, message)))
        self.assertGreaterEqual(len(calls), 3)
        for fraction, message in calls:
            self.assertGreaterEqual(fraction, 0.0)
            self.assertLessEqual(fraction, 1.0)
            self.assertIsInstance(message, str)
        self.assertEqual(calls[0][0], 0.0)
        self.assertEqual(calls[-1][0], 1.0)
        fractions = [fraction for fraction, _message in calls]
        self.assertEqual(fractions, sorted(fractions))

    def test_a_stop_leaves_the_collection_unchanged(self):
        def snapshot():
            return (self.target.note_count(), self.target.card_count(),
                    len(self.target.decks()), len(self.target.notetypes()),
                    len(self.target.deck_configs()),
                    self.target.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0],
                    self.target.get('next_position'), self.target.media.names())

        before = snapshot()
        asked = []

        def should_stop():
            asked.append(True)
            return len(asked) >= 2

        result = self.import_into_target(should_stop=should_stop)
        self.assertTrue(result.stopped)
        self.assertEqual(result.notes_added, 0)
        self.assertEqual(snapshot(), before)
        self.assertFalse(self.target.can_undo())

    def test_a_late_stop_removes_the_media_it_copied(self):
        asked = []

        def should_stop():
            asked.append(True)
            return len(asked) >= 4  # after the notes, and their media, went in

        result = self.import_into_target(should_stop=should_stop)
        self.assertTrue(result.stopped)
        self.assertEqual(len(asked), 4)
        self.assertEqual(self.target.media.names(), [])
        self.assertEqual(self.target.note_count(), 0)

    def test_an_import_is_not_an_undo_step(self):
        self.import_into_target()
        self.assertFalse(self.target.can_undo())


class HandMadePackageTest(unittest.TestCase):
    """Packages built in memory for the cases a round trip does not cover."""

    def setUp(self):
        self.collection = self.enterContext(temporary_collection())

    def test_a_type_of_the_same_name_with_other_fields_is_added_as_imported(self):
        package = basic_package(fields=['Front', 'Back', 'Extra'])
        result = importing.import_contents(self.collection, package)
        self.assertEqual(result.notetypes_added, 1)
        imported = self.collection.notetype_by_name('Basic (imported)')
        self.assertIsNotNone(imported)
        self.assertEqual(imported.field_names(), ['Front', 'Back', 'Extra'])
        self.assertEqual(imported.original_id, TYPE_ID)
        note = self.collection.note(self.collection.find_notes('deck:"Imported deck"')[0])
        self.assertEqual(note.notetype_id, imported.id)
        self.assertEqual(note.fields, ['front', 'back', ''])
        # A second package with the same Anki id matches by that id, whatever its name.
        again = basic_package(fields=['Front', 'Back', 'Extra'], guid='guid000002')
        again.notetypes[0].name = 'Renamed'
        result = importing.import_contents(self.collection, again)
        self.assertEqual(result.notetypes_added, 0)
        self.assertEqual(result.notes_added, 1)

    def test_a_type_of_the_same_name_and_fields_is_matched(self):
        result = importing.import_contents(self.collection, basic_package())
        self.assertEqual(result.notetypes_added, 0)
        note = self.collection.note(self.collection.find_notes('deck:"Imported deck"')[0])
        self.assertEqual(note.notetype_id, self.collection.notetype_by_name('Basic').id)

    def test_decks_are_matched_case_insensitively_and_keep_descriptions(self):
        existing = self.collection.add_deck('IMPORTED DECK')
        result = importing.import_contents(self.collection, basic_package())
        self.assertEqual(result.decks_added, 0)
        card = self.collection.card(self.collection.find_cards('deck:"imported deck"')[0])
        self.assertEqual(card.deck_id, existing.id)
        self.assertEqual(self.collection.deck(existing.id).description, '')
        result = importing.import_contents(
            self.collection, basic_package(guid='guid000002', deck_name='Fresh'))
        self.assertEqual(result.decks_added, 1)
        self.assertEqual(self.collection.deck_by_name('Fresh').description, 'About this deck')

    def test_a_deck_configuration_becomes_a_preset(self):
        config = {'id': 1234, 'name': 'Fast', 'new': {'perDay': 50, 'delays': [2, 20]},
                  'rev': {'perDay': 500}, 'lapse': {'delays': [5]}, 'desiredRetention': 0.85}
        package = basic_package(config=config)
        package.decks.append(apkg.Deck(DECK_ID + 1, 'Slow', '', {'id': 1, 'name': 'Default'}))
        package.notes.append(apkg.Note(NOTE_ID + 1, 'guid000002', TYPE_ID, ['a', 'b']))
        package.cards.append(apkg.Card(CARD_ID + 1, NOTE_ID + 1, DECK_ID + 1, 0))
        importing.import_contents(self.collection, package)
        preset = next(preset for preset in self.collection.deck_configs()
                      if preset.name == 'Fast')
        self.assertEqual(preset.new_per_day, 50)
        self.assertEqual(preset.reviews_per_day, 500)
        self.assertEqual(preset.learning_steps, [2, 20])
        self.assertEqual(self.collection.deck_by_name('Imported deck').config_id, preset.id)
        self.assertEqual(self.collection.deck_by_name('Slow').config_id, DEFAULT_ID)
        # Importing again reuses the preset instead of making another.
        importing.import_contents(self.collection, basic_package(config=config, guid='g3',
                                                                 deck_name='Another'))
        self.assertEqual([p.name for p in self.collection.deck_configs()].count('Fast'), 1)

    def test_the_default_deck_goes_to_into_deck_or_our_default(self):
        package = basic_package()
        package.decks = [apkg.Deck(1, 'Default', '', None)]
        package.cards[0].deck_id = 1
        importing.import_contents(self.collection, package)
        card = self.collection.card(self.collection.find_cards('')[0])
        self.assertEqual(self.collection.deck(card.deck_id).name, 'Default')
        package = basic_package(guid='guid000002')
        package.decks = [apkg.Deck(1, 'Default', '', None)]
        package.cards[0].deck_id = 1
        importing.import_contents(self.collection, package, into_deck='Box')
        card = self.collection.card(self.collection.find_cards('deck:Box')[0])
        self.assertEqual(self.collection.deck(card.deck_id).name, 'Box')

    def test_media_is_copied_and_renamed_when_the_name_is_taken(self):
        self.collection.media.add_bytes(OTHER_PNG, 'cat.png')
        package = basic_package('<img src="cat.png"> [sound:meow.ogg]', 'back',
                                media={'cat.png': PNG, 'meow.ogg': OGG})
        result = importing.import_contents(self.collection, package)
        self.assertEqual(result.media_added, 2)
        self.assertEqual(result.problems, [])
        note = self.collection.note(self.collection.find_notes('')[0])
        self.assertEqual(note.fields[0], '<img src="cat-2.png"> [sound:meow.ogg]')
        self.assertEqual(self.collection.media.path('cat-2.png').read_bytes(), PNG)
        self.assertEqual(self.collection.media.path('cat.png').read_bytes(), OTHER_PNG)
        self.assertEqual(self.collection.media.path('meow.ogg').read_bytes(), OGG)

    def test_a_missing_media_file_is_a_problem(self):
        package = basic_package('<img src="ghost.png"><img src="ghost.png">', 'back')
        result = importing.import_contents(self.collection, package)
        self.assertEqual(result.notes_added, 1)
        self.assertEqual(result.problems, ['Missing media: ghost.png'])
        self.assertEqual(result.media_added, 0)

    def test_many_missing_files_are_summarised(self):
        front = ''.join(f'<img src="m{index:02d}.png">' for index in range(25))
        result = importing.import_contents(self.collection, basic_package(front, 'back'))
        self.assertEqual(len(result.problems), 21)
        self.assertEqual(result.problems[0], 'Missing media: m00.png')
        self.assertEqual(result.problems[-1], '… and 5 more')

    def test_a_note_without_cards_in_the_package_gets_its_cards_generated(self):
        package = basic_package()
        package.cards = []
        result = importing.import_contents(self.collection, package)
        self.assertEqual(result.notes_added, 1)
        self.assertEqual(result.cards_added, 1)
        self.assertEqual(self.collection.card_count(), 1)

    def test_a_revlog_id_that_collides_is_bumped(self):
        package = basic_package()
        package.cards[0].state = 'review'
        package.cards[0].due = self.collection.today() + 3
        package.cards[0].interval = 3
        package.cards[0].reps = 1
        package.reviews = [apkg.Review(1_700_000_000_000, CARD_ID, GOOD, 'learning',
                                       scheduled_days=3, duration_ms=1500)]
        self.collection.db.execute(
            "INSERT INTO revlog (id, card_id, rating, state) VALUES (1700000000000, 5, 3, 'new')")
        result = importing.import_contents(self.collection, package)
        self.assertEqual(result.reviews_added, 1)
        card = self.collection.card(self.collection.find_cards('')[0])
        reviews = self.collection.reviews_of(card.id)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]['id'], 1_700_000_000_001)
        self.assertEqual(reviews[0]['duration_ms'], 1500)
        self.assertEqual(card.state, 'review')
        self.assertEqual(card.due, self.collection.today() + 3)

    def test_an_unreadable_file_raises_package_error(self):
        directory = tempfile.mkdtemp(prefix='retain-import-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, 'bad.apkg')
        with open(path, 'wb') as file:
            file.write(b'not a zip')
        with self.assertRaises(apkg.PackageError):
            importing.import_package(self.collection, path)
        self.assertEqual(self.collection.note_count(), 0)


class RenameMediaReferencesTest(unittest.TestCase):

    def test_quoted_unquoted_and_sound_references(self):
        text = ('<img src="cat.png"> <img src=\'cat.png\'> <img src=cat.png> '
                '[sound:cat.png] <img src="cat.png.bak"> <img src="bigcat.png">')
        self.assertEqual(
            rename_media_references(text, 'cat.png', 'cat-2.png'),
            '<img src="cat-2.png"> <img src=\'cat-2.png\'> <img src=cat-2.png> '
            '[sound:cat-2.png] <img src="cat.png.bak"> <img src="bigcat.png">')

    def test_other_attributes_and_names_are_left_alone(self):
        text = '<a href="cat.png">cat.png</a> <object data="cat.png">'
        self.assertEqual(rename_media_references(text, 'cat.png', 'x.png'),
                         '<a href="cat.png">cat.png</a> <object data="x.png">')
        self.assertEqual(rename_media_references(text, 'cat.png', 'cat.png'), text)

    def test_an_escaped_name_is_renamed_too(self):
        text = '<img src="a&amp;b.png">'
        self.assertEqual(rename_media_references(text, 'a&b.png', 'a&b-2.png'),
                         '<img src="a&amp;b-2.png">')


class TextImportTest(unittest.TestCase):

    def setUp(self):
        self.collection = self.enterContext(temporary_collection())
        self.directory = tempfile.mkdtemp(prefix='retain-text-')
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.deck = self.collection.add_deck('Words')
        self.basic = self.collection.notetype_by_name('Basic')

    def write(self, name, text):
        path = os.path.join(self.directory, name)
        with open(path, 'w', encoding='utf-8') as file:
            file.write(text)
        return path

    def notes(self):
        return [self.collection.note(note_id) for note_id in sorted(
            row['id'] for row in self.collection.db.execute('SELECT id FROM notes'))]

    def test_a_tsv_with_header_lines_and_a_tags_column(self):
        path = self.write('words.txt', '#separator:tab\n#html:false\n#tags column:3\n'
                          'hola\thello\tgreeting basic\n'
                          'adiós\tgoodbye\t\n')
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [0, 1], tags=['spanish'])
        self.assertEqual(result.notes_added, 2)
        self.assertEqual(result.cards_added, 2)
        self.assertEqual(result.problems, [])
        notes = self.notes()
        self.assertEqual(notes[0].fields, ['hola', 'hello'])
        self.assertEqual(notes[0].tags, ['basic', 'greeting', 'spanish'])
        self.assertEqual(notes[1].tags, ['spanish'])
        card = self.collection.cards_of_note(notes[0].id)[0]
        self.assertEqual(card.deck_id, self.deck.id)
        self.assertFalse(self.collection.can_undo())

    def test_html_is_escaped_unless_allowed(self):
        path = self.write('html.txt', '#separator:tab\n#html:false\n'
                          'a <b>bold</b> one\t"line one\nline two"\n')
        importing.import_text(self.collection, path, self.basic.id, self.deck.id, [0, 1])
        note = self.notes()[0]
        self.assertEqual(note.fields, ['a &lt;b&gt;bold&lt;/b&gt; one', 'line one<br>line two'])
        importing.import_text(self.collection, path, self.basic.id, self.deck.id, [0, 1],
                              allow_html=True)
        note = self.notes()[1]
        self.assertEqual(note.fields, ['a <b>bold</b> one', 'line one\nline two'])

    def test_a_duplicate_first_field_updates_or_is_skipped(self):
        add_basic(self.collection, self.deck, 'hola', 'old meaning', ['kept'])
        path = self.write('dup.txt', '#separator:tab\n#html:true\nhola\tnew meaning\n'
                          'nuevo\tnew\n')
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [0, 1], tags=['added'])
        self.assertEqual((result.notes_added, result.notes_updated, result.notes_skipped),
                         (1, 1, 0))
        hola = self.notes()[0]
        self.assertEqual(hola.fields, ['hola', 'new meaning'])
        self.assertEqual(hola.tags, ['added', 'kept'])
        self.assertEqual(self.collection.note_count(), 2)
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [0, 1], update_existing=False)
        self.assertEqual((result.notes_added, result.notes_updated, result.notes_skipped),
                         (0, 0, 2))
        self.assertEqual(self.collection.note_count(), 2)

    def test_a_duplicate_within_the_file_is_not_added_twice(self):
        path = self.write('twice.txt', '#separator:tab\nhola\tone\nhola\ttwo\n')
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [0, 1])
        self.assertEqual((result.notes_added, result.notes_updated), (1, 1))
        self.assertEqual(self.notes()[0].fields, ['hola', 'two'])

    def test_an_empty_first_field_is_skipped_with_a_problem(self):
        path = self.write('empty.txt', '#separator:tab\n\thello\nhola\thello\n')
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [0, 1])
        self.assertEqual(result.notes_added, 1)
        self.assertEqual(result.problems, ['Row 1 skipped: its first field is empty'])

    def test_a_csv_with_a_header_row_and_a_column_map(self):
        path = self.write('words.csv', 'Back,Front\nhello,hola\n"good, bye",adiós\n')
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [1, 0])
        self.assertEqual(result.notes_added, 2)
        notes = self.notes()
        self.assertEqual(notes[0].fields, ['hola', 'hello'])
        self.assertEqual(notes[1].fields, ['adiós', 'good, bye'])

    def test_the_header_row_can_be_forced_or_kept(self):
        path = self.write('plain.txt', 'uno\tone\ndos\ttwo\n')
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [0, 1], first_line_is_header=True)
        self.assertEqual(result.notes_added, 1)
        self.assertEqual(self.notes()[0].fields, ['dos', 'two'])
        path = self.write('named.txt', 'Front\tBack\nuno\tone\n')
        result = importing.import_text(self.collection, path, self.basic.id, self.deck.id,
                                       [0, 1], first_line_is_header=False)
        self.assertEqual(result.notes_added, 2)

    def test_a_cloze_row_without_a_cloze_is_a_problem(self):
        cloze = self.collection.notetype_by_name('Cloze')
        path = self.write('cloze.txt', '#separator:tab\nno cloze here\textra\n'
                          '{{c1::one}} cloze\textra\n')
        result = importing.import_text(self.collection, path, cloze.id, self.deck.id, [0, 1])
        self.assertEqual(result.notes_added, 1)
        self.assertEqual(len(result.problems), 1)
        self.assertTrue(result.problems[0].startswith('Row 1: '))

    def test_progress_is_reported(self):
        path = self.write('p.txt', 'a\tb\n')
        calls = []
        importing.import_text(self.collection, path, self.basic.id, self.deck.id, [0, 1],
                              progress=lambda fraction, message: calls.append(fraction))
        self.assertTrue(all(0.0 <= fraction <= 1.0 for fraction in calls))
        self.assertEqual(calls[-1], 1.0)

    def test_preview_suggests_a_map_from_column_names(self):
        path = self.write('named.txt', '#separator:tab\n#columns:Tags\tBack\tFront\n'
                          '#tags column:1\ngreeting\thello\thola\nx\tbye\tadiós\n'
                          'y\tz\tw\n')
        preview = importing.preview_text(path, ['Front', 'Back'], limit=2)
        self.assertEqual(preview['suggested_map'], [2, 1])
        self.assertEqual(preview['columns'], 3)
        self.assertEqual(preview['rows'], [['greeting', 'hello', 'hola'], ['x', 'bye', 'adiós']])
        self.assertEqual(preview['info']['tags_column'], 0)

    def test_preview_falls_back_to_column_order_around_the_tags_column(self):
        path = self.write('plain.csv', 'hola,greeting,hello\n')
        preview = importing.preview_text(path, self.basic.fields)
        self.assertEqual(preview['suggested_map'], [0, 1])
        path = self.write('tagged.txt', '#separator:tab\n#tags column:2\nhola\tgreeting\thello\n')
        preview = importing.preview_text(path, self.basic.fields)
        self.assertEqual(preview['suggested_map'], [0, 2])
        preview = importing.preview_text(path, ['Front', 'Back', 'Extra'])
        self.assertEqual(preview['suggested_map'], [0, 2, None])


class DescribeResultTest(unittest.TestCase):

    def result(self, **counts):
        result = ImportResult()
        for name, value in counts.items():
            setattr(result, name, value)
        return result

    def test_added_and_updated(self):
        result = self.result(notes_added=120, cards_added=240, notes_updated=3)
        self.assertEqual(describe_result(result),
                         'Added 120 notes and 240 cards, updated 3 notes.')

    def test_singulars_skipped_and_problems(self):
        result = self.result(notes_added=1, cards_added=1, notes_skipped=1,
                             problems=['Missing media: x.png'])
        self.assertEqual(describe_result(result),
                         'Added 1 note and 1 card, skipped 1 unchanged note; 1 problem.')

    def test_nothing_and_stopped(self):
        self.assertEqual(describe_result(ImportResult()), 'Nothing was imported.')
        self.assertEqual(describe_result(self.result(problems=['a', 'b'])),
                         'Nothing was imported; 2 problems.')
        self.assertEqual(describe_result(self.result(stopped=True)),
                         'The import was stopped; nothing was changed.')


if __name__ == '__main__':
    unittest.main()
