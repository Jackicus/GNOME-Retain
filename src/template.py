# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Anki's card templates, rendered the way Anki renders them, so imported decks look right.

A template is HTML with `{{Field}}` replacements, `{{#Field}}…{{/Field}}` (shown when the
field is non-empty; whitespace, `<br>` and `<div>` only counts as empty) and
`{{^Field}}…{{/Field}}` (shown when it is empty) conditionals, which nest, and filters in front
of a field name, applied right to left: `{{cloze:Text}}`, `{{cloze-only:Text}}`,
`{{hint:Field}}`, `{{type:Field}}`, `{{type:cloze:Text}}`, `{{text:Field}}`,
`{{furigana:Field}}`, `{{kana:Field}}`, `{{kanji:Field}}`, and
`{{tts LANG [voices=…] [speed=…]:Field}}`, which renders Anki's
`[anki:tts lang=LANG …]text[/anki:tts]` tag (spoken, not shown); `{{tts-voices:}}` and an
unknown filter give the plain field. `{{FrontSide}}` on the answer is the rendered question
without its `[sound:…]` and `[anki:tts]` tags; `{{Tags}}`, `{{Type}}`, `{{Deck}}`,
`{{Subdeck}}`, `{{Card}}` and `{{CardFlag}}` come from the `special` dict. An unknown field
renders as `{unknown field Name}`. The legacy `<% %>` delimiters are accepted.

Clozes in a field (`{{c1::text::hint}}`, `{{c1,2::text}}` for two cards, nested clozes) render
for the card's cloze number `ord + 1`: on the question the active one is
`<span class="cloze" data-cloze="text" data-ordinal="1">[hint]</span>` (`[...]` without a
hint), on the answer `<span class="cloze" data-ordinal="1">text</span>`; the others are
`<span class="cloze-inactive" data-ordinal="2">text</span>` on both sides. A `{{type:…}}`
renders as `<span class="retain-type-answer" data-field="Field"></span>` on the question and
`<span class="retain-type-answer-result" …>` on the answer, with `data-cloze="N"` (the cloze
number) for `type:cloze:`; the review page draws the entry and the comparison there.

    render(qfmt, afmt, fields, ord=0, kind='standard', special=None) -> (question, answer)
    render_side(template, fields, ord=0, kind='standard', special=None, question=None,
                side='question') -> str
    cloze_numbers(text) -> set[int]
    cards_for_note(notetype_kind, templates, fields) -> list[int]
    field_names_in(template) -> set[str]
    strip_html(text) -> str            strip_html_media is the same function
    media_references(text) -> list[str]
    sound_tags(text) -> list[str]
    strip_sound_tags(text) -> str
    tts_tags(text) -> [(lang, voices, speed, text)]   the [anki:tts] tags, to speak
    av_tags(text) -> [('sound', name) | ('tts', (lang, voices, speed, text))]   in order
    strip_tts_tags(text) -> str
    speech_text(html, lang='') -> str   what a voice reads (furigana give the reading in ja)
    typed_answer_diff(expected, typed) -> str

