# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Editing a note type: notetypes.py's editing methods, template.rename_fields, and
Collection.update_notetype bringing notes and cards into line, each change undone exactly.
Everything is invented."""

import unittest

from tests import ROOT  # noqa: F401
from tests.support import add_basic, temporary_collection
from retain import notetypes, template
from retain.collection import CollectionError
from retain.notetypes import NoteType, NoteTypeError


def vocabulary():
    """A note type like a learner's: a word with furigana, its meaning, and two cards."""
    return NoteType('Vocabulary', fields=[
        {'name': 'Expression', 'description': ''}, {'name': 'Meaning', 'description': ''},
        {'name': 'Notes', 'description': ''}], templates=[
        {'name': 'Recognition', 'qfmt': '{{furigana:Expression}}',
         'afmt': '{{FrontSide}}<hr id=answer>{{Meaning}}{{#Notes}}<br>{{Notes}}{{/Notes}}'},
        {'name': 'Recall', 'qfmt': '{{Meaning}}',
         'afmt': '{{FrontSide}}<hr id=answer>{{kanji:Expression}}'}])


class RenameFieldsTest(unittest.TestCase):

    def test_every_kind_of_reference_is_renamed(self):
        text = ('{{Old}} {{furigana:Old}} {{#Old}}x{{/Old}} {{^Old}}y{{/Old}} {{ Old }} '
                '{{tts ja_JP:Old}} {{type:Old}} <%Old%> {{Older}} {{FrontSide}}')
        self.assertEqual(
            template.rename_fields(text, {'Old': 'New'}),
            '{{New}} {{furigana:New}} {{#New}}x{{/New}} {{^New}}y{{/New}} {{New}} '
            '{{tts ja_JP:New}} {{type:New}} <%New%> {{Older}} {{FrontSide}}')

    def test_a_deleted_field_goes_and_its_conditionals_keep_what_they_held(self):
        text = '<b>{{Front}}</b>{{#Gone}}<i>{{Gone}} kept</i>{{/Gone}}{{^Gone}}none{{/Gone}}'
        self.assertEqual(template.rename_fields(text, {'Gone': None}),
                         '<b>{{Front}}</b><i> kept</i>none')

    def test_nothing_to_rename_leaves_the_template(self):
        self.assertEqual(template.rename_fields('{{A}}', {}), '{{A}}')
        self.assertEqual(template.rename_fields('{{A}}', {'B': 'C'}), '{{A}}')

    def test_cloze_fields_in(self):
        self.assertEqual(template.cloze_fields_in('{{cloze:Text}}{{type:cloze:Other}}{{X}}'),
                         {'Text', 'Other'})


