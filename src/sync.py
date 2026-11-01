# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Sync between computers through a folder that something else keeps the same on each of
them (Syncthing, Nextcloud, Dropbox, a USB stick): no server, no account.

The folder holds:

    retain-sync.json        {"format": FORMAT}: a newer format is refused
    devices/ID.sqlite       one device's snapshot: a copy of its collection, graves and all
    devices/ID.json         {"id", "name", "synced"}: who that device is, when it last synced
    media/                  every device's media files

Each device writes only its own two files in devices/ (a temporary file in the same folder,
flushed to disk, then renamed over the old one), so a file-sync tool never sees two
computers change one file and never makes conflict copies of them. A sync:

    1. checks the folder (and writes retain-sync.json when it is new);
    2. backs the collection up (Collection.backup(force=True)) when another device's snapshot
       changed since the last sync, so a bad merge can be undone from the backups;
    3. exchanges media: a file the other side lacks is copied there; a name both have with
       different contents is a clash, and the local file is renamed (MediaStore's name-2
       scheme) with the local notes' references following it (they are then newer, so the
       rename travels), before the shared file is copied in under the name;
    4. merges every other device's snapshot that changed since the last sync (read-only,
       checked with quick_check; a partial, corrupt or newer snapshot is skipped and listed
       in `problems`), in one transaction each: last writer wins per object, by `modified`
       (ties broken by the contents, so both sides choose alike) — presets, note types,
       decks, notes (by guid), cards (by note guid and ord) — reviews are the union by id,
       and graves newer than (or as new as) an object delete it, while an object newer than
       its grave lives;
    5. writes this device's snapshot when the collection changed since the last one, and
       its devices/ID.json.

Objects made apart on two devices are matched by what they are when their ids differ: a
preset by name, a note type by Anki id or by name, kind and field names, a deck by name; the
two then take the smaller id, so the next sync matches them by id. A note type whose fields
were renamed elsewhere has its local notes' fields carried across by name.

    result = sync(collection, folder, device_name, progress=None, now=None) -> SyncResult
    devices(folder, own_id=None) -> [{'id', 'name', 'synced'}]   the other devices, newest first
    device_id(collection) -> str    this device's id (made on first use; a collection copied
                                    to another computer or folder gets a new one)
    describe_result(result) -> str  one sentence for a toast
    SyncRunner(settings, collection)   runs sync() in a thread for the app (below)

sync() runs in a thread on a Collection of its own (the runner opens one on the same path);
it touches no GTK. A merge is not an undo step: the caller clears the main collection's
undo stack after a sync, as after an import, and the backup covers it. Modification times
are compared as they are: a computer whose clock is wrong wins (or loses) every tie.
"""

import contextlib
import hashlib
import json
import logging
import os
import pathlib
import secrets
import shutil
import socket
import sqlite3
import threading
import time
from gettext import gettext as _
from gettext import ngettext

from gi.repository import GLib, GObject

from . import schema
from .collection import STATES, Card, Collection, CollectionError, Deck, Note, card_key
from .deck_config import DEFAULT_ID
from .importing import rename_media_references
from .notetypes import NoteType

log = logging.getLogger(__name__)

FORMAT = 1
MARKER = 'retain-sync.json'
DEVICES = 'devices'
MEDIA = 'media'
# Config keys of the sync's own, left out of the snapshot (they belong to this device).
DEVICE_KEY = 'sync_device'        # {'id', 'machine'}
SEEN_KEY = 'sync_seen'            # {snapshot file name: [mtime_ns, size]} merged
WRITTEN_KEY = 'sync_written'      # the fingerprint of the collection last written
MEDIA_KEY = 'sync_media'          # {name: [size, local mtime_ns, shared mtime_ns]} same
LAST_KEY = 'sync_last'            # when this device last synced
STALE_AFTER = 86400               # seconds after which a left temporary file is removed
TABLES = ('notes', 'cards', 'decks', 'notetypes', 'deck_configs', 'revlog', 'config')
CARD_STATE = ('deck_id', 'state', 'due', 'interval', 'stability', 'difficulty', 'reps',
              'lapses', 'left', 'last_review', 'flag', 'suspended', 'buried')
NOTETYPE_COLUMNS = ('name', 'kind', 'fields', 'templates', 'css', 'sort_field', 'original_id')


class SyncError(CollectionError):
    """The sync cannot run (the folder is missing, or newer than this app): a sentence."""


class SnapshotError(Exception):
    """A device's snapshot cannot be read: it is skipped."""


class SyncResult:
    """What a sync did: counts of what changed here, the devices the changes came from,
    and the problems met (sentences)."""

    COUNTS = ('notes_added', 'notes_updated', 'notes_removed', 'cards_added', 'cards_updated',
              'cards_removed', 'reviews_added', 'decks_changed', 'notetypes_changed',
              'configs_changed', 'media_received', 'media_sent', 'media_renamed')

    def __init__(self):
        for name in self.COUNTS:
            setattr(self, name, 0)
        self.sources = []     # the names of the devices whose snapshots changed something
        self.problems = []
        self.merged = 0       # snapshots merged
        self.written = False  # whether this device's snapshot was written

    def __repr__(self):
        counts = ', '.join(f'{name}={getattr(self, name)}' for name in self.COUNTS
                           if getattr(self, name))
        return (f'SyncResult({counts}, merged={self.merged}, written={self.written}, '
                f'problems={len(self.problems)})')

    @property
    def notes(self):
        return self.notes_added + self.notes_updated + self.notes_removed

    @property
    def cards(self):
        return self.cards_added + self.cards_updated + self.cards_removed

    def changed(self):
        """Whether the local collection changed (so its pages must refresh)."""
        return any(getattr(self, name) for name in self.COUNTS if name != 'media_sent')