`fields` maps field names to their HTML; `ord` is the template index (standard note types) or
the cloze number minus one (`kind` 'cloze' or 'occlusion'). A malformed template (an unclosed
or unopened conditional) raises TemplateError.
"""

import difflib
import html
import re
import unicodedata
import urllib.parse

FIELD_SEPARATOR = '\x1f'
SPECIAL_FIELDS = ('Tags', 'Type', 'Deck', 'Subdeck', 'Card', 'CardFlag')
CLOZE_KINDS = ('cloze', 'occlusion')
CLOZE_FILTERS = ('cloze', 'cloze-only')

_HANDLE = re.compile(r'\{\{(.*?)\}\}', re.DOTALL)
_CLOZE_ORDINAL = re.compile(r'c\d+')
_EMPTY_FIELD = re.compile(r'(?:\s|</?(?:br|div)\s*/?>)*', re.IGNORECASE)
_CLOZE_TOKEN = re.compile(r'\{\{c(\d+(?:,\d+)*)::|\}\}')
_FURIGANA = re.compile(r' ?([^ >]+?)\[(.+?)\]')
_SOUND = re.compile(r'\[sound:([^\]]+)\]')
_TTS = re.compile(r'\[anki:tts\b[^\]]*\].*?\[/anki:tts\]', re.DOTALL)
_TTS_TAG = re.compile(r'\[anki:tts\b([^\]]*)\](.*?)\[/anki:tts\]', re.DOTALL)
_AV_TAG = re.compile(r'\[sound:([^\]]+)\]|\[anki:tts\b([^\]]*)\](.*?)\[/anki:tts\]', re.DOTALL)
_RUBY = re.compile(r'<ruby\b[^>]*>(.*?)</ruby>', re.DOTALL | re.IGNORECASE)
_RT = re.compile(r'<rt\b[^>]*>(.*?)</rt>', re.DOTALL | re.IGNORECASE)
_BREAK = re.compile(
    r'<(?:br\b[^>]*|/(?:div|p|li|tr|td|th|h[1-6]|blockquote|pre|ul|ol|dl|dd|dt|table)\s*)>',
    re.IGNORECASE)
_TAG = re.compile(r'<!--.*?-->|<[^>]*>', re.DOTALL)
_MEDIA = re.compile(
    r'<(?:img|audio|video|source|object)\b[^>]*?(?<![\w-])(?:src|data)\s*=\s*'
    r'(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))'
    r'|\[sound:([^\]]+)\]', re.IGNORECASE | re.DOTALL)
_URL_SCHEME = re.compile(r'[a-z][a-z0-9+.-]*:', re.IGNORECASE)


class TemplateError(Exception):
    """A template that cannot be parsed: a conditional not closed, or closed but not open."""


# Templates


def _parse(template):
    """The template as nodes: ('text', s), ('field', key, filters), ('if' | 'unless', key,
    children). Filters are in the order written (`{{a:b:Field}}` gives ['a', 'b'])."""
    template = template.replace('<%', '{{').replace('%>', '}}')
    root = []
    stack = []
    position = 0
    for match in _HANDLE.finditer(template):
        current = stack[-1][2] if stack else root
        if match.start() > position:
            current.append(('text', template[position:match.start()]))
        position = match.end()
        handle = match.group(1).strip()
        if handle[:1] in ('#', '^'):
            node = ('if' if handle[0] == '#' else 'unless', handle[1:].strip(), [])
            current.append(node)
            stack.append(node)
        elif handle[:1] == '/':
            key = handle[1:].strip()
            if not stack or stack[-1][1] != key:
                raise TemplateError(f'{{{{/{key}}}}} closes a conditional that is not open')
            stack.pop()
        else:
            parts = [part.strip() for part in handle.split(':')]
            current.append(('field', parts[-1], parts[:-1]))
    if stack:
        raise TemplateError(f'{{{{#{stack[-1][1]}}}}} is not closed')
    if position < len(template):
        root.append(('text', template[position:]))
    return root


def _field_is_empty(text):
    """Anki's rule: only whitespace, `<br>` and `<div>` tags count as empty."""
    return _EMPTY_FIELD.fullmatch(text) is not None


def _strip_av_tags(text):
    """Removes `[sound:…]` and `[anki:tts]…[/anki:tts]`, as Anki does for FrontSide."""
    return _SOUND.sub('', _TTS.sub('', text))