class NoteTypeEditingTest(unittest.TestCase):

    def setUp(self):
        self.notetype = vocabulary()

    def test_rename_field_rewrites_the_templates(self):
        self.notetype.rename_field(0, 'Word')
        self.assertEqual(self.notetype.field_names(), ['Word', 'Meaning', 'Notes'])
        self.assertEqual(self.notetype.templates[0]['qfmt'], '{{furigana:Word}}')
        self.assertEqual(self.notetype.templates[1]['afmt'],
                         '{{FrontSide}}<hr id=answer>{{kanji:Word}}')
        self.assertEqual(self.notetype.field_ords, [0, 1, 2])

    def test_field_names_are_checked_and_cleaned(self):
        with self.assertRaises(NoteTypeError):
            self.notetype.rename_field(0, 'meaning')
        with self.assertRaises(NoteTypeError):
            self.notetype.add_field('  ')
        with self.assertRaises(NoteTypeError):
            self.notetype.add_field('Tags')
        index = self.notetype.add_field('#Part: of {speech}"')
        self.assertEqual(self.notetype.field_names()[index], 'Part of speech')
        self.assertEqual(self.notetype.field_ords, [0, 1, 2, None])

    def test_move_field_keeps_the_sort_field(self):
        self.notetype.set_sort_field(1)
        self.notetype.move_field(1, 0)
        self.assertEqual(self.notetype.field_names(), ['Meaning', 'Expression', 'Notes'])
        self.assertEqual(self.notetype.field_ords, [1, 0, 2])
        self.assertEqual(self.notetype.sort_field, 0)
        self.notetype.move_field(0, 2)
        self.assertEqual(self.notetype.sort_field, 2)

    def test_remove_field_drops_its_references(self):
        self.notetype.remove_field(2)
        self.assertEqual(self.notetype.templates[0]['afmt'],
                         '{{FrontSide}}<hr id=answer>{{Meaning}}<br>')
        self.assertEqual(self.notetype.field_ords, [0, 1])

    def test_a_front_left_without_a_field_gets_the_first(self):
        self.notetype.remove_field(1)
        self.assertEqual(self.notetype.templates[1]['qfmt'], '{{Expression}}')
        cloze = notetypes.stock('cloze')
        cloze.add_field('Source')
        cloze.move_field(2, 0)
        cloze.remove_field(1)
        self.assertEqual(cloze.templates[0]['qfmt'], '{{cloze:Source}}')
        self.assertIn('{{cloze:Source}}', cloze.templates[0]['afmt'])

    def test_the_last_field_and_template_cannot_go(self):
        basic = notetypes.stock('basic')
        basic.remove_field(1)
        with self.assertRaises(NoteTypeError):
            basic.remove_field(0)
        with self.assertRaises(NoteTypeError):
            basic.remove_template(0)
        with self.assertRaises(NoteTypeError):
            notetypes.stock('cloze').add_template()

    def test_add_template_starts_as_a_reverse_card(self):
        basic = notetypes.stock('basic')
        index = basic.add_template()
        self.assertEqual(basic.templates[index]['name'], 'Card 2')
        self.assertEqual(basic.templates[index]['qfmt'], '{{Back}}')
        self.assertIn('{{Front}}', basic.templates[index]['afmt'])
        self.assertEqual(basic.template_ords, [0, None])
        with self.assertRaises(NoteTypeError):
            basic.rename_template(1, 'card 1')

    def test_template_problems(self):
        self.assertIsNone(self.notetype.template_problem(0))
        card = self.notetype.templates[0]
        card['qfmt'] = '{{#Meaning}}{{Expression}}'
        self.assertEqual(self.notetype.template_problem(0),
                         'The front opens {{#Meaning}} and does not close it.')
        card['qfmt'] = '{{Expression}}{{/Meaning}}'
        self.assertEqual(self.notetype.template_problem(0),
                         'The front closes {{/Meaning}}, which is not open.')
        card['qfmt'] = '{{Expression}}'
        card['afmt'] = '{{Reading}}'
        self.assertEqual(self.notetype.template_problem(0),
                         'The back names “Reading”, which is not a field.')
        card['afmt'] = '{{Meaning}}'
        card['qfmt'] = 'Just text {{Tags}}'
        self.assertEqual(self.notetype.template_problem(0),
                         'The front needs a field, such as {{Expression}}.')
        cloze = notetypes.stock('cloze')
        cloze.templates[0]['qfmt'] = '{{Text}}'
        self.assertEqual(cloze.template_problem(0),
                         'The front needs a cloze, such as {{cloze:Text}}.')


