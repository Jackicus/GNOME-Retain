# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""media.py: the media folder on a temporary directory."""

import pathlib
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from retain.media import MediaStore, safe_name

PNG = b'\x89PNG\r\n\x1a\n invented picture bytes'
JPEG = b'\xff\xd8\xff another invented picture'


class SafeNameTest(unittest.TestCase):

    def test_path_separators_are_stripped_to_the_base_name(self):
        self.assertEqual(safe_name('../../etc/passwd'), 'passwd')
        self.assertEqual(safe_name('/home/me/pictures/cat.jpg'), 'cat.jpg')

    def test_unsafe_characters_become_underscores(self):
        self.assertEqual(safe_name('a\\b:c*d?e"f<g>h|i.png'), 'a_b_c_d_e_f_g_h_i.png')
        self.assertEqual(safe_name('tab\there.txt'), 'tab_here.txt')

    def test_blank_and_dotted_names_fall_back_to_file(self):
        self.assertEqual(safe_name(''), 'file')
        self.assertEqual(safe_name(None), 'file')
        self.assertEqual(safe_name('...'), 'file')
        self.assertEqual(safe_name('  .hidden.  '), 'hidden')

    def test_names_are_nfc_normalized(self):
        decomposed = 'café.jpg'
        self.assertEqual(safe_name(decomposed), 'café.jpg')


class MediaStoreTest(unittest.TestCase):

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='retain-media-'))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.store = MediaStore(self.directory / 'media')

    def test_the_folder_is_created(self):
        self.assertTrue((self.directory / 'media').is_dir())
        self.assertEqual(self.store.names(), [])

    def test_add_bytes_stores_the_file_under_the_name(self):
        name = self.store.add_bytes(PNG, 'paste.png')
        self.assertEqual(name, 'paste.png')
        self.assertTrue(self.store.exists('paste.png'))
        self.assertEqual(self.store.path('paste.png').read_bytes(), PNG)
        self.assertEqual(self.store.names(), ['paste.png'])

    def test_add_bytes_dedupes_by_content(self):
        first = self.store.add_bytes(PNG, 'cat.png')
        again = self.store.add_bytes(PNG, 'other-name.png')
        self.assertEqual(again, first)
        self.assertEqual(self.store.names(), ['cat.png'])

    def test_different_content_under_a_taken_name_gets_a_counter(self):
        self.assertEqual(self.store.add_bytes(PNG, 'cat.jpg'), 'cat.jpg')
        self.assertEqual(self.store.add_bytes(JPEG, 'cat.jpg'), 'cat-2.jpg')
        self.assertEqual(self.store.add_bytes(b'a third cat', 'cat.jpg'), 'cat-3.jpg')
        self.assertEqual(self.store.names(), ['cat-2.jpg', 'cat-3.jpg', 'cat.jpg'])

    def test_a_name_without_an_extension_gets_a_counter_too(self):
        self.assertEqual(self.store.add_bytes(b'one', 'readme'), 'readme')
        self.assertEqual(self.store.add_bytes(b'two', 'readme'), 'readme-2')

    def test_add_bytes_makes_the_preferred_name_safe(self):
        name = self.store.add_bytes(PNG, '../evil/na:me.png')
        self.assertEqual(name, 'na_me.png')
        self.assertTrue((self.directory / 'media' / 'na_me.png').is_file())
        self.assertFalse((self.directory / 'evil').exists())

    def test_add_file_copies_the_file(self):
        source = self.directory / 'dog.jpg'
        source.write_bytes(JPEG)
        self.assertEqual(self.store.add_file(source), 'dog.jpg')
        self.assertEqual(self.store.path('dog.jpg').read_bytes(), JPEG)
        self.assertTrue(source.exists())
        self.assertEqual(self.store.add_file(str(source), 'renamed.jpg'), 'dog.jpg')
        other = self.directory / 'other.jpg'
        other.write_bytes(b'other bytes')
        self.assertEqual(self.store.add_file(other, 'picked.jpg'), 'picked.jpg')

    def test_the_index_covers_files_already_in_the_folder(self):
        (self.directory / 'media' / 'old.png').write_bytes(PNG)
        self.assertEqual(self.store.add_bytes(PNG, 'new.png'), 'old.png')

    def test_remove_forgets_the_content(self):
        self.store.add_bytes(PNG, 'cat.png')
        self.store.remove('cat.png')
        self.assertFalse(self.store.exists('cat.png'))
        self.assertEqual(self.store.add_bytes(PNG, 'again.png'), 'again.png')
        self.store.remove('never-there.png')

    def test_remove_unused_deletes_what_no_field_refers_to(self):
        self.store.add_bytes(PNG, 'kept.png')
        self.store.add_bytes(JPEG, 'stray.jpg')
        self.store.add_bytes(b'more', 'also-stray.jpg')
        removed = self.store.remove_unused({'kept.png', 'not-a-file.png'})
        self.assertEqual(removed, ['also-stray.jpg', 'stray.jpg'])
        self.assertEqual(self.store.names(), ['kept.png'])

    def test_missing_lists_referenced_names_without_a_file(self):
        self.store.add_bytes(PNG, 'kept.png')
        self.assertEqual(self.store.missing(['kept.png', 'gone.mp3', 'absent.png', 'gone.mp3']),
                         ['absent.png', 'gone.mp3'])
        self.assertEqual(self.store.missing([]), [])

    def test_uri_points_into_the_folder(self):
        self.store.add_bytes(PNG, 'cat.png')
        uri = self.store.uri('cat.png')
        self.assertTrue(uri.startswith('file://'))
        self.assertEqual(uri, (self.directory / 'media' / 'cat.png').as_uri())


if __name__ == '__main__':
    unittest.main()