class _Context:
    """Everything a render needs: the fields, the card, the side and a counter for hint ids."""

    def __init__(self, fields, ord, kind, special, side, question, unknown_as_empty=False):
        self.fields = fields
        self.cloze_number = ord + 1
        self.cloze_kind = kind in CLOZE_KINDS
        self.special = special or {}
        self.side = side
        self.question = side == 'question'
        self.frontside = _strip_av_tags(question) if question else ''
        self.unknown_as_empty = unknown_as_empty
        # The answer's hint ids continue after the question's, which FrontSide may embed.
        self.hint_count = self.frontside.count('<div id="hint')

    def value(self, key):
        """The text behind a name, or None when nothing is called that."""
        if key in SPECIAL_FIELDS:
            return str(self.special.get(key, ''))
        if key == 'FrontSide':
            return self.frontside
        return self.fields.get(key)

    def is_nonempty(self, key):
        """Whether `{{#key}}` shows: a non-empty field, or `cN` for the active cloze."""
        if self.cloze_kind and _CLOZE_ORDINAL.fullmatch(key):
            return int(key[1:]) == self.cloze_number
        value = self.value(key)
        return value is not None and not _field_is_empty(value)

    def replacement(self, key, filters):
        if key == 'FrontSide':
            return self.frontside
        if key == '' and filters:
            text = ''
        else:
            text = self.value(key)
            if text is None:
                return '' if self.unknown_as_empty else f'{{unknown field {key}}}'
        return self.apply_filters(text, filters, key)

    def apply_filters(self, text, filters, key):
        """Applies a chain right to left; `type` ends it with the entry placeholder."""
        for index in range(len(filters) - 1, -1, -1):
            name = filters[index]
            if name == 'type':
                return self.type_placeholder(key, 'cloze' in filters[index + 1:])
            if name == 'cloze':
                text = _render_clozes(text, self.cloze_number, self.question)
            elif name == 'cloze-only':
                text = _cloze_only(text, self.cloze_number, self.question)
            elif name == 'hint':
                text = self.hint(text, key)
            elif name == 'text':
                text = strip_html(text)
            elif name == 'furigana':
                text = _furigana(text, lambda match: f'<ruby>{match[1]}<rt>{match[2]}</rt></ruby>')
            elif name == 'kana':
                text = _furigana(text, lambda match: match[2])
            elif name == 'kanji':
                text = _furigana(text, lambda match: match[1])
            elif name == 'tts' or name.startswith('tts '):
                text = _tts_tag(text, name)
            # `tts-voices` and unknown filters leave the field as it is.
        return text

    def type_placeholder(self, key, cloze):
        classname = 'retain-type-answer' if self.question else 'retain-type-answer-result'
        attributes = f'data-field="{html.escape(key, quote=True)}"'
        if cloze:
            attributes += f' data-cloze="{self.cloze_number}"'
        return f'<span class="{classname}" {attributes}></span>'

    def hint(self, text, key):
        if not text.strip():
            return ''
        self.hint_count += 1
        hint_id = f'hint{self.hint_count}'
        return (
            f'<a class=hint href="#" onclick="this.style.display=\'none\';'
            f'document.getElementById(\'{hint_id}\').style.display=\'block\';return false;">'
            f'{key}</a><div id="{hint_id}" class=hint style="display: none">{text}</div>')


def _render_nodes(nodes, context):
    output = []
    for node in nodes:
        kind = node[0]
        if kind == 'text':
            output.append(node[1])
        elif kind == 'field':
            output.append(context.replacement(node[1], node[2]))
        elif context.is_nonempty(node[1]) == (kind == 'if'):
            output.append(_render_nodes(node[2], context))
    return ''.join(output)


def render_side(template, fields, ord=0, kind='standard', special=None, question=None,
                side='question'):
    """One side of a card. On the answer side `question` is the rendered question, which
    `{{FrontSide}}` stands for (its sound tags stripped here)."""
    context = _Context(fields, ord, kind, special, side, question)
    return _render_nodes(_parse(template), context)


def render(qfmt, afmt, fields, ord=0, kind='standard', special=None):
    """The question and answer HTML of a card."""
    context = _Context(fields, ord, kind, special, 'question', None)
    question = _render_nodes(_parse(qfmt), context)
    context = _Context(fields, ord, kind, special, 'answer', question)
    return question, _render_nodes(_parse(afmt), context)


