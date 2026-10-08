# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Anki packages (.apkg, .colpkg) in and out, and CSV/TSV text in.

This module knows Anki's formats and nothing of our collection: `read_package()` turns a
package into the neutral NoteType, Deck, Note, Card and Review structures below, in our
conventions (schema.py's states and due values, day numbers from days.py, Unix seconds),
and `write_package()` turns such structures into a package Anki and AnkiDroid accept.
collection.py converts between these structures and its tables.

    read_package(path) -> Package          raises PackageError on anything unreadable
    write_package(path, notetypes, decks, notes, cards, reviews, media_files,
                  with_scheduling=True, created=None)
    read_text_rows(path) -> (rows, info)   CSV/TSV with Anki's header lines
    guid() -> str                          a new note guid
    checksum(text) -> int                  Anki's first-field checksum

Formats. A package is a zip. 'legacy1' (Anki 2.0, 2.1 up to 2.1.49, and what
`write_package()` writes) holds `collection.anki2`, a schema 11 SQLite file, and a JSON
`media` index {"0": "name.png"} naming the raw entries "0", "1", … 'legacy2' (exported with
"Support older Anki versions" ticked) is the same with the data in `collection.anki21` and a
dummy `collection.anki2`. 'latest' (2.1.50 onwards) has a `meta` entry, `collection.anki21b`
(a zstd frame around a schema 18 SQLite file, whose note types and decks are tables of
protobuf blobs) and a zstd-compressed protobuf media index; every media entry is
zstd-compressed too. Reading it needs a zstd module (`compression.zstd` on Python 3.14, else
`zstandard` or `pyzstd`); without one `read_package()` raises PackageError telling the user
to export with the legacy option. The protobuf messages are decoded by hand (`_proto()`),
which is all their few fields need.

Conventions of the structures. `original_id` is Anki's id (a millisecond timestamp) and the
key by which the other structures refer to it: `Note.notetype_id` is a NoteType's,
`Card.note_id` a Note's, `Card.deck_id` a Deck's, `Review.card_id` a Card's. For writing, an
`original_id` below 10**12 (one of our own ids, not Anki's) or None is replaced by a fresh
millisecond id made from `created`; the references are resolved before that. Deck names use
"::" and missing parents are written too; a deck's `config` (an Anki dconf dict,
DeckConfig.to_anki()) is written as its deck configuration, once per configuration id; a
deck named Default, or with id 1, is Anki's Default deck. Cards in a filtered deck come back
in their home deck with their home due. A review card's `due` is a day number (Anki stores
days since the collection's creation day, `Package.created`), a learning or relearning
card's Unix seconds (Anki's day-learn cards are given their day's start), a new card's its
position. Anki's buried cards come back unburied (`buried` 0). A card's `last_review` comes
from the revlog. The revlog's stability and difficulty are 0: Anki does not store them.

Text files. `read_text_rows()` returns the rows of a CSV/TSV file and what its Anki header
lines (`#separator:tab`, `#html:true`, `#tags column:2`, `#notetype column:1`,
`#deck column:3`, `#guid column:4`, `#columns:Front<tab>Back`, `#tags:`, `#deck:`,
`#notetype:`) said, the column numbers converted to 0-based indexes (None when absent);
without a header the delimiter is sniffed and `html` guessed from the content. Lines
starting with # are comments.
"""

import csv
import dataclasses
import hashlib
import html
import json
import logging
import os
import random
import re
import sqlite3
import tempfile
import time
import zipfile
from gettext import gettext as _

from retain import days

log = logging.getLogger(__name__)

try:
    from compression import zstd as _zstd

    _decompress = _zstd.decompress
except ImportError:  # pragma: no cover - depends on the Python version
    try:
        import zstandard as _zstandard

        def _decompress(data):
            return _zstandard.ZstdDecompressor().decompressobj().decompress(data)
    except ImportError:
        try:
            import pyzstd as _pyzstd

            _decompress = _pyzstd.decompress
        except ImportError:
            _decompress = None


class PackageError(Exception):
    """A package or text file that cannot be read or written; the message is for the user."""


@dataclasses.dataclass
class NoteType:
    original_id: int
    name: str
    kind: str = 'standard'          # standard, cloze, occlusion
    fields: list = dataclasses.field(default_factory=list)      # [{'name', 'description'}]
    templates: list = dataclasses.field(default_factory=list)   # [{'name', 'qfmt', 'afmt'}]
    css: str = ''
    sort_field: int = 0


@dataclasses.dataclass
class Deck:
    original_id: int
    name: str
    description: str = ''
    config: dict = None             # Anki's dconf entry as read, or None


@dataclasses.dataclass
class Note:
    original_id: int
    guid: str
    notetype_id: int
    fields: list
    tags: list = dataclasses.field(default_factory=list)
    created: int = 0
    modified: int = 0
    marked: bool = False


@dataclasses.dataclass
class Card:
    original_id: int
    note_id: int
    deck_id: int
    ord: int
    state: str = 'new'
    due: int = 0
    interval: float = 0
    stability: float = 0
    difficulty: float = 0
    reps: int = 0
    lapses: int = 0
    left: int = 0
    last_review: int = 0
    flag: int = 0
    suspended: int = 0
    buried: int = 0
    created: int = 0
    modified: int = 0


@dataclasses.dataclass
class Review:
    id_ms: int
    card_id: int
    rating: int
    state: str
    elapsed_days: float = 0
    scheduled_days: float = 0
    stability: float = 0
    difficulty: float = 0
    duration_ms: int = 0
    kind: str = 'review'


# Anki's card types and revlog types, in our words.
_STATES = {0: 'new', 1: 'learning', 2: 'review', 3: 'relearning'}
_TYPES = {'new': 0, 'learning': 1, 'review': 2, 'relearning': 3}
_REVLOG_STATES = {0: 'learning', 1: 'review', 2: 'relearning', 3: 'review', 4: 'review',
                  5: 'review'}
_REVLOG_TYPES = {'learning': 0, 'new': 0, 'review': 1, 'relearning': 2}
# The fields of Anki's image occlusion note type, which is a cloze type to the format.
_OCCLUSION_FIELDS = {'occlusion', 'image', 'header', 'back extra', 'comments'}
_STOCK_KIND_OCCLUSION = 6
# A due or a created value above this is a Unix time, not a day count or a position.
_TIMESTAMP = 1_000_000_000
# Anki's ids are millisecond timestamps; anything smaller is one of ours.
_MS_ID = 10 ** 12
_LATEX_PRE = ('\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n'
              '\\usepackage[utf8]{inputenc}\n\\usepackage{amssymb,amsmath}\n'
              '\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n')
_LATEX_POST = '\\end{document}'
_SQLITE_MAGIC = b'SQLite format 3\x00'

_SCHEMA_11 = """
CREATE TABLE col (
    id integer primary key, crt integer not null, mod integer not null, scm integer not null,
    ver integer not null, dty integer not null, usn integer not null, ls integer not null,
    conf text not null, models text not null, decks text not null, dconf text not null,
    tags text not null
);
CREATE TABLE notes (
    id integer primary key, guid text not null, mid integer not null, mod integer not null,
    usn integer not null, tags text not null, flds text not null, sfld integer not null,
    csum integer not null, flags integer not null, data text not null
);
CREATE TABLE cards (
    id integer primary key, nid integer not null, did integer not null, ord integer not null,
    mod integer not null, usn integer not null, type integer not null, queue integer not null,
    due integer not null, ivl integer not null, factor integer not null, reps integer not null,
    lapses integer not null, left integer not null, odue integer not null,
    odid integer not null, flags integer not null, data text not null
);
CREATE TABLE revlog (
    id integer primary key, cid integer not null, usn integer not null, ease integer not null,
    ivl integer not null, lastIvl integer not null, factor integer not null,
    time integer not null, type integer not null
);
CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
CREATE INDEX ix_notes_usn ON notes (usn);
CREATE INDEX ix_cards_usn ON cards (usn);
CREATE INDEX ix_revlog_usn ON revlog (usn);
CREATE INDEX ix_cards_nid ON cards (nid);
CREATE INDEX ix_cards_sched ON cards (did, queue, due);
CREATE INDEX ix_revlog_cid ON revlog (cid);
CREATE INDEX ix_notes_csum ON notes (csum);
"""

_DEFAULT_DCONF = {
    'id': 1, 'name': 'Default', 'mod': 0, 'usn': -1, 'maxTaken': 60, 'autoplay': True,
    'timer': 0, 'replayq': True, 'dyn': False,
    'new': {'bury': False, 'delays': [1, 10], 'initialFactor': 2500, 'ints': [1, 4, 0],
            'order': 1, 'perDay': 20, 'separate': True},
    'rev': {'bury': False, 'ease4': 1.3, 'ivlFct': 1, 'maxIvl': 36500, 'perDay': 200,
            'hardFactor': 1.2, 'minSpace': 1, 'fuzz': 0.05},
    'lapse': {'delays': [10], 'leechAction': 1, 'leechFails': 8, 'minInt': 1, 'mult': 0},
}


# Small helpers


def guid():
    """A new note guid: Anki's base91 of a random 64-bit number, always 10 characters."""
    table = ('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
             '!#$%&()*+,-./:;<=>?@[]^_`{|}~')
    number = random.randint(91 ** 9, 2 ** 64 - 1)
    text = ''
    while number:
        number, index = divmod(number, 91)
        text = table[index] + text
    return text


def checksum(text):
    """Anki's note checksum: the first 8 hex digits of the SHA-1 of the stripped sort
    field, as an integer."""
    return int(hashlib.sha1(text.encode('utf-8')).hexdigest()[:8], 16)


def strip_html(text):
    """`text` without its tags and entities, trimmed: how Anki fills a note's sort field."""
    return html.unescape(re.sub(r'<[^>]*>', '', text)).strip()


def _zstd_decompress(data):
    if _decompress is None:
        raise PackageError(_(
            'This package was made by a recent Anki and reading it needs zstd support, '
            'which is not installed. Export it from Anki again with '
            '"Support older Anki versions" ticked.'))
    try:
        return _decompress(data)
    except Exception as error:
        raise PackageError(_('The package is damaged: {}').format(error)) from error


def _varint(data, position):
    result = shift = 0
    while True:
        byte = data[position]
        position += 1
        result |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return result, position
        shift += 7


def _proto(data):
    """A protobuf message as {field number: [values]}: ints for varints and fixed-width
    fields, bytes for length-delimited ones (strings, nested messages)."""
    fields = {}
    position = 0
    try:
        while position < len(data):
            key, position = _varint(data, position)
            number, wire = key >> 3, key & 7
            if wire == 0:
                value, position = _varint(data, position)
            elif wire == 1:
                value = int.from_bytes(data[position:position + 8], 'little')
                position += 8
            elif wire == 2:
                length, position = _varint(data, position)
                if position + length > len(data):
                    raise IndexError(position)
                value = bytes(data[position:position + length])
                position += length
            elif wire == 5:
                value = int.from_bytes(data[position:position + 4], 'little')
                position += 4
            else:
                raise PackageError(_('The package is damaged: unexpected data'))
            fields.setdefault(number, []).append(value)
    except IndexError as error:
        raise PackageError(_('The package is damaged: unexpected data')) from error
    return fields


def _proto_str(fields, number, default=''):
    values = fields.get(number)
    return values[-1].decode('utf-8', 'replace') if values else default


def _proto_int(fields, number, default=0):
    values = fields.get(number)
    return values[-1] if values else default


def _days_from_ivl(value):
    """Anki's interval columns: positive days, negative seconds."""
    return -value / days.SECONDS_PER_DAY if value < 0 else float(value)


def _ivl_from_days(value):
    """The reverse: whole days, or negative seconds below a day."""
    if value >= 1:
        return int(round(value))
    if value > 0:
        return -int(round(value * days.SECONDS_PER_DAY))
    return 0


def _notetype_kind(cloze, stock_kind, field_names):
    if stock_kind == _STOCK_KIND_OCCLUSION:
        return 'occlusion'
    if cloze and _OCCLUSION_FIELDS <= {name.lower() for name in field_names}:
        return 'occlusion'
    return 'cloze' if cloze else 'standard'


# Reading


class Package:
    """An open package: the structures read from it, and its media on demand.

    `notetypes`, `decks`, `notes`, `cards`, `reviews` are lists of the dataclasses above,
    `media` maps a file name to its zip entry, `created` is the collection's creation time
    (Unix seconds, the start of its day 0) and `format` 'legacy1', 'legacy2' or 'latest'.
    A context manager; `close()` releases the zip.
    """

    def __init__(self, archive, path, format):
        self.archive = archive
        self.path = path
        self.format = format
        self.created = 0
        self.notetypes = []
        self.decks = []
        self.notes = []
        self.cards = []
        self.reviews = []
        self.media = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def media_names(self):
        return list(self.media)

    def read_media(self, name):
        """The bytes of media file `name`, decompressed."""
        entry = self.media.get(name)
        if entry is None or self.archive is None:
            raise PackageError(_('The package has no media file named {}').format(name))
        try:
            data = self.archive.read(entry)
        except (KeyError, zipfile.BadZipFile) as error:
            raise PackageError(_('The package is damaged: {}').format(error)) from error
        return _zstd_decompress(data) if self.format == 'latest' else data

    def close(self):
        if self.archive is not None:
            self.archive.close()
            self.archive = None


def read_package(path):
    """Read the .apkg or .colpkg at `path` into a Package (close it, or use `with`)."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise PackageError(
            _('{} is not an Anki package: {}').format(os.path.basename(str(path)), error)
        ) from error
    try:
        format, entry = _detect_format(archive)
        package = Package(archive, path, format)
        data = archive.read(entry)
        if format == 'latest':
            data = _zstd_decompress(data)
        if not data.startswith(_SQLITE_MAGIC):
            raise PackageError(_('The package is damaged: its collection is not a database'))
        _read_database(data, package)
        package.media = _read_media_index(archive, format)
    except PackageError:
        archive.close()
        raise
    except (KeyError, ValueError, TypeError, IndexError, OSError, sqlite3.Error,
            zipfile.BadZipFile, UnicodeDecodeError) as error:
        archive.close()
        raise PackageError(_('The package is damaged: {}').format(error)) from error
    return package


def _detect_format(archive):
    names = set(archive.namelist())
    format = None
    if 'meta' in names:
        version = _proto_int(_proto(archive.read('meta')), 1)
        format = {1: 'legacy1', 2: 'legacy2'}.get(version, 'latest')
    if format == 'latest' and 'collection.anki21b' in names:
        return 'latest', 'collection.anki21b'
    if format != 'legacy1' and 'collection.anki21' in names:
        return 'legacy2', 'collection.anki21'
    if 'collection.anki2' in names:
        return 'legacy1', 'collection.anki2'
    raise PackageError(_('The file is not an Anki package: it holds no collection'))


def _read_media_index(archive, format):
    if 'media' not in archive.namelist():
        return {}
    data = archive.read('media')
    if format == 'latest':
        entries = _proto(_zstd_decompress(data)).get(1, [])
        return {_proto_str(_proto(entry), 1): str(index) for index, entry in enumerate(entries)}
    index = json.loads(data.decode('utf-8')) if data.strip() else {}
    return {str(name): str(entry) for entry, name in index.items()}


def _read_database(data, package):
    fd, db_path = tempfile.mkstemp(prefix='retain-apkg-', suffix='.sqlite')
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
        db = sqlite3.connect(db_path)
        try:
            _read_collection(db, package)
        finally:
            db.close()
    finally:
        os.unlink(db_path)


def _read_collection(db, package):
    row = db.execute('SELECT crt, models, decks, dconf FROM col').fetchone()
    if row is None:
        raise PackageError(_('The package is damaged: its collection is empty'))
    crt, models_json, decks_json, dconf_json = row
    package.created = int(crt)
    tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'notetypes' in tables:
        package.notetypes = _read_notetype_tables(db)
    else:
        package.notetypes = [_notetype_from_json(model) for model in
                             json.loads(models_json or '{}').values()]
    if 'decks' in tables:
        package.decks = _read_deck_tables(db)
    else:
        package.decks = _decks_from_json(decks_json, dconf_json)
    package.notes = [_note_from_row(row) for row in db.execute(
        'SELECT id, guid, mid, mod, tags, flds FROM notes ORDER BY id')]
    last_reviews = {}
    package.reviews = []
    for row in db.execute(
            'SELECT id, cid, ease, ivl, lastIvl, time, type FROM revlog ORDER BY id'):
        package.reviews.append(_review_from_row(row))
        if row[2] > 0:
            last_reviews[row[1]] = row[0] // 1000
    day0 = days.day_number(package.created)
    package.cards = [_card_from_row(row, day0, last_reviews) for row in db.execute(
        'SELECT id, nid, did, ord, mod, type, queue, due, ivl, reps, lapses, left, odue, '
        'odid, flags, data FROM cards ORDER BY id')]


def _notetype_from_json(model):
    fields = sorted(model.get('flds', []), key=lambda f: f.get('ord', 0))
    templates = sorted(model.get('tmpls', []), key=lambda t: t.get('ord', 0))
    names = [field.get('name', '') for field in fields]
    kind = _notetype_kind(model.get('type') == 1, model.get('originalStockKind'), names)
    return NoteType(
        original_id=int(model['id']), name=model.get('name', ''), kind=kind,
        fields=[{'name': name, 'description': field.get('description', '')}
                for name, field in zip(names, fields, strict=True)],
        templates=[{'name': t.get('name', ''), 'qfmt': t.get('qfmt', ''),
                    'afmt': t.get('afmt', '')} for t in templates],
        css=model.get('css', ''), sort_field=int(model.get('sortf', 0) or 0))


def _read_notetype_tables(db):
    fields = {}
    for ntid, name, config in db.execute(
            'SELECT ntid, name, config FROM fields ORDER BY ntid, ord'):
        fields.setdefault(ntid, []).append(
            {'name': name, 'description': _proto_str(_proto(config), 5)})
    templates = {}
    for ntid, name, config in db.execute(
            'SELECT ntid, name, config FROM templates ORDER BY ntid, ord'):
        parsed = _proto(config)
        templates.setdefault(ntid, []).append(
            {'name': name, 'qfmt': _proto_str(parsed, 1), 'afmt': _proto_str(parsed, 2)})
    notetypes = []
    for ntid, name, config in db.execute('SELECT id, name, config FROM notetypes ORDER BY id'):
        parsed = _proto(config)
        names = [field['name'] for field in fields.get(ntid, [])]
        kind = _notetype_kind(_proto_int(parsed, 1) == 1, _proto_int(parsed, 9), names)
        notetypes.append(NoteType(
            original_id=ntid, name=name, kind=kind, fields=fields.get(ntid, []),
            templates=templates.get(ntid, []), css=_proto_str(parsed, 3),
            sort_field=_proto_int(parsed, 2)))
    return notetypes


def _decks_from_json(decks_json, dconf_json):
    dconf = json.loads(dconf_json or '{}')
    decks = []
    for deck in json.loads(decks_json or '{}').values():
        did = int(deck['id'])
        if deck.get('dyn'):  # a filtered deck: its cards carry their home deck in odid
            continue
        config = dconf.get(str(deck.get('conf', 1)))
        decks.append(Deck(original_id=did, name=deck.get('name', ''),
                          description=deck.get('desc', '') or '', config=config))
    return decks


def _read_deck_tables(db):
    decks = []
    for did, name, kind in db.execute('SELECT id, name, kind FROM decks ORDER BY id'):
        parsed = _proto(kind)
        if 2 in parsed and 1 not in parsed:  # filtered
            continue
        normal = _proto(parsed[1][-1]) if 1 in parsed else {}
        decks.append(Deck(original_id=did, name=name.replace('\x1f', '::'),
                          description=_proto_str(normal, 4), config=None))
    return decks


def _note_from_row(row):
    nid, note_guid, mid, mod, tags, flds = row
    tag_list = (tags or '').split()
    return Note(original_id=nid, guid=note_guid, notetype_id=mid, fields=flds.split('\x1f'),
                tags=tag_list, created=nid // 1000, modified=int(mod),
                marked=any(tag.lower() == 'marked' for tag in tag_list))


def _card_data(text):
    if not text or not text.strip().startswith('{'):
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _card_from_row(row, day0, last_reviews):
    (cid, nid, did, ord, mod, type, queue, due, ivl, reps, lapses, left, odue, odid, flags,
     data) = row
    if odid:
        did, due = odid, odue
    state = _STATES.get(type, 'new')
    if state == 'review':
        due = days.day_number(due) if due > _TIMESTAMP else day0 + due
    elif state != 'new' and (queue == 3 or (queue not in (1, 4) and due < _TIMESTAMP)):
        try:
            due = int(days.day_start(day0 + due))
        except (ValueError, OverflowError):
            due = 0
    extra = _card_data(data)
    in_learning = state in ('learning', 'relearning')
    return Card(
        original_id=cid, note_id=nid, deck_id=did, ord=ord, state=state, due=int(due),
        interval=_days_from_ivl(ivl), stability=float(extra.get('s') or 0),
        difficulty=float(extra.get('d') or 0), reps=reps, lapses=lapses,
        left=left % 1000 if in_learning else 0,
        last_review=int(extra.get('lrt') or last_reviews.get(cid, 0)), flag=flags & 7,
        suspended=1 if queue == -1 else 0, buried=0, created=cid // 1000, modified=int(mod))


def _review_from_row(row):
    rid, cid, ease, ivl, last_ivl, duration, type = row
    rating = ease if 1 <= ease <= 4 else 0
    kind = 'cram' if type == 3 else 'manual' if type in (4, 5) or rating == 0 else 'review'
    return Review(id_ms=rid, card_id=cid, rating=rating,
                  state=_REVLOG_STATES.get(type, 'review'),
                  elapsed_days=max(0.0, _days_from_ivl(last_ivl)),
                  scheduled_days=_days_from_ivl(ivl), stability=0, difficulty=0,
                  duration_ms=int(duration), kind=kind)


# Writing


class _IdMaker:
    """Unique Anki ids: a millisecond id given is kept, anything else made from seconds."""

    def __init__(self):
        self.taken = set()

    def make(self, wanted, seconds):
        if isinstance(wanted, int) and wanted >= _MS_ID and wanted not in self.taken:
            candidate = wanted
        else:
            candidate = max(int(seconds), 1) * 1000
            while candidate in self.taken:
                candidate += 1
        self.taken.add(candidate)
        return candidate

    def unique(self, wanted):
        candidate = int(wanted)
        while candidate in self.taken:
            candidate += 1
        self.taken.add(candidate)
        return candidate


def _template_requirement(ord, qfmt, field_names):
    """Anki's `req` entry: which fields a template's front needs."""
    indexes = []
    for reference in re.findall(r'{{([^}]*)}}', qfmt):
        name = reference.split(':')[-1].strip().lstrip('#^/')
        if name in field_names and field_names.index(name) not in indexes:
            indexes.append(field_names.index(name))
    return [ord, 'any', indexes] if indexes else [ord, 'none', []]


def _model_json(mid, notetype, now):
    field_names = [field['name'] for field in notetype.fields]
    cloze = notetype.kind in ('cloze', 'occlusion')
    model = {
        'id': mid, 'name': notetype.name, 'type': 1 if cloze else 0, 'mod': now, 'usn': -1,
        'sortf': notetype.sort_field, 'did': 1,
        'tmpls': [{'name': t['name'], 'ord': i, 'qfmt': t['qfmt'], 'afmt': t['afmt'],
                   'bqfmt': '', 'bafmt': '', 'did': None, 'bfont': '', 'bsize': 0}
                  for i, t in enumerate(notetype.templates)],
        'flds': [{'name': f['name'], 'ord': i, 'sticky': False, 'rtl': False, 'font': 'Arial',
                  'size': 20, 'description': f.get('description', ''), 'plainText': False,
                  'collapsed': False, 'excludeFromSearch': False}
                 for i, f in enumerate(notetype.fields)],
        'css': notetype.css, 'latexPre': _LATEX_PRE, 'latexPost': _LATEX_POST,
        'latexsvg': False,
        'req': [] if cloze else [_template_requirement(i, t['qfmt'], field_names)
                                 for i, t in enumerate(notetype.templates)],
        'tags': [], 'vers': [],
    }
    if notetype.kind == 'occlusion':
        model['originalStockKind'] = _STOCK_KIND_OCCLUSION
    return model


def _deck_json(did, name, description, now, conf=1):
    return {'id': did, 'name': name, 'mod': now, 'usn': -1, 'desc': description, 'dyn': 0,
            'collapsed': False, 'browserCollapsed': False, 'conf': conf, 'extendNew': 0,
            'extendRev': 0, 'newToday': [0, 0], 'revToday': [0, 0], 'lrnToday': [0, 0],
            'timeToday': [0, 0]}


def _scheduled_card_columns(card, day0):
    """(type, queue, due, ivl, factor, left, data) for a card with its scheduling."""
    state = card.state if card.state in _TYPES else 'new'
    interval = max(0.0, float(card.interval or 0))
    if state == 'new':
        queue, due, ivl, factor, left = 0, int(card.due), 0, 0, 0
    elif state == 'review':
        queue, due, ivl, factor, left = 2, int(card.due) - day0, max(1, round(interval)), 2500, 0
    else:
        left = max(0, int(card.left or 0))
        queue, due, ivl, factor, left = 1, int(card.due), round(interval), 2500, left + left * 1000
    if card.buried:
        queue = -3
    if card.suspended:
        queue = -1
    data = ''
    if card.stability and card.stability > 0:
        data = json.dumps({'s': round(float(card.stability), 4),
                           'd': round(float(card.difficulty or 0), 3)})
    return _TYPES[state], queue, due, ivl, factor, left, data


def write_package(path, notetypes, decks, notes, cards, reviews, media_files,
                  with_scheduling=True, created=None):
    """Write a legacy .apkg to `path` from the structures (see the module docstring for
    how they refer to each other). `media_files` maps a file name to its path on disk;
    `created` is the collection's creation time (Unix seconds; today's start by default).
    Without `with_scheduling` every card is written new and the revlog left out."""
    now = int(time.time())
    created = int(days.day_start(days.today()) if created is None else created)
    day0 = days.day_number(created)
    ids = _IdMaker()
    for name, file_path in media_files.items():
        if not os.path.isfile(file_path):
            raise PackageError(_('Media file {} is missing').format(name))

    model_ids, models = {}, {}
    for notetype in notetypes:
        mid = ids.make(notetype.original_id, now)
        model_ids[notetype.original_id] = (mid, notetype)
        models[str(mid)] = _model_json(mid, notetype, now)

    dconf_json = {'1': dict(_DEFAULT_DCONF)}
    conf_ids = {}  # the configuration's own id -> its id in the package

    def conf_id(config):
        """A deck's Anki configuration (Deck.config) stored once; its id in the package."""
        if not isinstance(config, dict):
            return 1
        key = config.get('id')
        if key not in conf_ids:
            cid = 1 if key in (None, 1) else ids.unique(max(int(key), 2))
            conf_ids[key] = cid
            dconf_json[str(cid)] = {**_DEFAULT_DCONF, **config, 'id': cid}
        return conf_ids[key]

    deck_ids, deck_names = {}, {'Default': 1}
    decks_json = {'1': _deck_json(1, 'Default', '', now)}
    for deck in decks:
        if deck.original_id == 1 or deck.name == 'Default':
            did = 1
        else:
            did = ids.make(deck.original_id, now)
        deck_ids[deck.original_id] = did
        deck_names[deck.name] = did
        decks_json[str(did)] = _deck_json(did, deck.name, deck.description or '', now,
                                          conf_id(deck.config))
    for name in list(deck_names):
        parts = name.split('::')
        for depth in range(1, len(parts)):
            parent = '::'.join(parts[:depth])
            if parent not in deck_names:
                pid = ids.make(None, now)
                deck_names[parent] = pid
                decks_json[str(pid)] = _deck_json(pid, parent, '', now)

    note_ids, note_rows = {}, []
    for note in notes:
        if note.notetype_id not in model_ids:
            raise PackageError(_('A note refers to a note type that is not being exported'))
        mid, notetype = model_ids[note.notetype_id]
        nid = ids.make(note.original_id, note.created or now)
        note_ids[note.original_id] = nid
        fields = [str(field) for field in note.fields]
        sort_index = notetype.sort_field if 0 <= notetype.sort_field < len(fields) else 0
        sort_text = strip_html(fields[sort_index]) if fields else ''
        tags = list(note.tags)
        if note.marked and not any(tag.lower() == 'marked' for tag in tags):
            tags.append('marked')
        tag_text = ' {} '.format(' '.join(tags)) if tags else ''
        note_rows.append((nid, note.guid or guid(), mid, int(note.modified or now), -1,
                          tag_text, '\x1f'.join(fields), sort_text, checksum(sort_text), 0, ''))

    card_ids, card_rows = {}, []
    next_position = 0
    for position, card in enumerate(cards):
        if card.note_id not in note_ids:
            raise PackageError(_('A card refers to a note that is not being exported'))
        if card.deck_id not in deck_ids:
            raise PackageError(_('A card refers to a deck that is not being exported'))
        cid = ids.make(card.original_id, card.created or now)
        card_ids[card.original_id] = cid
        if with_scheduling:
            type, queue, due, ivl, factor, left, data = _scheduled_card_columns(card, day0)
            reps, lapses = int(card.reps or 0), int(card.lapses or 0)
        else:
            type, queue, due, ivl, factor, left, data = 0, 0, position, 0, 0, 0, ''
            reps = lapses = 0
        if type == 0:
            next_position = max(next_position, due + 1)
        card_rows.append((cid, note_ids[card.note_id], deck_ids[card.deck_id], int(card.ord),
                          int(card.modified or now), -1, type, queue, due, ivl, factor, reps,
                          lapses, left, 0, 0, int(card.flag or 0) & 7, data))

    revlog_rows = []
    if with_scheduling:
        for review in reviews:
            if review.card_id not in card_ids:
                continue
            if review.kind == 'manual' or review.rating == 0:
                type = 4
            elif review.kind == 'cram':
                type = 3
            else:
                type = _REVLOG_TYPES.get(review.state, 1)
            revlog_rows.append((ids.unique(review.id_ms), card_ids[review.card_id], -1,
                                int(review.rating), _ivl_from_days(review.scheduled_days or 0),
                                _ivl_from_days(review.elapsed_days or 0), 0,
                                int(review.duration_ms or 0), type))

    conf = {
        'activeDecks': [1], 'curDeck': 1, 'newSpread': 0, 'collapseTime': 1200, 'timeLim': 0,
        'estTimes': True, 'dueCounts': True,
        'curModel': int(next(iter(models), 0)) or None, 'nextPos': next_position,
        'sortType': 'noteFld', 'sortBackwards': False, 'addToCur': True,
        'dayLearnFirst': False, 'schedVer': 2,
        'creationOffset': -time.localtime(created).tm_gmtoff // 60, 'sched2021': True,
    }
    with tempfile.TemporaryDirectory(prefix='retain-apkg-') as directory:
        db_path = os.path.join(directory, 'collection.anki2')
        db = sqlite3.connect(db_path)
        try:
            db.execute('PRAGMA journal_mode = delete')
            db.executescript(_SCHEMA_11)
            db.execute(
                'INSERT INTO col VALUES (1, ?, ?, ?, 11, 0, 0, 0, ?, ?, ?, ?, ?)',
                (created, now * 1000, now * 1000, json.dumps(conf), json.dumps(models),
                 json.dumps(decks_json), json.dumps(dconf_json), '{}'))
            db.executemany('INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           note_rows)
            db.executemany(
                'INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                card_rows)
            db.executemany('INSERT INTO revlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', revlog_rows)
            db.commit()
        finally:
            db.close()
        try:
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(db_path, 'collection.anki2')
                index = {}
                for number, (name, file_path) in enumerate(media_files.items()):
                    archive.write(file_path, str(number), compress_type=zipfile.ZIP_STORED)
                    index[str(number)] = name
                archive.writestr('media', json.dumps(index))
        except OSError as error:
            raise PackageError(_('Could not write {}: {}').format(path, error)) from error
    log.info('wrote %s: %d notes, %d cards, %d reviews, %d media files', path,
             len(note_rows), len(card_rows), len(revlog_rows), len(media_files))


# Text files


_SEPARATOR_NAMES = {'tab': '\t', 'comma': ',', 'semicolon': ';', 'pipe': '|', 'space': ' ',
                    'colon': ':'}
_HTML_HINT = re.compile(r'<(?:br|div|span|img|b|i|u|p|a|ul|ol|li|sub|sup)\b[^>]*>', re.I)


def _column_index(value):
    """Anki's 1-based column number as a 0-based index, None when absent or 0."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number - 1 if number > 0 else None


def _without_comments(lines):
    """`lines` minus those starting with # outside a quoted field."""
    kept = []
    inside_quotes = False
    for line in lines:
        if not inside_quotes and line.startswith('#'):
            continue
        kept.append(line)
        if line.count('"') % 2:
            inside_quotes = not inside_quotes
    return kept


def _sniff_separator(lines):
    """The delimiter of the text's first lines: a tab wins (as in Anki), else the Sniffer
    picks among comma, semicolon and pipe, else the first of them present."""
    first = lines[0] if lines else ''
    if '\t' in first:
        return '\t'
    try:
        return csv.Sniffer().sniff(''.join(lines[:20]), delimiters=',;|').delimiter
    except csv.Error:
        for candidate in ',;|':
            if candidate in first:
                return candidate
        return '\t'


def read_text_rows(path):
    """The rows of the CSV/TSV file at `path` as lists of strings, and an info dict:
    'separator', 'html', 'tags_column', 'notetype_column', 'deck_column', 'guid_column',
    'columns', 'tags', 'deck', 'notetype' (see the module docstring)."""
    try:
        with open(path, encoding='utf-8-sig', newline='') as file:
            text = file.read()
    except UnicodeDecodeError as error:
        raise PackageError(_('The file is not UTF-8 text')) from error
    except OSError as error:
        raise PackageError(_('Could not read {}: {}').format(path, error)) from error
    lines = text.splitlines(keepends=True)
    headers = {}
    body_start = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith('#'):
            body_start = index
            break
        key, colon, value = line.rstrip('\r\n')[1:].partition(':')
        if colon:
            headers[key.strip().lower()] = value.strip(' \r\n')
    data_lines = _without_comments(lines[body_start:])
    separator_name = headers.get('separator', '').lower()
    separator = _SEPARATOR_NAMES.get(separator_name)
    if separator is None and len(separator_name) == 1:
        separator = separator_name
    if separator is None:
        separator = _sniff_separator(data_lines)
    if 'html' in headers:
        html_flag = headers['html'].lower() == 'true'
    else:
        html_flag = bool(_HTML_HINT.search(''.join(data_lines[:50])))
    columns = headers.get('columns')
    info = {
        'separator': separator, 'html': html_flag,
        'tags_column': _column_index(headers.get('tags column')),
        'notetype_column': _column_index(headers.get('notetype column')),
        'deck_column': _column_index(headers.get('deck column')),
        'guid_column': _column_index(headers.get('guid column')),
        'columns': columns.split(separator) if columns else None,
        'tags': headers.get('tags', '').split(), 'deck': headers.get('deck') or None,
        'notetype': headers.get('notetype') or None,
    }
    try:
        reader = csv.reader(data_lines, delimiter=separator, quotechar='"', doublequote=True)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as error:
        raise PackageError(_('The file could not be read as a table: {}').format(error)) from error
    return rows, info
