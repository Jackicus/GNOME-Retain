# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Study modes besides flipping: typing the answer, and picking it from a few.

    MODES = ('flip', 'type', 'choice')
    expected_answer(notetype, note, ord) -> (field name, html) or None
    grade_typed(expected_html, typed) -> Verdict(rating, kind, shown)
    distractors(collection, note, notetype, field, deck_ids, count=3, rng=None) -> [html]
    choice_rating(correct, card) -> AGAIN, HARD or GOOD

Which text answers a card: the first field the card's answer template shows that its
question does not (Back for Basic's card, Front for its reverse, Meaning for a vocabulary
card showing its Expression). A cloze or occlusion card, an empty answer and a long one
(more than MAX_ANSWER characters, a paragraph rather than a word) have none: they are
studied by flipping whatever the mode.

Typing is graded leniently, both sides normalized the same way: NFKC (full-width letters
and half-width kana fold), case, katakana as hiragana, no parenthetical notes, no
punctuation, no leading "to", "a", "an" or "the", spaces collapsed. Any one alternative of
"tall; high; expensive" (split on ; , / and the Japanese 、；) counts, and a field with
furigana accepts both its kanji and its kana. An exact match suggests Good. A near miss
suggests Hard: a typo in a Latin-script answer (Damerau-Levenshtein, 0 edits up to 4
letters, 1 up to 8, 2 beyond) or only the accents wrong. No tolerance applies to Japanese,
where one kana is another word. Anything else suggests Again. The suggestion is the
page's preselected grade; the user can still pick any other (an alternative the field does
not list, a typo that mattered).

Picking from options: three wrong ones (DISTRACTORS) come from other notes of the same type
in the deck, scored by how alike they are (the same part of speech, from a field naming it
or an English "to …" verb; shared tags; a similar length) so they are not given away by
form, and never one that shares an alternative with the right answer (it would be right
too). Recognizing is easier than recalling, so a right pick suggests Good only while the
card is new or in learning, where a grade moves it along short steps, and Hard once it is a
review card, so the easier test does not stretch its interval as a recall would.
"""

import collections
import random
import re
import unicodedata

from . import template
from .fsrs import AGAIN, GOOD, HARD

MODES = ('flip', 'type', 'choice')
MAX_ANSWER = 80
DISTRACTORS = 3
CANDIDATES = 400  # notes looked at for distractors
POS_FIELDS = ('part of speech', 'pos', 'word type', 'type')

Verdict = collections.namedtuple('Verdict', 'rating kind shown')  # kind: exact, close, wrong

_ALTERNATIVE = re.compile(r'[;,/、；，]')
_PARENTHESES = re.compile(r'\([^)]*\)|（[^）]*）|\[[^\]]*\]')
_PUNCTUATION = re.compile(r'[^\w\s]|_')
_ARTICLE = re.compile(r'^(?:to|a|an|the)\s+')


def expected_answer(notetype, note, ord):
    """(field name, its HTML) answering a standard card, or None (flip it instead)."""
    if notetype.kind != 'standard' or not notetype.templates:
        return None
    card_template = notetype.templates[min(ord, len(notetype.templates) - 1)]
    question = template.field_names_in(card_template['qfmt'])
    answer_fields = _fields_in_order(card_template['afmt'])
    fields = dict(zip(notetype.field_names(), note.fields, strict=False))
    for name in answer_fields:
        if name in question or name not in fields:
            continue
        text = template.strip_html(fields[name])
        if not text or len(text) > MAX_ANSWER:
            return None
        return name, fields[name]
    return None


def _fields_in_order(card_template):
    """The fields a template shows, in the order they appear."""
    names = template.field_names_in(card_template)
    found = []
    for match in re.finditer(r'\{\{([^}]*)\}\}', card_template):
        name = match[1].split(':')[-1].strip().lstrip('#^/')
        if name in names and name not in found:
            found.append(name)
    return found


def normalize(text):
    text = unicodedata.normalize('NFKC', template.strip_html(text)).casefold()
    text = _PARENTHESES.sub(' ', text)
    text = ''.join(chr(ord(char) - 0x60) if 'ァ' <= char <= 'ヶ' else char for char in text)
    text = _PUNCTUATION.sub(' ', text.replace("'", '').replace('’', ''))
    text = ' '.join(text.split())
    return _ARTICLE.sub('', text)


def alternatives(expected_html):
    """[(as shown, normalized)] for each acceptable answer, in order, without repeats."""
    found, seen = [], set()
    for form in template.reading_forms(expected_html):
        plain = template.strip_html(form)
        for shown in [plain] + _ALTERNATIVE.split(plain):
            shown = shown.strip()
            key = normalize(shown)
            if key and key not in seen:
                seen.add(key)
                found.append((shown, key))
    return found


def _strip_accents(text):
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(char for char in decomposed if not unicodedata.combining(char))


def _is_latin(text):
    return all(char.isascii() or unicodedata.category(char).startswith('M') or
               'LATIN' in unicodedata.name(char, '') for char in text)


def _tolerance(text):
    letters = len(text.replace(' ', ''))
    return 0 if letters <= 4 else 1 if letters <= 8 else 2


def damerau_levenshtein(a, b):
    """Edits (insert, delete, substitute, swap two neighbours) turning a into b."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def grade_typed(expected_html, typed):
    """The suggested grade for a typed answer, and what to show it against: the
    alternative matched, or the whole answer when it is wrong."""
    options = alternatives(expected_html)
    if not options:
        return Verdict(GOOD, 'exact', '')
    answer = normalize(typed)
    if not answer:
        return Verdict(AGAIN, 'wrong', options[0][0])
    for shown, key in options:
        if answer == key:
            return Verdict(GOOD, 'exact', shown)
    for shown, key in options:
        if not _is_latin(key):
            continue  # Japanese: a dakuten or one kana more is another word
        if _strip_accents(answer) == _strip_accents(key):
            return Verdict(HARD, 'close', shown)
        if damerau_levenshtein(answer, key) <= _tolerance(key):
            return Verdict(HARD, 'close', shown)
    return Verdict(AGAIN, 'wrong', options[0][0])


