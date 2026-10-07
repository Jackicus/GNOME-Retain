# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What model tests share: a collection on a temporary path, and an invented clock.

    with temporary_collection() as collection:
        deck = collection.add_deck('Spanish')
        note = add_basic(collection, deck, 'hola', 'hello')

A Clock is a number of seconds a test moves on; the scheduler takes `now=clock.now`.
"""

import contextlib
import datetime
import shutil
import tempfile

from retain.collection import Collection


@contextlib.contextmanager
def temporary_collection(**kwargs):
    directory = tempfile.mkdtemp(prefix='retain-test-')
    collection = Collection(f'{directory}/collection.sqlite', backups=False, **kwargs)
    try:
        yield collection
    finally:
        collection.close()
        shutil.rmtree(directory, ignore_errors=True)


def add_basic(collection, deck, front, back, tags=()):
    basic = collection.notetype_by_name('Basic')
    return collection.add_note(basic.id, deck.id, [front, back], tags)


def add_cloze(collection, deck, text, extra='', tags=()):
    cloze = collection.notetype_by_name('Cloze')
    return collection.add_note(cloze.id, deck.id, [text, extra], tags)


class Clock:
    """An invented now: starts at noon on an invented day, moves with advance()."""

    def __init__(self, start=None):
        self.now = start or datetime.datetime(2026, 3, 10, 12, 0).timestamp()

    def advance(self, seconds=0, minutes=0, hours=0, days=0):
        self.now += seconds + minutes * 60 + hours * 3600 + days * 86400
        return self.now
