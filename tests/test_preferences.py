# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""dialogs/preferences.py: the switches and spin rows follow and change their settings,
Back Up Now writes a backup, Check Media counts the unused and missing files and Delete
Unused removes them. On a stand-in application with a collection on a temporary path and
settings on the memory backend."""

import os
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import pump, requires_gtk
from tests.support import add_basic
from tests.test_import_export_dialogs import FakeApp, make_window
from retain.collection import Collection

PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(24))


@requires_gtk
class PreferencesTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='retain-prefs-')
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.collection = Collection(os.path.join(self.directory, 'collection.sqlite'),
                                     backups=False)
        self.addCleanup(self.collection.close)
        self.app = FakeApp(self.collection)
        self.settings = self.app.settings
        for key in ('show-remaining', 'show-intervals', 'auto-play-audio', 'two-button-mode',
                    'card-text-scale', 'day-start-hour', 'learn-ahead-minutes'):
            self.settings.reset(key)
        self.window = make_window()
        self.window.present()
        self.addCleanup(self.window.destroy)
        pump()

    def present(self):
        from retain.dialogs import preferences

        dialog = preferences.present(self.app, self.window)
        self.addCleanup(dialog.force_close)
        pump()
        return dialog

    def test_the_switches_show_the_settings(self):
        self.settings.set_boolean('show-remaining', False)
        self.settings.set_boolean('two-button-mode', True)
        dialog = self.present()
        self.assertFalse(dialog.remaining_row.get_active())
        self.assertTrue(dialog.intervals_row.get_active())
        self.assertTrue(dialog.sound_row.get_active())
        self.assertTrue(dialog.two_button_row.get_active())

    def test_a_switch_changes_its_setting(self):
        dialog = self.present()
        dialog.sound_row.set_active(False)
        self.assertFalse(self.settings.get_boolean('auto-play-audio'))
        dialog.two_button_row.set_active(True)
        self.assertTrue(self.settings.get_boolean('two-button-mode'))
        self.settings.set_boolean('show-intervals', False)
        self.assertFalse(dialog.intervals_row.get_active())

    def test_the_text_size_is_a_percentage_of_the_scale(self):
        self.settings.set_double('card-text-scale', 1.5)
        dialog = self.present()
        self.assertEqual(dialog.text_size_row.get_value(), 150)
        dialog.text_size_row.set_value(80)
        self.assertAlmostEqual(self.settings.get_double('card-text-scale'), 0.8)
        self.settings.set_double('card-text-scale', 2.0)
        self.assertEqual(dialog.text_size_row.get_value(), 200)

    def test_the_day_rows_change_their_settings(self):
        dialog = self.present()
        self.assertEqual(dialog.day_start_row.get_value(), 4)
        self.assertEqual(dialog.learn_ahead_row.get_value(), 20)
        dialog.day_start_row.set_value(6)
        self.assertEqual(self.settings.get_int('day-start-hour'), 6)
        dialog.learn_ahead_row.set_value(45)
        self.assertEqual(self.settings.get_int('learn-ahead-minutes'), 45)
        self.settings.set_int('day-start-hour', 2)
        self.assertEqual(dialog.day_start_row.get_value(), 2)

    def test_closing_lets_the_settings_go(self):
        dialog = self.present()
        dialog.force_close()
        pump()
        self.settings.set_boolean('show-remaining', False)
        self.settings.set_int('learn-ahead-minutes', 99)
        self.assertTrue(dialog.remaining_row.get_active())
        self.assertEqual(dialog.learn_ahead_row.get_value(), 20)

    def test_the_location_row_names_the_folder(self):
        dialog = self.present()
        self.assertIn(os.path.basename(self.directory), dialog.location_row.get_subtitle())
        self.assertEqual(dialog.backups_row.get_subtitle(), 'No backups yet')

    def test_back_up_now_writes_a_backup_and_says_so(self):
        dialog = self.present()
        path = dialog.back_up()
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.collection.path.parent / 'backups')
        self.assertEqual(self.app.toasts, [f'Backed up to {path.name}'])
        self.assertTrue(dialog.backups_row.get_subtitle().startswith('1 backup,'))

    def test_check_media_reports_a_clean_folder(self):
        dialog = self.present()
        alert = dialog.check_media()
        self.addCleanup(alert.force_close)
        self.assertEqual(alert.get_heading(), 'Media Checked')
        self.assertIn('Every media file is used', alert.get_body())
        self.assertFalse(alert.has_response('delete'))

    def test_check_media_counts_unused_and_missing_and_deletes_the_unused(self):
        deck = self.collection.add_deck('Birds')
        add_basic(self.collection, deck, 'Wren', '<img src="wren.png"> [sound:wren.ogg]')
        self.collection.media.add_bytes(PNG, 'wren.png')
        self.collection.media.add_bytes(PNG + b'x', 'spare.png')
        dialog = self.present()
        alert = dialog.check_media()
        self.addCleanup(alert.force_close)
        body = alert.get_body()
        self.assertIn('1 file is used by no note: spare.png', body)
        self.assertIn('1 file the notes refer to is missing: wren.ogg', body)
        self.assertTrue(alert.has_response('delete'))
        alert.emit('response', 'delete')
        self.assertEqual(self.collection.media.names(), ['wren.png'])
        self.assertEqual(self.app.toasts, ['Removed 1 unused file'])

    def test_the_parent_hears_the_dialog_open_and_close(self):
        dialog = self.present()
        self.assertEqual(self.window.dialog_open, [True])
        dialog.force_close()
        pump()
        self.assertEqual(self.window.dialog_open, [True, False])


if __name__ == '__main__':
    unittest.main()
