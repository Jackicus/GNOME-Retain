# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""dialogs/import_export.py: the Import dialog on a package (imported in its thread,
cancelled, a bad file) and on a text file (the column mapping), and the Export dialog
writing a package and a text file through a replaced target chooser. Everything runs on a
stand-in application whose collection sits on a temporary path."""

import os
import shutil
import tempfile
import time
import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import add_basic
from retain import exporting, importing
from retain.collection import Collection

WAIT = 5.0  # seconds a thread's import or export may take under a loaded test runner


class FakeApp:
    """What the dialogs ask of the application: the collection, the settings, toasts."""

    def __init__(self, collection):
        from gi.repository import Gio

        self.collection = collection
        self.settings = Gio.Settings.new('io.github.jackicus.Retain')
        self.toasts = []
        self.reported = []

    def toast(self, text, undo=False, timeout=0):
        self.toasts.append(text)

    def report(self, error, context=None):
        self.reported.append(error)


def make_window():
    """A parent window stand-in with the seams the dialogs use."""
    from gi.repository import Adw

    class FakeWindow(Adw.Window):
        def __init__(self):
            super().__init__(default_width=640, default_height=480)
            self.toasts = []
            self.dialog_open = []

        def add_toast(self, toast):
            self.toasts.append(toast)

        def set_dialog_open(self, is_open):
            self.dialog_open.append(is_open)

    return FakeWindow()


@requires_gtk
class DialogCase(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='retain-dialogs-')
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.collection = Collection(os.path.join(self.directory, 'main', 'collection.sqlite'),
                                     backups=False)
        self.addCleanup(self.collection.close)
        self.app = FakeApp(self.collection)
        self.window = make_window()
        self.window.present()
        self.addCleanup(self.window.destroy)
        pump()
        self.changes = []
        self.collection.connect('changed', lambda _c, kind: self.changes.append(kind))

    def path(self, name):
        return os.path.join(self.directory, name)

    def write_package(self, name='two.apkg'):
        """A package of two Basic notes in a deck 'Islands', built from a source collection
        of its own."""
        source = Collection(os.path.join(self.directory, 'source', 'collection.sqlite'),
                            backups=False)
        try:
            deck = source.add_deck('Islands')
            add_basic(source, deck, 'Skye', 'Inner Hebrides', ['scotland'])
            add_basic(source, deck, 'Gozo', 'Malta')
            path = self.path(name)
            exporting.export_package(source, path, deck_id=deck.id)
        finally:
            source.close()
        return path

    def write_text(self, name, text):
        path = self.path(name)
        with open(path, 'w', encoding='utf-8') as file:
            file.write(text)
        return path

    def wait_done(self, dialog):
        self.assertTrue(wait_for(lambda: dialog.done, timeout=WAIT), 'the dialog never finished')
        return dialog


class ImportPackageTest(DialogCase):

    def test_a_package_shows_its_options(self):
        from retain.dialogs import import_export

        dialog = import_export.present_import(self.app, self.window, self.write_package())
        self.addCleanup(dialog.force_close)
        pump()
        self.assertEqual(dialog.kind, 'package')
        self.assertTrue(dialog.package_group.get_visible())
        self.assertFalse(dialog.text_group.get_visible())
        self.assertTrue(dialog.scheduling_row.get_active())
        self.assertEqual(dialog.deck_row.get_model().get_string(0), 'As in the file')
        self.assertEqual(dialog.file_row.get_subtitle(), 'two.apkg')
        self.assertEqual(dialog.stack.get_visible_child_name(), 'setup')

    def test_importing_a_package_adds_its_notes(self):
        from retain.dialogs import import_export

        dialog = import_export.present_import(self.app, self.window, self.write_package())
        self.addCleanup(dialog.force_close)
        pump()
        dialog.start()
        self.assertEqual(dialog.stack.get_visible_child_name(), 'working')
        self.wait_done(dialog)
        self.assertEqual(dialog.result.notes_added, 2)
        self.assertEqual(dialog.result.cards_added, 2)
        self.assertEqual(dialog.stack.get_visible_child_name(), 'done')
        self.assertIsNotNone(self.collection.deck_by_name('Islands'))
        self.assertEqual(len(self.collection.find_notes('deck:Islands')), 2)
        self.assertIn('Added 2 notes and 2 cards.', dialog.done_body.get_text())
        self.assertEqual(self.app.toasts, ['Added 2 notes and 2 cards.'])
        for kind in ('decks', 'notes', 'cards'):
            self.assertIn(kind, self.changes)
        self.assertFalse(self.collection.can_undo())
        self.assertTrue(dialog.close_button.get_visible())
        self.assertFalse(dialog.cancel_button.get_visible())

    def test_importing_into_a_deck_nests_the_package_decks(self):
        from retain.dialogs import import_export

        self.collection.add_deck('Travel')
        dialog = import_export.present_import(self.app, self.window, self.write_package())
        self.addCleanup(dialog.force_close)
        pump()
        names = [dialog.deck_row.get_model().get_string(i)
                 for i in range(dialog.deck_row.get_model().get_n_items())]
        dialog.deck_row.set_selected(names.index('Travel'))
        dialog.start()
        self.wait_done(dialog)
        self.assertIsNotNone(self.collection.deck_by_name('Travel::Islands'))

    def test_cancel_stops_the_import_and_changes_nothing(self):
        from retain.dialogs import import_export

        def slow_import(collection, path, progress=None, should_stop=None, **_options):
            # Waits for the stop, as a long import would notice it between phases.
            progress(0.1, 'Reading…')
            while not should_stop():
                time.sleep(0.005)
            result = importing.ImportResult()
            result.stopped = True
            return result

        real = import_export.importing.import_package
        import_export.importing.import_package = slow_import
        self.addCleanup(setattr, import_export.importing, 'import_package', real)
        dialog = import_export.present_import(self.app, self.window, self.write_package())
        self.addCleanup(dialog.force_close)
        pump()
        dialog.start()
        self.assertTrue(dialog.cancel_button.get_visible())
        dialog.cancel()
        self.wait_done(dialog)
        self.assertTrue(dialog.result.stopped)
        self.assertEqual(dialog.done_title.get_text(), 'Import Stopped')
        self.assertIsNone(self.collection.deck_by_name('Islands'))
        self.assertNotIn('notes', self.changes)

    def test_a_bad_file_shows_the_error(self):
        from retain.dialogs import import_export

        path = self.write_text('broken.apkg', 'this is not a zip file')
        dialog = import_export.present_import(self.app, self.window, path)
        self.addCleanup(dialog.force_close)
        pump()
        dialog.start()
        self.wait_done(dialog)
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.error)
        self.assertEqual(dialog.done_title.get_text(), 'Import Failed')
        self.assertEqual(dialog.done_body.get_text(), dialog.error)
        self.assertEqual(self.app.toasts, [])

    def test_the_parent_hears_the_dialog_open_and_close(self):
        from retain.dialogs import import_export

        dialog = import_export.present_import(self.app, self.window, self.write_package())
        pump()
        self.assertEqual(self.window.dialog_open, [True])
        dialog.force_close()
        pump()
        self.assertEqual(self.window.dialog_open, [True, False])


class ImportTextTest(DialogCase):

    def test_a_text_file_shows_its_columns_and_preview(self):
        from retain.dialogs import import_export

        path = self.write_text('words.txt', '#separator:tab\n#columns:Back\tFront\n'
                               'hello\thola\nbye\tadiós\n')
        dialog = import_export.present_import(self.app, self.window, path)
        self.addCleanup(dialog.force_close)
        pump()
        self.assertEqual(dialog.kind, 'text')
        self.assertFalse(dialog.package_group.get_visible())
        self.assertTrue(dialog.text_group.get_visible())
        rows = [row for _index, row in dialog._column_rows]
        self.assertEqual([row.get_title() for row in rows], ['Front', 'Back'])
        # The header names the columns: Front is the second column, Back the first.
        self.assertEqual(dialog.field_map(), [1, 0])
        self.assertEqual(rows[0].get_model().get_string(0), 'Skip')
        self.assertEqual(rows[0].get_model().get_string(1), 'Column 1: hello')
        self.assertIn('hello · hola', dialog.preview_label.get_text())

    def test_importing_a_text_file_with_a_mapping(self):
        from retain.dialogs import import_export

        path = self.write_text('plain.txt', '#separator:tab\nuno\tone\tnumbers\ndos\ttwo\tx\n')
        self.collection.add_deck('Spanish')
        dialog = import_export.present_import(self.app, self.window, path)
        self.addCleanup(dialog.force_close)
        pump()
        rows = [row for _index, row in dialog._column_rows]
        rows[0].set_selected(2)   # Front from column 2
        rows[1].set_selected(1)   # Back from column 1
        model = dialog.text_deck_row.get_model()
        names = [model.get_string(i) for i in range(model.get_n_items())]
        dialog.text_deck_row.set_selected(names.index('Spanish'))
        dialog.tags_row.set_text('imported')
        dialog.start()
        self.wait_done(dialog)
        self.assertEqual(dialog.result.notes_added, 2)
        notes = [self.collection.note(id) for id in self.collection.find_notes('deck:Spanish')]
        self.assertEqual(sorted(note.fields[0] for note in notes), ['one', 'two'])
        self.assertEqual(sorted(note.fields[1] for note in notes), ['dos', 'uno'])
        self.assertTrue(all('imported' in note.tags for note in notes))

    def test_an_unreadable_text_file_disables_import(self):
        from retain.dialogs import import_export

        dialog = import_export.present_import(self.app, self.window, self.path('missing.csv'))
        self.addCleanup(dialog.force_close)
        pump()
        self.assertTrue(dialog.error_label.get_visible())
        self.assertFalse(dialog.import_button.get_sensitive())


class ExportTest(DialogCase):

    def setUp(self):
        super().setUp()
        deck = self.collection.add_deck('Rivers')
        add_basic(self.collection, deck, 'Tagus', 'Lisbon')
        add_basic(self.collection, deck, 'Ebro', 'Tortosa')
        other = self.collection.add_deck('Capes')
        add_basic(self.collection, other, 'Finisterre', 'Galicia')
        self.deck = deck

    def present(self, deck_id=None):
        from retain.dialogs import import_export

        dialog = import_export.present_export(self.app, self.window, deck_id=deck_id)
        self.addCleanup(dialog.force_close)
        pump()
        return dialog

    def test_the_deck_given_is_preselected_and_named(self):
        dialog = self.present(self.deck.id)
        self.assertEqual(dialog.deck_id, self.deck.id)
        self.assertEqual(dialog.suggested_name(), 'Rivers.apkg')
        dialog.what_row.set_selected(0)
        self.assertIsNone(dialog.deck_id)
        self.assertEqual(dialog.suggested_name(), 'Retain collection.apkg')
        dialog.format_row.set_selected(1)
        self.assertEqual(dialog.suggested_name(), 'Retain collection.txt')

    def test_the_format_shows_its_own_switches(self):
        dialog = self.present()
        self.assertTrue(dialog.scheduling_row.get_visible())
        self.assertTrue(dialog.media_row.get_visible())
        self.assertFalse(dialog.tags_row.get_visible())
        dialog.format_row.set_selected(1)
        self.assertFalse(dialog.scheduling_row.get_visible())
        self.assertTrue(dialog.tags_row.get_visible())

    def test_exporting_a_deck_writes_a_package(self):
        dialog = self.present(self.deck.id)
        target = self.path('Rivers.apkg')
        dialog._choose_target = lambda name, done: done(target)
        dialog.start()
        self.wait_done(dialog)
        self.assertEqual(dialog.target, target)
        self.assertEqual(dialog.result['notes'], 2)
        self.assertTrue(os.path.getsize(target) > 0)
        self.assertEqual(self.app.toasts, ['Exported 2 notes to Rivers.apkg'])
        self.assertEqual(dialog.stack.get_visible_child_name(), 'done')
        self.assertTrue(dialog.show_button.get_visible())
        from retain import apkg

        package = apkg.read_package(target)
        self.addCleanup(package.close)
        self.assertEqual(sorted(note.fields[0] for note in package.notes), ['Ebro', 'Tagus'])

    def test_exporting_the_collection_as_text(self):
        dialog = self.present()
        dialog.format_row.set_selected(1)
        target = self.path('all.txt')
        dialog._choose_target = lambda name, done: done(target)
        dialog.start()
        self.wait_done(dialog)
        self.assertEqual(dialog.result, {'notes': 3})
        with open(target, encoding='utf-8') as file:
            lines = [line for line in file if not line.startswith('#')]
        self.assertEqual(len(lines), 3)
        self.assertEqual(self.app.toasts, ['Exported 3 notes to all.txt'])

    def test_a_dismissed_chooser_leaves_the_dialog_as_it_was(self):
        dialog = self.present()
        dialog._choose_target = lambda name, done: done(None)
        dialog.start()
        pump()
        self.assertFalse(dialog.done)
        self.assertEqual(dialog.stack.get_visible_child_name(), 'setup')

    def test_an_unwritable_target_shows_the_error(self):
        dialog = self.present()
        dialog._choose_target = lambda name, done: done(self.path('no/such/dir/x.apkg'))
        dialog.start()
        self.wait_done(dialog)
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.error)
        self.assertEqual(dialog.done_title.get_text(), 'Export Failed')


if __name__ == '__main__':
    unittest.main()