def choice_rating(correct, card):
    if not correct:
        return AGAIN
    return GOOD if card.state in ('new', 'learning', 'relearning') else HARD


# Distractors


def _pos(fields, names):
    for name, value in zip(names, fields, strict=False):
        if name.lower() in POS_FIELDS:
            return normalize(value)
    return None


def _english_pos(text):
    plain = template.strip_html(text).lower()
    return 'verb' if plain.startswith('to ') else None


def distractors(collection, note, notetype, field, deck_ids, count=DISTRACTORS, rng=None):
    """Up to `count` wrong answers (HTML of `field` in other notes of the type, in the
    decks), the most alike first among a few, shuffled. Fewer when the deck has fewer."""
    rng = rng or random.Random()
    names = notetype.field_names()
    index = names.index(field)
    correct = note.fields[index]
    taken = {key for _shown, key in alternatives(correct)}
    pos = _pos(note.fields, names) or _english_pos(correct)
    tags = {tag.lower() for tag in note.tags}
    length = len(template.strip_html(correct))
    marks = ','.join('?' * len(deck_ids))
    rows = collection.db.execute(
        f'SELECT DISTINCT notes.id, notes.fields, notes.tags FROM notes '
        f'JOIN cards ON cards.note_id = notes.id '
        f'WHERE notes.notetype_id = ? AND notes.id != ? AND cards.deck_id IN ({marks}) '
        f'ORDER BY RANDOM() LIMIT {CANDIDATES}',
        [notetype.id, note.id, *deck_ids]).fetchall()
    scored, seen = [], set()
    for row in rows:
        fields = row['fields'].split(template.FIELD_SEPARATOR)
        if index >= len(fields):
            continue
        value = fields[index]
        keys = {key for _shown, key in alternatives(value)}
        plain = template.strip_html(value)
        if not keys or keys & taken or len(plain) > MAX_ANSWER or normalize(plain) in seen:
            continue
        seen.add(normalize(plain))
        other_tags = {tag.lower() for tag in row['tags'].split()}
        score = 0.0
        other_pos = _pos(fields, names) or _english_pos(value)
        if pos is not None and other_pos == pos:
            score += 3
        if tags | other_tags:
            score += 2 * len(tags & other_tags) / len(tags | other_tags)
        score += 1 - abs(len(plain) - length) / max(len(plain), length, 1)
        scored.append((score + rng.random() * 0.5, value))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = [value for _score, value in scored[:count]]
    rng.shuffle(chosen)
    return chosen