def _report(progress, fraction, message):
    if progress is not None:
        progress(min(max(float(fraction), 0.0), 1.0), message)


# The device


def device_id(collection):
    """This device's id, in the collection's config: made on first use, and made anew when
    the collection was copied to another computer or folder (two devices never share one,
    or they would write the same snapshot)."""
    machine = _machine_key(collection.path)
    stored = collection.get(DEVICE_KEY) or {}
    if stored.get('id') and stored.get('machine') == machine:
        return stored['id']
    new = secrets.token_hex(8)
    collection._set_quietly(DEVICE_KEY, {'id': new, 'machine': machine})
    return new


def _machine_key(path):
    machine = ''
    for candidate in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        with contextlib.suppress(OSError):
            machine = pathlib.Path(candidate).read_text().strip()
            if machine:
                break
    machine = machine or socket.gethostname()
    text = f'{machine}\0{pathlib.Path(path).resolve()}'
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def default_device_name():
    """What a device is called when the user has not named it: the computer's name."""
    return GLib.get_host_name() or _('This Computer')


def devices(folder, own_id=None):
    """The devices that synced through the folder, but `own_id`: dicts with id, name and
    synced (a time), the most recent first."""
    found = []
    directory = pathlib.Path(folder) / DEVICES
    if not directory.is_dir():
        return found
    for path in directory.glob('*.json'):
        if path.name.startswith('.'):
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get('id') or data.get('id') == own_id:
            continue
        found.append({'id': str(data['id']), 'name': str(data.get('name') or ''),
                      'synced': int(data.get('synced') or 0)})
    return sorted(found, key=lambda device: -device['synced'])


# Files


def _fsync(path):
    with contextlib.suppress(OSError):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _temporary(directory):
    """A name for a file being written into `directory`: hidden, so no reader takes it."""
    return pathlib.Path(directory) / f'.retain-{secrets.token_hex(6)}.tmp'


def _replace(temporary, target):
    """Move a written temporary file over `target`, durably."""
    _fsync(temporary)
    os.replace(temporary, target)
    _fsync(pathlib.Path(target).parent)


def _write_atomic(path, data):
    temporary = _temporary(pathlib.Path(path).parent)
    try:
        temporary.write_bytes(data)
        _replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _copy_atomic(source, target):
    temporary = _temporary(pathlib.Path(target).parent)
    try:
        shutil.copyfile(source, temporary)
        _replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _ignored(name):
    """Files of the file-sync tools (partial downloads, conflict copies) and our own
    temporary ones, which are not media."""
    return (name.startswith(('.', '~')) or '.sync-conflict-' in name
            or name.endswith(('.tmp', '.part', '.partial')))


def _check_folder(folder):
    if not folder.is_dir():
        raise SyncError(_('The sync folder {path} is not there: is its drive connected?')
                        .format(path=folder))
    marker = folder / MARKER
    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding='utf-8'))
            version = int(data.get('format', FORMAT))
        except (OSError, ValueError, AttributeError, TypeError) as error:
            log.warning('%s unreadable (%s); taken as format %d', marker, error, FORMAT)
            version = FORMAT
        if version > FORMAT:
            raise SyncError(_('The sync folder was set up by a newer version of Retain: '
                              'update Retain on this computer.'))
    else:
        try:
            _write_atomic(marker, json.dumps({'format': FORMAT}).encode())
        except OSError as error:
            raise SyncError(_('Retain cannot write to the sync folder {path}.')
                            .format(path=folder)) from error
    (folder / DEVICES).mkdir(exist_ok=True)
    (folder / MEDIA).mkdir(exist_ok=True)
    _remove_stale_temporaries(folder / DEVICES)
    _remove_stale_temporaries(folder / MEDIA)


def _remove_stale_temporaries(directory, age=STALE_AFTER):
    """Our temporary files a device left when it stopped mid-write, a day on."""
    cutoff = time.time() - age
    for path in directory.glob('.retain-*.tmp'):
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()


# The sync


