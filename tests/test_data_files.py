# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The desktop file, the metainfo and the schema in data/, beyond what the validators check."""

import re
import unittest

from tests import ROOT

DATA = ROOT / 'data'
DESKTOP_FILE = DATA / 'io.github.jackicus.Retain.desktop.in'
METAINFO = DATA / 'io.github.jackicus.Retain.metainfo.xml.in'
SCHEMA = DATA / 'io.github.jackicus.Retain.gschema.xml'


def desktop_entries():
    lines = DESKTOP_FILE.read_text(encoding='utf-8').splitlines()
    return dict(line.split('=', 1) for line in lines if '=' in line and not line.startswith('#'))


class DesktopFileTest(unittest.TestCase):

    def test_fits_a_phone(self):
        self.assertEqual(desktop_entries()['X-Purism-FormFactor'], 'Workstation;Mobile;')
        self.assertIn('<display_length compare="ge">360</display_length>',
                      METAINFO.read_text(encoding='utf-8'))

    def test_opens_anki_packages(self):
        self.assertIn('application/x-anki-package', desktop_entries()['MimeType'])
        self.assertIn('%F', desktop_entries()['Exec'])

    def test_the_name_takes_the_development_suffix(self):
        self.assertEqual(desktop_entries()['Name'], 'Retain@NAME_SUFFIX@')
        self.assertIn('<name>Retain@NAME_SUFFIX@</name>', METAINFO.read_text(encoding='utf-8'))


class SchemaTest(unittest.TestCase):

    def test_every_key_the_app_reads_exists(self):
        keys = set(re.findall(r'<key name="([^"]+)"', SCHEMA.read_text(encoding='utf-8')))
        used = set()
        for path in (ROOT / 'src').rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            used.update(re.findall(r"settings\.(?:get|set|bind)\w*\(\s*'([a-z-]+)'", text))
            used.update(re.findall(r'changed::([a-z-]+)', text))
        self.assertEqual(used - keys, set(), 'keys used but not in the schema')


class MetainfoTest(unittest.TestCase):

    def test_the_help_link_is_the_user_guide(self):
        text = METAINFO.read_text(encoding='utf-8')
        self.assertIn('docs/user-guide.md</url>', text)
        self.assertTrue((ROOT / 'docs' / 'user-guide.md').is_file())

    def test_the_release_matches_meson(self):
        version = re.search(r"version: '([^']+)'",
                            (ROOT / 'meson.build').read_text(encoding='utf-8')).group(1)
        self.assertIn(f'<release version="{version}"', METAINFO.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