class UpdateNoteTypeTest(unittest.TestCase):
    """update_notetype() on a stored Vocabulary type with three notes."""

    def setUp(self):
        self.collection = self.enterContext(temporary_collection())
        self.deck = self.collection.add_deck('Japanese')
        self.notetype = self.collection.add_notetype(vocabulary())
        self.notes = [
            self.collection.add_note(self.notetype.id, self.deck.id, fields)
            for fields in (['水[みず]', 'water', 'noun'], ['食[た]べる', 'to eat', ''],
                           ['', 'empty word', 'a note'])]
        self.collection.clear_undo()

    def edit(self):
        return self.collection.notetype(self.notetype.id)

    def snapshot(self):
        """Every note, card and note type row, to compare after an undo."""
        db = self.collection.db
        return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('notes', 'cards', 'notetypes')}

    def fields(self):
        return [self.collection.note(note.id).fields for note in self.notes]

    def ords(self, note):
        return [card.ord for card in self.collection.cards_of_note(note.id)]

    def assert_undoes(self, before, label):
        self.assertEqual(self.collection.undo(), label)
        self.assertEqual(self.snapshot(), before)

    def test_the_setup(self):
        self.assertEqual([self.ords(note) for note in self.notes], [[0, 1], [0, 1], [1]])

    def test_rename_field_keeps_the_contents_and_rewrites_templates(self):
        before = self.snapshot()
        edit = self.edit()
        edit.rename_field(0, 'Word')
        self.collection.update_notetype(edit, label='Rename Field')
        stored = self.edit()
        self.assertEqual(stored.field_names(), ['Word', 'Meaning', 'Notes'])
        self.assertEqual(stored.templates[0]['qfmt'], '{{furigana:Word}}')
        self.assertEqual(self.fields()[0], ['水[みず]', 'water', 'noun'])
        question, _answer = self.collection.render_card(
            self.collection.cards_of_note(self.notes[0].id)[0])
        self.assertEqual(question, '<ruby>水<rt>みず</rt></ruby>')
        self.assertEqual(edit.field_ords, [0, 1, 2])
        self.assert_undoes(before, 'Rename Field')

    def test_reorder_fields_reorders_every_note(self):
        before = self.snapshot()
        edit = self.edit()
        edit.move_field(2, 0)
        edit.move_field(2, 1)
        self.collection.update_notetype(edit)
        self.assertEqual(self.edit().field_names(), ['Notes', 'Meaning', 'Expression'])
        self.assertEqual(self.fields(), [['noun', 'water', '水[みず]'],
                                         ['', 'to eat', '食[た]べる'],
                                         ['a note', 'empty word', '']])
        self.assertEqual([self.ords(note) for note in self.notes], [[0, 1], [0, 1], [1]])
        self.assert_undoes(before, 'Edit Note Type')

    def test_delete_field_drops_its_data(self):
        before = self.snapshot()
        edit = self.edit()
        edit.remove_field(2)
        self.collection.update_notetype(edit)
        self.assertEqual(self.fields(), [['水[みず]', 'water'], ['食[た]べる', 'to eat'],
                                         ['', 'empty word']])
        self.assertNotIn('Notes', self.edit().templates[0]['afmt'])
        self.assert_undoes(before, 'Edit Note Type')

    def test_add_field_appends_an_empty_value(self):
        before = self.snapshot()
        edit = self.edit()
        edit.add_field('Part of speech')
        self.collection.update_notetype(edit)
        self.assertEqual([fields[-1] for fields in self.fields()], ['', '', ''])
        self.assertEqual(len(self.fields()[0]), 4)
        self.assert_undoes(before, 'Edit Note Type')

    def test_rename_and_move_together_match_by_ord_not_name(self):
        edit = self.edit()
        edit.rename_field(0, 'Meaning2')
        edit.rename_field(1, 'Expression')
        edit.rename_field(0, 'Meaning')  # the two names traded
        self.collection.update_notetype(edit)
        self.assertEqual(self.fields()[0], ['水[みず]', 'water', 'noun'])

    def test_sort_field_change_updates_the_notes(self):
        edit = self.edit()
        edit.set_sort_field(1)
        self.collection.update_notetype(edit)
        self.assertEqual(self.collection.note(self.notes[1].id).sort_field, 'to eat')
        self.collection.undo()
        self.assertEqual(self.collection.note(self.notes[1].id).sort_field, '食[た]べる')

    def test_add_template_generates_its_cards(self):
        before = self.snapshot()
        other = self.collection.add_deck('Other')
        self.collection.clear_undo()
        before = self.snapshot()
        edit = self.edit()
        index = edit.add_template('Notes card')
        edit.templates[index]['qfmt'] = '{{Notes}}'
        self.collection.update_notetype(edit, deck_id=other.id)
        self.assertEqual([self.ords(note) for note in self.notes], [[0, 1, 2], [0, 1], [1, 2]])
        third = self.collection.cards_of_note(self.notes[0].id)[2]
        self.assertEqual(third.deck_id, other.id)
        self.assertEqual(third.state, 'new')
        self.assert_undoes(before, 'Edit Note Type')

    def test_delete_template_removes_its_cards_even_reviewed(self):
        first = self.collection.cards_of_note(self.notes[0].id)[0]
        self.collection.db.execute('UPDATE cards SET reps = 3 WHERE id = ?', (first.id,))
        self.assertEqual(self.collection.template_use(self.notetype.id, 0), 2)
        before = self.snapshot()
        edit = self.edit()
        edit.remove_template(0)
        self.collection.update_notetype(edit)
        self.assertIsNone(self.collection.card(first.id))
        # Card 2 is now the first template: its cards follow it.
        self.assertEqual([self.ords(note) for note in self.notes], [[0], [0], [0]])
        self.assert_undoes(before, 'Edit Note Type')

    def test_reorder_templates_moves_the_cards(self):
        cards = {note.id: {card.ord: card.id for card in self.collection.cards_of_note(note.id)}
                 for note in self.notes}
        before = self.snapshot()
        edit = self.edit()
        edit.move_template(1, 0)
        self.collection.update_notetype(edit)
        for note in self.notes:
            moved = {card.ord: card.id for card in self.collection.cards_of_note(note.id)}
            self.assertEqual(moved, {1 - ord: card_id for ord, card_id in cards[note.id].items()})
        recall = self.collection.card(cards[self.notes[0].id][1])
        question, _answer = self.collection.render_card(recall)
        self.assertEqual(question, 'water')
        self.assert_undoes(before, 'Edit Note Type')

    def test_a_template_delete_that_leaves_a_note_without_cards_is_refused(self):
        before = self.snapshot()
        edit = self.edit()
        edit.remove_template(1)  # the third note has only the Recall card
        with self.assertRaises(CollectionError) as caught:
            self.collection.update_notetype(edit)
        self.assertEqual(str(caught.exception), 'This would leave 1 note without a card.')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.collection.can_undo())

    def test_a_front_edit_that_empties_a_card_removes_it_unless_reviewed(self):
        recall = {note.id: self.collection.cards_of_note(note.id)[-1] for note in self.notes}
        self.collection.db.execute('UPDATE cards SET reps = 1 WHERE id = ?',
                                   (recall[self.notes[1].id].id,))
        before = self.snapshot()
        edit = self.edit()
        edit.templates[1]['qfmt'] = '{{Notes}}'
        self.collection.update_notetype(edit)
        # The first note has Notes: kept; the second has none but a review: kept; the third
        # has Notes too. A note's last card would stay whatever its front.
        self.assertEqual([self.ords(note) for note in self.notes], [[0, 1], [0, 1], [1]])
        edit = self.edit()
        edit.templates[1]['qfmt'] = '{{#Expression}}{{Meaning}}{{/Expression}}'
        self.collection.update_notetype(edit)
        self.assertEqual([self.ords(note) for note in self.notes], [[0, 1], [0, 1], [1]])
        self.collection.undo()
        self.collection.undo()
        self.assertEqual(self.snapshot(), before)

    def test_an_unreviewed_card_left_empty_goes(self):
        before = self.snapshot()
        edit = self.edit()
        edit.templates[1]['qfmt'] = '{{Notes}}'
        self.collection.update_notetype(edit)
        self.assertEqual([self.ords(note) for note in self.notes], [[0, 1], [0], [1]])
        self.assert_undoes(before, 'Edit Note Type')

    def test_a_front_edit_generates_new_cards(self):
        edit = self.edit()
        edit.templates[0]['qfmt'] = '{{Expression}}{{Notes}}'
        self.collection.update_notetype(edit)
        self.assertEqual(self.ords(self.notes[2]), [0, 1])

    def test_css_only_changes_no_card(self):
        before = self.snapshot()
        edit = self.edit()
        edit.css = '.card { color: red; }'
        self.collection.update_notetype(edit)
        self.assertEqual(self.snapshot()['cards'], before['cards'])
        self.assertEqual(self.edit().css, '.card { color: red; }')
        self.assert_undoes(before, 'Edit Note Type')

    def test_fields_replaced_by_hand_are_matched_by_name(self):
        edit = self.edit()
        edit.fields = [{'name': 'Meaning', 'description': ''},
                       {'name': 'Expression', 'description': ''}]
        edit.templates[0]['afmt'] = '{{Meaning}}'
        self.collection.update_notetype(edit)
        self.assertEqual(self.fields()[0], ['water', '水[みず]'])

    def test_bad_types_are_refused(self):
        edit = self.edit()
        edit.fields[1]['name'] = 'expression'
        with self.assertRaises(CollectionError):
            self.collection.update_notetype(edit)
        edit = self.edit()
        edit.templates = []
        with self.assertRaises(CollectionError):
            self.collection.update_notetype(edit)

    def test_many_edits_in_one_save(self):
        before = self.snapshot()
        edit = self.edit()
        edit.rename_field(0, 'Word')
        edit.add_field('Reading')
        edit.move_field(3, 1)
        edit.remove_field(3)  # Notes
        edit.move_template(1, 0)
        self.collection.update_notetype(edit)
        self.assertEqual(self.edit().field_names(), ['Word', 'Reading', 'Meaning'])
        self.assertEqual(self.fields()[0], ['水[みず]', '', 'water'])
        self.assertEqual(self.ords(self.notes[2]), [0])
        self.assert_undoes(before, 'Edit Note Type')


