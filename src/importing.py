# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Bringing an Anki package or a text file into a collection.

An import runs in a thread on a Collection of its own (the caller opens one on the same path
and closes it after), so nothing here touches GTK: progress goes through a callback,
`progress(fraction, message)`, and `should_stop()` is asked between phases and batches of
notes. The whole import is one transaction (`Collection.undoable(None)`: BEGIN, COMMIT, and
ROLLBACK on an error or a stop), so a stopped or failed import leaves the collection as it
was; it is not an undo step (the backups cover an import) and the undo stack is cleared
after it.

    import_package(collection, path, progress=None, should_stop=None, with_scheduling=True,
                   into_deck=None) -> ImportResult
    import_contents(collection, package, ...)   the same, for an apkg.Package already open
    import_text(collection, path, notetype_id, deck_id, field_map, tags=(), progress=None,
                allow_html=None, first_line_is_header=None, update_existing=True)
        -> ImportResult
    preview_text(path, notetype_fields, limit=5) -> dict
    describe_result(result) -> str              one sentence for a toast or a dialog
    rename_media_references(text, old, new) -> str

A package's note types are matched to ours by Anki id (`original_id`), else by name with the
same field names; otherwise added, named '… (imported)' when a different type of that name
exists. Decks are matched by name (case-insensitively), else added, with their descriptions;
`into_deck` puts every imported deck under that deck, and Anki's Default deck's cards into
`into_deck` itself (or our Default). A deck's Anki configuration becomes a preset, one per
distinct configuration, named after it (Anki's default one is our default preset). Notes are
matched by guid: a note we have in a newer or equal version is skipped (`notes_skipped`), an
older one gets the package's fields and tags (its cards keep their scheduling, cards it
lacks are added), a new guid is added with the cards the package lists. With
`with_scheduling` the cards come with their state, due, interval, memory, counts and flags
and their reviews are added to the revlog; without it every card is new, in the package's
order. Media a note refers to is copied from the package into the media folder; a file whose
name is taken by a different file is stored under another name and the note's references
follow. A referenced file the package lacks is listed in `problems`.