def _field_names(nodes, names):
    for node in nodes:
        if node[0] == 'text':
            continue
        key = node[1]
        if node[0] == 'field':
            if key and key != 'FrontSide' and key not in SPECIAL_FIELDS:
                names.add(key)
        else:
            if key not in SPECIAL_FIELDS and not _CLOZE_ORDINAL.fullmatch(key):
                names.add(key)
            _field_names(node[2], names)
    return names


def field_names_in(template):
    """The field names a template refers to, in replacements (whatever the filters) and in
    conditionals; not FrontSide, the specials, or a `{{#c1}}` cloze conditional."""
    return _field_names(_parse(template), set())


def _cloze_fields(nodes, names):
    """The fields the `cloze:` filters (and `cloze-only:`, `type:cloze:`) are applied to."""
    for node in nodes:
        if node[0] == 'field':
            if node[1] and any(name in CLOZE_FILTERS for name in node[2]):
                names.add(node[1])
        elif node[0] != 'text':
            _cloze_fields(node[2], names)
    return names


def _qfmt_of(template):
    if isinstance(template, str):
        return template
    if isinstance(template, dict):
        return template['qfmt']
    return template.qfmt


def cards_for_note(notetype_kind, templates, fields):
    """The ords of the cards a note generates. `templates` holds the note type's templates
    (each a qfmt string, or a dict or object with a `qfmt`).

    Standard: every template whose question renders to something once the HTML is stripped
    (a type-in entry or a hint of an empty field is nothing); a template that refers to no
    field at all always generates (a template that fails to parse does too: the error shows
    on the card). Cloze and occlusion: the cloze numbers in the fields the first template's
    cloze filters refer to, as ords, in order. A cloze note without any cloze gives [];
    the caller keeps card 1 for it, as Anki does.
    """
    if notetype_kind in CLOZE_KINDS:
        if not templates:
            return []
        numbers = set()
        for name in _cloze_fields(_parse(_qfmt_of(templates[0])), set()):
            numbers |= cloze_numbers(fields.get(name, ''))
        return sorted(number - 1 for number in numbers)
    ords = []
    for index, template in enumerate(templates):
        try:
            nodes = _parse(_qfmt_of(template))
        except TemplateError:
            ords.append(index)
            continue
        if not _field_names(nodes, set()):
            ords.append(index)
            continue
        context = _Context(fields, index, notetype_kind, None, 'question', None,
                           unknown_as_empty=True)
        rendered = _render_nodes(nodes, context)
        if strip_html(rendered) or media_references(rendered):
            ords.append(index)
    return ords


# Clozes


class _Cloze:
    """A `{{cN::…}}` in a field: its ordinals, its children (text and nested clozes) and
    the hint after the `::`, or None."""

    __slots__ = ('ordinals', 'children', 'hint')

    def __init__(self, ordinals):
        self.ordinals = ordinals
        self.children = []
        self.hint = None

    def plain_text(self):
        return ''.join(
            child if isinstance(child, str) else child.plain_text() for child in self.children)


def _parse_clozes(text):
    """The field as a list of strings and _Cloze nodes. The hint is what follows the first
    `::` after the text; an unclosed `{{cN::` is kept as text."""
    root = []
    stack = []

    def emit(piece):
        if not piece:
            return
        if stack and stack[-1].hint is None and '::' in piece:
            before, stack[-1].hint = piece.split('::', 1)
            piece = before
        (stack[-1].children if stack else root).append(piece)

    position = 0
    for match in _CLOZE_TOKEN.finditer(text):
        emit(text[position:match.start()])
        position = match.end()
        if match.group(0) == '}}':
            if stack:
                cloze = stack.pop()
                (stack[-1].children if stack else root).append(cloze)
            else:
                root.append('}}')
        else:
            stack.append(_Cloze([int(number) for number in match.group(1).split(',')]))
    emit(text[position:])
    while stack:
        cloze = stack.pop()
        parent = stack[-1].children if stack else root
        parent.append('{{c' + ','.join(str(n) for n in cloze.ordinals) + '::')
        parent.extend(cloze.children)
        if cloze.hint is not None:
            parent.append('::' + cloze.hint)
    return root


