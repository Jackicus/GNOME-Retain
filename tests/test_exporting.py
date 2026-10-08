# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""exporting.py: a collection written as a package and read back with apkg.py, one subdeck
only, without scheduling or media, and the text export's lines."""

import os
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from tests.support import Clock, add_basic, temporary_collection
from tests.test_importing import OGG, PNG, build_source
from retain import apkg, exporting
from retain.fsrs import GOOD


class ExportCase(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='retain-export-')
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.collection = self.enterContext(temporary_collection())
        self.clock = Clock()
        self.built = build_source(self.collection, self.clock)

    def path(self, name):
        return os.path.join(self.directory, name)

    def export(self, name='out.apkg', **kwargs):
        path = self.path(name)
        result = exporting.export_package(self.collection, path, **kwargs)
        package = apkg.read_package(path)
        self.addCleanup(package.close)
        return result, package


class PackageExportTest(ExportCase):

    def test_the_result_counts_what_was_written(self):
        result, package = self.export()
        self.assertEqual(result, {'notes': 4, 'cards': 5, 'media': 2, 'missing': []})
        self.assertEqual(len(package.notes), 4)
        self.assertEqual(len(package.cards), 5)
        self.assertEqual(package.created, self.collection.get('created'))

    def test_only_the_note_types_in_use_go_out(self):
        _result, package = self.export()
        self.assertEqual(sorted(notetype.name for notetype in package.notetypes),
                         ['Basic', 'Cloze', 'Vocab'])
        vocab = next(n for n in package.notetypes if n.name == 'Vocab')
        self.assertEqual(vocab.original_id, self.built['vocab'].id)
        self.assertEqual([field['name'] for field in vocab.fields],
                         ['Word', 'Meaning', 'Picture'])
        self.assertEqual(vocab.templates[0]['qfmt'], '{{Word}}<br>{{Picture}}')
        cloze = next(n for n in package.notetypes if n.name == 'Cloze')
        self.assertEqual(cloze.kind, 'cloze')

    def test_decks_keep_their_names_descriptions_and_ids(self):
        _result, package = self.export()
        by_name = {deck.name: deck for deck in package.decks}
        self.assertEqual(set(by_name), {'Default', 'Languages', 'Languages::Spanish'})
        self.assertEqual(by_name['Languages::Spanish'].description, 'Words and phrases')
        self.assertEqual(by_name['Languages::Spanish'].original_id, self.built['spanish'].id)
        self.assertEqual(by_name['Default'].original_id, 1)

    def test_notes_and_their_cards_refer_to_our_ids(self):
        _result, package = self.export()
        hola = next(note for note in package.notes if note.guid == self.built['hola'].guid)
        self.assertEqual(hola.original_id, self.built['hola'].id)
        self.assertEqual(hola.fields, ['hola', 'hello'])
        self.assertEqual(hola.tags, ['greeting'])
        self.assertEqual(hola.modified, self.built['hola'].modified)
        adios = next(note for note in package.notes if note.guid == self.built['adios'].guid)
        self.assertTrue(adios.marked)
        cards = [card for card in package.cards if card.note_id == hola.original_id]
        self.assertEqual(len(cards), 1)
        source = self.collection.cards_of_note(self.built['hola'].id)[0]
        self.assertEqual(cards[0].original_id, source.id)
        self.assertEqual(cards[0].state, 'review')
        self.assertEqual(cards[0].due, source.due)
        self.assertEqual(cards[0].deck_id, self.built['spanish'].id)
        self.assertAlmostEqual(cards[0].stability, source.stability, places=3)

    def test_reviews_go_with_the_cards(self):
        _result, package = self.export()
        source = self.collection.cards_of_note(self.built['hola'].id)[0]
        reviews = [review for review in package.reviews if review.card_id == source.id]
        self.assertEqual([review.rating for review in reviews], [GOOD, GOOD])
        self.assertEqual([review.id_ms for review in reviews],
                         [review['id'] for review in self.collection.reviews_of(source.id)])
        self.assertEqual(len(package.reviews), 3)

    def test_media_is_packed_and_missing_files_listed(self):
        self.collection.add_note(self.built['vocab'].id, self.built['spanish'].id,
                                 ['perro', 'dog', '<img src="dog.png">'])
        result, package = self.export()
        self.assertEqual(result['media'], 2)
        self.assertEqual(result['missing'], ['dog.png'])
        self.assertEqual(sorted(package.media_names()), ['cat.png', 'meow.ogg'])
        self.assertEqual(package.read_media('cat.png'), PNG)
        self.assertEqual(package.read_media('meow.ogg'), OGG)

    def test_without_media(self):
        result, package = self.export(include_media=False)
        self.assertEqual(result['media'], 0)
        self.assertEqual(package.media_names(), [])

    def test_without_scheduling_every_card_is_new(self):
        result, package = self.export(with_scheduling=False)
        self.assertEqual(result['cards'], 5)
        self.assertEqual({card.state for card in package.cards}, {'new'})
        self.assertEqual(package.reviews, [])
        self.assertEqual({card.reps for card in package.cards}, {0})

    def test_one_subdeck_only(self):
        result, package = self.export(deck_id=self.built['spanish'].id)
        self.assertEqual(result['notes'], 3)
        self.assertEqual(result['cards'], 4)
        guids = {note.guid for note in package.notes}
        self.assertIn(self.built['hola'].guid, guids)
        self.assertNotIn(self.built['adios'].guid, guids)
        names = {deck.name for deck in package.decks}
        self.assertIn('Languages::Spanish', names)
        self.assertEqual(sorted(notetype.name for notetype in package.notetypes),
                         ['Basic', 'Cloze', 'Vocab'])
        self.assertEqual(len(package.reviews), 2)

    def test_decks_keep_their_presets(self):
        from retain.deck_config import DeckConfig

        preset = DeckConfig(name='Invented Preset', new_per_day=7, learning_steps=[2, 15],
                            desired_retention=0.85, load_balancing=False,
                            easy_days=[1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.0])
        preset = self.collection.add_deck_config(preset)
        spanish = self.collection.deck_by_name('Languages::Spanish')
        self.collection.set_deck_config(spanish.id, preset.id)
        _result, package = self.export()
        configs = {deck.name: deck.config for deck in package.decks}
        self.assertEqual(configs['Languages::Spanish']['name'], 'Invented Preset')
        self.assertEqual(configs['Languages::Spanish']['new']['perDay'], 7)
        self.assertEqual(configs['Languages']['id'], 1)  # the default preset is Anki's 1
        with temporary_collection() as other:
            from retain import importing

            importing.import_package(other, self.path('out.apkg'))
            imported = other.config_for_deck(other.deck_by_name('Languages::Spanish').id)
            self.assertEqual((imported.name, imported.new_per_day, imported.learning_steps,
                              imported.desired_retention, imported.load_balancing,
                              imported.easy_days),
                             ('Invented Preset', 7, [2, 15], 0.85, False,
                              [1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.0]))

    def test_progress_is_reported_in_order(self):
        calls = []
        exporting.export_package(self.collection, self.path('p.apkg'),
                                 progress=lambda fraction, message: calls.append(fraction))
        self.assertTrue(all(0.0 <= fraction <= 1.0 for fraction in calls))
        self.assertEqual(calls, sorted(calls))
        self.assertEqual(calls[0], 0.0)
        self.assertEqual(calls[-1], 1.0)

    def test_an_unwritable_path_raises_package_error(self):
        with self.assertRaises(apkg.PackageError):
            exporting.export_package(self.collection, self.path('missing/dir/out.apkg'))