A text file (CSV or TSV, apkg.read_text_rows()) makes notes of one type in one deck:
`field_map[i]` is the index of the column for field i, or None. Without HTML the cells are
escaped and their newlines become <br>. A row whose first mapped field is empty is skipped;
a row whose first field matches an existing note of the type updates it (`update_existing`)
or is skipped. `preview_text()` gives a dialog the first rows and a guess at the map.
"""

import html
import logging
import re
import time
from gettext import gettext as _
from gettext import ngettext

from . import apkg, template
from .collection import STATES, Card, CollectionError, Note, normalize_deck_name
from .deck_config import DeckConfig
from .notetypes import NoteType

log = logging.getLogger(__name__)

BATCH = 500           # notes between two progress reports
PROBLEM_LIMIT = 20    # problems of one kind listed before '… and N more'
MARKED_TAG = 'marked'


class ImportStopped(Exception):
    """Raised inside the transaction when should_stop() says so; the import rolls back."""


class ImportResult:
    """What an import did: counts, the problems met (sentences for the user) and whether
    it was stopped (then nothing was changed)."""

    COUNTS = ('notes_added', 'notes_updated', 'notes_skipped', 'cards_added', 'decks_added',
              'notetypes_added', 'media_added', 'reviews_added')

    def __init__(self):
        for name in self.COUNTS:
            setattr(self, name, 0)
        self.problems = []
        self.stopped = False

    def __repr__(self):
        counts = ', '.join(f'{name}={getattr(self, name)}' for name in self.COUNTS)
        return f'ImportResult({counts}, problems={len(self.problems)}, stopped={self.stopped})'


# Shared helpers


def _report(progress, fraction, message):
    if progress is not None:
        progress(min(max(float(fraction), 0.0), 1.0), message)


def _check_stop(should_stop):
    if should_stop is not None and should_stop():
        raise ImportStopped()


def _pad_fields(fields, count):
    """`fields` cut or padded with '' to `count` entries, None read as ''."""
    fields = [value or '' for value in list(fields)[:count]]
    return fields + [''] * (count - len(fields))


def _chunks(items, size=BATCH):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _list_problems(result, label, items):
    """Add `label` lines for `items` to the result, at most PROBLEM_LIMIT of them."""
    items = list(items)
    for item in items[:PROBLEM_LIMIT]:
        result.problems.append(label.format(item))
    if len(items) > PROBLEM_LIMIT:
        remaining = len(items) - PROBLEM_LIMIT
        result.problems.append(
            ngettext('… and {} more', '… and {} more', remaining).format(remaining))


def rename_media_references(text, old, new):
    """`text` with every reference to the media file `old` (the src or data of a tag, quoted
    or not, and `[sound:…]`) pointing at `new` instead."""
    if not old or old == new:
        return text
    variants = [(old, new)]
    if html.escape(old) != old:
        variants.append((html.escape(old), html.escape(new)))
    for old_text, new_text in variants:
        pattern = re.compile(
            r'((?:src|data)\s*=\s*)(["\']?)' + re.escape(old_text) + r'(?(2)\2|(?=[\s>]|$))',
            re.IGNORECASE)
        text = pattern.sub(lambda match, new_text=new_text: match[1] + match[2] + new_text
                           + match[2], text)
        text = text.replace(f'[sound:{old_text}]', f'[sound:{new_text}]')
    return text


# Packages


def import_package(collection, path, progress=None, should_stop=None, with_scheduling=True,
                   into_deck=None):
    """Import the .apkg or .colpkg at `path` (see the module docstring). Raises
    apkg.PackageError when it cannot be read."""
    _report(progress, 0.0, _('Reading the package…'))
    with apkg.read_package(path) as package:
        return import_contents(collection, package, progress, should_stop, with_scheduling,
                               into_deck)


def import_contents(collection, package, progress=None, should_stop=None, with_scheduling=True,
                    into_deck=None):
    """Import an open apkg.Package; what import_package() does once the file is read."""
    result = ImportResult()
    importer = _PackageImport(collection, package, result, progress, should_stop,
                              with_scheduling, into_deck)
    try:
        with collection.undoable(None):
            importer.run()
    except ImportStopped:
        importer.media.discard()
        log.info('import of %s stopped; nothing changed', package.path)
        result = ImportResult()
        result.stopped = True
    except BaseException:
        importer.media.discard()
        raise
    finally:
        collection.clear_undo()
    if not result.stopped:
        log.info('imported %s: %r', package.path, result)
        _report(progress, 1.0, _('Done'))
    return result


class _PackageMedia:
    """The package's media files, copied into the store as notes refer to them, each once."""

    def __init__(self, collection, package, result):
        self.collection = collection
        self.package = package
        self.result = result
        self.stored = {}        # package name -> the name in the store
        self.existing = set(collection.media.names())
        self.added = []         # the files this import wrote, removed if it rolls back
        self.missing = set()

    def discard(self):
        """Remove the files this import wrote: the media folder is outside the transaction."""
        for name in self.added:
            self.collection.media.remove(name)
        self.added = []

    def store(self, name):
        """The store's name for the package file `name`, copying it on first use; None when
        the package has no such file."""
        if name in self.stored:
            return self.stored[name]
        if name not in self.package.media:
            self.missing.add(name)
            return None
        stored = self.collection.media.add_bytes(self.package.read_media(name), name)
        if stored not in self.existing:
            self.existing.add(stored)
            self.added.append(stored)
            self.result.media_added += 1
        self.stored[name] = stored
        return stored

    def rewrite(self, fields):
        """`fields` with their media copied in and the references renamed where the store
        gave a file another name."""
        for name in template.media_references(template.FIELD_SEPARATOR.join(fields)):
            stored = self.store(name)
            if stored is not None and stored != name:
                fields = [rename_media_references(field, name, stored) for field in fields]
        return fields