class NoteTypeManagementTest(unittest.TestCase):

    def setUp(self):
        self.collection = self.enterContext(temporary_collection())
        self.deck = self.collection.add_deck('Spanish')
        self.basic = self.collection.notetype_by_name('Basic')

    def test_rename_notetype(self):
        self.collection.rename_notetype(self.basic.id, 'Simple')
        self.assertEqual(self.collection.notetype(self.basic.id).name, 'Simple')
        with self.assertRaises(CollectionError):
            self.collection.rename_notetype(self.basic.id, 'cloze')
        with self.assertRaises(CollectionError):
            self.collection.rename_notetype(self.basic.id, ' ')
        self.assertEqual(self.collection.undo(), 'Rename Note Type')
        self.assertEqual(self.collection.notetype(self.basic.id).name, 'Basic')

    def test_new_notetype_from_stock_or_a_clone(self):
        stocked = self.collection.new_notetype(notetypes.stock('cloze'), 'Sentences')
        self.assertEqual(self.collection.notetype(stocked.id).kind, 'cloze')
        clone = self.collection.new_notetype(self.collection.notetype(self.basic.id), 'Words')
        self.assertNotEqual(clone.id, self.basic.id)
        self.assertIsNone(clone.original_id)
        self.assertEqual(self.collection.notetype(clone.id).templates, self.basic.templates)
        with self.assertRaises(CollectionError):
            self.collection.new_notetype(notetypes.stock('basic'), 'words')
        self.assertEqual(self.collection.undo(), 'Add Note Type')
        self.assertIsNone(self.collection.notetype(clone.id))

    def test_field_and_template_use(self):
        add_basic(self.collection, self.deck, 'hola', '')
        add_basic(self.collection, self.deck, 'adiós', 'goodbye')
        self.assertEqual(self.collection.field_use(self.basic.id, 0), 2)
        self.assertEqual(self.collection.field_use(self.basic.id, 1), 1)
        self.assertEqual(self.collection.template_use(self.basic.id, 0), 2)
        self.assertEqual(self.collection.sample_note(self.basic.id).fields, ['adiós', 'goodbye'])
        cloze = self.collection.notetype_by_name('Cloze')
        self.assertIsNone(self.collection.sample_note(cloze.id))


if __name__ == '__main__':
    unittest.main()
