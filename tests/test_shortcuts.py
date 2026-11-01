# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Keyboard Shortcuts dialog lists every key and opens, and the accelerators are sane."""

import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import pump, requires_gtk
from retain import shortcuts

# Accelerators the HIG gives a meaning the app must honour if it binds them.
STANDARD = {'<primary>n': 'app.add', '<primary>o': 'app.import', '<primary>q': 'app.quit',
            '<primary>f': 'win.search', '<primary>comma': 'app.preferences',
            '<primary>question': 'app.shortcuts', '<primary>z': 'app.undo'}


class ShortcutsTest(unittest.TestCase):

    def test_the_dialog_lists_every_accelerator(self):
        listed = set()
        for _title, items in shortcuts.sections():
            for _description, accel in items:
                listed.update(accel.split())
        for name, accels in shortcuts.ACCELS.items():
            for accel in accels:
                self.assertIn(accel, listed, f'{name} ({accel}) is not in the dialog')
        for table in (shortcuts.REVIEW, shortcuts.BROWSE, shortcuts.EDITOR):
            for key, accel in table.items():
                self.assertIn(accel, listed, f'{key} ({accel}) is not in the dialog')

    def test_standard_accelerators_keep_their_meaning(self):
        for accel, action in STANDARD.items():
            owners = [name for name, accels in shortcuts.ACCELS.items() if accel in accels]
            self.assertEqual(owners, [action], accel)

    def test_no_accelerator_is_bound_twice(self):
        seen = {}
        for name, accels in shortcuts.ACCELS.items():
            for accel in accels:
                self.assertNotIn(accel, seen, f'{accel}: {name} and {seen.get(accel)}')
                seen[accel] = name

    def test_accelerator_lookup(self):
        self.assertEqual(shortcuts.accelerator('again'), '1')
        self.assertEqual(shortcuts.accelerator('app.add'), '<primary>n')
        with self.assertRaises(KeyError):
            shortcuts.accelerator('nothing')



@requires_gtk
class DialogTest(unittest.TestCase):

    def test_the_dialog_opens_with_every_section(self):
        from gi.repository import Adw, Gtk
        from retain.dialogs import shortcuts as dialog_module

        window = Adw.Window()
        window.present()
        self.addCleanup(window.destroy)
        dialog = dialog_module.present(window)
        self.addCleanup(dialog.force_close)
        pump()
        self.assertIsInstance(dialog, Adw.ShortcutsDialog)
        self.assertTrue(dialog.get_mapped())
        rows = []
        stack = [dialog]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.ListBoxRow) and hasattr(widget, 'get_title'):
                rows.append(widget.get_title())
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        listed = [title for _section, items in shortcuts.sections() for title, _accel in items]
        self.assertTrue(rows)
        self.assertLessEqual(set(rows), set(listed))


if __name__ == '__main__':
    unittest.main()