class _PackageImport:
    """One package import: the steps of import_contents(), inside its transaction."""

    def __init__(self, collection, package, result, progress, should_stop, with_scheduling,
                 into_deck):
        self.collection = collection
        self.package = package
        self.result = result
        self.progress = progress
        self.should_stop = should_stop
        self.with_scheduling = with_scheduling
        self.into_deck = normalize_deck_name(into_deck) if into_deck else None
        self.now = int(time.time())
        self.notetypes = {}     # package note type id -> our NoteType
        self.decks = {}         # package deck id -> our deck id
        self.card_ids = {}      # package card id -> our card id
        self._fallback = None
        self.media = _PackageMedia(collection, package, result)
        self.unknown_types = 0

    def run(self):
        _check_stop(self.should_stop)
        _report(self.progress, 0.05, _('Importing note types and decks…'))
        self._import_notetypes()
        self._import_decks()
        _check_stop(self.should_stop)
        self._import_notes()
        _check_stop(self.should_stop)
        if self.with_scheduling:
            _report(self.progress, 0.9, _('Importing review history…'))
            self._import_reviews()
        self._report_problems()

    # Note types and decks

    def _import_notetypes(self):
        for package_type in self.package.notetypes:
            existing = self._notetype_by_original_id(package_type.original_id)
            if existing is None:
                same_name = self.collection.notetype_by_name(package_type.name)
                if same_name is not None and _same_field_names(same_name, package_type):
                    existing = same_name
            if existing is None:
                existing = self._add_notetype(package_type)
                self.result.notetypes_added += 1
            self.notetypes[package_type.original_id] = existing

    def _notetype_by_original_id(self, original_id):
        if not original_id:
            return None
        row = self.collection.db.execute('SELECT * FROM notetypes WHERE original_id = ?',
                                         (original_id,)).fetchone()
        return NoteType.from_row(row) if row else None

    def _add_notetype(self, package_type):
        name = package_type.name or _('Imported')
        if self.collection.notetype_by_name(name) is not None:
            candidate = _('{name} (imported)').format(name=name)
            number = 2
            while self.collection.notetype_by_name(candidate) is not None:
                candidate = _('{name} (imported {number})').format(name=name, number=number)
                number += 1
            name = candidate
        notetype = NoteType(
            name=name, kind=package_type.kind,
            fields=[{'name': field.get('name', ''), 'description': field.get('description', '')}
                    for field in package_type.fields],
            templates=[{'name': t.get('name', ''), 'qfmt': t.get('qfmt', ''),
                        'afmt': t.get('afmt', '')} for t in package_type.templates],
            css=package_type.css, sort_field=package_type.sort_field,
            original_id=package_type.original_id)
        return self.collection.add_notetype(notetype)

    def _import_configs(self):
        """A preset for each Anki deck configuration the package's decks use, by its Anki id;
        Anki's default (id 1) is left to our default preset. A preset of the same name is
        reused."""
        presets = {}
        for deck in self.package.decks:
            config = deck.config
            if not isinstance(config, dict) or config.get('id') in (None, 1):
                continue
            key = config['id']
            if key in presets:
                continue
            name = str(config.get('name') or _('Imported'))
            existing = next((preset for preset in self.collection.deck_configs()
                             if preset.name.lower() == name.lower()), None)
            if existing is not None:
                presets[key] = existing.id
            else:
                preset = DeckConfig.from_anki(config, name)
                presets[key] = self.collection.add_deck_config(preset).id
        return presets

    def _import_decks(self):
        presets = self._import_configs()
        used = {card.deck_id for card in self.package.cards}
        for package_deck in self.package.decks:
            is_default = package_deck.original_id == 1 or package_deck.name == 'Default'
            if is_default:
                if package_deck.original_id not in used:
                    continue
                name = self.into_deck or 'Default'
            else:
                name = normalize_deck_name(package_deck.name)
                if not name:
                    continue
                if self.into_deck:
                    name = f'{self.into_deck}::{name}'
            deck = self.collection.deck_by_name(name)
            if deck is None:
                deck = self.collection.add_deck(name)
                self.result.decks_added += 1
                config_id = None
                if isinstance(package_deck.config, dict):
                    config_id = presets.get(package_deck.config.get('id'))
                if (package_deck.description and not is_default) or config_id:
                    if package_deck.description and not is_default:
                        deck.description = package_deck.description
                    if config_id:
                        deck.config_id = config_id
                    self.collection.update_deck(deck)
            self.decks[package_deck.original_id] = deck.id

    def _fallback_deck(self):
        """Where a card whose deck the package does not describe goes."""
        if self._fallback is None:
            if self.into_deck:
                deck = self.collection.add_deck(self.into_deck)
            else:
                deck = self.collection.deck_by_name('Default') or self.collection.decks()[0]
            self._fallback = deck.id
        return self._fallback

    # Notes and cards

    def _plan_notes(self):
        """What to do with each package note: ('add' | 'update' | 'skip', the note, our
        note id or None, the ords of the cards we already have)."""
        existing = self._notes_by_guid([note.guid for note in self.package.notes])
        plans = []
        for note in self.package.notes:
            found = existing.get(note.guid)
            if found is None:
                plans.append(('add', note, None, set()))
                existing[note.guid] = (None, note.modified)
            elif found[1] >= note.modified or found[0] is None:
                plans.append(('skip', note, found[0], set()))
            else:
                ords = {card.ord for card in self.collection.cards_of_note(found[0])}
                plans.append(('update', note, found[0], ords))
                existing[note.guid] = (found[0], note.modified)
        return plans

    def _notes_by_guid(self, guids):
        found = {}
        for chunk in _chunks(list(set(guids))):
            marks = ','.join('?' * len(chunk))
            for row in self.collection.db.execute(
                    f'SELECT id, guid, modified FROM notes WHERE guid IN ({marks})', chunk):
                found[row['guid']] = (row['id'], row['modified'])
        return found

    def _new_positions(self, plans, cards_by_note):
        """A position in the new queue for every card that will be inserted new, in the order
        of the package's due values."""
        new_cards = []
        for action, note, _note_id, ords in plans:
            if action == 'skip':
                continue
            for card in cards_by_note.get(note.original_id, []):
                if card.ord in ords:
                    continue
                if not self.with_scheduling or card.state == 'new':
                    new_cards.append(card)
        new_cards.sort(key=lambda card: (card.due, card.original_id))
        return {card.original_id: self.collection._next_new_position() for card in new_cards}

    def _import_notes(self):
        plans = self._plan_notes()
        self.result.notes_skipped = sum(1 for action, *_rest in plans if action == 'skip')
        cards_by_note = {}
        for card in self.package.cards:
            cards_by_note.setdefault(card.note_id, []).append(card)
        positions = self._new_positions(plans, cards_by_note)
        pending = [plan for plan in plans if plan[0] != 'skip']
        total = max(len(pending), 1)
        done = 0
        _report(self.progress, 0.1, _('Importing notes…'))
        types_by_id = {}
        for chunk in _chunks(pending):
            _check_stop(self.should_stop)
            for action, note, note_id, ords in chunk:
                if action == 'add':
                    notetype = self.notetypes.get(note.notetype_id)
                else:
                    record = self.collection.note(note_id)
                    if record.notetype_id not in types_by_id:
                        types_by_id[record.notetype_id] = self.collection.notetype(
                            record.notetype_id)
                    notetype = types_by_id[record.notetype_id]
                if notetype is None:
                    self.unknown_types += 1
                    continue
                fields = self.media.rewrite(_pad_fields(note.fields, len(notetype.fields)))
                tags = [tag for tag in note.tags if tag.lower() != MARKED_TAG]
                if action == 'add':
                    record = Note(id=self.collection.next_id(), guid=note.guid or apkg.guid(),
                                  notetype_id=notetype.id, fields=fields, sort_field='',
                                  tags=tags, marked=bool(note.marked),
                                  created=note.created or self.now,
                                  modified=note.modified or self.now)
                    self.collection._store_note(record, notetype, insert=True)
                    self.result.notes_added += 1
                else:
                    record.fields = fields
                    record.tags = tags
                    record.marked = bool(note.marked)
                    record.modified = note.modified or self.now
                    self.collection._store_note(record, notetype)
                    self.result.notes_updated += 1
                self._import_cards(record, notetype, action, cards_by_note.get(
                    note.original_id, []), ords, positions)
            done += len(chunk)
            _report(self.progress, 0.1 + 0.8 * done / total,
                    _('Importing notes… {done} of {total}').format(done=done, total=total))
        self.collection._touch('notes')
        self.collection._touch('cards')

    def _import_cards(self, record, notetype, action, package_cards, ords, positions):
        wanted = [card for card in package_cards if card.ord not in ords]
        if not wanted:
            if action == 'add':
                self.collection._generate_cards(record, notetype, self._fallback_deck())
                self.result.cards_added += len(self.collection.cards_of_note(record.id))
            return
        for card in wanted:
            deck_id = self.decks.get(card.deck_id) or self._fallback_deck()
            our_card = self._card_record(card, record.id, deck_id, positions)
            self.collection._insert('cards', our_card.to_row())
            self.card_ids[card.original_id] = our_card.id
            self.result.cards_added += 1

    def _card_record(self, card, note_id, deck_id, positions):
        scheduled = self.with_scheduling and card.state in STATES and card.state != 'new'
        common = {
            'id': self.collection.next_id(), 'note_id': note_id, 'deck_id': deck_id,
            'ord': int(card.ord), 'flag': int(card.flag or 0) & 7, 'buried': 0,
            'created': int(card.created or self.now), 'modified': int(card.modified or self.now),
        }
        if scheduled:
            return Card(state=card.state, due=int(card.due), interval=float(card.interval or 0),
                        stability=float(card.stability or 0),
                        difficulty=float(card.difficulty or 0), reps=int(card.reps or 0),
                        lapses=int(card.lapses or 0), left=int(card.left or 0),
                        last_review=int(card.last_review or 0),
                        suspended=1 if card.suspended else 0, **common)
        keep = self.with_scheduling
        return Card(state='new', due=positions[card.original_id], interval=0, stability=0,
                    difficulty=0, reps=int(card.reps or 0) if keep else 0,
                    lapses=int(card.lapses or 0) if keep else 0, left=0,
                    last_review=int(card.last_review or 0) if keep else 0,
                    suspended=1 if keep and card.suspended else 0, **common)

    # Reviews

    def _import_reviews(self):
        used = set()
        for review in self.package.reviews:
            card_id = self.card_ids.get(review.card_id)
            if card_id is None:
                continue
            revlog_id = self._free_revlog_id(int(review.id_ms), used)
            self.collection._insert('revlog', {
                'id': revlog_id, 'card_id': card_id, 'rating': int(review.rating),
                'state': review.state if review.state in STATES else 'review',
                'elapsed_days': float(review.elapsed_days or 0),
                'scheduled_days': float(review.scheduled_days or 0),
                'stability': float(review.stability or 0),
                'difficulty': float(review.difficulty or 0),
                'duration_ms': int(review.duration_ms or 0), 'kind': review.kind or 'review'})
            self.result.reviews_added += 1

    def _free_revlog_id(self, candidate, used):
        while candidate in used or self.collection.db.execute(
                'SELECT 1 FROM revlog WHERE id = ?', (candidate,)).fetchone():
            candidate += 1
        used.add(candidate)
        return candidate

    # Problems

    def _report_problems(self):
        _list_problems(self.result, _('Missing media: {}'), sorted(self.media.missing))
        if self.unknown_types:
            self.result.problems.append(ngettext(
                '{} note was skipped: its note type is not in the package',
                '{} notes were skipped: their note type is not in the package',
                self.unknown_types).format(self.unknown_types))