def sync(collection, folder, device_name='', progress=None, now=None, backup=True):
    """Sync the collection with the folder (see the module docstring); a SyncResult.
    Raises SyncError when the folder cannot be used."""
    now = int(now or time.time())
    folder = pathlib.Path(folder)
    result = SyncResult()
    _report(progress, 0.0, _('Looking for changes…'))
    _check_folder(folder)
    own = device_id(collection)
    snapshots = _changed_snapshots(collection, folder / DEVICES, own)
    if snapshots and backup:
        try:
            collection.backup(force=True)
        except OSError as error:
            raise SyncError(_('No backup could be made before syncing: {error}')
                            .format(error=error)) from error
    _report(progress, 0.05, _('Exchanging media…'))
    _sync_media(collection, folder / MEDIA, result, now)
    names = {device['id']: device['name'] for device in devices(folder, own)}
    for index, (path, stamp) in enumerate(snapshots):
        device = path.stem
        name = names.get(device) or _('another device')
        _report(progress, 0.3 + 0.6 * index / max(len(snapshots), 1),
                _('Merging changes from {name}…').format(name=name))
        try:
            changed = _merge_snapshot(collection, path, stamp, result, now)
        except SnapshotError as error:
            log.warning('snapshot %s skipped: %s', path, error)
            result.problems.append(_('The changes from {name} were skipped: {error}')
                                   .format(name=name, error=error))
            continue
        result.merged += 1
        if changed and name not in result.sources:
            result.sources.append(name)
    _report(progress, 0.9, _('Saving this device’s changes…'))
    result.written = _write_snapshot(collection, folder / DEVICES, own)
    _write_atomic(folder / DEVICES / f'{own}.json', json.dumps({
        'id': own, 'name': device_name or default_device_name(), 'synced': now,
        'format': FORMAT}).encode())
    collection._set_quietly(LAST_KEY, now)
    log.info('synced with %s: %r', folder, result)
    _report(progress, 1.0, _('Done'))
    return result


def _changed_snapshots(collection, directory, own):
    """The other devices' snapshots that changed since they were last merged: [(path,
    [mtime_ns, size])]."""
    seen = collection.get(SEEN_KEY) or {}
    found = []
    for path in sorted(directory.glob('*.sqlite')):
        if path.name.startswith('.') or path.stem == own:
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        stamp = [info.st_mtime_ns, info.st_size]
        if seen.get(path.name) != stamp:
            found.append((path, stamp))
    return found


def _merge_snapshot(collection, path, stamp, result, now):
    """Merge one snapshot into the collection, in one transaction; whether anything
    changed. Raises SnapshotError when it cannot be read."""
    remote = _open_snapshot(path)
    try:
        before = _counts(result)
        with collection.undoable(None):
            try:
                _Merge(collection, remote, result, now).run()
            except sqlite3.DatabaseError as error:
                if _is_remote_error(error, remote):
                    raise SnapshotError(str(error)) from error
                raise
            seen = collection.get(SEEN_KEY) or {}
            seen[path.name] = stamp
            collection._set_quietly(SEEN_KEY, seen)
        return _counts(result) != before
    finally:
        remote.close()


def _is_remote_error(error, remote):
    """Whether a database error mid-merge came from the snapshot (it is read lazily)."""
    try:
        remote.execute('SELECT COUNT(*) FROM notes').fetchone()
    except sqlite3.DatabaseError:
        return True
    return 'malformed' in str(error) or 'not a database' in str(error)


def _counts(result):
    return [getattr(result, name) for name in SyncResult.COUNTS]


def _open_snapshot(path):
    """The snapshot, read-only, checked: a SnapshotError when it is partial, corrupt, not a
    collection or newer than this app."""
    uri = pathlib.Path(path).resolve().as_uri() + '?mode=ro&immutable=1'
    try:
        db = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise SnapshotError(str(error)) from error
    db.row_factory = sqlite3.Row
    try:
        check = db.execute('PRAGMA quick_check').fetchone()[0]
        if check != 'ok':
            raise SnapshotError(_('the file is damaged'))
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not set(TABLES) <= tables:
            raise SnapshotError(_('the file is not a Retain collection'))
        row = db.execute("SELECT value FROM config WHERE key = 'schema_version'").fetchone()
        version = json.loads(row[0]) if row else 1
        if version > schema.VERSION:
            raise SnapshotError(_('it was made by a newer version of Retain'))
    except sqlite3.DatabaseError as error:
        db.close()
        raise SnapshotError(_('the file is damaged or incomplete')) from error
    except BaseException:
        db.close()
        raise
    return db


# Media


def _sync_media(collection, shared, result, now):
    """Exchange media files with the shared folder (see the module docstring)."""
    store = collection.media
    local_dir = store.directory
    local = {name for name in store.names() if not _ignored(name)}
    remote = {path.name for path in shared.iterdir() if path.is_file() and not _ignored(path.name)}
    known = collection.get(MEDIA_KEY) or {}
    checked = {}
    renames = {}
    taken = local | remote
    for name in sorted(local & remote):
        here, there = local_dir / name, shared / name
        stamp = _media_stamp(here, there)
        if stamp is None:
            continue
        if stamp[0] == there.stat().st_size and (known.get(name) == stamp
                                                  or _same_contents(here, there)):
            checked[name] = stamp
            continue
        new = _free_name(name, taken)
        taken.add(new)
        os.replace(here, local_dir / new)
        renames[name] = new
        _copy_atomic(there, here)
        result.media_received += 1
        result.media_renamed += 1
    if renames:
        _rename_references(collection, renames, now)
    local = {name for name in store.names() if not _ignored(name)}
    for name in sorted(remote - local):
        _copy_atomic(shared / name, local_dir / name)
        result.media_received += 1
    for name in sorted(local - remote):
        _copy_atomic(local_dir / name, shared / name)
        result.media_sent += 1
    for name in (remote - local) | (local - remote) | set(renames):
        stamp = _media_stamp(local_dir / name, shared / name)
        if stamp is not None:
            checked[name] = stamp
    collection._set_quietly(MEDIA_KEY, checked)
    store.refresh()


def _media_stamp(here, there):
    try:
        mine, theirs = here.stat(), there.stat()
    except OSError:
        return None
    return [mine.st_size, mine.st_mtime_ns, theirs.st_mtime_ns]


