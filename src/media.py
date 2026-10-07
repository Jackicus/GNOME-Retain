# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The media folder: the pictures and sounds a note's fields refer to by bare file name.

    store = MediaStore(directory)
    name = store.add_file('/home/me/cat.jpg')          # 'cat.jpg', or 'cat-2.jpg' if taken
    name = store.add_bytes('0', data, 'paste.png')     # from an import or the clipboard
    store.path(name), store.exists(name), store.names(), store.uri(name)
    store.remove_unused(referenced)                   # a check-media pass

A file already in the folder with the same content (SHA-1) keeps its name: adding it again
answers the existing name. Names are made safe (no path separators, NFC) as Anki keeps them.
"""

import hashlib
import os
import pathlib
import re
import unicodedata

UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name):
    """A file name Anki and every file system accept: the base name, control and path
    characters replaced, NFC-normalized, 'file' when nothing is left."""
    name = unicodedata.normalize('NFC', os.path.basename(name or ''))
    name = UNSAFE.sub('_', name).strip().strip('.')
    return name or 'file'


class MediaStore:

    def __init__(self, directory):
        self.directory = pathlib.Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._hashes = None  # sha1 -> name, read on first use

    def path(self, name):
        return self.directory / safe_name(name)

    def uri(self, name):
        return self.path(name).as_uri()

    def exists(self, name):
        return self.path(name).is_file()

    def names(self):
        return sorted(path.name for path in self.directory.iterdir() if path.is_file())

    def _index(self):
        if self._hashes is None:
            self._hashes = {}
            for path in self.directory.iterdir():
                if path.is_file():
                    self._hashes[_sha1(path.read_bytes())] = path.name
        return self._hashes

    def add_bytes(self, data, preferred_name):
        """Store `data` under `preferred_name` (made unique); the name used."""
        digest = _sha1(data)
        existing = self._index().get(digest)
        if existing and self.exists(existing):
            return existing
        name = self._unique_name(safe_name(preferred_name))
        self.path(name).write_bytes(data)
        self._index()[digest] = name
        return name

    def add_file(self, source, preferred_name=None):
        """Copy the file at `source` into the folder; the name used."""
        source = pathlib.Path(source)
        return self.add_bytes(source.read_bytes(), preferred_name or source.name)

    def remove(self, name):
        path = self.path(name)
        if path.is_file():
            path.unlink()
        if self._hashes is not None:
            self._hashes = {digest: kept for digest, kept in self._hashes.items()
                            if kept != name}

    def remove_unused(self, referenced):
        """Delete every file no field refers to; the names removed."""
        referenced = set(referenced)
        removed = [name for name in self.names() if name not in referenced]
        for name in removed:
            self.remove(name)
        return removed

    def missing(self, referenced):
        """The referenced names with no file."""
        return sorted(name for name in set(referenced) if not self.exists(name))

    def _unique_name(self, name):
        if not self.exists(name):
            return name
        stem, dot, suffix = name.rpartition('.')
        if not dot:
            stem, suffix = name, ''
        counter = 2
        while True:
            candidate = f'{stem}-{counter}.{suffix}' if dot else f'{stem}-{counter}'
            if not self.exists(candidate):
                return candidate
            counter += 1


def _sha1(data):
    return hashlib.sha1(data).hexdigest()