def _render_cloze_nodes(nodes, number, question):
    output = []
    for node in nodes:
        if isinstance(node, str):
            output.append(node)
            continue
        inner = _render_cloze_nodes(node.children, number, question)
        if number not in node.ordinals:
            output.append(
                f'<span class="cloze-inactive" data-ordinal="{node.ordinals[0]}">{inner}</span>')
        elif question:
            hint = '...' if node.hint is None else node.hint
            output.append(
                f'<span class="cloze" data-cloze="{html.escape(inner, quote=True)}" '
                f'data-ordinal="{number}">[{hint}]</span>')
        else:
            output.append(f'<span class="cloze" data-ordinal="{number}">{inner}</span>')
    return ''.join(output)


def _render_clozes(text, number, question):
    """The `cloze:` filter: innermost clozes first, so an outer active cloze hides the
    rendered inner ones with it."""
    return _render_cloze_nodes(_parse_clozes(text), number, question)


def _active_clozes(nodes, number, found):
    for node in nodes:
        if isinstance(node, _Cloze):
            if number in node.ordinals:
                found.append(node)
            _active_clozes(node.children, number, found)
    return found


def _cloze_only(text, number, question):
    """The `cloze-only:` filter: the active clozes' hints (question) or text (answer),
    joined by commas."""
    clozes = _active_clozes(_parse_clozes(text), number, [])
    if question:
        return ', '.join('...' if cloze.hint is None else cloze.hint for cloze in clozes)
    return ', '.join(cloze.plain_text() for cloze in clozes)


def _collect_ordinals(nodes, found):
    for node in nodes:
        if isinstance(node, _Cloze):
            found.update(node.ordinals)
            _collect_ordinals(node.children, found)
    return found


def cloze_numbers(text):
    """The cloze numbers in a field, nested and multi-ordinal ones included; never 0."""
    return _collect_ordinals(_parse_clozes(text), set()) - {0}


# Furigana


def _furigana(text, replacement):
    """`漢字[かんじ]` groups (a space before one is swallowed), leaving `[sound:…]` alone."""

    def substitute(match):
        if match[2].startswith('sound:'):
            return match[0]
        return replacement(match)

    return _FURIGANA.sub(substitute, text.replace('&nbsp;', ' '))


# Text and media


def strip_html(text):
    """The text of a field for sorting and searching: no tags (`<br>` and block ends become
    spaces), no `[sound:…]` or `[anki:tts]` tags, entities decoded, whitespace collapsed."""
    text = _strip_av_tags(text)
    text = _BREAK.sub(' ', text)
    text = _TAG.sub('', text)
    text = html.unescape(text).replace('\xa0', ' ')
    return ' '.join(text.split())


strip_html_media = strip_html


def media_references(text):
    """The media file names a field refers to, in order, each once: `src` of `<img>`,
    `<audio>`, `<video>` and `<source>`, `data` of `<object>` (quoted or not, entities and
    percent-encoding decoded), and `[sound:…]`. Addresses with a scheme are not files."""
    names = []
    for match in _MEDIA.finditer(text):
        name = match[4] or html.unescape(match[1] or match[2] or match[3] or '').strip()
        if match[4] is None:
            name = urllib.parse.unquote(name)
        if name and name not in names and not _URL_SCHEME.match(name):
            names.append(name)
    return names


def sound_tags(text):
    """The file names of the `[sound:…]` tags, in order."""
    return _SOUND.findall(text)


def strip_sound_tags(text):
    return _SOUND.sub('', text)


# Text to speech


