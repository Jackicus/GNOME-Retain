# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A field's HTML and the editor's Gtk.TextBuffer, both ways.

    anchors = html_to_buffer(html, buffer, media_dir=None)   # replaces the buffer's content
    html = buffer_to_html(buffer)
    can_round_trip(html)                      # whether the editor can hold it as rich text
    anchor = insert_media(buffer, iter, 'img', 'cat.jpg', media_dir=None)
    insert_plain_text(buffer, text)           # at the cursor, formatting stripped
    ensure_tags(buffer)                       # the buffer's bold, italic, … tags

The buffer holds bold, italic, underline, sub, sup and code as text tags of those names
(TAGS maps the names to their HTML element), newlines for `<br>` and block ends, and one
child anchor per picture (`<img src="name">`) or sound (`[sound:name]`), carrying a Media
(`media_of(anchor)`). html_to_buffer
accepts b/strong, i/em, u, sub, sup, code, br, div, p and img (with `src`); it keeps the text
of any other element and drops any other attribute. can_round_trip says whether the HTML
holds only that subset, so the editor can keep a richer field as raw HTML instead of
silently losing a table or a styled span. buffer_to_html writes a canonical form (tags nested
in TAG_ORDER, `<br>` for a newline, `&nbsp;` for a non-breaking space), so writing what was
read is stable from the first round.
"""

import html
import re
from html.parser import HTMLParser

from gi.repository import Gtk, Pango

# Buffer tag name -> HTML element, in the order the elements nest when written.
TAGS = {
    'bold': 'b',
    'italic': 'i',
    'underline': 'u',
    'sub': 'sub',
    'sup': 'sup',
    'code': 'code',
}
TAG_ORDER = tuple(TAGS)

# HTML element -> buffer tag name, for reading.
_ELEMENT_TAGS = {
    'b': 'bold', 'strong': 'bold',
    'i': 'italic', 'em': 'italic',
    'u': 'underline',
    'sub': 'sub', 'sup': 'sup',
    'code': 'code',
}
_BLOCKS = ('div', 'p')
_ACCEPTED = set(_ELEMENT_TAGS) | set(_BLOCKS) | {'br', 'img'}
_SOUND = re.compile(r'\[sound:([^\]]+)\]')
_MEDIA_KINDS = ('img', 'sound')
OBJECT_CHAR = '￼'  # what an anchor is in a slice of the buffer


class Media:
    """What a media anchor stands for: `kind` is 'img' or 'sound', `name` the media file's
    name, `media_dir` where the editor finds it (None when unknown)."""

    def __init__(self, kind, name, media_dir=None):
        if kind not in _MEDIA_KINDS:
            raise ValueError(f'unknown media kind {kind!r}')
        self.kind = kind
        self.name = name
        self.media_dir = media_dir

    @property
    def path(self):
        """The file's path, or None when the media folder is unknown."""
        return None if self.media_dir is None else str(self.media_dir) + '/' + self.name

    def to_html(self):
        if self.kind == 'img':
            return f'<img src="{html.escape(self.name, quote=True)}">'
        return f'[sound:{self.name}]'


def make_media_anchor(kind, name, media_dir=None):
    """A Gtk.TextChildAnchor carrying a Media as its `media` attribute. (A Python subclass
    of the anchor cannot be used: GTK makes the anchor's segment in gtk_text_child_anchor_new,
    which g_object_new skips. The attribute survives: PyGObject keeps a wrapper that has one.)"""
    anchor = Gtk.TextChildAnchor.new()
    anchor.media = Media(kind, name, media_dir)
    return anchor


def media_of(anchor):
    """The Media an anchor stands for, or None for an anchor that is not one of ours."""
    return getattr(anchor, 'media', None)


def ensure_tags(buffer):
    """Make sure the buffer's tag table has the editor's tags; the table."""
    table = buffer.get_tag_table()
    if table.lookup('bold') is None:
        buffer.create_tag('bold', weight=Pango.Weight.BOLD)
        buffer.create_tag('italic', style=Pango.Style.ITALIC)
        buffer.create_tag('underline', underline=Pango.Underline.SINGLE)
        buffer.create_tag('sub', rise=-3 * Pango.SCALE, scale=0.75)
        buffer.create_tag('sup', rise=5 * Pango.SCALE, scale=0.75)
        buffer.create_tag('code', family='monospace')
    return table


def insert_media(buffer, iter, kind, name, media_dir=None):
    """Put a picture or a sound at `iter` (an anchor); the anchor, for the editor to place
    its widget at."""
    anchor = make_media_anchor(kind, name, media_dir)
    buffer.insert_child_anchor(iter, anchor)
    return anchor


