# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Writing a collection, or one deck of it, out as an Anki package or a text file.

    export_package(collection, path, deck_id=None, with_scheduling=True, include_media=True,
                   progress=None) -> {'notes': n, 'cards': n, 'media': n, 'missing': [names]}
    export_text(collection, path, deck_id=None, include_tags=True, include_html=True,
                notetype_id=None) -> the number of notes written

A package (apkg.write_package()) holds the decks under `deck_id` (every deck when None),
their cards, the notes of those cards, the note types those notes use, the cards' reviews
(with `with_scheduling`) and the media the notes refer to that the media folder has; the
names of referenced files it lacks come back under 'missing'. Our ids go out as the
structures' `original_id`: they are millisecond ids, unique within the collection, which
Anki accepts as its own (the Default deck, id 1, is Anki's Default). A subdeck keeps its full
name, so Anki rebuilds the parents. `progress(fraction, message)` is called at each step.

A text file is a tab-separated file with Anki's header lines (`#separator:tab`,
`#html:true`, `#tags column:N`), one line per note: the fields, then the tags when
`include_tags`. Newlines inside a field become <br> with `include_html`, else the HTML is
stripped and newlines become spaces. With `notetype_id` only notes of that type are written;
otherwise every note, padded to the widest type's number of fields.
"""

import csv
import logging
from gettext import gettext as _

from . import apkg, template

log = logging.getLogger(__name__)


def _report(progress, fraction, message):
    if progress is not None:
        progress(min(max(float(fraction), 0.0), 1.0), message)


def _chunks(items, size=500):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _deck_ids(collection, deck_id):
    if deck_id is None:
        return [deck.id for deck in collection.decks()]
    return collection.deck_and_children(deck_id)


def _card_rows(collection, deck_ids):
    rows = []
    for chunk in _chunks(deck_ids):
        marks = ','.join('?' * len(chunk))
        rows.extend(dict(row) for row in collection.db.execute(
            f'SELECT * FROM cards WHERE deck_id IN ({marks}) ORDER BY id', chunk))
    rows.sort(key=lambda row: row['id'])
    return rows


def _note_rows(collection, note_ids):
    rows = []
    for chunk in _chunks(note_ids):
        marks = ','.join('?' * len(chunk))
        rows.extend(dict(row) for row in collection.db.execute(
            f'SELECT * FROM notes WHERE id IN ({marks})', chunk))
    rows.sort(key=lambda row: row['id'])
    return rows


def _review_rows(collection, card_ids):
    rows = []
    for chunk in _chunks(card_ids):
        marks = ','.join('?' * len(chunk))
        rows.extend(dict(row) for row in collection.db.execute(
            f'SELECT * FROM revlog WHERE card_id IN ({marks})', chunk))
    rows.sort(key=lambda row: row['id'])
    return rows


def export_package(collection, path, deck_id=None, with_scheduling=True, include_media=True,
                   progress=None):
    """Write the collection, or the deck `deck_id` with its subdecks, to the .apkg at `path`
    (see the module docstring). Raises apkg.PackageError when it cannot be written."""
    _report(progress, 0.0, _('Collecting cards…'))
    deck_ids = _deck_ids(collection, deck_id)
    decks = [apkg.Deck(original_id=deck.id, name=deck.name,
                       description=deck.description or '', config=None)
             for deck in collection.decks() if deck.id in set(deck_ids)]
    card_rows = _card_rows(collection, deck_ids)
    note_ids = sorted({row['note_id'] for row in card_rows})
    note_rows = _note_rows(collection, note_ids)
    notetype_ids = sorted({row['notetype_id'] for row in note_rows})

    _report(progress, 0.2, _('Collecting notes…'))
    notetypes = []
    for notetype_id in notetype_ids:
        notetype = collection.notetype(notetype_id)
        if notetype is None:
            continue
        notetypes.append(apkg.NoteType(
            original_id=notetype.id, name=notetype.name, kind=notetype.kind,
            fields=[dict(field) for field in notetype.fields],
            templates=[dict(tmpl) for tmpl in notetype.templates], css=notetype.css,
            sort_field=notetype.sort_field))
    known_types = {notetype.original_id for notetype in notetypes}
    notes = []
    referenced = set()
    for row in note_rows:
        if row['notetype_id'] not in known_types:
            continue
        fields = row['fields'].split(template.FIELD_SEPARATOR)
        referenced.update(template.media_references(row['fields']))
        notes.append(apkg.Note(
            original_id=row['id'], guid=row['guid'], notetype_id=row['notetype_id'],
            fields=fields, tags=row['tags'].split(), created=row['created'],
            modified=row['modified'], marked=bool(row['marked'])))
    exported_notes = {note.original_id for note in notes}
    cards = [apkg.Card(
        original_id=row['id'], note_id=row['note_id'], deck_id=row['deck_id'], ord=row['ord'],
        state=row['state'], due=row['due'], interval=row['interval'],
        stability=row['stability'], difficulty=row['difficulty'], reps=row['reps'],
        lapses=row['lapses'], left=row['left'], last_review=row['last_review'],
        flag=row['flag'], suspended=row['suspended'], buried=1 if row['buried'] else 0,
        created=row['created'], modified=row['modified'])
        for row in card_rows if row['note_id'] in exported_notes]

    reviews = []
    if with_scheduling:
        _report(progress, 0.4, _('Collecting review history…'))
        reviews = [apkg.Review(
            id_ms=row['id'], card_id=row['card_id'], rating=row['rating'], state=row['state'],
            elapsed_days=row['elapsed_days'], scheduled_days=row['scheduled_days'],
            stability=row['stability'], difficulty=row['difficulty'],
            duration_ms=row['duration_ms'], kind=row['kind'])
            for row in _review_rows(collection, [card.original_id for card in cards])]

    media_files = {}
    missing = []
    if include_media:
        _report(progress, 0.6, _('Collecting media…'))
        for name in sorted(referenced):
            if collection.media.exists(name):
                media_files[name] = str(collection.media.path(name))
            else:
                missing.append(name)

    _report(progress, 0.8, _('Writing the package…'))
    apkg.write_package(path, notetypes, decks, notes, cards, reviews, media_files,
                       with_scheduling=with_scheduling, created=collection.get('created'))
    _report(progress, 1.0, _('Done'))
    result = {'notes': len(notes), 'cards': len(cards), 'media': len(media_files),
              'missing': missing}
    log.info('exported %s: %r', path, result)
    return result


def _cell_text(text, include_html):
    if include_html:
        return text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')
    return ' '.join(template.strip_html(text).split())


def export_text(collection, path, deck_id=None, include_tags=True, include_html=True,
                notetype_id=None):
    """Write the notes of the collection, or of the deck `deck_id` with its subdecks, to a
    tab-separated file at `path` (see the module docstring); the number of notes written."""
    deck_ids = _deck_ids(collection, deck_id)
    note_ids = sorted({row['note_id'] for row in _card_rows(collection, deck_ids)})
    note_rows = _note_rows(collection, note_ids)
    if notetype_id is not None:
        note_rows = [row for row in note_rows if row['notetype_id'] == notetype_id]
    widths = {}
    for notetype in collection.notetypes():
        widths[notetype.id] = len(notetype.fields)
    width = max((widths.get(row['notetype_id'], 0) for row in note_rows), default=0)
    if notetype_id is not None:
        width = widths.get(notetype_id, width)
    try:
        with open(path, 'w', encoding='utf-8', newline='') as file:
            file.write('#separator:tab\n')
            file.write(f'#html:{"true" if include_html else "false"}\n')
            if include_tags:
                file.write(f'#tags column:{width + 1}\n')
            writer = csv.writer(file, delimiter='\t', lineterminator='\n')
            for row in note_rows:
                fields = row['fields'].split(template.FIELD_SEPARATOR)[:width]
                fields += [''] * (width - len(fields))
                cells = [_cell_text(field, include_html) for field in fields]
                if include_tags:
                    cells.append(' '.join(row['tags'].split()))
                writer.writerow(cells)
    except OSError as error:
        raise apkg.PackageError(_('Could not write {}: {}').format(path, error)) from error
    log.info('exported %d notes to %s', len(note_rows), path)
    return len(note_rows)