def _tts_tag(text, spec):
    """`{{tts ja_JP voices=A,B speed=0.8:…}}` as Anki renders it: a tag around the text."""
    words = spec.split()[1:]
    lang = next((word for word in words if '=' not in word), '')
    options = [word for word in words if '=' in word]
    return '[anki:tts {}]{}[/anki:tts]'.format(' '.join([f'lang={lang}'] + options), text)


def _tts_options(attributes):
    options = dict(word.split('=', 1) for word in attributes.split() if '=' in word)
    try:
        speed = float(options.get('speed', 1.0))
    except ValueError:
        speed = 1.0
    voices = [voice for voice in options.get('voices', '').split(',') if voice]
    return options.get('lang', ''), voices, speed


def speech_text(text, lang=''):
    """What a TTS voice reads for some card HTML: no tags, `[sound:…]` or `[...]`; in
    Japanese, furigana give the reading (`<ruby>一日<rt>ついたち</rt></ruby>`, `一日[ついたち]`)
    so the voice does not guess it."""
    text = text.replace('[...]', ' ')
    if lang.lower().startswith('ja'):
        text = _RUBY.sub(lambda match: ''.join(_RT.findall(match[1])) or match[1], text)
        text = _furigana(text, lambda match: match[2])
    else:
        text = _RT.sub('', text)
    text = strip_html(_SOUND.sub('', text))
    return ' '.join(text.split())


def tts_tags(text):
    """The `[anki:tts]` tags, in order: (lang, voices, speed, text to speak)."""
    tags = []
    for match in _TTS_TAG.finditer(text):
        lang, voices, speed = _tts_options(match[1])
        tags.append((lang, voices, speed, speech_text(match[2], lang)))
    return tags


def av_tags(text):
    """The sounds and speech of some card HTML, in order: ('sound', name) or
    ('tts', (lang, voices, speed, text))."""
    found = []
    for match in _AV_TAG.finditer(text):
        if match[1] is not None:
            found.append(('sound', match[1]))
        else:
            lang, voices, speed = _tts_options(match[2])
            spoken = speech_text(match[3], lang)
            if spoken:
                found.append(('tts', (lang, voices, speed, spoken)))
    return found


def strip_tts_tags(text):
    return _TTS.sub('', text)


# Typed answers


def _diff_row(parts):
    """Spans for a row of (class, text) parts; neighbours of one class are merged."""
    merged = []
    for classname, text in parts:
        if merged and merged[-1][0] == classname:
            merged[-1][1] += text
        elif text:
            merged.append([classname, text])
    return ''.join(f'<span class={c}>{html.escape(text, quote=False)}</span>' for c, text in merged)


def typed_answer_diff(expected, typed):
    """The comparison Anki shows after `{{type:…}}`: a `<code id=typeans>` holding the typed
    text (typeGood / typeBad / typeMissed spans), an arrow and the expected text; just the
    expected text when nothing was typed, one typeGood span when they match."""
    expected = unicodedata.normalize('NFC', strip_html(expected))
    typed = unicodedata.normalize('NFC', typed.strip())
    if not typed:
        return f'<code id=typeans>{html.escape(expected, quote=False)}</code>'
    if typed == expected:
        escaped = html.escape(typed, quote=False)
        return f'<code id=typeans><span class=typeGood>{escaped}</span></code>'
    typed_row = []
    expected_row = []
    matcher = difflib.SequenceMatcher(None, typed, expected, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            typed_row.append(('typeGood', typed[i1:i2]))
            expected_row.append(('typeGood', expected[j1:j2]))
        elif tag == 'replace':
            typed_row.append(('typeBad', typed[i1:i2]))
            expected_row.append(('typeMissed', expected[j1:j2]))
        elif tag == 'delete':
            typed_row.append(('typeBad', typed[i1:i2]))
        else:
            typed_row.append(('typeMissed', '-' * (j2 - j1)))
            expected_row.append(('typeMissed', expected[j1:j2]))
    return (f'<code id=typeans>{_diff_row(typed_row)}<br><span id=typearrow>&darr;</span><br>'
            f'{_diff_row(expected_row)}</code>')