def _same_field_names(notetype, package_type):
    ours = [name.lower() for name in notetype.field_names()]
    theirs = [field.get('name', '').lower() for field in package_type.fields]
    return ours == theirs


# Text files


def import_text(collection, path, notetype_id, deck_id, field_map, tags=(), progress=None,
                allow_html=None, first_line_is_header=None, update_existing=True):
    """Import the CSV/TSV at `path` as notes of `notetype_id` in `deck_id` (see the module
    docstring). Raises apkg.PackageError when the file cannot be read."""
    result = ImportResult()
    notetype = collection.notetype(notetype_id)
    if notetype is None:
        raise CollectionError(_('The note type no longer exists.'))
    _report(progress, 0.0, _('Reading the file…'))
    rows, info = apkg.read_text_rows(path)
    field_map = list(field_map)[:len(notetype.fields)]
    field_map += [None] * (len(notetype.fields) - len(field_map))
    if first_line_is_header is None:
        first_line_is_header = (bool(rows) and info['columns'] is None
                                and _looks_like_header(rows[0], field_map, notetype))
    if first_line_is_header and rows:
        rows = rows[1:]
    keep_html = info['html'] if allow_html is None else bool(allow_html)
    first_mapped = next((index for index, column in enumerate(field_map) if column is not None),
                        None)
    tags_column = info.get('tags_column')
    base_tags = list(tags) + list(info.get('tags') or [])
    existing = _first_field_index(collection, notetype_id)
    empty_rows, failed_rows = [], []
    total = max(len(rows), 1)
    with collection.undoable(None):
        for start in range(0, len(rows), BATCH):
            for number, row in enumerate(rows[start:start + BATCH], start + 1):
                fields = _pad_fields([_cell(row, column, keep_html) for column in field_map],
                                     len(notetype.fields))
                if first_mapped is None or not template.strip_html(fields[first_mapped]):
                    empty_rows.append(number)
                    continue
                row_tags = list(base_tags)
                if tags_column is not None and tags_column < len(row):
                    row_tags.extend(row[tags_column].split())
                key = template.strip_html(fields[0]).lower()
                note_id = existing.get(key) if key else None
                if note_id is not None:
                    if update_existing:
                        note = collection.note(note_id)
                        for index, column in enumerate(field_map):
                            if column is not None and index < len(note.fields):
                                note.fields[index] = fields[index]
                        note.tags = note.tags + row_tags
                        collection.update_note(note, deck_id)
                        result.notes_updated += 1
                    else:
                        result.notes_skipped += 1
                    continue
                try:
                    note = collection.add_note(notetype_id, deck_id, fields, row_tags)
                except CollectionError as error:
                    failed_rows.append(_('Row {number}: {error}').format(number=number,
                                                                         error=error))
                    continue
                result.notes_added += 1
                result.cards_added += len(collection.cards_of_note(note.id))
                if key:
                    existing[key] = note.id
            done = min(start + BATCH, len(rows))
            _report(progress, done / total,
                    _('Importing notes… {done} of {total}').format(done=done, total=len(rows)))
    collection.clear_undo()
    if empty_rows:
        _list_problems(result, _('Row {} skipped: its first field is empty'), empty_rows)
    _list_problems(result, '{}', failed_rows)
    log.info('imported %s: %r', path, result)
    _report(progress, 1.0, _('Done'))
    return result


