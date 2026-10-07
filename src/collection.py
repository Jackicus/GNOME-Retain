# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The collection: every deck, note type, note, card and review, in one SQLite file
(schema.py), with the media folder beside it (media.py) and an undo stack.

    collection = Collection(path)            # created when missing, with the stock note types
    deck = collection.add_deck('Spanish::Verbs')
    note = collection.add_note(notetype.id, deck.id, ['hablar', 'to speak'], ['verbs'])
    cards = collection.find_cards('deck:Spanish is:due')
    with collection.undoable(_('Suspend')):   # one undo step, one transaction
        collection.suspend(cards)
    collection.undo()                        # -> the label, and `changed` fires
    collection.close()

Objects (Note, Card, Deck) are plain records of their rows: reading one gives a copy, and
changing the collection goes through its methods, which write, record what to restore for
undo, and emit `changed` with what kind of thing changed ('cards', 'notes', 'decks',
'notetypes', 'configs', 'config') once the outermost undoable block ends. Scheduling (what to
show, what an answer does) is scheduler.py's, which calls record_answer() here; searches are
search.py's syntax; rendering a card's two sides is render_card(), on template.py.

The collection is not thread-safe: a thread that needs it (an import) opens its own
Collection on the same path and closes it; the main one then emits `changed` itself.
"""

import contextlib
import json
import logging
import os
import pathlib
import random
import shutil
import sqlite3
import string
import time

from gi.repository import GObject

from . import days, notetypes, schema, search, template
from .deck_config import DEFAULT_ID, LEECH_TAG, DeckConfig
from .media import MediaStore

log = logging.getLogger(__name__)

STATES = ('new', 'learning', 'review', 'relearning')
BACKUP_COUNT = 10
BACKUP_INTERVAL = 6 * 3600  # a backup at most this often (seconds)
UNDO_LIMIT = 50
GUID_ALPHABET = string.ascii_letters + string.digits + '!#$%&()*+,-./:;<=>?@[]^_`{|}~'


class CollectionError(Exception):
    """A user-facing problem: the message is a sentence."""


def default_data_dir(profile='default'):
    """Where the collection lives: $RETAIN_DATA_DIR, else $XDG_DATA_HOME/retain (retain-devel
    for the development build)."""
    override = os.environ.get('RETAIN_DATA_DIR')
    if override:
        return pathlib.Path(override)
    base = pathlib.Path(os.environ.get('XDG_DATA_HOME') or pathlib.Path.home() / '.local' / 'share')
    return base / ('retain-devel' if profile == 'development' else 'retain')


class Record:
    """A row as an object: `FIELDS` are the columns, in the table's order."""

    FIELDS = ()

    def __init__(self, **values):
        for name in self.FIELDS:
            setattr(self, name, values.pop(name, None))
        if values:
            raise TypeError(f'{type(self).__name__} has no {", ".join(values)}')

    @classmethod
    def from_row(cls, row):
        return cls(**{name: row[name] for name in cls.FIELDS})

    def to_row(self):
        return {name: getattr(self, name) for name in self.FIELDS}

    def copy(self):
        return type(self)(**self.to_row())

    def __eq__(self, other):
        return type(other) is type(self) and other.to_row() == self.to_row()

    def __repr__(self):
        return f'{type(self).__name__}({self.to_row()})'


class Deck(Record):
    FIELDS = ('id', 'name', 'config_id', 'description', 'collapsed', 'original_id', 'modified')

    @property
    def basename(self):
        """The last part of the name: 'Verbs' for 'Spanish::Verbs'."""
        return self.name.rsplit('::', 1)[-1]

    @property
    def parent_name(self):
        return self.name.rsplit('::', 1)[0] if '::' in self.name else None

    @property
    def level(self):
        return self.name.count('::')


class Note(Record):
    FIELDS = ('id', 'guid', 'notetype_id', 'fields', 'sort_field', 'tags', 'marked', 'created',
              'modified')

    @classmethod
    def from_row(cls, row):
        note = super().from_row(row)
        note.fields = note.fields.split(template.FIELD_SEPARATOR)
        note.tags = note.tags.split()
        note.marked = bool(note.marked)
        return note

    def to_row(self):
        row = super().to_row()
        row['fields'] = template.FIELD_SEPARATOR.join(self.fields)
        row['tags'] = join_tags(self.tags)
        row['marked'] = int(bool(self.marked))
        return row

    def copy(self):
        note = Note(**super().to_row())
        note.fields = list(self.fields)
        note.tags = list(self.tags)
        return note


class Card(Record):
    FIELDS = ('id', 'note_id', 'deck_id', 'ord', 'state', 'due', 'interval', 'stability',
              'difficulty', 'reps', 'lapses', 'left', 'last_review', 'flag', 'suspended',
              'buried', 'created', 'modified')

    @property
    def is_new(self):
        return self.state == 'new'

    @property
    def in_learning(self):
        return self.state in ('learning', 'relearning')


class DeckNode:
    """A deck in the tree: its children, and the counts the scheduler fills in."""

    def __init__(self, deck, level):
        self.deck = deck
        self.level = level
        self.children = []
        self.new = self.learning = self.due = 0

    @property
    def total(self):
        return self.new + self.learning + self.due

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def join_tags(tags):
    """The tags column: ' a b ' (so ' tag ' finds a whole tag), ' ' for none. A tag is the
    same whatever its case: the first spelling given is kept."""
    kept = {}
    for tag in tags:
        tag = search.normalize_tag(tag)
        if tag and tag.lower() not in kept:
            kept[tag.lower()] = tag
    tags = sorted(kept.values(), key=str.lower)
    return ' ' + ' '.join(tags) + ' ' if tags else ' '


