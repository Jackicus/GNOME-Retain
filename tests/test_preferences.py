# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""dialogs/preferences.py: the switches and spin rows follow and change their settings,
Back Up Now writes a backup, Check Media counts the unused and missing files and Delete
Unused removes them, the Sync group follows the sync folder, syncs and lists the other
devices. On a stand-in application with a collection on a temporary path and
settings on the memory backend."""

import json
import os
import pathlib
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import pump, requires_gtk, wait_for
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
                    'card-text-scale', 'day-start-hour', 'learn-ahead-minutes',
                    'sync-folder', 'sync-automatically', 'sync-device-name'):
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

    def test_without_a_sync_folder_the_sync_rows_wait(self):
        dialog = self.present()
        self.assertEqual(dialog.folder_row.get_subtitle(), 'None chosen')
        self.assertFalse(dialog.forget_button.get_visible())
        self.assertFalse(dialog.sync_row.get_sensitive())
        self.assertFalse(dialog.sync_button.get_sensitive())
        self.assertFalse(dialog.devices_group.get_visible())
        self.assertTrue(dialog.auto_row.get_active())
        self.assertTrue(dialog.device_name_row.get_text())  # the host name, to start with

    def test_choosing_a_folder_syncs_and_lists_the_other_devices(self):
        folder = pathlib.Path(self.directory) / 'shared'
        (folder / 'devices').mkdir(parents=True)
        (folder / 'devices' / 'feedc0de.json').write_text(json.dumps(
            {'id': 'feedc0de', 'name': 'Invented Laptop', 'synced': 1_780_000_000}))
        self.settings.set_string('sync-device-name', 'Test Desk')
        dialog = self.present()
        self.assertEqual(dialog.sync_row.get_subtitle(), 'Never')
        dialog.set_folder(folder)
        self.assertTrue(dialog.sync_spinner.get_visible())
        self.assertFalse(dialog.sync_button.get_sensitive())
        self.assertTrue(wait_for(lambda: not self.app.sync.running, timeout=10))
        pump()
        self.assertEqual(self.settings.get_string('sync-folder'), str(folder))
        self.assertFalse(dialog.sync_spinner.get_visible())
        self.assertTrue(dialog.sync_button.get_sensitive())
        self.assertNotEqual(dialog.sync_row.get_subtitle(), 'Never')
        self.assertTrue(dialog.forget_button.get_visible())
        self.assertEqual([row.get_title() for row in dialog._device_rows], ['Invented Laptop'])
        own = json.loads(next(path for path in (folder / 'devices').glob('*.json')
                              if path.stem != 'feedc0de').read_text())
        self.assertEqual(own['name'], 'Test Desk')
        dialog.forget_folder()
        self.assertEqual(dialog.folder_row.get_subtitle(), 'None chosen')
        self.assertFalse(dialog.devices_group.get_visible())


if __name__ == '__main__':
    unittest.main()
