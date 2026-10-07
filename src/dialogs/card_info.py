# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Card Information: what the collection knows about one card, and its review history.

    card_info.present(app, parent, card_id)   # an Adw.Dialog; None when the card is gone
    card_info.rows(app, card)                 # [(label, value)] of the first group (tests)

An Adw.PreferencesPage of property rows (Added, First Review, Latest Review, Due, Interval,
Stability, Difficulty, Retrievability, Reviews, Lapses, Average Time, Total Time, Card Type,
Note Type, Deck, Note ID, Card ID) and a Review History group: every revlog row, newest
first, with its date, rating, the interval given and the time taken.
"""

import datetime
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gtk

from ..scheduler import describe_interval

RATING_WORDS = {1: _('Again'), 2: _('Hard'), 3: _('Good'), 4: _('Easy')}
KIND_WORDS = {'manual': _('Manual'), 'cram': _('Cram')}


def _date(timestamp):
    return datetime.datetime.fromtimestamp(timestamp).strftime('%x')


def _datetime(timestamp):
    return datetime.datetime.fromtimestamp(timestamp).strftime('%x %X')


def _seconds(ms):
    """A duration in words: 4.2s, 1m 10s."""
    seconds = ms / 1000
    if seconds < 60:
        return _('{n}s').format(n=f'{seconds:.1f}')
    minutes, rest = divmod(int(round(seconds)), 60)
    return _('{m}m {s}s').format(m=minutes, s=rest)


def _due(collection, card):
    if card.suspended:
        return _('Suspended')
    if card.state == 'new':
        return _('New card #{n}').format(n=card.due)
    if card.state == 'review':
        return _date(collection.day_start(card.due))
    return _datetime(card.due)


def rows(app, card):
    """The property rows: [(label, value)]."""
    collection = app.collection
    reviews = [row for row in collection.reviews_of(card.id) if row['kind'] != 'manual']
    note = collection.note(card.note_id)
    notetype = collection.notetype(note.notetype_id) if note else None
    deck = collection.deck(card.deck_id)
    if notetype is not None and notetype.templates:
        index = card.ord if notetype.kind == 'standard' else 0
        index = min(index, len(notetype.templates) - 1)
        card_type = notetype.templates[index]['name']
        if notetype.kind != 'standard':
            card_type = _('{name} {n}').format(name=card_type, n=card.ord + 1)
    else:
        card_type = _('Unknown')
    total_ms = sum(row['duration_ms'] for row in reviews)
    retrievability = app.scheduler.retrievability(card)
    result = [(_('Added'), _date(card.created))]
    if reviews:
        result.append((_('First Review'), _date(reviews[0]['id'] / 1000)))
        result.append((_('Latest Review'), _date(reviews[-1]['id'] / 1000)))
    result.append((_('Due'), _due(collection, card)))
    if card.state != 'new':
        result.append((_('Interval'), describe_interval(card.interval or 0)))
    if card.stability:
        result.append((_('Stability'), ngettext('{n} day', '{n} days', round(card.stability))
                       .format(n=f'{card.stability:.1f}')))
        result.append((_('Difficulty'), f'{(card.difficulty - 1) / 9 * 100:.0f}%'))
    if retrievability is not None:
        result.append((_('Retrievability'), f'{retrievability * 100:.0f}%'))
    result.append((_('Reviews'), str(card.reps)))
    result.append((_('Lapses'), str(card.lapses)))
    if reviews:
        result.append((_('Average Time'), _seconds(total_ms / len(reviews))))
        result.append((_('Total Time'), _seconds(total_ms)))
    result.append((_('Card Type'), card_type))
    result.append((_('Note Type'), notetype.name if notetype else _('Unknown')))
    result.append((_('Deck'), deck.name if deck else _('Unknown')))
    result.append((_('Note ID'), str(card.note_id)))
    result.append((_('Card ID'), str(card.id)))
    return result


def history(app, card):
    """The review history, newest first: [(date, rating word, interval, time)]."""
    entries = []
    for row in reversed(app.collection.reviews_of(card.id)):
        rating = RATING_WORDS.get(row['rating'], KIND_WORDS.get(row['kind'], _('Manual')))
        if row['kind'] == 'cram':
            rating = _('{rating} (cram)').format(rating=rating)
        interval = describe_interval(row['scheduled_days']) if row['scheduled_days'] else ''
        entries.append((_datetime(row['id'] / 1000), rating, interval,
                        _seconds(row['duration_ms'])))
    return entries


def build(app, card):
    dialog = Adw.Dialog(title=_('Card Information'), content_width=520, content_height=640)
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    page = Adw.PreferencesPage()
    group = Adw.PreferencesGroup()
    for label, value in rows(app, card):
        row = Adw.ActionRow(title=label, subtitle=value, subtitle_selectable=True)
        row.add_css_class('property')
        group.add(row)
    page.add(group)
    entries = history(app, card)
    reviews = Adw.PreferencesGroup(title=_('Review History'))
    if entries:
        for date, rating, interval, taken in entries:
            subtitle = ' · '.join(part for part in (rating, interval) if part)
            row = Adw.ActionRow(title=date, subtitle=subtitle)
            time_label = Gtk.Label(label=taken, valign=Gtk.Align.CENTER)
            time_label.add_css_class('numeric')
            time_label.add_css_class('dimmed')
            row.add_suffix(time_label)
            reviews.add(row)
    else:
        row = Adw.ActionRow(title=_('No reviews yet'))
        row.add_css_class('dimmed')
        reviews.add(row)
    page.add(reviews)
    view.set_content(page)
    dialog.set_child(view)
    return dialog


def present(app, parent, card_id):
    card = app.collection.card(card_id)
    if card is None:
        return None
    dialog = build(app, card)
    dialog.present(parent)
    return dialog