def _cell(row, column, keep_html):
    if column is None or column < 0 or column >= len(row):
        return ''
    text = row[column]
    if keep_html:
        return text
    return html.escape(text).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')


def _looks_like_header(row, field_map, notetype):
    """Whether the first row only names the fields it is mapped to."""
    names = notetype.field_names()
    mapped = [(column, names[index]) for index, column in enumerate(field_map)
              if column is not None and index < len(names)]
    if not mapped:
        return False
    return all(column < len(row) and row[column].strip().lower() == name.lower()
               for column, name in mapped)


def _first_field_index(collection, notetype_id):
    """The notes of the type by their first field, HTML stripped and folded: {key: id}."""
    index = {}
    for row in collection.db.execute('SELECT id, fields FROM notes WHERE notetype_id = ?',
                                     (notetype_id,)):
        first = row['fields'].split(template.FIELD_SEPARATOR, 1)[0]
        key = template.strip_html(first).lower()
        if key and key not in index:
            index[key] = row['id']
    return index


def preview_text(path, notetype_fields, limit=5):
    """The first `limit` rows of the text file, the number of columns, read_text_rows()'s
    info and a guess at the field map for a note type with `notetype_fields` (names, or
    field dicts): a column named like a field goes to it, the rest in order, the tags, deck,
    note type and guid columns left out."""
    rows, info = apkg.read_text_rows(path)
    columns = max((len(row) for row in rows), default=0)
    names = [field['name'] if isinstance(field, dict) else str(field) for field in notetype_fields]
    reserved = {info.get(key) for key in ('tags_column', 'notetype_column', 'deck_column',
                                           'guid_column')}
    reserved.discard(None)
    headers = [name.strip().lower() for name in (info.get('columns') or [])]
    taken = set(reserved)
    suggested = []
    for name in names:
        match = next((index for index, header in enumerate(headers)
                      if header == name.lower() and index not in taken), None)
        if match is not None:
            taken.add(match)
        suggested.append(match)
    for index, value in enumerate(suggested):
        if value is None:
            free = next((column for column in range(columns) if column not in taken), None)
            if free is not None:
                taken.add(free)
                suggested[index] = free
    return {'rows': rows[:limit], 'columns': columns, 'info': info, 'suggested_map': suggested}


# Describing


def describe_result(result):
    """One sentence on what an import did: 'Added 120 notes and 240 cards, updated 3
    notes.'"""
    if result.stopped:
        return _('The import was stopped; nothing was changed.')
    parts = []
    if result.notes_added or result.cards_added:
        notes = ngettext('{} note', '{} notes', result.notes_added).format(result.notes_added)
        cards = ngettext('{} card', '{} cards', result.cards_added).format(result.cards_added)
        parts.append(_('added {notes} and {cards}').format(notes=notes, cards=cards))
    if result.notes_updated:
        parts.append(ngettext('updated {} note', 'updated {} notes',
                              result.notes_updated).format(result.notes_updated))
    if result.notes_skipped:
        parts.append(ngettext('skipped {} unchanged note', 'skipped {} unchanged notes',
                              result.notes_skipped).format(result.notes_skipped))
    if parts:
        sentence = ', '.join(parts)
        sentence = sentence[0].upper() + sentence[1:]
    else:
        sentence = _('Nothing was imported')
    if result.problems:
        count = len(result.problems)
        sentence += '; ' + ngettext('{} problem', '{} problems', count).format(count)
    return sentence + '.'
