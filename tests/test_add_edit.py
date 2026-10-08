# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""dialogs/add_edit.py: the note editor, over a stand-in application (a collection on a
temporary path, settings, recorded toasts) and a stand-in window."""

import shutil
import tempfile
import time
import unittest

from tests import ROOT  # noqa: F401  (registers src/ as retain)
from tests.gtk import SCHEMA_ID, pump, requires_gtk, wait_for

_stand_ins = {}


def classes():
    """The stand-in application and window classes, made once GTK is known to work."""
    if _stand_ins:
        return _stand_ins
    from gi.repository import Adw, Gio

    class App(Adw.Application):
        def __init__(self, collection):
            super().__init__(application_id='io.github.jackicus.Retain.AddEditTest',
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.collection = collection
            self.settings = Gio.Settings.new(SCHEMA_ID)
            self.toasts = []  # (text, undo)
            self.reported = []

        def toast(self, text, undo=False, timeout=0):
            self.toasts.append((text, undo))

        def report(self, error, context=None):
            self.reported.append(error)

        def undo(self):
            return self.collection.undo()

    class Window(Adw.Window):
        def __init__(self):
            super().__init__(default_width=1000, default_height=700)
            self.dialog_open = []  # what set_dialog_open was told

        def set_dialog_open(self, is_open):
            self.dialog_open.append(is_open)

    _stand_ins.update(App=App, Window=Window)
    return _stand_ins


def write_png(path, shade=200):
    """A tiny invented picture file (`shade` tells two apart: the media store keeps one
    copy of identical bytes)."""
    from gi.repository import Gdk, GLib

    texture = Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8,
                                    GLib.Bytes.new(bytes([shade, 100, 50, 255] * 4)), 8)
    texture.save_to_png(path)
    return path


@requires_gtk
class AddEditTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        from retain.collection import Collection

        cls.directory = tempfile.mkdtemp(prefix='retain-test-')
        cls.collection = Collection(f'{cls.directory}/collection.sqlite', backups=False)
        cls.app = classes()['App'](cls.collection)
        cls.app.set_default()
        cls.gtk_settings = Gtk.Settings.get_default()
        cls.animations = cls.gtk_settings.props.gtk_enable_animations
        cls.gtk_settings.props.gtk_enable_animations = False
        cls.window = classes()['Window']()
        cls.window.present()
        deadline = time.monotonic() + 2
        while not cls.window.get_mapped() and time.monotonic() < deadline:
            pump(20)
            time.sleep(0.005)
        cls.deck = cls.collection.add_deck('Invented')
        cls.other_deck = cls.collection.add_deck('Invented::Sub')
        cls.basic = cls.collection.notetype_by_name('Basic')
        cls.cloze = cls.collection.notetype_by_name('Cloze')

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()
        cls.gtk_settings.props.gtk_enable_animations = cls.animations
        cls.collection.close()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def setUp(self):
        self.app.set_default()
        self.app.toasts = []
        self.window.dialog_open = []
        self.dialog = None

    def tearDown(self):
        if self.dialog is not None:
            self.dialog.force_close()
            self.dialog = None
        pump()

    # -- helpers ------------------------------------------------------------------------

    def present_add(self, **kwargs):
        from retain.dialogs import add_edit

        self.dialog = add_edit.present_add(self.app, self.window, **kwargs)
        self.assertTrue(wait_for(self.dialog.get_mapped), 'the dialog did not show')
        pump()
        return self.dialog

    def present_edit(self, note_id):
        from retain.dialogs import add_edit

        self.dialog = add_edit.present_edit(self.app, self.window, note_id)
        self.assertTrue(wait_for(self.dialog.get_mapped), 'the dialog did not show')
        pump()
        return self.dialog

    def painted(self, dialog):
        """Run the loop until the dialog has been painted twice more (a frame comes only
        for a redraw, so each is asked for)."""
        clock = dialog.get_frame_clock()
        for _frame in range(2):
            start = clock.get_frame_counter()
            dialog.queue_draw()
            self.assertTrue(wait_for(lambda s=start: clock.get_frame_counter() > s))

    def select_type(self, dialog, notetype):
        names = [n.name for n in dialog._notetypes]
        dialog.type_row.set_selected(names.index(notetype.name))
        pump()

    def names(self, dialog):
        return [editor.name for editor in dialog.editors]

    # -- building ----------------------------------------------------------------------------

    def test_basic_has_two_fields_and_switching_type_rebuilds(self):
        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        self.assertEqual(self.names(dialog), ['Front', 'Back'])
        self.assertEqual(dialog.selected_deck_id(), self.deck.id)
        self.assertFalse(dialog.editors[0].cloze_button.get_visible())
        self.select_type(dialog, self.cloze)
        self.assertEqual(self.names(dialog), ['Text', 'Back Extra'])
        self.assertTrue(dialog.editors[0].cloze_button.get_visible())
        self.assertTrue(dialog.editors[0].hint_label.get_visible())

    def test_tells_the_window_a_dialog_is_open(self):
        dialog = self.present_add(deck_id=self.deck.id)
        self.assertEqual(self.window.dialog_open, [True])
        dialog.close()
        self.assertTrue(wait_for(lambda: not dialog.get_mapped()))
        self.assertEqual(self.window.dialog_open, [True, False])
        self.dialog = None

    def test_fits_a_narrow_window(self):
        from gi.repository import Gtk

        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        minimum, _natural, _b, _c = dialog.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)
        self.assertLessEqual(minimum, 360)
        for button in dialog.editors[0].toolbar:
            self.assertEqual(button.get_size_request(), (32, 32))

    # -- adding --------------------------------------------------------------------------

    def test_add_makes_a_note_and_clears_the_fields(self):
        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        before = self.collection.note_count()
        dialog.editors[0].set_html('hola')
        dialog.editors[1].set_html('<b>hello</b>')
        dialog.tags_row.set_text('greeting  invented')
        dialog.primary_button.emit('clicked')
        pump()
        self.assertEqual(self.collection.note_count(), before + 1)
        note = self.collection.notes(self.collection.find_notes('hola'))[-1]
        self.assertEqual(note.fields, ['hola', '<b>hello</b>'])
        self.assertEqual(note.tags, ['greeting', 'invented'])
        self.assertEqual(self.collection.cards_of_note(note.id)[0].deck_id, self.deck.id)
        self.assertEqual(self.app.toasts, [('Card added', True)])
        self.assertTrue(dialog.get_mapped())
        self.assertTrue(all(editor.is_empty() for editor in dialog.editors))
        self.assertEqual(dialog.tags_row.get_text(), 'greeting  invented')
        self.assertEqual(dialog.selected_notetype().id, self.basic.id)
        self.assertEqual(self.collection.get('last_notetype'), self.basic.id)
        self.assertEqual(self.collection.get('last_deck'), self.deck.id)
        self.assertFalse(dialog.error_banner.get_revealed())

    def test_an_empty_front_shows_the_banner_and_adds_nothing(self):
        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        before = self.collection.note_count()
        dialog.editors[1].set_html('only a back')
        self.assertFalse(dialog.add())
        self.assertEqual(self.collection.note_count(), before)
        self.assertTrue(dialog.error_banner.get_revealed())
        self.assertIn('first field is empty', dialog.error_banner.get_title())
        self.assertEqual(self.app.toasts, [])
        self.assertEqual(dialog.editors[1].get_html(), 'only a back')

    def test_cloze_hint_counts_the_cards(self):
        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.cloze.id)
        text = dialog.editors[0]
        self.assertEqual(text.hint_label.get_label(),
                         'Select a word and choose Cloze to make a card')
        text.set_html('{{c1::uno}} y {{c2::dos}}')
        self.assertEqual(text.hint_label.get_label(), 'Makes 2 cards')
        text.set_html('{{c1::uno}}')
        self.assertEqual(text.hint_label.get_label(), 'Makes 1 card')
        text.set_html('{{c1::uno}} y {{c2::dos}}')
        self.assertTrue(dialog.add())
        self.assertEqual(self.app.toasts, [('2 cards added', True)])

    def test_cloze_command_wraps_the_selection(self):
        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.cloze.id)
        text = dialog.editors[0]
        text.set_html('{{c1::uno}} dos tres')
        buffer = text.buffer
        buffer.select_range(buffer.get_iter_at_offset(12), buffer.get_iter_at_offset(15))
        text.command('cloze')
        self.assertEqual(text.get_html(), '{{c1::uno}} {{c2::dos}} tres')
        buffer.select_range(buffer.get_iter_at_offset(24), buffer.get_end_iter())
        text.command('cloze-same')
        self.assertEqual(text.get_html(), '{{c1::uno}} {{c2::dos}} {{c2::tres}}')

    def test_duplicate_banner(self):
        self.collection.add_note(self.basic.id, self.deck.id, ['el gato', 'the cat'])
        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        self.assertFalse(dialog.duplicate_banner.get_revealed())
        dialog.editors[0].set_html('<b>el gato</b>')
        dialog.editors[0].emit('focus-left')
        self.assertTrue(dialog.duplicate_banner.get_revealed())
        dialog.editors[0].set_html('el perro')
        # The debounce is 400 ms; a loaded machine (CI's container) needs the margin.
        self.assertTrue(wait_for(lambda: not dialog.duplicate_banner.get_revealed(),
                                 timeout=3.0),
                        'the banner did not go after the debounce')

    def test_picture_attached_to_a_field(self):
        from gi.repository import Gtk

        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        path = write_png(f'{self.directory}/invented-cat.png', shade=20)
        front = dialog.editors[0]
        front.set_html('a cat')
        front.buffer.place_cursor(front.buffer.get_end_iter())
        name = front.attach_path(path)
        self.assertEqual(name, 'invented-cat.png')
        self.assertTrue(self.collection.media.exists(name))
        self.assertEqual(front.get_html(), 'a cat<img src="invented-cat.png">')
        anchor = front.buffer.get_iter_at_offset(5).get_child_anchor()
        self.assertIsInstance(anchor.get_widgets()[0], Gtk.Picture)

    # -- image occlusion -------------------------------------------------------------------

    def test_image_occlusion_fields(self):
        occlusion = self.collection.notetype_by_name('Image Occlusion')
        dialog = self.present_add(deck_id=self.deck.id, notetype_id=occlusion.id)
        self.assertIsNotNone(dialog.occlusion)
        self.assertEqual(set(dialog.occlusion['others']), {'Header', 'Back Extra', 'Comments'})
        dialog.set_occlusion_image(write_png(f'{self.directory}/invented-diagram.png'))
        values = dict(zip(occlusion.field_names(), dialog.field_values(), strict=True))
        self.assertEqual(values['Image'], '<img src="invented-diagram.png">')
        dialog.clear_fields()
        self.assertEqual(dialog.field_values()[1], '')

    # -- editing ----------------------------------------------------------------------------

    def test_edit_loads_and_saves_undoably(self):
        note = self.collection.add_note(self.basic.id, self.deck.id, ['hola', 'hello'],
                                        ['invented'])
        dialog = self.present_edit(note.id)
        self.assertEqual(dialog.get_title(), 'Edit Note')
        self.assertEqual([e.get_html() for e in dialog.editors], ['hola', 'hello'])
        self.assertEqual(dialog.tags_row.get_text(), 'invented')
        self.assertEqual(dialog.selected_deck_id(), self.deck.id)
        self.assertFalse(dialog.type_row.get_sensitive())
        dialog.editors[0].set_html('adiós')
        dialog.tags_row.set_text('invented farewell')
        dialog.deck_row.set_selected([d.id for d in dialog._decks].index(self.other_deck.id))
        dialog.primary_button.emit('clicked')
        self.assertTrue(wait_for(lambda: not dialog.get_mapped()))
        self.dialog = None
        saved = self.collection.note(note.id)
        self.assertEqual(saved.fields, ['adiós', 'hello'])
        self.assertEqual(saved.tags, ['farewell', 'invented'])
        self.assertEqual(self.collection.cards_of_note(note.id)[0].deck_id, self.other_deck.id)
        self.assertEqual(self.app.toasts, [('Note saved', True)])
        self.assertEqual(self.collection.undo_label, 'Edit Note')
        self.collection.undo()
        self.assertEqual(self.collection.note(note.id).fields, ['hola', 'hello'])
        self.assertEqual(self.collection.cards_of_note(note.id)[0].deck_id, self.deck.id)

    def test_richer_html_is_edited_as_html(self):
        html = '<table><tr><td>x</td></tr></table>'
        note = self.collection.add_note(self.basic.id, self.deck.id, [html, 'y'])
        dialog = self.present_edit(note.id)
        front = dialog.editors[0]
        self.assertTrue(front.raw)
        self.assertTrue(front.banner.get_revealed())
        self.assertFalse(dialog.editors[1].raw)
        self.assertEqual(front.get_html(), html)
        self.assertEqual(dialog.field_values(), [html, 'y'])

    def test_editing_a_missing_note_reports(self):
        from retain.dialogs import add_edit

        self.assertIsNone(add_edit.present_edit(self.app, self.window, 1))
        self.assertEqual(len(self.app.reported), 1)

    # -- keys -------------------------------------------------------------------------------

    def test_tab_moves_between_fields(self):
        from gi.repository import Gtk

        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        first, second = dialog.editors
        self.assertFalse(first.text_view.get_accepts_tab())
        self.painted(dialog)  # until the sheet has settled, Tab lands on the sheet itself
        first.grab_focus()
        pump()
        self.assertIs(dialog.focused_editor(), first)
        self.assertTrue(first.toolbar.get_visible())
        self.assertFalse(second.toolbar.get_visible())
        first.get_root().child_focus(Gtk.DirectionType.TAB_FORWARD)
        pump()
        self.assertIs(dialog.focused_editor(), second)
        self.assertTrue(second.toolbar.get_visible())
        self.assertFalse(first.toolbar.get_visible())
        second.get_root().child_focus(Gtk.DirectionType.TAB_BACKWARD)
        pump()
        self.assertIs(dialog.focused_editor(), first)

    def test_editor_keys(self):
        from gi.repository import Gdk

        dialog = self.present_add(deck_id=self.deck.id, notetype_id=self.basic.id)
        front = dialog.editors[0]
        front.set_html('hola')
        front.grab_focus()
        pump()
        front.buffer.select_range(front.buffer.get_start_iter(), front.buffer.get_end_iter())
        control = Gdk.ModifierType.CONTROL_MASK
        # Ctrl+B formats the focused field; Tab is left to the text views; Ctrl+Enter adds.
        self.assertEqual(dialog._on_key_pressed(None, Gdk.KEY_b, 0, control), Gdk.EVENT_STOP)
        self.assertEqual(front.get_html(), '<b>hola</b>')
        self.assertEqual(dialog._on_key_pressed(None, Gdk.KEY_Tab, 0, 0), Gdk.EVENT_PROPAGATE)
        before = self.collection.note_count()
        self.assertEqual(dialog._on_key_pressed(None, Gdk.KEY_Return, 0, control),
                         Gdk.EVENT_STOP)
        self.assertEqual(self.collection.note_count(), before + 1)
        self.assertTrue(front.is_empty())
