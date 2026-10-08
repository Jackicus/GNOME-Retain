# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Manage Note Types (dialogs/notetypes.py), over test_today's stand-ins: the list, a type's
fields and templates, and the template editor with its preview. Everything is invented."""

import unittest

from tests import ROOT  # noqa: F401  (registers src/ as retain)
from tests.gtk import pump
from tests.test_today import DeckPagesTestCase


class NoteTypesDialogTest(DeckPagesTestCase):

    def dialog(self, notetype_id=None):
        from retain.dialogs import notetypes

        return self.keep(notetypes.present(self.app, self.window, notetype_id))

    @property
    def basic(self):
        return self.collection.notetype_by_name('Basic')

    def toast(self, dialog):
        return dialog.last_toast.get_title() if dialog.last_toast else None

    def test_lists_the_types_with_their_note_counts(self):
        dialog = self.dialog()
        self.assertEqual(dialog.get_title(), 'Note Types')
        row = dialog.row_for(self.basic.id)
        self.assertEqual(row.get_title(), 'Basic')
        self.assertEqual(row.get_subtitle(), '5 notes')
        cloze = self.collection.notetype_by_name('Cloze')
        self.assertEqual(dialog.row_for(cloze.id).get_subtitle(), '0 notes')
        self.assertEqual(self.window.dialog_open, [True])

    def test_add_from_a_stock_kind_or_a_copy(self):
        from retain import notetypes

        dialog = self.dialog()
        labels = [label for label, _source in dialog.sources()]
        self.assertEqual(labels[0], 'Basic')
        self.assertIn('Copy of “Basic”', labels)
        dialog.add_notetype('Vocabulary', notetypes.stock('basic-reversed'))
        added = self.collection.notetype_by_name('Vocabulary')
        self.assertEqual(len(added.templates), 2)
        self.assertEqual(self.toast(dialog), 'Note type “Vocabulary” added')
        self.assertEqual(dialog.navigation_view.get_visible_page().get_title(), 'Vocabulary')
        self.assertIsNotNone(dialog.row_for(added.id))

    def test_add_dialog_refuses_a_taken_name(self):
        dialog = self.dialog()
        name_dialog = self.keep(dialog._on_add(None))
        name_dialog.entry.set_text('basic')
        name_dialog.apply_button.emit('clicked')
        self.assertIn('already exists', name_dialog.error_label.get_text())
        name_dialog.entry.set_text('Sentences')
        name_dialog.apply_button.emit('clicked')
        self.assertIsNotNone(self.collection.notetype_by_name('Sentences'))

    def test_rename_and_undo(self):
        dialog = self.dialog()
        dialog.rename_notetype(self.basic.id, 'Simple')
        self.assertIsNotNone(self.collection.notetype_by_name('Simple'))
        self.assertEqual(self.toast(dialog), 'Note type renamed to “Simple”')
        dialog.last_toast.emit('button-clicked')
        pump()
        self.assertIsNotNone(self.collection.notetype_by_name('Basic'))
        self.assertEqual(dialog.row_for(self.basic.id).get_title(), 'Basic')

    def test_delete_asks_with_the_note_count(self):
        dialog = self.dialog()
        basic_id = self.basic.id
        alert = dialog.confirm_delete(basic_id)
        self.assertEqual(alert.get_heading(), 'Delete “Basic”?')
        self.assertEqual(alert.get_body(),
                         'This deletes its 5 notes and their cards. You can undo this.')
        alert.emit('response', 'delete')
        self.assertIsNone(self.collection.notetype(basic_id))
        self.assertEqual(self.collection.note_count(), 0)
        self.assertIsNone(dialog.row_for(basic_id))
        self.assertEqual(self.toast(dialog), 'Note type deleted')
        self.collection.undo()
        self.assertEqual(self.collection.note_count(), 5)

    def test_a_types_page_lists_fields_and_templates(self):
        dialog = self.dialog(self.basic.id)
        page = dialog.navigation_view.get_visible_page()
        self.assertEqual(page.get_title(), 'Basic')
        self.assertEqual([row.get_title() for row in page.field_rows], ['Front', 'Back'])
        self.assertEqual(page.field_rows[0].get_subtitle(), 'Sorts the browser')
        self.assertEqual([row.get_title() for row in page.template_rows], ['Card 1'])
        self.assertEqual(page.template_rows[0].get_subtitle(), '5 cards')
        self.assertTrue(page.add_template_button.get_visible())
        cloze = dialog.show_notetype(self.collection.notetype_by_name('Cloze').id)
        self.assertFalse(cloze.add_template_button.get_visible())
        self.assertEqual(cloze.templates_group.get_title(), 'Card Template')

    def test_field_edits_store_at_once_with_undo(self):
        dialog = self.dialog()
        page = dialog.show_notetype(self.basic.id)
        note_id = self.collection.find_notes('hola')[0]
        self.assertTrue(page.rename_field(1, 'Meaning'))
        self.assertEqual(self.toast(dialog), 'Field renamed to “Meaning”')
        self.assertIn('{{Meaning}}', self.basic.templates[0]['afmt'])
        self.assertTrue(page.add_field('Notes'))
        self.assertEqual(self.collection.note(note_id).fields, ['hola', 'hello', ''])
        self.assertTrue(page.move_field(2, 0))
        self.assertEqual([row.get_title() for row in page.field_rows],
                         ['Notes', 'Front', 'Meaning'])
        self.assertEqual(page.field_rows[1].get_subtitle(), 'Sorts the browser')
        self.assertTrue(page.remove_field(2))
        self.assertEqual(self.toast(dialog), 'Field “Meaning” deleted with its text in 5 notes')
        self.assertEqual(self.collection.note(note_id).fields, ['', 'hola'])
        dialog.last_toast.emit('button-clicked')
        pump()
        self.assertEqual(self.collection.note(note_id).fields, ['', 'hola', 'hello'])
        self.assertEqual([row.get_title() for row in page.field_rows],
                         ['Notes', 'Front', 'Meaning'])

    def test_a_refused_edit_is_toasted(self):
        dialog = self.dialog()
        page = dialog.show_notetype(self.basic.id)
        self.assertFalse(page.rename_field(1, 'front'))
        self.assertEqual(self.toast(dialog), 'A field called “Front” already exists.')
        name_dialog = self.keep(page._on_rename_field(None, _int(1)))
        name_dialog.entry.set_text('Front')
        name_dialog.apply_button.emit('clicked')
        self.assertIn('already exists', name_dialog.error_label.get_text())

    def test_template_add_and_delete_say_what_goes(self):
        dialog = self.dialog()
        page = dialog.show_notetype(self.basic.id)
        self.assertTrue(page.add_template())
        self.assertEqual(self.collection.card_count(), 10)
        template_page = dialog.navigation_view.get_visible_page()
        self.assertEqual(template_page.get_title(), 'Card 2')
        dialog.navigation_view.pop()
        pump()
        self.assertEqual(page.template_rows[1].get_subtitle(), '5 cards')
        self.assertTrue(page.move_template(1, 0))
        self.assertEqual([row.get_title() for row in page.template_rows], ['Card 2', 'Card 1'])
        self.assertTrue(page.remove_template(0))
        self.assertEqual(self.toast(dialog), 'Card template “Card 2” deleted with its 5 cards')
        self.assertEqual(self.collection.card_count(), 5)

    def test_template_page_previews_and_saves_on_leaving(self):
        dialog = self.dialog()
        page = dialog.show_notetype(self.basic.id)
        editor = page.show_template(0)
        self.assertEqual(editor.text('front'), '{{Front}}')
        question, _answer = editor.preview_html()
        self.assertIn(question, ('hola', 'adiós', 'gracias', 'hablar', 'comer'))
        self.assertEqual(editor.preview_caption.get_text(), 'Your latest note')
        editor.buffers['front'].set_text('<b>{{Front}}</b>')
        editor.set_side('styling')
        self.assertIs(editor.view.get_buffer(), editor.buffers['styling'])
        editor.buffers['styling'].set_text('.card { font-size: 30px; }')
        self.assertTrue(editor.preview_html()[0].startswith('<b>'))
        self.assertTrue(editor.render_preview())
        dialog.navigation_view.pop()
        pump()
        stored = self.basic
        self.assertEqual(stored.templates[0]['qfmt'], '<b>{{Front}}</b>')
        self.assertEqual(stored.css, '.card { font-size: 30px; }')
        self.assertEqual(self.toast(dialog), 'Card template saved')
        self.assertEqual(self.collection.undo(), 'Edit Card Template')
        self.assertEqual(self.basic.templates[0]['qfmt'], '{{Front}}')

    def test_a_broken_template_is_shown_and_kept_from_storing(self):
        dialog = self.dialog()
        page = dialog.show_notetype(self.basic.id)
        editor = page.show_template(0)
        editor.buffers['front'].set_text('{{#Back}}{{Front}}')
        self.assertTrue(editor.problem_banner.get_revealed())
        self.assertEqual(editor.problem_banner.get_title(),
                         'The front opens {{#Back}} and does not close it.')
        self.assertIsNone(editor.preview_html())
        self.assertFalse(editor.get_can_pop())
        self.assertFalse(dialog.get_can_close())
        self.assertFalse(editor.save())
        editor.buffers['front'].set_text('{{Nope}}')
        self.assertEqual(editor.problem_banner.get_title(),
                         'The front names “Nope”, which is not a field.')
        editor.problem_banner.emit('button-clicked')  # Revert
        self.assertEqual(editor.text('front'), '{{Front}}')
        self.assertFalse(editor.problem_banner.get_revealed())
        self.assertTrue(dialog.get_can_close())
        self.assertFalse(editor.save())  # nothing changed

    def test_closing_the_dialog_saves_the_open_template(self):
        dialog = self.dialog()
        editor = dialog.show_notetype(self.basic.id).show_template(0)
        editor.buffers['back'].set_text('{{Back}}')
        dialog.force_close()
        pump()
        self.assertEqual(self.basic.templates[0]['afmt'], '{{Back}}')

    def test_sample_text_without_notes(self):
        cloze = self.collection.notetype_by_name('Cloze')
        dialog = self.dialog()
        editor = dialog.show_notetype(cloze.id).show_template(0)
        self.assertEqual(editor.preview_caption.get_text(), 'Sample text')
        question, answer = editor.preview_html()
        self.assertIn('[...]', question)
        self.assertIn('(Text)', answer)
        self.assertIn('(Back Extra)', answer)


class AddCardsManageTypesTest(DeckPagesTestCase):

    def test_the_type_row_opens_the_types_and_the_fields_follow(self):
        from retain.dialogs import add_edit

        editor = self.keep(add_edit.present_add(self.app, self.window, deck_id=self.spanish.id))
        basic = self.collection.notetype_by_name('Basic')
        editor.type_row.set_selected(
            [n.id for n in editor._notetypes].index(basic.id))
        editor.editors[1].set_html('water')  # the first would check for duplicates
        manage = self.keep(editor._on_manage_types(None))
        page = manage.navigation_view.get_visible_page()
        self.assertEqual(page.get_title(), 'Basic')
        page.add_field('Notes')
        manage.force_close()
        pump()
        self.assertEqual([e.name for e in editor.editors], ['Front', 'Back', 'Notes'])
        self.assertEqual(editor.editors[1].get_html(), 'water')


def _int(value):
    from gi.repository import GLib

    return GLib.Variant('i', value)


if __name__ == '__main__':
    unittest.main()
