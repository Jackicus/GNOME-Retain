# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The build's lists agree with the tree: every Python module under src/ is installed, every
.blp is compiled and its .ui in the gresource, and every module with translatable strings is
in po/POTFILES.in."""

import re
import unittest

from tests import ROOT

SRC = ROOT / 'src'
MESON = (SRC / 'meson.build').read_text(encoding='utf-8')
GRESOURCE = (SRC / 'retain.gresource.xml').read_text(encoding='utf-8')
POTFILES = (ROOT / 'po' / 'POTFILES.in').read_text(encoding='utf-8').split()


def quoted(text):
    return set(re.findall(r"'([^']+)'", text))


class InstallListTest(unittest.TestCase):

    def test_every_module_is_installed(self):
        modules = {str(path.relative_to(SRC)) for path in SRC.rglob('*.py')}
        listed = {name for name in quoted(MESON)
                  if name.endswith('.py') and name != 'blueprint-compiler.py'}
        self.assertEqual(modules - listed, set(), 'modules missing from src/meson.build')
        self.assertEqual(listed - modules, set(), 'src/meson.build lists modules that are gone')

    def test_every_blueprint_is_compiled_and_bundled(self):
        blueprints = {str(path.relative_to(SRC)) for path in SRC.rglob('*.blp')}
        listed = {name for name in quoted(MESON) if name.endswith('.blp')}
        self.assertEqual(blueprints, listed)
        bundled = set(re.findall(r'>([\w.]+\.ui)<', GRESOURCE))
        expected = {path.rsplit('/', 1)[-1].replace('.blp', '.ui') for path in blueprints}
        self.assertEqual(bundled, expected)

    def test_every_translated_module_is_in_potfiles(self):
        pattern = re.compile(r'\b(?:_|ngettext|N_)\(')
        translated = set()
        for path in list(SRC.rglob('*.py')) + list(SRC.rglob('*.blp')):
            if path.name == 'i18n.py':
                continue  # it explains _() without calling it
            if pattern.search(path.read_text(encoding='utf-8')):
                translated.add('src/' + str(path.relative_to(SRC)))
        listed = {name for name in POTFILES if name.startswith('src/')}
        self.assertEqual(translated - listed, set(), 'translated files missing from POTFILES.in')
        for name in listed:
            self.assertTrue((ROOT / name).exists(), f'{name} in POTFILES.in is gone')


if __name__ == '__main__':
    unittest.main()