class TextExportTest(ExportCase):

    def lines(self, name):
        with open(self.path(name), encoding='utf-8') as file:
            return file.read().splitlines()

    def test_header_lines_fields_and_tags(self):
        count = exporting.export_text(self.collection, self.path('all.txt'))
        self.assertEqual(count, 4)
        lines = self.lines('all.txt')
        self.assertEqual(lines[:3], ['#separator:tab', '#html:true', '#tags column:4'])
        body = lines[3:]
        self.assertEqual(len(body), 4)
        self.assertIn('hola\thello\t\tgreeting', body)
        self.assertIn('adiós\tgoodbye\t\t', body)
        # A field holding a quote is quoted the CSV way, as Anki writes it.
        self.assertIn('gato [sound:meow.ogg]\tcat\t"<img src=""cat.png"">"\tpets', body)

    def test_one_type_without_tags_and_without_html(self):
        add_basic(self.collection, self.built['spanish'], 'dos\nlíneas', '<b>bold</b>')
        count = exporting.export_text(self.collection, self.path('basic.txt'),
                                      include_tags=False, include_html=False,
                                      notetype_id=self.collection.notetype_by_name('Basic').id)
        self.assertEqual(count, 3)
        lines = self.lines('basic.txt')
        self.assertEqual(lines[:2], ['#separator:tab', '#html:false'])
        self.assertEqual(lines[2:], ['hola\thello', 'adiós\tgoodbye', 'dos líneas\tbold'])

    def test_newlines_become_breaks_with_html(self):
        add_basic(self.collection, self.built['spanish'], 'dos\nlíneas', 'two')
        exporting.export_text(self.collection, self.path('br.txt'),
                              deck_id=self.built['spanish'].id, include_tags=False,
                              notetype_id=self.collection.notetype_by_name('Basic').id)
        self.assertEqual(self.lines('br.txt')[2:], ['hola\thello', 'dos<br>líneas\ttwo'])

    def test_a_subdeck_only_and_the_file_reads_back(self):
        count = exporting.export_text(self.collection, self.path('sub.txt'),
                                      deck_id=self.built['spanish'].id)
        self.assertEqual(count, 3)
        rows, info = apkg.read_text_rows(self.path('sub.txt'))
        self.assertEqual(info['separator'], '\t')
        self.assertTrue(info['html'])
        self.assertEqual(info['tags_column'], 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ['hola', 'hello', '', 'greeting'])


if __name__ == '__main__':
    unittest.main()
