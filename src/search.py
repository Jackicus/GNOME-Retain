# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Anki's search syntax, compiled to an SQL WHERE clause over the collection.

    compile(query, today, now=None, day_start_hour=4) -> (where_sql, params)
    register_functions(connection)     the SQLite functions the clause needs
    normalize_tag(tag) -> str          a tag as stored: no surrounding or internal whitespace
    tokenize(query) -> list[str]       the query's raw tokens (quotes kept)
    SQL_FROM                           the join the clause is written against
    SearchError                        a query that cannot be compiled; the message is for the user

The caller (collection.py) runs `SELECT cards.id FROM <SQL_FROM> WHERE <where_sql>` with the
params, on a connection that `register_functions()` was called on: the clause uses
`fold(text)` (a Unicode-aware lower()), `regexp(pattern, text)` (re.search, case-insensitive)
and `fieldn(fields, n)` (the n-th \\x1f-joined field). An empty query compiles to '1'.

The syntax is Anki's (https://docs.ankiweb.net/searching.html): bare terms match anywhere in
a note's fields, case-insensitively, with `*` for any characters and `_` for one (a literal
`*`, `_`, `\\`, `"`, `:`, `(`, `)` or `-` is escaped with `\\`); "quoted phrases"; `-term`
negates; terms are ANDed, `or` ORs, parentheses group. The keys: deck:, tag:, is:, flag:,
prop:, rated:, resched:, introduced:, added:, edited:, nid:, cid:, did:, mid:, note:, card:,
re:, nc:, w:; any other key names a field (`front:dog` is a Front field of exactly "dog",
`front:*dog*` one containing it, `front:*` a non-empty one, `front:` an empty one,
`front:re:…` a regular expression). In names (decks, tags, note types, cards, fields) `*` is
the only wildcard. `deck:current`, `deck:filtered`, `prop:ease` and `dupe:` are not supported.
"""

import functools
import re
import time

from . import days

SQL_FROM = ('cards JOIN notes ON notes.id = cards.note_id '
            'JOIN decks ON decks.id = cards.deck_id '
            'JOIN notetypes ON notetypes.id = notes.notetype_id')

_ESCAPE = " ESCAPE '\\'"
_PROP = re.compile(r'^([a-z]+)(<=|>=|!=|=|<|>)(.*)$')
_STATES = {
    'new': "cards.state = 'new'",
    'learn': "cards.state IN ('learning', 'relearning')",
    'review': "cards.state IN ('review', 'relearning')",
    'suspended': 'cards.suspended = 1',
    'marked': 'notes.marked = 1',
}
_PROP_COLUMNS = {'ivl': 'cards.interval', 'reps': 'cards.reps', 'lapses': 'cards.lapses',
                 's': 'cards.stability', 'd': 'cards.difficulty'}
_ID_COLUMNS = {'nid': 'notes.id', 'cid': 'cards.id', 'did': 'cards.deck_id',
               'mid': 'notes.notetype_id'}
_REVLOG = ('(EXISTS (SELECT 1 FROM revlog WHERE revlog.card_id = cards.id '
           'AND revlog.id >= ? AND {condition}))')
_FIELD = ('(EXISTS (SELECT 1 FROM json_each(notetypes.fields) '
          "WHERE fold(json_extract(json_each.value, '$.name')) LIKE ?" + _ESCAPE +
          ' AND {condition}))')


class SearchError(Exception):
    """A query that cannot be compiled; the message is a sentence for the user."""


def normalize_tag(tag):
    """The tag with surrounding whitespace dropped and internal whitespace as `_`."""
    return '_'.join(tag.split())


def register_functions(connection):
    """Register fold(), regexp() and fieldn() on an sqlite3 connection."""
    connection.create_function('fold', 1, _fold, deterministic=True)
    connection.create_function('regexp', 2, _regexp, deterministic=True)
    connection.create_function('fieldn', 2, _fieldn, deterministic=True)


def _fold(text):
    return '' if text is None else text.lower()


@functools.lru_cache(maxsize=128)
def _regex(pattern):
    return re.compile(pattern, re.IGNORECASE)


def _regexp(pattern, text):
    if pattern is None or text is None:
        return 0
    return 1 if _regex(pattern).search(text) else 0


def _fieldn(fields, n):
    if fields is None or n is None:
        return ''
    parts = fields.split('\x1f')
    n = int(n)
    return parts[n] if 0 <= n < len(parts) else ''


def compile(query, today, now=None, day_start_hour=days.DEFAULT_DAY_START_HOUR):
    """The WHERE clause and its parameters for `query`, or a SearchError.

    `today` is the current day number, `now` Unix seconds (the clock's by default).
    """
    tokens = tokenize(query)
    if not tokens:
        return '1', []
    compiler = _Compiler(today, time.time() if now is None else now, day_start_hour)
    return _Parser(tokens, compiler).parse()


def tokenize(query):
    """The query's tokens: '(', ')', '-' and terms, which keep their quotes and escapes."""
    tokens = []
    i, n = 0, len(query)
    while i < n:
        char = query[i]
        if char.isspace():
            i += 1
        elif char in '()-':
            tokens.append(char)
            i += 1
        else:
            start = i
            while i < n and not query[i].isspace() and query[i] not in '()':
                if query[i] == '\\':
                    i += 2
                elif query[i] == '"':
                    i = _closing_quote(query, i + 1) + 1
                else:
                    i += 1
            tokens.append(query[start:min(i, n)])
    return tokens


def _closing_quote(query, i):
    while i < len(query):
        if query[i] == '\\':
            i += 2
        elif query[i] == '"':
            return i
        else:
            i += 1
    raise SearchError('Unbalanced quotes')


def _is_word(token, word):
    return token is not None and token.lower() == word


def _join(operator, parts):
    if len(parts) == 1:
        return parts[0]
    params = []
    for _, part_params in parts:
        params.extend(part_params)
    return '(' + operator.join(sql for sql, _ in parts) + ')', params


class _Parser:
    """Recursive descent over the tokens: `or` binds loosest, then (implicit) `and`, then
    `-` and parentheses. Every clause returned is a single parenthesised expression."""

    def __init__(self, tokens, compiler):
        self.tokens = tokens
        self.pos = 0
        self.compiler = compiler

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse(self):
        clause = self.disjunction()
        if self.peek() is not None:
            raise SearchError('Unbalanced parentheses')
        return clause

    def disjunction(self):
        parts = [self.conjunction()]
        while _is_word(self.peek(), 'or'):
            self.pos += 1
            parts.append(self.conjunction())
        return _join(' OR ', parts)

    def conjunction(self):
        parts = [self.unary()]
        while True:
            token = self.peek()
            if token is None or token == ')' or _is_word(token, 'or'):
                break
            if _is_word(token, 'and'):
                self.pos += 1
            parts.append(self.unary())
        return _join(' AND ', parts)

    def unary(self):
        token = self.peek()
        if token is None:
            raise SearchError('Expected a search term at the end of the query')
        self.pos += 1
        if token == '-':
            sql, params = self.unary()
            return 'NOT ' + sql, params
        if token == '(':
            if self.peek() == ')':
                raise SearchError('Empty parentheses')
            clause = self.disjunction()
            if self.peek() != ')':
                raise SearchError('Unbalanced parentheses')
            self.pos += 1
            return clause
        if token == ')':
            raise SearchError('Unbalanced parentheses')
        if _is_word(token, 'or') or _is_word(token, 'and'):
            raise SearchError(f'Expected a search term, not "{token}"')
        return self.compiler.term(token)


def _split(raw):
    """A raw token as (key, value): the key before the first unescaped colon, lowercased,
    or None; the value with its quotes removed (the whole token's, or the value's own)."""
    quoted = len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"'
    text = raw[1:-1] if quoted else raw
    i = 0
    while i < len(text):
        if text[i] == '\\':
            i += 2
        elif text[i] == ':':
            return text[:i].lower(), _unquote(text[i + 1:])
        else:
            i += 1
    return None, text


def _unquote(value):
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _like(text, single_char):
    """`text` as a LIKE pattern: `*` is `%`, `_` one character when `single_char` else a
    literal, backslash escapes resolved and `%`, `_` and `\\` themselves escaped."""
    out = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == '\\':
            following = text[i + 1] if i + 1 < len(text) else '\\'
            out.append('\\' + following if following in '*_%\\' else following)
            i += 2
        else:
            if char == '*':
                out.append('%')
            elif char == '_':
                out.append('_' if single_char else '\\_')
            elif char in '%\\':
                out.append('\\' + char)
            else:
                out.append(char)
            i += 1
    return ''.join(out)


def _regex_of(text):
    """`text` as a regular expression: `*` any characters, `_` one, the rest literal."""
    out = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == '\\' and i + 1 < len(text):
            out.append(re.escape(text[i + 1]))
            i += 2
        else:
            out.append('.*' if char == '*' else '.' if char == '_' else re.escape(char))
            i += 1
    return ''.join(out)


def _validated_regex(pattern, raw):
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as error:
        raise SearchError(f'Invalid regular expression: {raw} ({error})') from None
    return pattern.replace('\\"', '"')


def _number(text, raw):
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    raise SearchError(f'Expected a number: {raw}')


def _integer(text, raw):
    try:
        return int(text)
    except ValueError:
        raise SearchError(f'Expected a whole number: {raw}') from None


def _day_count(text, raw):
    count = _integer(text, raw)
    if count < 1:
        raise SearchError(f'Expected a number of days, from 1: {raw}')
    return count


class _Compiler:
    """One term to its clause, given the moment the search is made."""

    def __init__(self, today, now, day_start_hour):
        self.today = today
        self.now = now
        self.day_start_hour = day_start_hour

    def term(self, raw):
        key, value = _split(raw)
        if key is None:
            return self.text(value)
        if key in _ID_COLUMNS:
            return self.ids(_ID_COLUMNS[key], value, raw)
        handler = self.KEYS.get(key)
        if handler is not None:
            return handler(self, value, raw)
        return self.field(key, value, raw)

    def cutoff(self, count):
        """The Unix time `count` days ago began (1 is today)."""
        return days.day_start(self.today - count + 1, self.day_start_hour)

    def text(self, value, raw=None):
        pattern = '%' + _like(value.lower(), True) + '%'
        return '(fold(notes.fields) LIKE ?' + _ESCAPE + ')', [pattern]

    def deck(self, value, raw):
        if value.lower() in ('current', 'filtered'):
            raise SearchError(f'Not supported: {raw}')
        pattern = _like(value.lower(), False)
        sql = '((fold(decks.name) LIKE ?' + _ESCAPE + ' OR fold(decks.name) LIKE ?' + _ESCAPE + '))'
        return sql, [pattern, pattern + '::%']

    def tag(self, value, raw):
        name = normalize_tag(value).lower()
        if name == 'none':
            return "(trim(notes.tags) = '')", []
        if name == 'marked':
            return '(notes.marked = 1)', []
        pattern = _like(name, False)
        sql = '((fold(notes.tags) LIKE ?' + _ESCAPE + ' OR fold(notes.tags) LIKE ?' + _ESCAPE + '))'
        return sql, ['% ' + pattern + ' %', '% ' + pattern + '::%']

    def is_(self, value, raw):
        state = value.lower()
        if state == 'due':
            sql = ("((cards.state = 'review' AND cards.due <= ?) OR "
                   "(cards.state IN ('learning', 'relearning') AND cards.due <= ?))")
            return sql, [self.today, self.now]
        if state == 'buried':
            return '(cards.buried >= ?)', [self.today]
        if state in _STATES:
            return '(' + _STATES[state] + ')', []
        raise SearchError(f'Unknown search: is:{value}')

    def flag(self, value, raw):
        flag = _integer(value, raw)
        if not 0 <= flag <= 7:
            raise SearchError(f'Expected a flag from 0 to 7: {raw}')
        return '(cards.flag = ?)', [flag]

    def prop(self, value, raw):
        match = _PROP.match(value.lower())
        if match is None:
            raise SearchError(f'Expected a property, a comparison and a number: {raw}')
        name, operator, text = match.groups()
        if name == 'ease':
            raise SearchError(f'Not supported: {raw} (search prop:d for the difficulty)')
        if name == 'due':
            sql = f"(cards.state = 'review' AND cards.due {operator} ?)"
            return sql, [self.today + _integer(text, raw)]
        if name == 'pos':
            return f"(cards.state = 'new' AND cards.due {operator} ?)", [_integer(text, raw)]
        if name in _PROP_COLUMNS:
            return f'({_PROP_COLUMNS[name]} {operator} ?)', [_number(text, raw)]
        raise SearchError(f'Unknown search: prop:{name}')

    def rated(self, value, raw):
        count, _, rating = value.partition(':')
        params = [int(self.cutoff(_day_count(count, raw)) * 1000)]
        condition = 'revlog.rating > 0'
        if rating:
            rating = _integer(rating, raw)
            if not 1 <= rating <= 4:
                raise SearchError(f'Expected a rating from 1 to 4: {raw}')
            condition = 'revlog.rating = ?'
            params.append(rating)
        return _REVLOG.format(condition=condition), params

    def resched(self, value, raw):
        params = [int(self.cutoff(_day_count(value, raw)) * 1000)]
        return _REVLOG.format(condition="revlog.kind = 'manual'"), params

    def introduced(self, value, raw):
        sql = ('((SELECT min(revlog.id) FROM revlog WHERE revlog.card_id = cards.id '
               'AND revlog.rating > 0) >= ?)')
        return sql, [int(self.cutoff(_day_count(value, raw)) * 1000)]

    def added(self, value, raw):
        return '(cards.created >= ?)', [self.cutoff(_day_count(value, raw))]

    def edited(self, value, raw):
        return '(notes.modified >= ?)', [self.cutoff(_day_count(value, raw))]

    def ids(self, column, value, raw):
        ids = [_integer(part.strip(), raw) for part in value.split(',') if part.strip()]
        if not ids:
            raise SearchError(f'Expected numbers: {raw}')
        return f'({column} IN ({", ".join("?" * len(ids))}))', ids

    def note(self, value, raw):
        return '(fold(notetypes.name) LIKE ?' + _ESCAPE + ')', [_like(value.lower(), False)]

    def card(self, value, raw):
        if value.isdigit():
            return '(cards.ord = ?)', [int(value) - 1]
        sql = ('(EXISTS (SELECT 1 FROM json_each(notetypes.templates) '
               'WHERE json_each.key = cards.ord '
               "AND fold(json_extract(json_each.value, '$.name')) LIKE ?" + _ESCAPE + '))')
        return sql, [_like(value.lower(), False)]

    def field(self, name, value, raw):
        params = [_like(name, False)]
        content = 'fieldn(notes.fields, json_each.key)'
        if value == '*':
            condition = content + " != ''"
        elif value == '':
            condition = content + " = ''"
        elif value.lower().startswith('re:'):
            condition = 'regexp(?, ' + content + ')'
            params.append(_validated_regex(value[3:], raw))
        else:
            condition = 'fold(' + content + ') LIKE ?' + _ESCAPE
            params.append(_like(value.lower(), True))
        return _FIELD.format(condition=condition), params

    def regex(self, value, raw):
        return '(regexp(?, notes.fields))', [_validated_regex(value, raw)]

    def word(self, value, raw):
        return '(regexp(?, notes.fields))', [r'\b' + _regex_of(value) + r'\b']

    def dupe(self, value, raw):
        raise SearchError('Not supported: dupe:')

    KEYS = {
        'deck': deck, 'tag': tag, 'is': is_, 'flag': flag, 'prop': prop, 'rated': rated,
        'resched': resched, 'introduced': introduced, 'added': added, 'edited': edited,
        'note': note, 'card': card, 're': regex, 'nc': text, 'w': word, 'dupe': dupe,
    }