def _same_contents(first, second):
    return _file_hash(first) == _file_hash(second)


def _file_hash(path):
    digest = hashlib.sha1()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _free_name(name, taken):
    """name-2.ext (or -3, …), free in both folders: MediaStore's scheme."""
    stem, dot, suffix = name.rpartition('.')
    if not dot:
        stem, suffix = name, ''
    counter = 2
    while True:
        candidate = f'{stem}-{counter}.{suffix}' if dot else f'{stem}-{counter}'
        if candidate not in taken:
            return candidate
        counter += 1


def _rename_references(collection, renames, now):
    """Point the local notes at the renamed local files; the notes are modified now, so the
    new names reach the other devices."""
    with collection.undoable(None):
        types = {}
        for old, new in renames.items():
            pattern = '%' + old.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            rows = collection.db.execute(
                "SELECT * FROM notes WHERE fields LIKE ? ESCAPE '\\'", (pattern,)).fetchall()
            for row in rows:
                note = Note.from_row(row)
                fields = [rename_media_references(field, old, new) for field in note.fields]
                if fields == note.fields:
                    continue
                if note.notetype_id not in types:
                    types[note.notetype_id] = collection.notetype(note.notetype_id)
                notetype = types[note.notetype_id]
                if notetype is None:
                    continue
                note.fields = fields
                note.modified = now
                collection._store_note(note, notetype)
        collection._touch('notes')


# Writing this device's snapshot


def _fingerprint(collection):
    """What changes when anything a snapshot carries changes."""
    parts = []
    for table in ('notes', 'cards', 'decks', 'notetypes', 'deck_configs'):
        parts.append(list(collection.db.execute(
            f'SELECT COUNT(*), TOTAL(modified) FROM {table}').fetchone()))
    parts.append(list(collection.db.execute('SELECT COUNT(*), TOTAL(id) FROM revlog').fetchone()))
    parts.append(list(collection.db.execute(
        'SELECT COUNT(*), TOTAL(deleted) FROM graves').fetchone()))
    return json.dumps(parts)