def new_guid():
    return ''.join(random.choice(GUID_ALPHABET) for _ in range(10))


def natural_key(name):
    """Sort decks as people read them: case-insensitive, numbers by value."""
    import re

    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', name)]


class Collection(GObject.Object):
    """See the module. `day_start_hour` (the setting) says when a day turns over."""

    __gtype_name__ = 'RetainCollection'

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, path, day_start_hour=days.DEFAULT_DAY_START_HOUR, backups=True):
        super().__init__()
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.media = MediaStore(self.path.parent / 'media')
        self.day_start_hour = day_start_hour
        self.backups = backups
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode = WAL')
        self.db.execute('PRAGMA foreign_keys = OFF')
        search.register_functions(self.db)
        self.db.executescript(schema.SCHEMA)
        self._undo = []  # [(label, restore dict)], the newest last
        self._open = None  # the outermost undoable block's restore dict while one runs
        self._changes = set()
        self._last_ids = {}  # table -> the last id given
        self._modified = False
        self._init_collection()
        self.unbury_past()
        self._modified = False  # what the opening wrote is no reason for a backup

    # -- lifecycle ----------------------------------------------------------------------

    def _init_collection(self):
        if self.get('created') is None:
            self.set('created', int(time.time()))
            self.set('schema_version', schema.VERSION)
        if not self.db.execute('SELECT 1 FROM deck_configs LIMIT 1').fetchone():
            config = DeckConfig(id=DEFAULT_ID, name='Default')
            self._insert('deck_configs', config.to_row())
        if not self.db.execute('SELECT 1 FROM notetypes LIMIT 1').fetchone():
            for notetype in notetypes.all_stock():
                self._add_notetype_row(notetype)
        if not self.db.execute('SELECT 1 FROM decks LIMIT 1').fetchone():
            self._insert('decks', Deck(id=1, name='Default', config_id=DEFAULT_ID,
                                       description='', collapsed=0, original_id=None,
                                       modified=int(time.time())).to_row())

    def close(self):
        """Close the file, after a backup when something changed and the last one is old."""
        if self.db is None:
            return
        if self._modified and self.backups:
            try:
                self.backup()
            except OSError as error:
                log.warning('backup failed: %s', error)
        self.db.close()
        self.db = None

    def backup(self, force=False):
        """Copy the collection to backups/ (the newest BACKUP_COUNT kept), unless one was
        made within BACKUP_INTERVAL; the path written, or None."""
        folder = self.path.parent / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        existing = sorted(folder.glob('collection-*.sqlite'))
        if existing and not force:
            if time.time() - existing[-1].stat().st_mtime < BACKUP_INTERVAL:
                return None
        target = folder / time.strftime('collection-%Y%m%d-%H%M%S.sqlite')
        backup_db = sqlite3.connect(target)
        with backup_db:
            self.db.backup(backup_db)
        backup_db.close()
        for old in sorted(folder.glob('collection-*.sqlite'))[:-BACKUP_COUNT]:
            old.unlink()
        return target

    def today(self, now=None):
        return days.today(self.day_start_hour, now)

    def day_number(self, timestamp):
        return days.day_number(timestamp, self.day_start_hour)

    def day_start(self, number):
        return days.day_start(number, self.day_start_hour)

    # -- config ---------------------------------------------------------------------------

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set(self, key, value):
        self._set_quietly(key, value)
        self._touch('config')

    def _set_quietly(self, key, value):
        """Store a value of the collection's own (a counter) without reporting a change."""
        self.db.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                        (key, json.dumps(value)))
        self._modified = True

    # -- undo -------------------------------------------------------------------------------

    @contextlib.contextmanager
    def undoable(self, label):
        """One undo step: every change inside is put back together by undo(). Nested blocks
        join the outermost. Emits `changed` (once per kind) when the outermost block ends."""
        if self._open is not None:
            yield self._open
            return
        restore = {'label': label, 'cards': {}, 'notes': {}, 'decks': {}, 'notetypes': {},
                   'configs': {}, 'new_cards': set(), 'new_notes': set(), 'new_decks': set(),
                   'new_notetypes': set(), 'new_configs': set(), 'revlog': set(),
                   'config': {}}
        self._open = restore
        self.db.execute('BEGIN')
        try:
            yield restore
        except BaseException:
            self.db.execute('ROLLBACK')
            self._open = None
            self._changes.clear()
            raise
        self.db.execute('COMMIT')
        self._open = None
        if label is not None:
            self._undo.append(restore)
            del self._undo[:-UNDO_LIMIT]
        self._flush_changes()

    @property
    def undo_label(self):
        """What undo() would put back, or None."""
        return self._undo[-1]['label'] if self._undo else None

    def can_undo(self):
        return bool(self._undo)

    def clear_undo(self):
        self._undo.clear()

    def undo(self):
        """Put the newest undo step back; its label, or None when there is none."""
        if not self._undo:
            return None
        restore = self._undo.pop()
        with self.undoable(None):
            for table, key in (('cards', 'new_cards'), ('notes', 'new_notes'),
                               ('decks', 'new_decks'), ('notetypes', 'new_notetypes'),
                               ('deck_configs', 'new_configs')):
                for row_id in restore[key]:
                    self.db.execute(f'DELETE FROM {table} WHERE id = ?', (row_id,))
                    self._touch(table if table != 'deck_configs' else 'configs')
            for table, kind in (('cards', 'cards'), ('notes', 'notes'), ('decks', 'decks'),
                                ('notetypes', 'notetypes'), ('deck_configs', 'configs')):
                for row in restore[kind].values():
                    self._insert(table, row, replace=True)
                    self._touch(kind)
            for revlog_id in restore['revlog']:
                self.db.execute('DELETE FROM revlog WHERE id = ?', (revlog_id,))
            for key, value in restore['config'].items():
                if value is None:
                    self.db.execute('DELETE FROM config WHERE key = ?', (key,))
                else:
                    self.db.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                                    (key, value))
                self._touch('config')
        return restore['label']

    def _remember(self, kind, table, ids):
        """Keep the rows of `ids` (those not yet kept, nor created in this step) so undo
        can put them back."""
        if self._open is None:
            return
        kept = self._open[kind]
        created = self._open['new_' + kind]
        wanted = [row_id for row_id in ids if row_id not in kept and row_id not in created]
        for chunk in _chunks(wanted):
            marks = ','.join('?' * len(chunk))
            for row in self.db.execute(f'SELECT * FROM {table} WHERE id IN ({marks})', chunk):
                kept[row['id']] = dict(row)

    def _created(self, kind, row_id):
        if self._open is not None:
            self._open['new_' + kind].add(row_id)

    def _touch(self, kind):
        self._modified = True
        self._changes.add(kind)
        if self._open is None:
            self._flush_changes()

    def _flush_changes(self):
        changes, self._changes = sorted(self._changes), set()
        for kind in changes:
            self.emit('changed', kind)

    # -- ids -----------------------------------------------------------------------------

    def next_id(self, now=None, table='rows'):
        """A new row id: the time (`now`, else the clock's) in milliseconds, unique within
        this collection. The revlog's ids are their own sequence (`table='revlog'`): a
        review stamped with a session's clock may be older than the newest note."""
        candidate = int((now or time.time()) * 1000)
        if table == 'revlog':
            row = self.db.execute('SELECT MAX(id) AS m FROM revlog').fetchone()
        else:
            row = self.db.execute(
                'SELECT MAX(id) AS m FROM (SELECT MAX(id) AS id FROM notes UNION ALL '
                'SELECT MAX(id) FROM cards UNION ALL SELECT MAX(id) FROM decks UNION ALL '
                'SELECT MAX(id) FROM notetypes UNION ALL SELECT MAX(id) FROM deck_configs)'
            ).fetchone()
        floor = max(self._last_ids.get(table, 0), row['m'] or 0)
        self._last_ids[table] = max(candidate, floor + 1)
        return self._last_ids[table]

    def _insert(self, table, row, replace=False):
        columns = ', '.join(row)
        marks = ', '.join('?' * len(row))
        verb = 'INSERT OR REPLACE' if replace else 'INSERT'
        self.db.execute(f'{verb} INTO {table} ({columns}) VALUES ({marks})', list(row.values()))

    def _update(self, table, row):
        assignments = ', '.join(f'{name} = ?' for name in row if name != 'id')
        values = [value for name, value in row.items() if name != 'id'] + [row['id']]
        self.db.execute(f'UPDATE {table} SET {assignments} WHERE id = ?', values)

    # -- note types -----------------------------------------------------------------------

    def notetypes(self):
        rows = self.db.execute('SELECT * FROM notetypes ORDER BY name COLLATE NOCASE').fetchall()
        return [notetypes.NoteType.from_row(row) for row in rows]

    def notetype(self, notetype_id):
        row = self.db.execute('SELECT * FROM notetypes WHERE id = ?', (notetype_id,)).fetchone()
        return notetypes.NoteType.from_row(row) if row else None

    def notetype_by_name(self, name):
        row = self.db.execute('SELECT * FROM notetypes WHERE name = ? COLLATE NOCASE',
                              (name,)).fetchone()
        return notetypes.NoteType.from_row(row) if row else None

    def _add_notetype_row(self, notetype):
        notetype.id = notetype.id or self.next_id()
        notetype.modified = int(time.time())
        self._insert('notetypes', notetype.to_row())
        return notetype

    def add_notetype(self, notetype):
        """Store a NoteType (notetypes.py); it gets an id. Returns it."""
        with self.undoable(_('Add Note Type')):
            self._add_notetype_row(notetype)
            self._created('notetypes', notetype.id)
            self._touch('notetypes')
        return notetype

    def update_notetype(self, notetype, deck_id=None):
        """Store changed fields, templates or CSS, and bring its notes' cards into line:
        cards for templates that now generate are added (into `deck_id`, else the note's
        first card's deck), cards of templates that no longer exist are removed. A field
        added gets '' in every note; a field removed loses its contents."""
        with self.undoable(_('Edit Note Type')):
            old = self.notetype(notetype.id)
            self._remember('notetypes', 'notetypes', [notetype.id])
            notetype.modified = int(time.time())
            self._update('notetypes', notetype.to_row())
            if old is not None and old.field_names() != notetype.field_names():
                self._remap_fields(old, notetype)
            for note in self._notes_of_type(notetype.id):
                self._generate_cards(note, notetype, deck_id)
            self._touch('notetypes')

    def _remap_fields(self, old, new):
        """Carry each note's field contents across a change of fields, matched by name."""
        old_names = old.field_names()
        new_names = new.field_names()
        for note in self._notes_of_type(new.id):
            self._remember('notes', 'notes', [note.id])
            values = dict(zip(old_names, note.fields, strict=False))
            note.fields = [values.get(name, '') for name in new_names]
            self._store_note(note, new)

    def remove_notetype(self, notetype_id):
        """Delete a note type and every note of it."""
        with self.undoable(_('Delete Note Type')):
            note_ids = [row['id'] for row in self.db.execute(
                'SELECT id FROM notes WHERE notetype_id = ?', (notetype_id,))]
            self.remove_notes(note_ids)
            self._remember('notetypes', 'notetypes', [notetype_id])
            self.db.execute('DELETE FROM notetypes WHERE id = ?', (notetype_id,))
            self._touch('notetypes')

    def notetype_use(self, notetype_id):
        """How many notes use the type."""
        return self.db.execute('SELECT COUNT(*) FROM notes WHERE notetype_id = ?',
                               (notetype_id,)).fetchone()[0]

    def _notes_of_type(self, notetype_id):
        rows = self.db.execute('SELECT * FROM notes WHERE notetype_id = ?', (notetype_id,))
        return [Note.from_row(row) for row in rows]

    # -- deck configs -----------------------------------------------------------------------

    def deck_configs(self):
        rows = self.db.execute('SELECT * FROM deck_configs ORDER BY name COLLATE NOCASE')
        return [DeckConfig.from_row(row) for row in rows]

    def deck_config(self, config_id):
        row = self.db.execute('SELECT * FROM deck_configs WHERE id = ?', (config_id,)).fetchone()
        return DeckConfig.from_row(row) if row else None

    def config_for_deck(self, deck_id):
        """The preset a deck uses (the default one when its own is gone)."""
        deck = self.deck(deck_id)
        config = self.deck_config(deck.config_id) if deck else None
        return config or self.deck_config(DEFAULT_ID)

    def add_deck_config(self, config):
        with self.undoable(_('Add Preset')):
            config.id = config.id or self.next_id()
            config.modified = int(time.time())
            self._insert('deck_configs', config.to_row())
            self._created('configs', config.id)
            self._touch('configs')
        return config

    def update_deck_config(self, config):
        with self.undoable(_('Change Deck Options')):
            self._remember('configs', 'deck_configs', [config.id])
            config.modified = int(time.time())
            self._update('deck_configs', config.to_row())
            self._touch('configs')

    def remove_deck_config(self, config_id):
        """Delete a preset; its decks use the default one."""
        if config_id == DEFAULT_ID:
            raise CollectionError(_('The default preset cannot be deleted.'))
        with self.undoable(_('Delete Preset')):
            deck_ids = [row['id'] for row in self.db.execute(
                'SELECT id FROM decks WHERE config_id = ?', (config_id,))]
            self._remember('decks', 'decks', deck_ids)
            self.db.execute('UPDATE decks SET config_id = ? WHERE config_id = ?',
                            (DEFAULT_ID, config_id))
            self._remember('configs', 'deck_configs', [config_id])
            self.db.execute('DELETE FROM deck_configs WHERE id = ?', (config_id,))
            self._touch('configs')
            self._touch('decks')

    def set_deck_config(self, deck_id, config_id):
        with self.undoable(_('Change Deck Options')):
            self._remember('decks', 'decks', [deck_id])
            self.db.execute('UPDATE decks SET config_id = ?, modified = ? WHERE id = ?',
                            (config_id, int(time.time()), deck_id))
            self._touch('decks')

    # -- decks ----------------------------------------------------------------------------

    def decks(self):
        """Every deck, parents before children, in reading order."""
        rows = self.db.execute('SELECT * FROM decks').fetchall()
        return sorted((Deck.from_row(row) for row in rows), key=lambda d: natural_key(d.name))

    def deck(self, deck_id):
        row = self.db.execute('SELECT * FROM decks WHERE id = ?', (deck_id,)).fetchone()
        return Deck.from_row(row) if row else None

    def deck_by_name(self, name):
        row = self.db.execute('SELECT * FROM decks WHERE name = ? COLLATE NOCASE',
                              (name,)).fetchone()
        return Deck.from_row(row) if row else None

    def deck_tree(self):
        """The decks as a tree of DeckNodes (no counts: scheduler.py fills those)."""
        roots = []
        by_name = {}
        for deck in self.decks():
            node = DeckNode(deck, deck.level)
            by_name[deck.name] = node
            parent = by_name.get(deck.parent_name) if deck.parent_name else None
            (parent.children if parent else roots).append(node)
        return roots

    def deck_and_children(self, deck_id):
        """The ids of a deck and every deck under it."""
        deck = self.deck(deck_id)
        if deck is None:
            return []
        rows = self.db.execute("SELECT id FROM decks WHERE id = ? OR name LIKE ? ESCAPE '\\'",
                               (deck_id, _like_escape(deck.name) + '::%'))
        return [row['id'] for row in rows]

    def add_deck(self, name, config_id=None):
        """The deck of that name, created (parents too) when missing."""
        name = normalize_deck_name(name)
        if not name:
            raise CollectionError(_('A deck needs a name.'))
        existing = self.deck_by_name(name)
        if existing:
            return existing
        with self.undoable(_('Add Deck')):
            parts = name.split('::')
            deck = None
            for depth in range(1, len(parts) + 1):
                partial = '::'.join(parts[:depth])
                deck = self.deck_by_name(partial)
                if deck is None:
                    deck = Deck(id=self.next_id(), name=partial,
                                config_id=config_id or DEFAULT_ID, description='',
                                collapsed=0, original_id=None, modified=int(time.time()))
                    self._insert('decks', deck.to_row())
                    self._created('decks', deck.id)
            self._touch('decks')
        return deck

    def rename_deck(self, deck_id, name):
        """Rename a deck (and move its children with it). 'A::B' under another parent moves
        it there."""
        name = normalize_deck_name(name)
        deck = self.deck(deck_id)
        if deck is None or not name:
            raise CollectionError(_('A deck needs a name.'))
        if name.lower() == deck.name.lower():
            if name == deck.name:
                return
        other = self.deck_by_name(name)
        if other is not None and other.id != deck_id:
            raise CollectionError(_('A deck called “{name}” already exists.').format(name=name))
        if name.lower().startswith(deck.name.lower() + '::'):
            raise CollectionError(_('A deck cannot be moved under itself.'))
        with self.undoable(_('Rename Deck')):
            parent = name.rsplit('::', 1)[0] if '::' in name else None
            if parent:
                self.add_deck(parent)
            ids = self.deck_and_children(deck_id)
            self._remember('decks', 'decks', ids)
            prefix = deck.name + '::'
            now = int(time.time())
            for child_id in ids:
                child = self.deck(child_id)
                new_name = name + child.name[len(deck.name):] \
                    if child.name.startswith(prefix) or child.id == deck_id else child.name
                self.db.execute('UPDATE decks SET name = ?, modified = ? WHERE id = ?',
                                (new_name, now, child_id))
            self._touch('decks')

    def update_deck(self, deck):
        """Store a deck's description, collapsed state or preset."""
        with self.undoable(_('Edit Deck')):
            self._remember('decks', 'decks', [deck.id])
            deck.modified = int(time.time())
            self._update('decks', deck.to_row())
            self._touch('decks')

    def set_deck_collapsed(self, deck_id, collapsed):
        """Not undoable: the sidebar's disclosure state."""
        self.db.execute('UPDATE decks SET collapsed = ? WHERE id = ?',
                        (int(collapsed), deck_id))
        self._modified = True

    def remove_deck(self, deck_id):
        """Delete a deck, its subdecks, their cards, and the notes left without cards."""
        with self.undoable(_('Delete Deck')):
            ids = self.deck_and_children(deck_id)
            card_ids = self._card_ids_in_decks(ids)
            self.remove_cards(card_ids)
            self._remember('decks', 'decks', ids)
            for child_id in ids:
                self.db.execute('DELETE FROM decks WHERE id = ?', (child_id,))
            if not self.db.execute('SELECT 1 FROM decks LIMIT 1').fetchone():
                self.add_deck('Default')
            self._touch('decks')

    def _card_ids_in_decks(self, deck_ids):
        found = []
        for chunk in _chunks(deck_ids):
            marks = ','.join('?' * len(chunk))
            found.extend(row['id'] for row in self.db.execute(
                f'SELECT id FROM cards WHERE deck_id IN ({marks})', chunk))
        return found

    def deck_card_count(self, deck_id):
        ids = self.deck_and_children(deck_id)
        total = 0
        for chunk in _chunks(ids):
            marks = ','.join('?' * len(chunk))
            total += self.db.execute(f'SELECT COUNT(*) FROM cards WHERE deck_id IN ({marks})',
                                     chunk).fetchone()[0]
        return total

    # -- notes ------------------------------------------------------------------------------

    def note(self, note_id):
        row = self.db.execute('SELECT * FROM notes WHERE id = ?', (note_id,)).fetchone()
        return Note.from_row(row) if row else None

    def notes(self, note_ids):
        found = []
        for chunk in _chunks(note_ids):
            marks = ','.join('?' * len(chunk))
            found.extend(Note.from_row(row) for row in self.db.execute(
                f'SELECT * FROM notes WHERE id IN ({marks})', chunk))
        order = {note_id: index for index, note_id in enumerate(note_ids)}
        return sorted(found, key=lambda note: order[note.id])

    def note_count(self):
        return self.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]

    def card_count(self):
        return self.db.execute('SELECT COUNT(*) FROM cards').fetchone()[0]

    def add_note(self, notetype_id, deck_id, fields, tags=(), guid=None, created=None):
        """A new note with its cards in `deck_id`. Raises CollectionError when the note
        would make no card (an empty front, a cloze type without a cloze)."""
        notetype = self.notetype(notetype_id)
        if notetype is None:
            raise CollectionError(_('The note type no longer exists.'))
        fields = _pad_fields(fields, len(notetype.fields))
        if not template.cards_for_note(notetype.kind, notetype.templates, _field_map(
                notetype, fields)):
            if notetype.kind in ('cloze', 'occlusion'):
                raise CollectionError(_('The text needs at least one cloze, such as '
                                        '{{c1::word}}, to make a card.'))
            raise CollectionError(_('The first field is empty, so no card would be made.'))
        with self.undoable(_('Add Note')):
            now = int(time.time())
            note = Note(id=self.next_id(), guid=guid or new_guid(), notetype_id=notetype_id,
                        fields=fields, sort_field='', tags=list(tags), marked=False,
                        created=created or now, modified=now)
            self._store_note(note, notetype, insert=True)
            self._created('notes', note.id)
            self._generate_cards(note, notetype, deck_id)
            self._touch('notes')
        return note

    def update_note(self, note, deck_id=None):
        """Store changed fields or tags; cards a changed cloze adds go to `deck_id` (the
        note's first card's deck by default), cards a removed cloze leaves go away."""
        notetype = self.notetype(note.notetype_id)
        with self.undoable(_('Edit Note')):
            self._remember('notes', 'notes', [note.id])
            note.fields = _pad_fields(note.fields, len(notetype.fields))
            note.modified = int(time.time())
            self._store_note(note, notetype)
            self._generate_cards(note, notetype, deck_id)
            self._touch('notes')

    def _store_note(self, note, notetype, insert=False):
        sort_index = min(notetype.sort_field or 0, len(note.fields) - 1)
        note.sort_field = template.strip_html(note.fields[sort_index]).lower()
        if insert:
            self._insert('notes', note.to_row())
        else:
            self._update('notes', note.to_row())

    def _generate_cards(self, note, notetype, deck_id=None):
        """Add the cards the note's fields call for and remove the ones they no longer do
        (a cloze deleted); the ones with reviews are kept whatever the template says, as
        Anki keeps them."""
        wanted = set(template.cards_for_note(notetype.kind, notetype.templates,
                                             _field_map(notetype, note.fields)))
        if not wanted and notetype.kind in ('cloze', 'occlusion'):
            wanted = {0}
        existing = {card.ord: card for card in self.cards_of_note(note.id)}
        if deck_id is None:
            first = min(existing.values(), key=lambda card: card.ord, default=None)
            deck_id = first.deck_id if first else self.decks()[0].id
        now = int(time.time())
        for ord in sorted(wanted - set(existing)):
            card = Card(id=self.next_id(), note_id=note.id, deck_id=deck_id, ord=ord,
                        state='new', due=self._next_new_position(), interval=0,
                        stability=0, difficulty=0, reps=0, lapses=0, left=0,
                        last_review=0, flag=0, suspended=0, buried=0, created=now,
                        modified=now)
            self._insert('cards', card.to_row())
            self._created('cards', card.id)
            self._touch('cards')
        stale = [card for ord, card in existing.items()
                 if ord not in wanted and card.reps == 0]
        if stale:
            self.remove_cards([card.id for card in stale], orphans=False)

    def _next_new_position(self):
        position = self.get('next_position', 0) + 1
        self._set_quietly('next_position', position)
        return position

    def remove_notes(self, note_ids):
        """Delete notes and their cards."""
        with self.undoable(_('Delete Notes')):
            for chunk in _chunks(note_ids):
                marks = ','.join('?' * len(chunk))
                card_ids = [row['id'] for row in self.db.execute(
                    f'SELECT id FROM cards WHERE note_id IN ({marks})', chunk)]
                self._remember('cards', 'cards', card_ids)
                self.db.execute(f'DELETE FROM cards WHERE note_id IN ({marks})', chunk)
                self._remember('notes', 'notes', chunk)
                self.db.execute(f'DELETE FROM notes WHERE id IN ({marks})', chunk)
            self._touch('notes')
            self._touch('cards')

    def remove_cards(self, card_ids, orphans=True):
        """Delete cards; with `orphans`, notes left without a card go too."""
        with self.undoable(_('Delete Cards')):
            note_ids = set()
            for chunk in _chunks(card_ids):
                marks = ','.join('?' * len(chunk))
                note_ids.update(row['note_id'] for row in self.db.execute(
                    f'SELECT note_id FROM cards WHERE id IN ({marks})', chunk))
                self._remember('cards', 'cards', chunk)
                self.db.execute(f'DELETE FROM cards WHERE id IN ({marks})', chunk)
            if orphans:
                orphaned = [note_id for note_id in note_ids if not self.db.execute(
                    'SELECT 1 FROM cards WHERE note_id = ? LIMIT 1', (note_id,)).fetchone()]
                if orphaned:
                    self.remove_notes(orphaned)
            self._touch('cards')

    def set_tags(self, note_ids, add=(), remove=()):
        with self.undoable(_('Change Tags')):
            self._remember('notes', 'notes', list(note_ids))
            now = int(time.time())
            removed = {search.normalize_tag(tag).lower() for tag in remove}
            for note in self.notes(note_ids):
                tags = [tag for tag in note.tags if tag.lower() not in removed]
                tags.extend(add)
                note.tags = tags
                note.modified = now
                self.db.execute('UPDATE notes SET tags = ?, modified = ? WHERE id = ?',
                                (join_tags(tags), now, note.id))
            self._touch('notes')

    def all_tags(self):
        """Every tag in use, sorted."""
        tags = set()
        for row in self.db.execute('SELECT DISTINCT tags FROM notes'):
            tags.update(row['tags'].split())
        return sorted(tags, key=str.lower)

    def set_marked(self, note_ids, marked):
        with self.undoable(_('Mark') if marked else _('Unmark')):
            self._remember('notes', 'notes', list(note_ids))
            for chunk in _chunks(note_ids):
                marks = ','.join('?' * len(chunk))
                self.db.execute(f'UPDATE notes SET marked = ? WHERE id IN ({marks})',
                                [int(bool(marked))] + list(chunk))
            self._touch('notes')

    def find_duplicates(self, notetype_id, first_field, except_note=None):
        """Notes of the type whose first field (HTML stripped) is this one's: the Add
        dialog's duplicate warning."""
        key = template.strip_html(first_field).lower()
        if not key:
            return []
        found = []
        for row in self.db.execute('SELECT id, fields FROM notes WHERE notetype_id = ?',
                                   (notetype_id,)):
            first = row['fields'].split(template.FIELD_SEPARATOR, 1)[0]
            if row['id'] != except_note and template.strip_html(first).lower() == key:
                found.append(row['id'])
        return found

    # -- cards ----------------------------------------------------------------------------

    def card(self, card_id):
        row = self.db.execute('SELECT * FROM cards WHERE id = ?', (card_id,)).fetchone()
        return Card.from_row(row) if row else None

    def cards(self, card_ids):
        found = []
        for chunk in _chunks(card_ids):
            marks = ','.join('?' * len(chunk))
            found.extend(Card.from_row(row) for row in self.db.execute(
                f'SELECT * FROM cards WHERE id IN ({marks})', chunk))
        order = {card_id: index for index, card_id in enumerate(card_ids)}
        return sorted(found, key=lambda card: order[card.id])

    def cards_of_note(self, note_id):
        rows = self.db.execute('SELECT * FROM cards WHERE note_id = ? ORDER BY ord', (note_id,))
        return [Card.from_row(row) for row in rows]

    def find_cards(self, query, order='cards.id'):
        """The ids of the cards a search (search.py's syntax) finds. Raises SearchError."""
        where, params = search.compile(query, self.today(), day_start_hour=self.day_start_hour)
        rows = self.db.execute(
            f'SELECT cards.id FROM {search.SQL_FROM} WHERE {where} ORDER BY {order}', params)
        return [row[0] for row in rows]

    def find_notes(self, query):
        """The ids of the notes whose cards a search finds, each once, in card order."""
        where, params = search.compile(query, self.today(), day_start_hour=self.day_start_hour)
        rows = self.db.execute(
            f'SELECT DISTINCT notes.id FROM {search.SQL_FROM} WHERE {where} ORDER BY notes.id',
            params)
        return [row[0] for row in rows]

    def browse_rows(self, query, order='notes.sort_field', descending=False, limit=None):
        """What the browser shows: one dict per card the search finds, with the note's sort
        field, deck and note type names joined in. `order` is a column of the join."""
        where, params = search.compile(query, self.today(), day_start_hour=self.day_start_hour)
        direction = 'DESC' if descending else 'ASC'
        sql = (f'SELECT cards.*, notes.sort_field, notes.tags, notes.marked, '
               f'notes.notetype_id, decks.name AS deck_name, notetypes.name AS notetype_name, '
               f'notes.fields AS note_fields '
               f'FROM {search.SQL_FROM} WHERE {where} ORDER BY {order} {direction}, cards.id')
        if limit:
            sql += f' LIMIT {int(limit)}'
        return [dict(row) for row in self.db.execute(sql, params)]

    def set_deck(self, card_ids, deck_id):
        with self.undoable(_('Change Deck')):
            self._update_cards(card_ids, 'deck_id = ?', (deck_id,))

    def set_flag(self, card_ids, flag):
        label = _('Remove Flag') if not flag else _('Flag')
        with self.undoable(label):
            self._update_cards(card_ids, 'flag = ?', (int(flag),))

    def suspend(self, card_ids):
        with self.undoable(_('Suspend')):
            self._update_cards(card_ids, 'suspended = 1', ())

    def unsuspend(self, card_ids):
        with self.undoable(_('Unsuspend')):
            self._update_cards(card_ids, 'suspended = 0', ())

    def bury(self, card_ids):
        with self.undoable(_('Bury')):
            self._update_cards(card_ids, 'buried = ?', (self.today(),))

    def unbury(self, card_ids):
        with self.undoable(_('Unbury')):
            self._update_cards(card_ids, 'buried = 0', ())

    def unbury_past(self):
        """Bring back the cards buried before today (at start, and when the day turns)."""
        changed = self.db.execute('UPDATE cards SET buried = 0 WHERE buried != 0 AND buried < ?',
                                  (self.today(),)).rowcount
        if changed:
            self._touch('cards')

    def unbury_deck(self, deck_id):
        """Bring back today's buried cards of a deck (and its subdecks)."""
        with self.undoable(_('Unbury')):
            ids = []
            for chunk in _chunks(self.deck_and_children(deck_id)):
                marks = ','.join('?' * len(chunk))
                ids.extend(row['id'] for row in self.db.execute(
                    f'SELECT id FROM cards WHERE buried != 0 AND deck_id IN ({marks})', chunk))
            self._update_cards(ids, 'buried = 0', ())

    def forget(self, card_ids, keep_position=True):
        """Make cards new again (their reviews stay in the log, marked manual)."""
        with self.undoable(_('Reset')):
            now = int(time.time())
            for card in self.cards(card_ids):
                self._remember('cards', 'cards', [card.id])
                due = card.due if (keep_position and card.state == 'new') \
                    else self._next_new_position()
                self.db.execute(
                    'UPDATE cards SET state = ?, due = ?, interval = 0, stability = 0, '
                    'difficulty = 0, left = 0, last_review = 0, modified = ? WHERE id = ?',
                    ('new', due, now, card.id))
                self._log_manual(card, now)
            self._touch('cards')

    def set_due(self, card_ids, day_offset, now=None):
        """Make cards review cards due in `day_offset` days (0 is today)."""
        with self.undoable(_('Set Due Date')):
            now = now or int(time.time())
            target = self.today(now) + int(day_offset)
            for card in self.cards(card_ids):
                self._remember('cards', 'cards', [card.id])
                interval = max(1, target - self.today(now)) if card.state != 'review' \
                    else max(1, card.interval)
                self.db.execute(
                    'UPDATE cards SET state = ?, due = ?, interval = ?, left = 0, '
                    'modified = ? WHERE id = ?',
                    ('review', target, float(interval), now, card.id))
                self._log_manual(card, now)
            self._touch('cards')

    def reposition(self, card_ids, start=0):
        """Give new cards positions from `start` on, in the order given."""
        with self.undoable(_('Reposition')):
            now = int(time.time())
            position = start
            for card in self.cards(card_ids):
                if card.state != 'new':
                    continue
                self._remember('cards', 'cards', [card.id])
                self.db.execute('UPDATE cards SET due = ?, modified = ? WHERE id = ?',
                                (position, now, card.id))
                position += 1
            if position > self.get('next_position', 0):
                self._set_quietly('next_position', position)
            self._touch('cards')

    def _log_manual(self, card, now):
        revlog_id = self.next_id(now, 'revlog')
        self._insert('revlog', {
            'id': revlog_id, 'card_id': card.id, 'rating': 0, 'state': card.state,
            'elapsed_days': 0, 'scheduled_days': 0, 'stability': card.stability,
            'difficulty': card.difficulty, 'duration_ms': 0, 'kind': 'manual'})
        if self._open is not None:
            self._open['revlog'].add(revlog_id)

    def _update_cards(self, card_ids, assignment, params):
        card_ids = list(card_ids)
        self._remember('cards', 'cards', card_ids)
        now = int(time.time())
        for chunk in _chunks(card_ids):
            marks = ','.join('?' * len(chunk))
            self.db.execute(f'UPDATE cards SET {assignment}, modified = ? WHERE id IN ({marks})',
                            list(params) + [now] + list(chunk))
        self._touch('cards')

    # -- answering (called by scheduler.py) --------------------------------------------------

    def record_answer(self, before, after, rating, now, duration_ms=0, kind='review',
                      bury_siblings=False, leech_threshold=0, leech_action='tag'):
        """Store what an answer did: the card as `after`, a revlog row, siblings buried when
        asked, and the leech tag or suspension when the lapses reach the threshold. Returns
        the revlog id. One undo step, labelled with the rating."""
        label = {1: _('Again'), 2: _('Hard'), 3: _('Good'), 4: _('Easy')}.get(rating, _('Answer'))
        with self.undoable(label):
            self._remember('cards', 'cards', [after.id])
            after.modified = int(now)
            after.reps = before.reps + (1 if kind != 'cram' else 0)
            self._update('cards', after.to_row())
            revlog_id = self.next_id(now, 'revlog')
            elapsed = 0.0
            if before.last_review:
                elapsed = max(0.0, (now - before.last_review) / days.SECONDS_PER_DAY)
            self._insert('revlog', {
                'id': revlog_id, 'card_id': after.id, 'rating': rating, 'state': before.state,
                'elapsed_days': elapsed, 'scheduled_days': after.interval,
                'stability': after.stability, 'difficulty': after.difficulty,
                'duration_ms': int(duration_ms), 'kind': kind})
            self._open['revlog'].add(revlog_id)
            if bury_siblings:
                siblings = [card.id for card in self.cards_of_note(after.note_id)
                            if card.id != after.id and card.state in ('new', 'review')
                            and not card.suspended and not card.buried]
                if siblings:
                    self._update_cards(siblings, 'buried = ?', (self.today(now),))
            if (leech_threshold and rating == 1 and after.lapses >= leech_threshold
                    and (after.lapses - leech_threshold) % max(1, leech_threshold // 2) == 0):
                self.set_tags([after.note_id], add=[LEECH_TAG])
                if leech_action == 'suspend':
                    self._update_cards([after.id], 'suspended = 1', ())
                self._touch('leech')
            self._touch('cards')
        return revlog_id

    def reviews_of(self, card_id):
        rows = self.db.execute('SELECT * FROM revlog WHERE card_id = ? ORDER BY id', (card_id,))
        return [dict(row) for row in rows]

    # -- rendering ------------------------------------------------------------------------

    def render_card(self, card, note=None, notetype=None):
        """The card's question and answer HTML (template.py), with the specials filled."""
        note = note or self.note(card.note_id)
        notetype = notetype or self.notetype(note.notetype_id)
        ord = card.ord
        if notetype.kind == 'standard':
            index = min(ord, len(notetype.templates) - 1)
            tmpl = notetype.templates[index]
        else:
            tmpl = notetype.templates[0]
        deck = self.deck(card.deck_id)
        special = {
            'Tags': ' '.join(note.tags), 'Type': notetype.name,
            'Deck': deck.name if deck else '', 'Subdeck': deck.basename if deck else '',
            'Card': tmpl['name'], 'CardFlag': f'flag{card.flag}' if card.flag else '',
        }
        return template.render(tmpl['qfmt'], tmpl['afmt'], _field_map(notetype, note.fields),
                               ord=ord, kind=notetype.kind, special=special)

    def card_summary(self, card):
        """A line of text for the card: its question, HTML stripped."""
        question, _answer = self.render_card(card)
        return template.strip_html(question)

    def referenced_media(self):
        """Every media file name a field refers to."""
        names = set()
        for row in self.db.execute('SELECT fields FROM notes'):
            names.update(template.media_references(row['fields']))
        return names

    # -- maintenance ------------------------------------------------------------------------

    def check(self):
        """Repair what can be: cards whose note or deck is gone, notes without cards;
        the number of problems fixed."""
        fixed = 0
        with self.undoable(None):
            default = self.decks()[0].id
            fixed += self.db.execute(
                'DELETE FROM cards WHERE note_id NOT IN (SELECT id FROM notes)').rowcount
            fixed += self.db.execute(
                'UPDATE cards SET deck_id = ? WHERE deck_id NOT IN (SELECT id FROM decks)',
                (default,)).rowcount
            fixed += self.db.execute(
                'DELETE FROM notes WHERE id NOT IN (SELECT note_id FROM cards)').rowcount
            fixed += self.db.execute(
                'DELETE FROM notes WHERE notetype_id NOT IN (SELECT id FROM notetypes)').rowcount
            if fixed:
                self._touch('cards')
                self._touch('notes')
        return fixed

    def copy_to(self, path):
        """A copy of the database file (for export)."""
        target = sqlite3.connect(path)
        with target:
            self.db.backup(target)
        target.close()


def normalize_deck_name(name):
    parts = [part.strip() for part in (name or '').replace('\x1f', '::').split('::')]
    return '::'.join(part for part in parts if part)


def _field_map(notetype, fields):
    return dict(zip(notetype.field_names(), fields, strict=False))


def _pad_fields(fields, count):
    fields = [value or '' for value in list(fields)[:count]]
    return fields + [''] * (count - len(fields))


def _chunks(items, size=500):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _like_escape(text):
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _(text):
    """gettext, bound late: the launcher binds the domain before this module is used."""
    import gettext

    return gettext.gettext(text)


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
