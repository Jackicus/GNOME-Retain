# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""collection.py on a temporary file: decks, notes, cards, answers, the undo stack and the
`changed` signal. Everything is invented."""

import os
import pathlib
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from tests.support import Clock, add_basic, add_cloze, temporary_collection
from retain import notetypes, schema
from retain.collection import (BACKUP_COUNT, UNDO_LIMIT, Collection, CollectionError,
                               default_data_dir, natural_key)
from retain.deck_config import DEFAULT_ID, LEECH_TAG, DeckConfig
from retain.notetypes import NoteType

DAY = 86400


class CollectionCase(unittest.TestCase):
    """A fresh collection, the stock Basic and Cloze types, and a recorder for `changed`."""

    def setUp(self):
        self.collection = self.enterContext(temporary_collection())
        self.basic = self.collection.notetype_by_name('Basic')
        self.cloze = self.collection.notetype_by_name('Cloze')
        self.clock = Clock()

    def changes(self):
        """A list the kinds of every `changed` emitted from now on land in."""
        kinds = []
        self.collection.connect('changed', lambda _collection, kind: kinds.append(kind))
        return kinds

    def card_of(self, note):
        return self.collection.cards_of_note(note.id)[0]


class NewCollectionTest(CollectionCase):

    def test_a_new_collection_has_the_stock_types_a_default_deck_and_preset(self):
        names = [notetype.name for notetype in self.collection.notetypes()]
        self.assertEqual(sorted(names), sorted(stock.name for stock in notetypes.all_stock()))
        self.assertEqual(len(names), len(notetypes.STOCK_KINDS))
        self.assertEqual(self.basic.kind, 'standard')
        self.assertEqual(self.cloze.kind, 'cloze')
        decks = self.collection.decks()
        self.assertEqual([deck.name for deck in decks], ['Default'])
        self.assertEqual(decks[0].id, 1)
        self.assertEqual(decks[0].config_id, DEFAULT_ID)
        preset = self.collection.deck_config(DEFAULT_ID)
        self.assertEqual(preset.name, 'Default')
        self.assertEqual(preset.new_per_day, 20)
        self.assertEqual(self.collection.config_for_deck(1).id, DEFAULT_ID)
        self.assertEqual(self.collection.note_count(), 0)
        self.assertEqual(self.collection.card_count(), 0)
        self.assertFalse(self.collection.can_undo())

    def test_reopening_keeps_the_data_without_duplicating_the_stock(self):
        directory = tempfile.mkdtemp(prefix='retain-test-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = pathlib.Path(directory) / 'collection.sqlite'
        first = Collection(path, backups=False)
        deck = first.add_deck('Kept')
        add_basic(first, deck, 'hola', 'hello')
        first.close()
        first.close()  # a second close is harmless
        second = Collection(path, backups=False)
        try:
            self.assertEqual(second.deck_by_name('Kept').id, deck.id)
            self.assertEqual(second.note_count(), 1)
            self.assertEqual(len(second.notetypes()), len(notetypes.STOCK_KINDS))
            self.assertEqual(len(second.deck_configs()), 1)
        finally:
            second.close()

    def test_default_data_dir_honours_the_override_and_the_profile(self):
        with mock.patch.dict(os.environ, {'RETAIN_DATA_DIR': '/elsewhere/retain-data'}):
            self.assertEqual(default_data_dir(), pathlib.Path('/elsewhere/retain-data'))
            self.assertEqual(default_data_dir('development'),
                             pathlib.Path('/elsewhere/retain-data'))
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('RETAIN_DATA_DIR', 'XDG_DATA_HOME')}
        with mock.patch.dict(os.environ, {**environment, 'XDG_DATA_HOME': '/data'}, clear=True):
            self.assertEqual(default_data_dir(), pathlib.Path('/data/retain'))
            self.assertEqual(default_data_dir('development'), pathlib.Path('/data/retain-devel'))
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(default_data_dir(), pathlib.Path.home() / '.local/share/retain')


class DeckTest(CollectionCase):

    def test_add_deck_creates_parents_and_returns_an_existing_deck(self):
        verbs = self.collection.add_deck('Languages::Spanish::Verbs')
        names = [deck.name for deck in self.collection.decks()]
        self.assertEqual(names, ['Default', 'Languages', 'Languages::Spanish',
                                 'Languages::Spanish::Verbs'])
        self.assertEqual(verbs.basename, 'Verbs')
        self.assertEqual(verbs.parent_name, 'Languages::Spanish')
        self.assertEqual(verbs.level, 2)
        self.assertEqual(self.collection.add_deck('Languages::Spanish::Verbs').id, verbs.id)
        self.assertEqual(self.collection.add_deck(' Languages :: Spanish ').name,
                         'Languages::Spanish')
        self.assertEqual(self.collection.add_deck('languages').name, 'Languages')
        self.assertEqual(len(self.collection.decks()), 4)
        with self.assertRaises(CollectionError):
            self.collection.add_deck('  ::  ')

    def test_add_deck_is_one_undo_step_for_the_whole_path(self):
        kinds = self.changes()
        self.collection.add_deck('Languages::Spanish')
        self.assertEqual(kinds, ['decks'])
        self.assertEqual(self.collection.undo_label, 'Add Deck')
        self.assertEqual(self.collection.undo(), 'Add Deck')
        self.assertEqual([deck.name for deck in self.collection.decks()], ['Default'])
        self.assertEqual(kinds, ['decks', 'decks'])

    def test_deck_tree_nests_and_sorts_naturally(self):
        self.collection.add_deck('Deck 10')
        self.collection.add_deck('Deck 2::Sub')
        self.collection.add_deck('deck 3')
        roots = self.collection.deck_tree()
        self.assertEqual([node.deck.name for node in roots],
                         ['Deck 2', 'deck 3', 'Deck 10', 'Default'])
        self.assertEqual([node.level for node in roots], [0, 0, 0, 0])
        sub = roots[0].children
        self.assertEqual([node.deck.name for node in sub], ['Deck 2::Sub'])
        self.assertEqual(sub[0].level, 1)
        self.assertEqual(sub[0].children, [])
        self.assertEqual([node.deck.basename for node in roots[0].walk()], ['Deck 2', 'Sub'])
        self.assertEqual(roots[0].total, 0)
        self.assertLess(natural_key('Deck 2'), natural_key('Deck 10'))
        self.assertEqual(natural_key('Deck 2'), natural_key('deck 2'))

    def test_deck_and_children(self):
        languages = self.collection.add_deck('Languages')
        spanish = self.collection.add_deck('Languages::Spanish')
        verbs = self.collection.add_deck('Languages::Spanish::Verbs')
        self.collection.add_deck('Languages Two')
        self.collection.add_deck('Science')
        self.assertEqual(set(self.collection.deck_and_children(languages.id)),
                         {languages.id, spanish.id, verbs.id})
        self.assertEqual(self.collection.deck_and_children(verbs.id), [verbs.id])
        self.assertEqual(self.collection.deck_and_children(424242), [])

    def test_rename_deck_moves_its_children(self):
        languages = self.collection.add_deck('Languages')
        verbs = self.collection.add_deck('Languages::Spanish::Verbs')
        self.collection.add_deck('Languages Two')
        self.collection.rename_deck(languages.id, 'Tongues')
        self.assertEqual(self.collection.deck(languages.id).name, 'Tongues')
        self.assertEqual(self.collection.deck(verbs.id).name, 'Tongues::Spanish::Verbs')
        self.assertIsNone(self.collection.deck_by_name('Languages'))
        self.assertIsNotNone(self.collection.deck_by_name('Languages Two'))
        self.assertEqual(self.collection.undo(), 'Rename Deck')
        self.assertEqual(self.collection.deck(verbs.id).name, 'Languages::Spanish::Verbs')
        self.assertEqual(self.collection.deck(languages.id).name, 'Languages')

    def test_rename_deck_under_another_parent_creates_it(self):
        science = self.collection.add_deck('Science')
        self.collection.rename_deck(science.id, 'School::Science')
        self.assertEqual(self.collection.deck(science.id).parent_name, 'School')
        self.assertIsNotNone(self.collection.deck_by_name('School'))
        self.collection.rename_deck(science.id, 'School::Physics')
        self.assertEqual(self.collection.deck(science.id).name, 'School::Physics')
        self.collection.rename_deck(science.id, 'School::physics')
        self.assertEqual(self.collection.deck(science.id).name, 'School::physics')

    def test_rename_deck_refuses_a_duplicate_and_self_nesting(self):
        languages = self.collection.add_deck('Languages')
        self.collection.add_deck('Science')
        with self.assertRaises(CollectionError):
            self.collection.rename_deck(languages.id, 'science')
        with self.assertRaises(CollectionError):
            self.collection.rename_deck(languages.id, 'Languages::Spanish::Deep')
        with self.assertRaises(CollectionError):
            self.collection.rename_deck(languages.id, '')
        with self.assertRaises(CollectionError):
            self.collection.rename_deck(424242, 'Anything')
        self.assertEqual(self.collection.deck(languages.id).name, 'Languages')
        self.assertFalse(self.collection.can_undo() and self.collection.undo_label == 'Rename Deck')

    def test_remove_deck_deletes_cards_and_orphaned_notes_and_undo_restores(self):
        spanish = self.collection.add_deck('Spanish')
        science = self.collection.add_deck('Science')
        whole = add_basic(self.collection, spanish, 'hola', 'hello')
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        shared = self.collection.add_note(reversed_type.id, spanish.id, ['agua', 'water'])
        shared_cards = self.collection.cards_of_note(shared.id)
        self.collection.set_deck([shared_cards[1].id], science.id)
        self.assertEqual(self.collection.card_count(), 3)
        self.collection.remove_deck(spanish.id)
        self.assertIsNone(self.collection.deck(spanish.id))
        self.assertIsNone(self.collection.note(whole.id))
        self.assertIsNotNone(self.collection.note(shared.id))
        self.assertEqual([card.id for card in self.collection.cards_of_note(shared.id)],
                         [shared_cards[1].id])
        self.assertEqual(self.collection.card_count(), 1)
        self.assertEqual(self.collection.undo(), 'Delete Deck')
        self.assertEqual(self.collection.deck(spanish.id).name, 'Spanish')
        self.assertEqual(self.collection.note(whole.id).fields, ['hola', 'hello'])
        self.assertEqual(self.collection.card_count(), 3)
        self.assertEqual([card.id for card in self.collection.cards_of_note(shared.id)],
                         [card.id for card in shared_cards])
        self.assertEqual(self.collection.deck_card_count(spanish.id), 2)

    def test_removing_the_last_deck_leaves_a_default(self):
        self.collection.remove_deck(1)
        self.assertEqual([deck.name for deck in self.collection.decks()], ['Default'])
        self.collection.undo()
        self.assertEqual([deck.id for deck in self.collection.decks()], [1])

    def test_update_deck_stores_the_description(self):
        deck = self.collection.add_deck('Spanish')
        deck.description = 'Words from the course'
        self.collection.update_deck(deck)
        self.assertEqual(self.collection.deck(deck.id).description, 'Words from the course')
        self.assertEqual(self.collection.undo(), 'Edit Deck')
        self.assertEqual(self.collection.deck(deck.id).description, '')
        self.collection.set_deck_collapsed(deck.id, True)
        self.assertEqual(self.collection.deck(deck.id).collapsed, 1)
        self.assertEqual(self.collection.undo_label, 'Add Deck')  # collapsing is not a step

    def test_presets_are_added_assigned_and_removed(self):
        deck = self.collection.add_deck('Spanish')
        preset = self.collection.add_deck_config(DeckConfig(name='Fast', new_per_day=5))
        self.assertIsNotNone(preset.id)
        self.assertEqual([config.name for config in self.collection.deck_configs()],
                         ['Default', 'Fast'])
        self.collection.set_deck_config(deck.id, preset.id)
        self.assertEqual(self.collection.config_for_deck(deck.id).new_per_day, 5)
        preset.new_per_day = 6
        self.collection.update_deck_config(preset)
        self.assertEqual(self.collection.config_for_deck(deck.id).new_per_day, 6)
        self.assertEqual(self.collection.undo(), 'Change Deck Options')
        self.assertEqual(self.collection.config_for_deck(deck.id).new_per_day, 5)
        with self.assertRaises(CollectionError):
            self.collection.remove_deck_config(DEFAULT_ID)
        self.collection.remove_deck_config(preset.id)
        self.assertEqual(self.collection.config_for_deck(deck.id).id, DEFAULT_ID)
        self.assertIsNone(self.collection.deck_config(preset.id))
        self.assertEqual(self.collection.undo(), 'Delete Preset')
        self.assertEqual(self.collection.config_for_deck(deck.id).id, preset.id)


class NoteTest(CollectionCase):

    def setUp(self):
        super().setUp()
        self.deck = self.collection.add_deck('Spanish')

    def test_a_basic_note_makes_one_card(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello')
        self.assertIsNotNone(note.id)
        self.assertEqual(len(note.guid), 10)
        self.assertEqual(note.fields, ['hola', 'hello'])
        cards = self.collection.cards_of_note(note.id)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual((card.ord, card.state, card.deck_id), (0, 'new', self.deck.id))
        self.assertTrue(card.is_new)
        self.assertFalse(card.in_learning)
        self.assertGreater(card.due, 0)
        second = add_basic(self.collection, self.deck, 'adios', 'bye')
        self.assertEqual(self.card_of(second).due, card.due + 1)
        self.assertEqual(self.collection.note_count(), 2)
        self.assertEqual(self.collection.card_count(), 2)
        self.assertEqual(self.collection.notes([second.id, note.id]), [second, note])

    def test_a_reversed_note_makes_two_cards(self):
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        note = self.collection.add_note(reversed_type.id, self.deck.id, ['agua', 'water'])
        self.assertEqual([card.ord for card in self.collection.cards_of_note(note.id)], [0, 1])

    def test_an_optional_reversed_card_needs_add_reverse(self):
        optional = self.collection.notetype_by_name('Basic (optional reversed card)')
        plain = self.collection.add_note(optional.id, self.deck.id, ['uno', 'one', ''])
        self.assertEqual([card.ord for card in self.collection.cards_of_note(plain.id)], [0])
        both = self.collection.add_note(optional.id, self.deck.id, ['dos', 'two', 'y'])
        self.assertEqual([card.ord for card in self.collection.cards_of_note(both.id)], [0, 1])

    def test_a_cloze_note_makes_a_card_per_ordinal(self):
        note = add_cloze(self.collection, self.deck, '{{c1::uno}} {{c3::tres}} {{c1::one}}')
        self.assertEqual([card.ord for card in self.collection.cards_of_note(note.id)], [0, 2])
        single = add_cloze(self.collection, self.deck, 'only {{c2::dos}}', 'extra')
        self.assertEqual([card.ord for card in self.collection.cards_of_note(single.id)], [1])

    def test_add_note_refuses_an_empty_front_or_no_cloze(self):
        with self.assertRaises(CollectionError):
            add_basic(self.collection, self.deck, '', 'hello')
        with self.assertRaises(CollectionError):
            add_basic(self.collection, self.deck, '<br> ', 'hello')
        with self.assertRaises(CollectionError):
            add_cloze(self.collection, self.deck, 'no cloze here')
        with self.assertRaises(CollectionError):
            self.collection.add_note(424242, self.deck.id, ['hola', 'hello'])
        self.assertEqual(self.collection.note_count(), 0)
        self.assertNotEqual(self.collection.undo_label, 'Add Note')

    def test_fields_are_padded_and_cut_to_the_type(self):
        short = self.collection.add_note(self.basic.id, self.deck.id, ['solo'])
        self.assertEqual(self.collection.note(short.id).fields, ['solo', ''])
        long = self.collection.add_note(self.basic.id, self.deck.id, ['a', 'b', 'c'])
        self.assertEqual(self.collection.note(long.id).fields, ['a', 'b'])
        none = self.collection.add_note(self.basic.id, self.deck.id, ['x', None])
        self.assertEqual(self.collection.note(none.id).fields, ['x', ''])

    def test_update_note_adds_and_removes_cloze_cards(self):
        note = add_cloze(self.collection, self.deck, 'The {{c1::cat}}')
        first = self.card_of(note)
        note.fields[0] = 'The {{c1::cat}} sat on the {{c2::mat}}'
        self.collection.update_note(note)
        cards = self.collection.cards_of_note(note.id)
        self.assertEqual([card.ord for card in cards], [0, 1])
        self.assertEqual(cards[0].id, first.id)
        self.assertEqual(cards[1].deck_id, self.deck.id)
        note.fields[0] = 'The cat sat on the {{c2::mat}}'
        self.collection.update_note(note)
        self.assertEqual([card.ord for card in self.collection.cards_of_note(note.id)], [1])
        self.assertIsNone(self.collection.card(first.id))

    def test_update_note_keeps_a_reviewed_card_of_a_deleted_cloze(self):
        note = add_cloze(self.collection, self.deck, '{{c1::uno}} {{c2::dos}}')
        first = self.card_of(note)
        self.collection.db.execute('UPDATE cards SET reps = 1 WHERE id = ?', (first.id,))
        note.fields[0] = 'uno {{c2::dos}}'
        self.collection.update_note(note)
        self.assertEqual([card.ord for card in self.collection.cards_of_note(note.id)], [0, 1])

    def test_update_note_stores_fields_and_tags_and_can_be_undone(self):
        note = add_cloze(self.collection, self.deck, '{{c1::uno}}', tags=['numbers'])
        note.fields = ['{{c1::uno}} {{c2::dos}}', 'extra']
        note.tags = ['spanish']
        kinds = self.changes()
        self.collection.update_note(note)
        stored = self.collection.note(note.id)
        self.assertEqual(stored.fields, ['{{c1::uno}} {{c2::dos}}', 'extra'])
        self.assertEqual(stored.tags, ['spanish'])
        self.assertEqual(len(self.collection.cards_of_note(note.id)), 2)
        self.assertEqual(kinds, ['cards', 'notes'])
        self.assertEqual(self.collection.undo(), 'Edit Note')
        stored = self.collection.note(note.id)
        self.assertEqual(stored.fields, ['{{c1::uno}}', ''])
        self.assertEqual(stored.tags, ['numbers'])
        self.assertEqual(len(self.collection.cards_of_note(note.id)), 1)

    def test_sort_field_is_stripped_html_lowercased(self):
        note = add_basic(self.collection, self.deck, '<b>Hola</b>&nbsp;<i>Mundo</i><br>!', 'x')
        self.assertEqual(self.collection.note(note.id).sort_field, 'hola mundo !')
        cloze = add_cloze(self.collection, self.deck, 'The {{c1::Cat}}')
        self.assertEqual(self.collection.note(cloze.id).sort_field, 'the {{c1::cat}}')

    def test_tags_are_normalised_and_listed(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello',
                         tags=['Verbs', ' a ', 'two words', 'a', ''])
        self.assertEqual(self.collection.note(note.id).tags, ['a', 'two_words', 'Verbs'])
        add_basic(self.collection, self.deck, 'adios', 'bye', tags=['zeta', 'Beta'])
        add_basic(self.collection, self.deck, 'gato', 'cat')
        self.assertEqual(self.collection.all_tags(), ['a', 'Beta', 'two_words', 'Verbs', 'zeta'])
        row = self.collection.db.execute('SELECT tags FROM notes WHERE id = ?',
                                         (note.id,)).fetchone()
        self.assertEqual(row['tags'], ' a two_words Verbs ')

    def test_set_tags_adds_and_removes(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello', tags=['a', 'b'])
        other = add_basic(self.collection, self.deck, 'adios', 'bye', tags=['b'])
        self.collection.set_tags([note.id, other.id], add=['x', 'A'], remove=['B'])
        self.assertEqual(self.collection.note(note.id).tags, ['a', 'x'])  # A is a
        self.assertEqual(self.collection.note(other.id).tags, ['A', 'x'])
        self.assertEqual(self.collection.undo(), 'Change Tags')
        self.assertEqual(self.collection.note(note.id).tags, ['a', 'b'])
        self.assertEqual(self.collection.note(other.id).tags, ['b'])

    def test_set_marked(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello')
        self.assertFalse(self.collection.note(note.id).marked)
        self.collection.set_marked([note.id], True)
        self.assertTrue(self.collection.note(note.id).marked)
        self.assertEqual(self.collection.find_notes('tag:marked'), [note.id])
        self.assertEqual(self.collection.undo(), 'Mark')
        self.assertFalse(self.collection.note(note.id).marked)
        self.collection.set_marked([note.id], True)
        self.collection.set_marked([note.id], False)
        self.assertEqual(self.collection.undo_label, 'Unmark')

    def test_find_duplicates_compares_the_stripped_first_field(self):
        note = add_basic(self.collection, self.deck, '<b>Hola</b>', 'hello')
        add_basic(self.collection, self.deck, 'adios', 'bye')
        cloze = add_cloze(self.collection, self.deck, 'hola {{c1::x}}')
        self.assertEqual(self.collection.find_duplicates(self.basic.id, 'hola '), [note.id])
        self.assertEqual(self.collection.find_duplicates(self.basic.id, 'hola', note.id), [])
        self.assertEqual(self.collection.find_duplicates(self.basic.id, '<br>'), [])
        self.assertEqual(self.collection.find_duplicates(self.cloze.id, 'hola {{c1::x}}'),
                         [cloze.id])

    def test_remove_notes_deletes_their_cards_and_undo_restores(self):
        note = add_cloze(self.collection, self.deck, '{{c1::a}} {{c2::b}}')
        kept = add_basic(self.collection, self.deck, 'hola', 'hello')
        self.collection.remove_notes([note.id])
        self.assertIsNone(self.collection.note(note.id))
        self.assertEqual(self.collection.cards_of_note(note.id), [])
        self.assertEqual(self.collection.card_count(), 1)
        self.assertEqual(self.collection.undo(), 'Delete Notes')
        self.assertEqual(self.collection.note(note.id), note)
        self.assertEqual(len(self.collection.cards_of_note(note.id)), 2)
        self.assertIsNotNone(self.collection.note(kept.id))

    def test_remove_cards_removes_a_note_left_without_cards(self):
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        note = self.collection.add_note(reversed_type.id, self.deck.id, ['agua', 'water'])
        first, second = self.collection.cards_of_note(note.id)
        self.collection.remove_cards([first.id])
        self.assertIsNotNone(self.collection.note(note.id))
        self.collection.remove_cards([second.id])
        self.assertIsNone(self.collection.note(note.id))
        self.assertEqual(self.collection.undo(), 'Delete Cards')
        self.assertIsNotNone(self.collection.note(note.id))
        self.assertEqual([card.id for card in self.collection.cards_of_note(note.id)],
                         [second.id])


class SearchTest(CollectionCase):

    def setUp(self):
        super().setUp()
        self.spanish = self.collection.add_deck('Spanish')
        self.verbs = self.collection.add_deck('Spanish::Verbs')
        self.science = self.collection.add_deck('Science')
        self.hola = add_basic(self.collection, self.spanish, 'hola', 'hello', tags=['greeting'])
        self.hablar = add_basic(self.collection, self.verbs, 'hablar', 'to speak',
                                tags=['verbs'])
        self.atom = add_basic(self.collection, self.science, 'atom', 'smallest', tags=['physics'])

    def test_find_cards_with_deck_tag_and_state(self):
        hola, hablar, atom = (self.card_of(note) for note in (self.hola, self.hablar, self.atom))
        self.assertEqual(self.collection.find_cards('deck:Spanish'), [hola.id, hablar.id])
        self.assertEqual(self.collection.find_cards('deck:Spanish::Verbs'), [hablar.id])
        self.assertEqual(self.collection.find_cards('tag:verbs'), [hablar.id])
        self.assertEqual(self.collection.find_cards('tag:VERBS or tag:physics'),
                         [hablar.id, atom.id])
        self.assertEqual(self.collection.find_cards('is:new'), [hola.id, hablar.id, atom.id])
        self.collection.set_due([atom.id], 0, now=self.clock.now)
        self.assertEqual(self.collection.find_cards('-is:new'), [atom.id])
        self.assertEqual(self.collection.find_cards('is:review'), [atom.id])
        self.assertEqual(self.collection.find_cards('hablar'), [hablar.id])
        self.assertEqual(self.collection.find_cards('deck:Nowhere'), [])
        self.assertEqual(self.collection.find_cards('is:new', order='cards.id DESC'),
                         [hablar.id, hola.id])

    def test_find_notes_lists_each_note_once(self):
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        agua = self.collection.add_note(reversed_type.id, self.spanish.id, ['agua', 'water'])
        self.assertEqual(self.collection.find_notes('deck:Spanish'),
                         sorted([self.hola.id, self.hablar.id, agua.id]))
        self.assertEqual(self.collection.find_notes('agua'), [agua.id])

    def test_browse_rows_join_deck_and_notetype_names(self):
        rows = self.collection.browse_rows('deck:Spanish')
        self.assertEqual([row['sort_field'] for row in rows], ['hablar', 'hola'])
        self.assertEqual(rows[0]['deck_name'], 'Spanish::Verbs')
        self.assertEqual(rows[1]['deck_name'], 'Spanish')
        self.assertEqual({row['notetype_name'] for row in rows}, {'Basic'})
        self.assertEqual(rows[0]['note_fields'], 'hablar\x1fto speak')
        self.assertEqual(rows[0]['tags'], ' verbs ')
        self.assertEqual(rows[0]['id'], self.card_of(self.hablar).id)
        descending = self.collection.browse_rows('deck:Spanish', descending=True)
        self.assertEqual([row['sort_field'] for row in descending], ['hola', 'hablar'])
        self.assertEqual(len(self.collection.browse_rows('', limit=2)), 2)
        by_deck = self.collection.browse_rows('', order='decks.name')
        self.assertEqual([row['deck_name'] for row in by_deck],
                         ['Science', 'Spanish', 'Spanish::Verbs'])


class CardTest(CollectionCase):

    def setUp(self):
        super().setUp()
        self.deck = self.collection.add_deck('Spanish')
        self.note = add_basic(self.collection, self.deck, 'hola', 'hello')
        self.card = self.card_of(self.note)

    def test_set_deck_moves_cards(self):
        science = self.collection.add_deck('Science')
        self.collection.set_deck([self.card.id], science.id)
        self.assertEqual(self.collection.card(self.card.id).deck_id, science.id)
        self.assertEqual(self.collection.find_cards('deck:Science'), [self.card.id])
        self.assertEqual(self.collection.undo(), 'Change Deck')
        self.assertEqual(self.collection.card(self.card.id).deck_id, self.deck.id)

    def test_set_flag(self):
        self.collection.set_flag([self.card.id], 3)
        self.assertEqual(self.collection.card(self.card.id).flag, 3)
        self.assertEqual(self.collection.find_cards('flag:3'), [self.card.id])
        self.assertEqual(self.collection.undo_label, 'Flag')
        self.collection.set_flag([self.card.id], 0)
        self.assertEqual(self.collection.card(self.card.id).flag, 0)
        self.assertEqual(self.collection.undo(), 'Remove Flag')
        self.assertEqual(self.collection.card(self.card.id).flag, 3)

    def test_suspend_and_unsuspend(self):
        self.collection.suspend([self.card.id])
        self.assertEqual(self.collection.card(self.card.id).suspended, 1)
        self.assertEqual(self.collection.find_cards('is:suspended'), [self.card.id])
        self.collection.unsuspend([self.card.id])
        self.assertEqual(self.collection.card(self.card.id).suspended, 0)
        self.assertEqual(self.collection.undo(), 'Unsuspend')
        self.assertEqual(self.collection.card(self.card.id).suspended, 1)
        self.assertEqual(self.collection.undo(), 'Suspend')
        self.assertEqual(self.collection.card(self.card.id).suspended, 0)

    def test_bury_and_unbury(self):
        self.collection.bury([self.card.id])
        self.assertEqual(self.collection.card(self.card.id).buried, self.collection.today())
        self.assertEqual(self.collection.find_cards('is:buried'), [self.card.id])
        self.collection.unbury([self.card.id])
        self.assertEqual(self.collection.card(self.card.id).buried, 0)
        self.assertEqual(self.collection.undo(), 'Unbury')
        self.assertEqual(self.collection.undo(), 'Bury')
        self.assertEqual(self.collection.card(self.card.id).buried, 0)

    def test_unbury_past_brings_back_a_card_buried_yesterday(self):
        today = self.collection.today()
        other = self.card_of(add_basic(self.collection, self.deck, 'adios', 'bye'))
        self.collection.db.execute('UPDATE cards SET buried = ? WHERE id = ?',
                                   (today - 1, self.card.id))
        self.collection.db.execute('UPDATE cards SET buried = ? WHERE id = ?', (today, other.id))
        kinds = self.changes()
        self.collection.unbury_past()
        self.assertEqual(self.collection.card(self.card.id).buried, 0)
        self.assertEqual(self.collection.card(other.id).buried, today)
        self.assertEqual(kinds, ['cards'])
        self.collection.unbury_past()
        self.assertEqual(kinds, ['cards'])

    def test_unbury_deck_brings_back_todays_buried_cards(self):
        sub = self.collection.add_deck('Spanish::Verbs')
        other = self.card_of(add_basic(self.collection, sub, 'hablar', 'to speak'))
        elsewhere = self.card_of(add_basic(self.collection, self.collection.add_deck('Science'),
                                           'atom', 'smallest'))
        self.collection.bury([self.card.id, other.id, elsewhere.id])
        self.collection.unbury_deck(self.deck.id)
        self.assertEqual(self.collection.card(self.card.id).buried, 0)
        self.assertEqual(self.collection.card(other.id).buried, 0)
        self.assertNotEqual(self.collection.card(elsewhere.id).buried, 0)

    def test_forget_resets_to_new_and_logs_a_manual_review(self):
        self.collection.set_due([self.card.id], 5, now=self.clock.now)
        self.collection.db.execute(
            'UPDATE cards SET stability = 12.5, difficulty = 6, reps = 4, lapses = 1, '
            'last_review = ? WHERE id = ?', (int(self.clock.now), self.card.id))
        before = self.collection.card(self.card.id)
        self.assertEqual(before.state, 'review')
        self.collection.forget([self.card.id])
        card = self.collection.card(self.card.id)
        self.assertEqual(card.state, 'new')
        self.assertEqual((card.interval, card.stability, card.difficulty, card.left,
                          card.last_review), (0, 0, 0, 0, 0))
        self.assertEqual(card.reps, 4)
        self.assertGreater(card.due, 0)
        reviews = self.collection.reviews_of(self.card.id)
        self.assertEqual([review['kind'] for review in reviews], ['manual', 'manual'])
        last = reviews[-1]
        self.assertEqual((last['rating'], last['state'], last['stability']), (0, 'review', 12.5))
        self.assertEqual(self.collection.undo(), 'Reset')
        self.assertEqual(self.collection.card(self.card.id), before)
        self.assertEqual(len(self.collection.reviews_of(self.card.id)), 1)

    def test_forget_keeps_a_new_cards_position(self):
        position = self.card.due
        self.collection.forget([self.card.id])
        self.assertEqual(self.collection.card(self.card.id).due, position)
        self.collection.forget([self.card.id], keep_position=False)
        self.assertGreater(self.collection.card(self.card.id).due, position)

    def test_set_due_makes_a_review_card_due_in_so_many_days(self):
        today = self.collection.today(self.clock.now)
        self.collection.set_due([self.card.id], 3, now=self.clock.now)
        card = self.collection.card(self.card.id)
        self.assertEqual((card.state, card.due, card.interval, card.left),
                         ('review', today + 3, 3.0, 0))
        review = self.collection.reviews_of(self.card.id)[-1]
        self.assertEqual((review['kind'], review['rating'], review['state']),
                         ('manual', 0, 'new'))
        self.collection.db.execute('UPDATE cards SET interval = 40 WHERE id = ?', (self.card.id,))
        self.collection.set_due([self.card.id], 0, now=self.clock.now)
        card = self.collection.card(self.card.id)
        self.assertEqual((card.due, card.interval), (today, 40.0))
        self.assertEqual(self.collection.undo(), 'Set Due Date')
        self.assertEqual(self.collection.card(self.card.id).due, today + 3)
        self.collection.undo()
        self.assertEqual(self.collection.card(self.card.id).state, 'new')
        self.assertEqual(self.collection.reviews_of(self.card.id), [])

    def test_reposition_gives_new_cards_positions_in_order(self):
        second = self.card_of(add_basic(self.collection, self.deck, 'adios', 'bye'))
        third = self.card_of(add_basic(self.collection, self.deck, 'gato', 'cat'))
        self.collection.set_due([third.id], 1, now=self.clock.now)
        self.collection.reposition([second.id, third.id, self.card.id], start=100)
        self.assertEqual(self.collection.card(second.id).due, 100)
        self.assertEqual(self.collection.card(self.card.id).due, 101)
        self.assertEqual(self.collection.card(third.id).state, 'review')
        self.assertGreater(self.card_of(add_basic(self.collection, self.deck, 'x', 'y')).due, 101)
        self.assertEqual(self.collection.undo(), 'Add Note')
        self.assertEqual(self.collection.undo(), 'Reposition')
        self.assertEqual(self.collection.card(self.card.id).due, self.card.due)
        self.assertEqual(self.collection.card(second.id).due, second.due)


class AnswerTest(CollectionCase):

    def setUp(self):
        super().setUp()
        self.deck = self.collection.add_deck('Spanish')
        self.note = add_basic(self.collection, self.deck, 'hola', 'hello')
        self.card = self.card_of(self.note)
        self.now = self.clock.now

    def learning_after(self, card):
        after = card.copy()
        after.state = 'learning'
        after.due = int(self.now + 600)
        after.left = 1
        after.stability, after.difficulty = 2.3, 5.0
        after.last_review = int(self.now)
        return after

    def test_record_answer_writes_the_revlog_and_can_be_undone(self):
        kinds = self.changes()
        revlog_id = self.collection.record_answer(self.card, self.learning_after(self.card), 3,
                                                  self.now, duration_ms=1500)
        card = self.collection.card(self.card.id)
        self.assertEqual((card.state, card.reps, card.modified), ('learning', 1, int(self.now)))
        reviews = self.collection.reviews_of(self.card.id)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertEqual(review['id'], revlog_id)
        self.assertEqual((review['rating'], review['state'], review['kind'],
                          review['duration_ms'], review['elapsed_days'], review['stability']),
                         (3, 'new', 'review', 1500, 0.0, 2.3))
        self.assertEqual(kinds, ['cards'])
        self.assertEqual(self.collection.undo(), 'Good')
        self.assertEqual(self.collection.card(self.card.id), self.card)
        self.assertEqual(self.collection.reviews_of(self.card.id), [])
        self.collection.record_answer(self.card, self.learning_after(self.card), 1, self.now)
        self.assertEqual(self.collection.undo_label, 'Again')

    def test_record_answer_measures_elapsed_days_from_the_last_review(self):
        before = self.card.copy()
        before.state, before.last_review = 'review', int(self.now - 2.5 * DAY)
        self.collection.record_answer(before, self.learning_after(before), 2, self.now)
        review = self.collection.reviews_of(self.card.id)[0]
        self.assertAlmostEqual(review['elapsed_days'], 2.5)
        self.assertEqual(review['state'], 'review')

    def test_a_cram_answer_counts_no_rep(self):
        self.collection.record_answer(self.card, self.card.copy(), 3, self.now, kind='cram')
        self.assertEqual(self.collection.card(self.card.id).reps, 0)
        self.assertEqual(self.collection.reviews_of(self.card.id)[0]['kind'], 'cram')

    def test_record_answer_buries_siblings_when_asked(self):
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        note = self.collection.add_note(reversed_type.id, self.deck.id, ['agua', 'water'])
        first, second = self.collection.cards_of_note(note.id)
        self.collection.record_answer(first, self.learning_after(first), 3, self.now)
        self.assertEqual(self.collection.card(second.id).buried, 0)
        self.collection.record_answer(first, self.learning_after(first), 3, self.now,
                                      bury_siblings=True)
        self.assertEqual(self.collection.card(second.id).buried, self.collection.today(self.now))
        self.assertEqual(self.collection.card(first.id).buried, 0)
        self.collection.undo()
        self.assertEqual(self.collection.card(second.id).buried, 0)

    def test_a_leech_is_tagged_at_the_threshold(self):
        kinds = self.changes()
        before = self.card.copy()
        before.state, before.lapses = 'review', 6
        after = self.learning_after(before)
        after.state, after.lapses = 'relearning', 7
        self.collection.record_answer(before, after, 1, self.now, leech_threshold=8)
        self.assertEqual(self.collection.note(self.note.id).tags, [])
        before.lapses, after.lapses = 7, 8
        self.collection.record_answer(before, after, 1, self.now, leech_threshold=8)
        self.assertEqual(self.collection.note(self.note.id).tags, [LEECH_TAG])
        self.assertEqual(self.collection.card(self.card.id).suspended, 0)
        self.assertIn('leech', kinds)
        self.assertIn('notes', kinds)
        self.assertEqual(self.collection.undo(), 'Again')
        self.assertEqual(self.collection.note(self.note.id).tags, [])

    def test_a_leech_is_suspended_with_that_action(self):
        before = self.card.copy()
        before.state, before.lapses = 'review', 7
        after = self.learning_after(before)
        after.state, after.lapses = 'relearning', 8
        self.collection.record_answer(before, after, 1, self.now, leech_threshold=8,
                                      leech_action='suspend')
        self.assertEqual(self.collection.card(self.card.id).suspended, 1)
        self.assertEqual(self.collection.note(self.note.id).tags, [LEECH_TAG])
        self.collection.record_answer(before, after, 3, self.now, leech_threshold=8,
                                      leech_action='suspend')
        self.assertEqual(len(self.collection.reviews_of(self.card.id)), 2)


class RenderTest(CollectionCase):

    def setUp(self):
        super().setUp()
        self.deck = self.collection.add_deck('Languages::Spanish')

    def test_render_basic_puts_the_front_side_in_the_answer(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello')
        question, answer = self.collection.render_card(self.card_of(note))
        self.assertEqual(question, 'hola')
        self.assertTrue(answer.startswith('hola'))
        self.assertIn('<hr id=answer>', answer)
        self.assertTrue(answer.endswith('hello'))
        self.assertEqual(self.collection.card_summary(self.card_of(note)), 'hola')

    def test_render_cloze_hides_the_cards_own_cloze(self):
        note = add_cloze(self.collection, self.deck, 'The {{c1::cat}} sat on the {{c2::mat}}',
                         'extra')
        first, second = self.collection.cards_of_note(note.id)
        question, answer = self.collection.render_card(first)
        self.assertIn('[...]', question)
        self.assertNotIn('>cat<', question)
        self.assertIn('mat', question)
        self.assertIn('>cat<', answer)
        self.assertIn('extra', answer)
        question, _answer = self.collection.render_card(second)
        self.assertIn('>cat<', question)
        self.assertNotIn('>mat<', question)
        self.assertEqual(self.collection.card_summary(second), 'The cat sat on the [...]')

    def test_render_fills_the_special_fields(self):
        notetype = NoteType('Specials', fields=[{'name': 'Front', 'description': ''},
                                                {'name': 'Back', 'description': ''}],
                            templates=[{'name': 'Card 1', 'qfmt': '{{Front}}',
                                        'afmt': '{{Deck}}|{{Subdeck}}|{{Type}}|{{Card}}|{{Tags}}'
                                                '|{{CardFlag}}'}])
        self.collection.add_notetype(notetype)
        note = self.collection.add_note(notetype.id, self.deck.id, ['q', 'a'], ['verbs'])
        card = self.card_of(note)
        self.collection.set_flag([card.id], 2)
        _question, answer = self.collection.render_card(self.collection.card(card.id))
        self.assertEqual(answer, 'Languages::Spanish|Spanish|Specials|Card 1|verbs|flag2')

    def test_referenced_media(self):
        add_basic(self.collection, self.deck, '<img src="cat.jpg"> gato', '[sound:meow.mp3]')
        add_basic(self.collection, self.deck, '<img src="cat.jpg">', 'again')
        add_basic(self.collection, self.deck, '<a href="https://example.org/x.png">x</a>', 'y')
        self.assertEqual(self.collection.referenced_media(), {'cat.jpg', 'meow.mp3'})


class NoteTypeTest(CollectionCase):

    def setUp(self):
        super().setUp()
        self.deck = self.collection.add_deck('Spanish')

    def test_add_notetype_stores_it_and_can_be_undone(self):
        notetype = notetypes.stock('basic')
        notetype.name = 'Vocabulary'
        kinds = self.changes()
        self.collection.add_notetype(notetype)
        self.assertIsNotNone(notetype.id)
        self.assertEqual(self.collection.notetype(notetype.id).name, 'Vocabulary')
        self.assertEqual(self.collection.notetype_by_name('vocabulary').id, notetype.id)
        self.assertEqual(kinds, ['notetypes'])
        self.assertEqual(self.collection.undo(), 'Add Note Type')
        self.assertIsNone(self.collection.notetype(notetype.id))

    def test_remove_notetype_deletes_its_notes(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello')
        kept = add_cloze(self.collection, self.deck, '{{c1::x}}')
        self.assertEqual(self.collection.notetype_use(self.basic.id), 1)
        self.collection.remove_notetype(self.basic.id)
        self.assertIsNone(self.collection.notetype(self.basic.id))
        self.assertIsNone(self.collection.note(note.id))
        self.assertEqual(self.collection.card_count(), 1)
        self.assertIsNotNone(self.collection.note(kept.id))
        self.assertEqual(self.collection.undo(), 'Delete Note Type')
        self.assertEqual(self.collection.notetype(self.basic.id).name, 'Basic')
        self.assertEqual(self.collection.note(note.id), note)
        self.assertEqual(self.collection.card_count(), 2)

    def test_update_notetype_with_an_added_field_pads_notes(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello')
        self.basic.fields.append({'name': 'Extra', 'description': ''})
        self.collection.update_notetype(self.basic)
        self.assertEqual(self.collection.notetype(self.basic.id).field_names(),
                         ['Front', 'Back', 'Extra'])
        self.assertEqual(self.collection.note(note.id).fields, ['hola', 'hello', ''])
        self.assertEqual(len(self.collection.cards_of_note(note.id)), 1)
        self.assertEqual(self.collection.undo(), 'Edit Note Type')
        self.assertEqual(self.collection.note(note.id).fields, ['hola', 'hello'])
        self.assertEqual(self.collection.notetype(self.basic.id).field_names(), ['Front', 'Back'])

    def test_update_notetype_remaps_fields_by_name(self):
        note = add_basic(self.collection, self.deck, 'hola', 'hello')
        self.basic.fields = [{'name': 'Back', 'description': ''},
                             {'name': 'Front', 'description': ''},
                             {'name': 'Note', 'description': ''}]
        self.collection.update_notetype(self.basic)
        self.assertEqual(self.collection.note(note.id).fields, ['hello', 'hola', ''])
        self.basic.fields = [{'name': 'Front', 'description': ''}]
        self.collection.update_notetype(self.basic)
        self.assertEqual(self.collection.note(note.id).fields, ['hola'])

    def test_update_notetype_templates_add_and_remove_cards(self):
        reversed_type = self.collection.notetype_by_name('Basic (and reversed card)')
        note = self.collection.add_note(reversed_type.id, self.deck.id, ['agua', 'water'])
        second_template = reversed_type.templates.pop()
        self.collection.update_notetype(reversed_type)
        self.assertEqual([card.ord for card in self.collection.cards_of_note(note.id)], [0])
        reversed_type.templates.append(second_template)
        other = self.collection.add_deck('Science')
        self.collection.update_notetype(reversed_type, deck_id=other.id)
        cards = self.collection.cards_of_note(note.id)
        self.assertEqual([card.ord for card in cards], [0, 1])
        self.assertEqual(cards[1].deck_id, other.id)
        self.assertEqual(cards[0].deck_id, self.deck.id)


class UndoTest(CollectionCase):

    def setUp(self):
        super().setUp()
        self.deck = self.collection.add_deck('Spanish')
        self.card = self.card_of(add_basic(self.collection, self.deck, 'hola', 'hello'))
        self.collection.clear_undo()

    def test_undo_with_nothing_to_undo(self):
        self.assertFalse(self.collection.can_undo())
        self.assertIsNone(self.collection.undo_label)
        self.assertIsNone(self.collection.undo())

    def test_the_stack_is_undone_newest_first(self):
        self.collection.suspend([self.card.id])
        self.collection.set_flag([self.card.id], 1)
        self.assertEqual(self.collection.undo_label, 'Flag')
        self.assertEqual(self.collection.undo(), 'Flag')
        card = self.collection.card(self.card.id)
        self.assertEqual((card.flag, card.suspended), (0, 1))
        self.assertEqual(self.collection.undo(), 'Suspend')
        self.assertEqual(self.collection.card(self.card.id).suspended, 0)
        self.assertIsNone(self.collection.undo())

    def test_the_stack_keeps_undo_limit_steps(self):
        for flag in range(UNDO_LIMIT + 5):
            self.collection.set_flag([self.card.id], flag % 8)
        undone = 0
        while self.collection.undo() is not None:
            undone += 1
        self.assertEqual(undone, UNDO_LIMIT)
        self.assertEqual(self.collection.card(self.card.id).flag, 4 % 8)

    def test_nested_undoable_blocks_join_the_outer_one(self):
        kinds = self.changes()
        with self.collection.undoable('Outer'):
            self.collection.suspend([self.card.id])
            self.collection.set_flag([self.card.id], 2)
            self.assertEqual(kinds, [])
            self.assertIsNone(self.collection.undo_label)
        self.assertEqual(kinds, ['cards'])
        self.assertEqual(self.collection.undo_label, 'Outer')
        self.assertEqual(self.collection.undo(), 'Outer')
        card = self.collection.card(self.card.id)
        self.assertEqual((card.suspended, card.flag), (0, 0))
        self.assertFalse(self.collection.can_undo())
        self.assertEqual(kinds, ['cards', 'cards'])

    def test_an_exception_inside_undoable_rolls_back(self):
        kinds = self.changes()
        with self.assertRaises(RuntimeError):
            with self.collection.undoable('Doomed'):
                self.collection.suspend([self.card.id])
                self.collection.add_deck('Never')
                raise RuntimeError('boom')
        self.assertEqual(self.collection.card(self.card.id).suspended, 0)
        self.assertIsNone(self.collection.deck_by_name('Never'))
        self.assertFalse(self.collection.can_undo())
        self.assertEqual(kinds, [])
        self.collection.suspend([self.card.id])
        self.assertEqual(self.collection.card(self.card.id).suspended, 1)
        self.assertEqual(kinds, ['cards'])

    def test_changed_reports_each_kind_once_and_again_on_undo(self):
        kinds = self.changes()
        note = add_cloze(self.collection, self.deck, '{{c1::a}} {{c2::b}}', tags=['x'])
        self.assertEqual(kinds, ['cards', 'notes'])
        self.assertEqual(self.collection.undo(), 'Add Note')
        self.assertEqual(kinds, ['cards', 'notes', 'cards', 'notes'])
        self.assertIsNone(self.collection.note(note.id))
        self.assertEqual(self.collection.cards_of_note(note.id), [])

    def test_a_block_without_a_label_is_not_an_undo_step(self):
        with self.collection.undoable(None):
            self.collection.suspend([self.card.id])
        self.assertFalse(self.collection.can_undo())
        self.assertEqual(self.collection.card(self.card.id).suspended, 1)

    def test_clear_undo(self):
        self.collection.suspend([self.card.id])
        self.collection.clear_undo()
        self.assertFalse(self.collection.can_undo())
        self.assertEqual(self.collection.card(self.card.id).suspended, 1)


class MaintenanceTest(CollectionCase):

    def test_next_id_is_unique_and_increasing(self):
        start = int(time.time() * 1000)
        ids = [self.collection.next_id() for _ in range(500)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))
        self.assertGreaterEqual(ids[0], start)
        deck = self.collection.add_deck('Spanish')
        note = add_basic(self.collection, deck, 'hola', 'hello')
        self.assertGreater(note.id, ids[-1])
        self.assertGreater(self.card_of(note).id, note.id)

    def test_backup_writes_a_file_and_keeps_backup_count(self):
        deck = self.collection.add_deck('Spanish')
        folder = self.collection.path.parent / 'backups'
        folder.mkdir()
        for index in range(BACKUP_COUNT + 2):
            (folder / f'collection-202001{index:02d}-000000.sqlite').write_bytes(b'')
        self.assertIsNone(self.collection.backup())
        target = self.collection.backup(force=True)
        self.assertTrue(target.is_file())
        self.assertEqual(target.parent, folder)
        remaining = sorted(folder.glob('collection-*.sqlite'))
        self.assertEqual(len(remaining), BACKUP_COUNT)
        self.assertEqual(remaining[-1], target)
        self.assertNotIn(folder / 'collection-20200100-000000.sqlite', remaining)
        copy = sqlite3.connect(target)
        try:
            names = [row[0] for row in copy.execute('SELECT name FROM decks ORDER BY name')]
        finally:
            copy.close()
        self.assertEqual(names, ['Default', deck.name])

    def test_close_backs_up_a_changed_collection(self):
        directory = tempfile.mkdtemp(prefix='retain-test-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        collection = Collection(pathlib.Path(directory) / 'collection.sqlite')
        collection.close()
        self.assertFalse((pathlib.Path(directory) / 'backups').exists())
        collection = Collection(pathlib.Path(directory) / 'collection.sqlite')
        collection.add_deck('Spanish')
        collection.close()
        self.assertEqual(len(list((pathlib.Path(directory) / 'backups').iterdir())), 1)

    def test_check_repairs_orphans(self):
        deck = self.collection.add_deck('Spanish')
        note = add_basic(self.collection, deck, 'hola', 'hello')
        self.assertEqual(self.collection.check(), 0)
        orphan = self.collection.next_id()
        self.collection.db.execute(
            'INSERT INTO cards (id, note_id, deck_id, ord, created, modified) '
            'VALUES (?, ?, ?, 0, 0, 0)', (orphan, 424242, deck.id))
        self.assertEqual(self.collection.check(), 1)
        self.assertIsNone(self.collection.card(orphan))
        card = self.card_of(note)
        self.collection.db.execute('UPDATE cards SET deck_id = 424242 WHERE id = ?', (card.id,))
        self.assertEqual(self.collection.check(), 1)
        self.assertEqual(self.collection.card(card.id).deck_id, self.collection.decks()[0].id)
        self.collection.db.execute('DELETE FROM cards WHERE id = ?', (card.id,))
        self.assertEqual(self.collection.check(), 1)
        self.assertIsNone(self.collection.note(note.id))

    def test_copy_to_writes_a_readable_copy(self):
        deck = self.collection.add_deck('Spanish')
        target = self.collection.path.parent / 'copy.sqlite'
        self.collection.copy_to(target)
        copy = sqlite3.connect(target)
        try:
            self.assertEqual(copy.execute('SELECT COUNT(*) FROM decks').fetchone()[0], 2)
            self.assertEqual(copy.execute('SELECT name FROM decks WHERE id = ?',
                                          (deck.id,)).fetchone()[0], 'Spanish')
        finally:
            copy.close()

    def test_config_values_round_trip_as_json(self):
        self.collection.set('favourite', {'deck': 1, 'tags': ['a', 'b']})
        self.assertEqual(self.collection.get('favourite'), {'deck': 1, 'tags': ['a', 'b']})
        self.assertEqual(self.collection.get('missing', 'fallback'), 'fallback')
        self.assertEqual(self.collection.get('schema_version'), schema.VERSION)


if __name__ == '__main__':
    unittest.main()