def _write_snapshot(collection, directory, own):
    """Write devices/ID.sqlite when the collection changed since it was last written;
    whether it was."""
    target = directory / f'{own}.sqlite'
    fingerprint = _fingerprint(collection)
    if target.exists() and collection.get(WRITTEN_KEY) == fingerprint:
        return False
    temporary = _temporary(directory)
    try:
        collection.copy_to(temporary)
        db = sqlite3.connect(temporary, isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode = DELETE')
            db.execute("DELETE FROM config WHERE key LIKE 'sync\\_%' ESCAPE '\\'")
        finally:
            db.close()
        _replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    collection._set_quietly(WRITTEN_KEY, fingerprint)
    return True


# Merging one snapshot


def _wins(remote, local, columns):
    """Whether the remote row replaces the local one: it is newer, or as new and its
    contents sort after (so both devices pick the same row)."""
    if remote['modified'] != local['modified']:
        return remote['modified'] > local['modified']
    theirs = json.dumps([remote[column] for column in columns])
    ours = json.dumps([local[column] for column in columns])
    return theirs > ours


class _Merge:
    """One snapshot merged into the collection, inside its transaction."""

    def __init__(self, collection, remote, result, now):
        self.collection = collection
        self.db = collection.db
        self.remote = remote
        self.result = result
        self.now = now
        self.configs = {}    # remote preset id -> local id
        self.notetypes = {}  # remote note type id -> local NoteType
        self.decks = {}      # remote deck id -> local id
        self.notes = {}      # remote note id -> local id
        self.cards = {}      # remote card id -> local id
        self.graves = {}     # (kind, key) -> (deleted, name)
        self._fallback = None

    def run(self):
        self._merge_graves()
        self._merge_configs()
        self._merge_notetypes()
        self._merge_decks()
        self._merge_notes()
        self._merge_cards()
        self._merge_reviews()
        self._apply_graves()
        for kind in ('configs', 'notetypes', 'decks', 'notes', 'cards'):
            self.collection._touch(kind)

    # Helpers

    def _buried(self, kind, key):
        """When the object was deleted, by a grave here or there; -1 when it was not."""
        grave = self.graves.get((kind, str(key)))
        return grave[0] if grave else -1

    def _free(self, table, row_id):
        return row_id is not None and not self.db.execute(
            f'SELECT 1 FROM {table} WHERE id = ?', (row_id,)).fetchone()

    def _new_id(self, table, wanted):
        return wanted if self._free(table, wanted) else self.collection.next_id()

    def _unify(self, table, local_id, remote_id, references, config_key=None):
        """Give the local object the remote's id when that is smaller and free (both
        devices end on the smaller one); the local id now."""
        if remote_id is None or remote_id >= local_id or not self._free(table, remote_id):
            return local_id
        self.db.execute(f'UPDATE {table} SET id = ? WHERE id = ?', (remote_id, local_id))
        for other, column in references:
            self.db.execute(f'UPDATE {other} SET {column} = ? WHERE {column} = ?',
                            (remote_id, local_id))
        if config_key and self.collection.get(config_key) == local_id:
            self.collection._set_quietly(config_key, remote_id)
        return remote_id

    def _fallback_deck(self):
        """Where a card whose deck is gone goes: Default, made when missing."""
        if self._fallback is None or self._free('decks', self._fallback):
            row = self.db.execute("SELECT id FROM decks WHERE name = 'Default' COLLATE NOCASE"
                                  ).fetchone() or self.db.execute(
                'SELECT id FROM decks ORDER BY id LIMIT 1').fetchone()
            if row is None:
                deck_id = self._new_id('decks', 1)
                self.db.execute(
                    'INSERT INTO decks (id, name, config_id, modified) VALUES (?, ?, ?, ?)',
                    (deck_id, 'Default', DEFAULT_ID, self.now))
                self._fallback = deck_id
            else:
                self._fallback = row[0]
        return self._fallback

    # Graves

    def _merge_graves(self):
        tables = {row[0] for row in self.remote.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'graves' in tables:
            rows = [tuple(row) for row in self.remote.execute(
                'SELECT kind, key, name, deleted FROM graves')]
            self.db.executemany(
                'INSERT INTO graves (kind, key, name, deleted) VALUES (?, ?, ?, ?) '
                'ON CONFLICT (kind, key) DO UPDATE SET deleted = excluded.deleted, '
                'name = excluded.name WHERE excluded.deleted > graves.deleted', rows)
        self.graves = {(row['kind'], row['key']): (row['deleted'], row['name'])
                       for row in self.db.execute('SELECT * FROM graves')}

    # Presets

    def _merge_configs(self):
        local = {row['id']: dict(row) for row in self.db.execute('SELECT * FROM deck_configs')}
        remote = [dict(row) for row in self.remote.execute(
            'SELECT id, name, data, modified FROM deck_configs ORDER BY id')]
        claimed = {row['id'] for row in remote if row['id'] in local}
        by_name = {row['name'].casefold(): row_id for row_id, row in local.items()
                   if row_id not in claimed}
        for row in remote:
            mine = local.get(row['id'])
            if mine is None:
                match = by_name.pop(row['name'].casefold(), None)
                mine = local.get(match) if match is not None else None
            if mine is None:
                if self._buried('config', row['id']) >= row['modified']:
                    self.configs[row['id']] = DEFAULT_ID
                    continue
                new = dict(row, id=self._new_id('deck_configs', row['id']))
                self.collection._insert('deck_configs', new)
                local[new['id']] = new
                self.configs[row['id']] = new['id']
                self.result.configs_changed += 1
                continue
            if _wins(row, mine, ('name', 'data')):
                self.db.execute('UPDATE deck_configs SET name = ?, data = ?, modified = ? '
                                'WHERE id = ?', (row['name'], row['data'], row['modified'],
                                                 mine['id']))
                self.result.configs_changed += 1
            self.configs[row['id']] = self._unify('deck_configs', mine['id'], row['id'],
                                                  [('decks', 'config_id')])

    # Note types

    def _merge_notetypes(self):
        local = {row['id']: dict(row) for row in self.db.execute('SELECT * FROM notetypes')}
        remote = [dict(row) for row in self.remote.execute(
            'SELECT * FROM notetypes ORDER BY id')]
        claimed = {row['id'] for row in remote if row['id'] in local}
        for row in remote:
            mine = local.get(row['id'])
            if mine is None:
                mine = self._match_notetype(row, local, claimed)
            if mine is None:
                if self._buried('notetype', row['id']) >= row['modified']:
                    continue
                new = {column: row[column] for column in ('id', 'modified') + NOTETYPE_COLUMNS}
                new['id'] = self._new_id('notetypes', row['id'])
                self.collection._insert('notetypes', new)
                local[new['id']] = new
                claimed.add(new['id'])
                self.notetypes[row['id']] = NoteType.from_row(new)
                self.result.notetypes_changed += 1
                continue
            claimed.add(mine['id'])
            if _wins(row, mine, NOTETYPE_COLUMNS):
                old = NoteType.from_row(mine)
                assignments = ', '.join(f'{column} = ?' for column in NOTETYPE_COLUMNS)
                self.db.execute(f'UPDATE notetypes SET {assignments}, modified = ? WHERE id = ?',
                                [row[column] for column in NOTETYPE_COLUMNS]
                                + [row['modified'], mine['id']])
                new = NoteType.from_row(dict(row, id=mine['id']))
                if old.field_names() != new.field_names():
                    self._carry_fields(old, new)
                self.result.notetypes_changed += 1
            local_id = self._unify('notetypes', mine['id'], row['id'],
                                   [('notes', 'notetype_id')], 'last_notetype')
            stored = self.db.execute('SELECT * FROM notetypes WHERE id = ?',
                                     (local_id,)).fetchone()
            self.notetypes[row['id']] = NoteType.from_row(stored)

    def _match_notetype(self, row, local, claimed):
        """The local note type a remote one with another id is: the same Anki id, else the
        same name, kind and field names."""
        free = [mine for mine in local.values() if mine['id'] not in claimed]
        if row['original_id']:
            for mine in free:
                if mine['original_id'] == row['original_id']:
                    return mine
        theirs = NoteType.from_row(row)
        names = [name.casefold() for name in theirs.field_names()]
        for mine in free:
            ours = NoteType.from_row(mine)
            if (ours.name.casefold() == theirs.name.casefold() and ours.kind == theirs.kind
                    and [name.casefold() for name in ours.field_names()] == names):
                return mine
        return None

    def _carry_fields(self, old, new):
        """Carry the local notes' contents across the type's new fields, by name; the notes
        keep their modified time (the change is the type's)."""
        old_names, new_names = old.field_names(), new.field_names()
        rows = self.db.execute('SELECT * FROM notes WHERE notetype_id = ?', (new.id,)).fetchall()
        for row in rows:
            note = Note.from_row(row)
            values = dict(zip(old_names, note.fields, strict=False))
            note.fields = [values.get(name, '') for name in new_names]
            self.collection._store_note(note, new)

    # Decks

    def _merge_decks(self):
        local = {row['id']: dict(row) for row in self.db.execute('SELECT * FROM decks')}
        remote = [dict(row) for row in self.remote.execute('SELECT * FROM decks')]
        remote.sort(key=lambda row: (row['name'].count('::'), row['id']))
        claimed = {row['id'] for row in remote if row['id'] in local}
        by_name = {row['name'].casefold(): row_id for row_id, row in local.items()
                   if row_id not in claimed}
        names = {row['name'].casefold(): row_id for row_id, row in local.items()}
        deck_graves = {}
        for (kind, _key), (deleted, name) in self.graves.items():
            if kind == 'deck' and name:
                folded = name.casefold()
                deck_graves[folded] = max(deck_graves.get(folded, -1), deleted)
        for row in remote:
            row['config_id'] = self.configs.get(row['config_id'], DEFAULT_ID)
            mine = local.get(row['id'])
            if mine is None:
                match = by_name.pop(row['name'].casefold(), None)
                mine = local.get(match) if match is not None else None
            if mine is None:
                deleted = max(self._buried('deck', row['id']),
                              deck_graves.get(row['name'].casefold(), -1))
                if deleted >= row['modified']:
                    continue
                new = {column: row[column] for column in Deck.FIELDS}
                new['id'] = self._new_id('decks', row['id'])
                if new['name'].casefold() in names:
                    new['name'] = self._free_deck_name(new['name'], names)
                    new['modified'] = self.now
                self.collection._insert('decks', new)
                local[new['id']] = new
                names[new['name'].casefold()] = new['id']
                self.decks[row['id']] = new['id']
                self.result.decks_changed += 1
                continue
            if _wins(row, mine, ('name', 'config_id', 'description')):
                name = row['name']
                holder = names.get(name.casefold())
                if holder is not None and holder != mine['id']:
                    name = mine['name']  # the name is another deck's here: keep ours
                self.db.execute('UPDATE decks SET name = ?, config_id = ?, description = ?, '
                                'modified = ? WHERE id = ?',
                                (name, row['config_id'], row['description'], row['modified'],
                                 mine['id']))
                names.pop(mine['name'].casefold(), None)
                names[name.casefold()] = mine['id']
                self.result.decks_changed += 1
            local_id = self._unify('decks', mine['id'], row['id'], [('cards', 'deck_id')],
                                   'last_deck')
            if local_id != mine['id']:
                names[self.db.execute('SELECT name FROM decks WHERE id = ?', (local_id,)
                                      ).fetchone()[0].casefold()] = local_id
            self.decks[row['id']] = local_id
        self._add_missing_parents()

    @staticmethod
    def _free_deck_name(name, names):
        counter = 2
        while f'{name} ({counter})'.casefold() in names:
            counter += 1
        return f'{name} ({counter})'

    def _add_missing_parents(self):
        names = {row['name'].casefold() for row in self.db.execute('SELECT name FROM decks')}
        for row in self.db.execute('SELECT name FROM decks ORDER BY name').fetchall():
            parts = row['name'].split('::')
            for depth in range(1, len(parts)):
                parent = '::'.join(parts[:depth])
                if parent.casefold() not in names:
                    self.db.execute('INSERT INTO decks (id, name, config_id, modified) '
                                    'VALUES (?, ?, ?, ?)',
                                    (self.collection.next_id(), parent, DEFAULT_ID, self.now))
                    names.add(parent.casefold())

    # Notes

    def _merge_notes(self):
        local = {row['guid']: dict(row) for row in self.db.execute(
            'SELECT id, guid, notetype_id, fields, tags, marked, modified FROM notes')}
        for row in self.remote.execute('SELECT * FROM notes ORDER BY id'):
            row = dict(row)
            notetype = self.notetypes.get(row['notetype_id'])
            if notetype is None:
                continue
            row['notetype_id'] = notetype.id
            mine = local.get(row['guid'])
            if mine is None:
                if self._buried('note', row['guid']) >= row['modified']:
                    continue
                note = self._note(row, notetype, self._new_id('notes', row['id']))
                self.collection._store_note(note, notetype, insert=True)
                local[row['guid']] = dict(row, id=note.id)
                self.result.notes_added += 1
            elif _wins(row, mine, ('notetype_id', 'fields', 'tags', 'marked')):
                note = self._note(row, notetype, mine['id'])
                self.collection._store_note(note, notetype)
                self.result.notes_updated += 1
            self.notes[row['id']] = local[row['guid']]['id']

    @staticmethod
    def _note(row, notetype, note_id):
        note = Note.from_row(row)
        note.id = note_id
        count = len(notetype.fields)
        note.fields = (list(note.fields) + [''] * count)[:count]
        return note

    # Cards

    def _merge_cards(self):
        local = {(row['note_id'], row['ord']): dict(row)
                 for row in self.db.execute('SELECT * FROM cards')}
        next_position = self.collection.get('next_position', 0)
        newest = next_position
        for row in self.remote.execute(
                'SELECT cards.*, notes.guid AS guid FROM cards '
                'JOIN notes ON notes.id = cards.note_id ORDER BY cards.id'):
            row = dict(row)
            note_id = self.notes.get(row['note_id'])
            if note_id is None:
                continue
            row['deck_id'] = self.decks.get(row['deck_id']) or self._fallback_deck()
            if row['state'] not in STATES:
                row['state'] = 'new'
            mine = local.get((note_id, row['ord']))
            if mine is None:
                if self._buried('card', card_key(row['guid'], row['ord'])) >= row['modified']:
                    continue
                new = {column: row[column] for column in Card.FIELDS}
                new['id'] = self._new_id('cards', row['id'])
                new['note_id'] = note_id
                self.collection._insert('cards', new)
                local[(note_id, row['ord'])] = new
                if new['state'] == 'new':
                    newest = max(newest, int(new['due']))
                self.cards[row['id']] = new['id']
                self.result.cards_added += 1
                continue
            if _wins(row, mine, CARD_STATE):
                assignments = ', '.join(f'{column} = ?' for column in CARD_STATE)
                self.db.execute(f'UPDATE cards SET {assignments}, modified = ? WHERE id = ?',
                                [row[column] for column in CARD_STATE]
                                + [row['modified'], mine['id']])
                self.result.cards_updated += 1
            self.cards[row['id']] = mine['id']
        if newest > next_position:
            self.collection._set_quietly('next_position', newest)

    # Reviews

    def _merge_reviews(self):
        columns = ('id', 'card_id', 'rating', 'state', 'elapsed_days', 'scheduled_days',
                   'stability', 'difficulty', 'duration_ms', 'kind')
        marks = ', '.join('?' * len(columns))
        before = self.db.total_changes
        rows = []
        for row in self.remote.execute(f'SELECT {", ".join(columns)} FROM revlog'):
            row = list(row)
            row[1] = self.cards.get(row[1], row[1])
            rows.append(row)
        self.db.executemany(
            f'INSERT OR IGNORE INTO revlog ({", ".join(columns)}) VALUES ({marks})', rows)
        self.result.reviews_added += self.db.total_changes - before

    # Deletions

    def _apply_graves(self):
        """Delete what a grave as new as it or newer says was deleted: cards, then notes,
        decks (their remaining cards go to Default), note types no note uses, presets."""
        by_kind = {}
        for (kind, key), (deleted, name) in self.graves.items():
            by_kind.setdefault(kind, []).append((key, deleted, name))
        if by_kind.get('card'):
            cards = {card_key(row['guid'], row['ord']): (row['id'], row['modified'])
                     for row in self.db.execute(
                         'SELECT cards.id, cards.ord, cards.modified, notes.guid FROM cards '
                         'JOIN notes ON notes.id = cards.note_id')}
            for key, deleted, _name in by_kind['card']:
                found = cards.get(key)
                if found and found[1] <= deleted:
                    self.db.execute('DELETE FROM cards WHERE id = ?', (found[0],))
                    self.result.cards_removed += 1
        if by_kind.get('note'):
            notes = {row['guid']: (row['id'], row['modified'])
                     for row in self.db.execute('SELECT id, guid, modified FROM notes')}
            for key, deleted, _name in by_kind['note']:
                found = notes.get(key)
                if found and found[1] <= deleted:
                    removed = self.db.execute('DELETE FROM cards WHERE note_id = ?',
                                              (found[0],)).rowcount
                    self.db.execute('DELETE FROM notes WHERE id = ?', (found[0],))
                    self.result.notes_removed += 1
                    self.result.cards_removed += removed
        if by_kind.get('deck'):
            self._apply_deck_graves(by_kind['deck'])
        for key, deleted, _name in by_kind.get('notetype', []):
            row = self.db.execute('SELECT id, modified FROM notetypes WHERE id = ?',
                                  (_int(key),)).fetchone()
            if (row and row['modified'] <= deleted and not self.db.execute(
                    'SELECT 1 FROM notes WHERE notetype_id = ? LIMIT 1', (row['id'],)
            ).fetchone() and self.db.execute('SELECT COUNT(*) FROM notetypes').fetchone()[0] > 1):
                self.db.execute('DELETE FROM notetypes WHERE id = ?', (row['id'],))
                self.result.notetypes_changed += 1
        for key, deleted, _name in by_kind.get('config', []):
            row = self.db.execute('SELECT id, modified FROM deck_configs WHERE id = ?',
                                  (_int(key),)).fetchone()
            if row and row['id'] != DEFAULT_ID and row['modified'] <= deleted:
                self.db.execute('UPDATE decks SET config_id = ?, modified = ? '
                                'WHERE config_id = ?', (DEFAULT_ID, self.now, row['id']))
                self.db.execute('DELETE FROM deck_configs WHERE id = ?', (row['id'],))
                self.result.configs_changed += 1

    def _apply_deck_graves(self, graves):
        decks = {row['id']: dict(row) for row in self.db.execute(
            'SELECT id, name, modified FROM decks')}
        by_name = {row['name'].casefold(): row_id for row_id, row in decks.items()}
        doomed = []
        for key, deleted, name in graves:
            deck_id = _int(key)
            if deck_id not in decks:
                deck_id = by_name.get(name.casefold()) if name else None
            row = decks.get(deck_id)
            if row is not None and row['modified'] <= deleted and deck_id not in doomed:
                doomed.append(deck_id)
        if not doomed:
            return
        for deck_id in doomed:
            self.db.execute('DELETE FROM decks WHERE id = ?', (deck_id,))
            self.result.decks_changed += 1
        self._fallback = None
        fallback = self._fallback_deck()
        for deck_id in doomed:
            self.db.execute('UPDATE cards SET deck_id = ?, modified = ? WHERE deck_id = ?',
                            (fallback, self.now, deck_id))


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


# Describing


def describe_result(result):
    """One sentence for a toast: 'Synced: 12 cards, 3 notes from Laptop'."""
    parts = []
    if result.cards:
        parts.append(ngettext('{} card', '{} cards', result.cards).format(result.cards))
    if result.notes:
        parts.append(ngettext('{} note', '{} notes', result.notes).format(result.notes))
    if result.decks_changed:
        parts.append(ngettext('{} deck', '{} decks', result.decks_changed).format(
            result.decks_changed))
    if result.notetypes_changed:
        parts.append(ngettext('{} note type', '{} note types', result.notetypes_changed).format(
            result.notetypes_changed))
    if result.configs_changed:
        parts.append(ngettext('{} preset', '{} presets', result.configs_changed).format(
            result.configs_changed))
    if not parts and result.reviews_added:
        parts.append(ngettext('{} review', '{} reviews', result.reviews_added).format(
            result.reviews_added))
    if not parts and result.media_received:
        parts.append(ngettext('{} media file', '{} media files', result.media_received).format(
            result.media_received))
    if parts:
        if len(result.sources) == 1:
            source = result.sources[0]
        elif result.sources:
            source = ngettext('{} other device', '{} other devices', len(result.sources)).format(
                len(result.sources))
        else:
            source = _('another device')
        # Translators: {changes} is "12 cards, 3 notes", {source} a device's name.
        sentence = _('Synced: {changes} from {source}').format(changes=', '.join(parts),
                                                                source=source)
    else:
        sentence = _('Synced: nothing new from other devices')
    if result.problems:
        count = len(result.problems)
        sentence += '; ' + ngettext('{} device skipped', '{} devices skipped', count).format(
            count)
    return sentence


# Running in the app


class SyncRunner(GObject.Object):
    """Runs sync() in a thread, on a Collection of its own over `collection`'s file, with
    the folder and device name from `settings` (sync-folder, sync-device-name). One sync at
    a time: start() while one runs asks for another after it.

    Signals: `started`; `progress` (fraction, message); `finished` (the SyncResult or None,
    the error message or ''). Before `finished`, on success, the main collection's undo
    stack is cleared (what it would undo may already be on the other devices), its media
    index refreshed and `changed` emitted for what the sync changed."""

    __gtype_name__ = 'RetainSyncRunner'

    __gsignals__ = {
        'started': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'progress': (GObject.SignalFlags.RUN_FIRST, None, (float, str)),
        'finished': (GObject.SignalFlags.RUN_FIRST, None, (object, str)),
    }

    def __init__(self, settings, collection):
        super().__init__()
        self.settings = settings
        self.collection = collection
        self.running = False
        self.again = False
        self.thread = None

    @property
    def folder(self):
        return self.settings.get_string('sync-folder')

    def device_name(self):
        return self.settings.get_string('sync-device-name').strip() or default_device_name()

    def start(self):
        """Start a sync; False when no folder is set."""
        folder = self.folder
        if not folder:
            return False
        if self.running:
            self.again = True
            return True
        self.running = True
        self.again = False
        main = self.collection
        self.thread = threading.Thread(
            target=self._work, args=(main.path, main.day_start_hour, folder, self.device_name()),
            name='retain-sync', daemon=True)
        self.emit('started')
        self.thread.start()
        return True

    def _work(self, path, day_start_hour, folder, device_name):
        collection = None
        try:
            collection = Collection(path, day_start_hour=day_start_hour, backups=False)
            result = sync(collection, folder, device_name, progress=self._progress)
        except (CollectionError, OSError, sqlite3.Error) as error:
            log.warning('sync failed: %s', error, exc_info=not isinstance(error, SyncError))
            GLib.idle_add(self._done, None, str(error))
        except Exception as error:  # the app must hear back whatever went wrong
            log.exception('sync failed')
            GLib.idle_add(self._done, None,
                          _('Something went wrong: {error}').format(error=error))
        else:
            GLib.idle_add(self._done, result, '')
        finally:
            if collection is not None:
                collection.close()

    def _progress(self, fraction, message):
        GLib.idle_add(self._show_progress, fraction, message)

    def _show_progress(self, fraction, message):
        if self.running:
            self.emit('progress', fraction, message)
        return GLib.SOURCE_REMOVE

    def _done(self, result, error):
        self.running = False
        self.thread = None
        main = self.collection
        if result is not None and main.db is not None:
            main.clear_undo()
            if result.changed():
                main.media.refresh()
                for kind in ('configs', 'notetypes', 'decks', 'notes', 'cards'):
                    main.emit('changed', kind)
            main.emit('changed', 'config')
        self.emit('finished', result, error)
        if self.again:
            self.start()
        return GLib.SOURCE_REMOVE