def insert_plain_text(buffer, text):
    """Insert text at the cursor, replacing the selection, as plain text: whatever
    formatting the surrounding text has, the inserted text has none."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    buffer.begin_user_action()
    try:
        buffer.delete_selection(True, True)
        start_offset = buffer.get_iter_at_mark(buffer.get_insert()).get_offset()
        buffer.insert_at_cursor(text)
        start = buffer.get_iter_at_offset(start_offset)
        end = buffer.get_iter_at_mark(buffer.get_insert())
        buffer.remove_all_tags(start, end)
    finally:
        buffer.end_user_action()


# -- HTML to buffer ------------------------------------------------------------------------


class _Reader(HTMLParser):
    """Feeds the buffer as it parses: formatting elements push buffer tags, blocks and
    `<br>` make newlines, `<img>` and `[sound:…]` make anchors, anything else is its text."""

    def __init__(self, buffer, media_dir):
        super().__init__(convert_charrefs=True)
        self.buffer = buffer
        self.media_dir = media_dir
        self.open = []  # the buffer tag names of the open formatting elements
        self.anchors = []

    def _end(self):
        return self.buffer.get_end_iter()

    def _insert(self, text):
        if self.open:
            self.buffer.insert_with_tags_by_name(self._end(), text, *self.open)
        else:
            self.buffer.insert(self._end(), text)

    def _break_line(self):
        """A block starts: on a new line, unless the buffer is empty or already on one."""
        end = self._end()
        if end.get_offset() == 0:
            return
        before = end.copy()
        before.backward_char()
        if before.get_char() != '\n':
            self._insert('\n')

    def handle_starttag(self, tag, attrs):
        tag_name = _ELEMENT_TAGS.get(tag)
        if tag_name is not None:
            self.open.append(tag_name)
        elif tag == 'br':
            self._insert('\n')
        elif tag in _BLOCKS:
            self._break_line()
        elif tag == 'img':
            src = dict(attrs).get('src')
            if src:
                self.anchors.append(insert_media(self.buffer, self._end(), 'img', src,
                                                 self.media_dir))

    def handle_endtag(self, tag):
        tag_name = _ELEMENT_TAGS.get(tag)
        if tag_name is not None and tag_name in self.open:
            # Close the nearest open element of that kind (a stray end tag is ignored).
            index = len(self.open) - 1 - self.open[::-1].index(tag_name)
            del self.open[index]

    def handle_data(self, data):
        if '\n' in data or '\r' in data:
            if not data.strip():
                return  # source layout between tags, not content
            data = ' '.join(data.replace('\r', '\n').split('\n'))
        position = 0
        for match in _SOUND.finditer(data):
            self._insert(data[position:match.start()])
            self.anchors.append(insert_media(self.buffer, self._end(), 'sound', match[1],
                                             self.media_dir))
            position = match.end()
        self._insert(data[position:])


def html_to_buffer(html_text, buffer, media_dir=None):
    """Replace the buffer's content with a field's HTML; the media anchors inserted, in
    order (the editor puts a thumbnail or a chip at each)."""
    ensure_tags(buffer)
    buffer.begin_irreversible_action()
    try:
        buffer.set_text('')
        reader = _Reader(buffer, media_dir)
        reader.feed(html_text or '')
        reader.close()
    finally:
        buffer.end_irreversible_action()
    return reader.anchors


class _Checker(HTMLParser):
    """Whether the HTML stays within what the buffer can hold."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ok = True

    def handle_starttag(self, tag, attrs):
        if tag not in _ACCEPTED:
            self.ok = False
        elif tag == 'img':
            if [name for name, _value in attrs if name != 'src'] or not dict(attrs).get('src'):
                self.ok = False
        elif attrs:
            self.ok = False

    def handle_endtag(self, tag):
        if tag not in _ACCEPTED:
            self.ok = False

    def handle_comment(self, data):
        self.ok = False

    def handle_decl(self, decl):
        self.ok = False

    def handle_pi(self, data):
        self.ok = False


def can_round_trip(html_text):
    """Whether the editor can show the HTML as rich text and write it back without losing
    anything it cannot show: only the accepted elements, with no attributes but an image's
    `src`."""
    checker = _Checker()
    checker.feed(html_text or '')
    checker.close()
    return checker.ok


# -- buffer to HTML ------------------------------------------------------------------------


def _escape(text):
    return html.escape(text, quote=False).replace('\xa0', '&nbsp;').replace('\n', '<br>')


def buffer_to_html(buffer):
    """The buffer as a field's HTML (see the module)."""
    parts = []
    open_elements = []

    def set_formatting(wanted):
        # Close down to the deepest open element that is no longer wanted, then open the
        # missing ones in the canonical order.
        keep = len(open_elements)
        for index, name in enumerate(open_elements):
            if name not in wanted:
                keep = index
                break
        for name in reversed(open_elements[keep:]):
            parts.append(f'</{TAGS[name]}>')
        del open_elements[keep:]
        for name in TAG_ORDER:
            if name in wanted and name not in open_elements:
                parts.append(f'<{TAGS[name]}>')
                open_elements.append(name)

    start = buffer.get_start_iter()
    while not start.is_end():
        end = start.copy()
        if not end.forward_to_tag_toggle(None):
            end = buffer.get_end_iter()
        wanted = {tag.get_property('name') for tag in start.get_tags()} & set(TAGS)
        set_formatting(wanted)
        text = buffer.get_slice(start, end, True)
        offset = start.get_offset()
        position = 0
        while True:
            index = text.find(OBJECT_CHAR, position)
            if index < 0:
                break
            parts.append(_escape(text[position:index]))
            media = media_of(buffer.get_iter_at_offset(offset + index).get_child_anchor())
            if media is not None:
                parts.append(media.to_html())
            position = index + 1
        parts.append(_escape(text[position:]))
        start = end
    set_formatting(set())
    return ''.join(parts)
