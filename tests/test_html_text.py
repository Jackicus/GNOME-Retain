# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""widgets/html_text.py: a field's HTML into a text buffer and back."""

import gc
import unittest

from tests import ROOT  # noqa: F401  (registers src/ as retain)
from tests.gtk import requires_gtk


def round_trip(html):
    """The HTML as the editor writes it back, after reading it; and once more, to show
    the written form is stable."""
    from gi.repository import Gtk

    from retain.widgets import html_text

    buffer = Gtk.TextBuffer()
    html_text.html_to_buffer(html, buffer)
    gc.collect()  # the anchors must keep what they stand for without a Python reference
    first = html_text.buffer_to_html(buffer)
    again = Gtk.TextBuffer()
    html_text.html_to_buffer(first, again)
    return first, html_text.buffer_to_html(again)


@requires_gtk
class RoundTripTests(unittest.TestCase):

    def assert_stable(self, html, expected=None):
        first, second = round_trip(html)
        self.assertEqual(first, expected if expected is not None else html)
        self.assertEqual(second, first)

    def test_formatting(self):
        self.assert_stable('<b>bold</b>, <i>italic</i> and <u>underlined</u>')

    def test_strong_and_em_are_bold_and_italic(self):
        self.assert_stable('<strong>a</strong> <em>b</em>', '<b>a</b> <i>b</i>')

    def test_nested_formatting_in_canonical_order(self):
        self.assert_stable('<i><b>x</b></i>', '<b><i>x</i></b>')
        self.assert_stable('<b>a<i>b</i>c</b>')

    def test_line_breaks(self):
        self.assert_stable('one<br>two')
        self.assert_stable('one<br/>two', 'one<br>two')

    def test_divs_are_lines(self):
        self.assert_stable('<div>one</div><div>two</div>', 'one<br>two')
        self.assert_stable('one<div>two</div>', 'one<br>two')
        # Anki's empty line: a div holding only a break.
        self.assert_stable('one<div><br></div><div>two</div>', 'one<br><br>two')

    def test_sub_sup_code(self):
        self.assert_stable('H<sub>2</sub>O, x<sup>2</sup>, <code>f(x)</code>')

    def test_image(self):
        self.assert_stable('a cat: <img src="cat.jpg"> there')

    def test_sound(self):
        self.assert_stable('[sound:meow.mp3] listen')

    def test_entities(self):
        self.assert_stable('a &amp; b &lt; c &gt; d&nbsp;e')
        self.assert_stable('&eacute;t&#233;', 'été')

    def test_unknown_tags_keep_their_text(self):
        first, _second = round_trip('<span style="color: red">red</span> <font>x</font>')
        self.assertEqual(first, 'red x')

    def test_source_newlines_are_not_lines(self):
        first, _second = round_trip('<div>one</div>\n<div>two</div>')
        self.assertEqual(first, 'one<br>two')
        first, _second = round_trip('one\ntwo')
        self.assertEqual(first, 'one two')

    def test_empty(self):
        self.assert_stable('')


@requires_gtk
class AnchorTests(unittest.TestCase):

    def test_anchors_carry_their_media(self):
        from gi.repository import Gtk

        from retain.widgets import html_text

        buffer = Gtk.TextBuffer()
        anchors = html_text.html_to_buffer('<img src="a.png">[sound:b.ogg]', buffer, '/m')
        self.assertEqual([(html_text.media_of(a).kind, html_text.media_of(a).name)
                          for a in anchors], [('img', 'a.png'), ('sound', 'b.ogg')])
        self.assertEqual(html_text.media_of(anchors[0]).path, '/m/a.png')
        del anchors
        gc.collect()
        found = buffer.get_start_iter().get_child_anchor()
        self.assertEqual(html_text.media_of(found).name, 'a.png')

    def test_insert_media_at_cursor(self):
        from gi.repository import Gtk

        from retain.widgets import html_text

        buffer = Gtk.TextBuffer()
        html_text.html_to_buffer('ab', buffer)
        html_text.insert_media(buffer, buffer.get_iter_at_offset(1), 'img', 'x.png')
        self.assertEqual(html_text.buffer_to_html(buffer), 'a<img src="x.png">b')

    def test_a_foreign_anchor_is_not_written(self):
        from gi.repository import Gtk

        from retain.widgets import html_text

        buffer = Gtk.TextBuffer()
        buffer.set_text('ab')
        buffer.insert_child_anchor(buffer.get_iter_at_offset(1), Gtk.TextChildAnchor.new())
        self.assertEqual(html_text.buffer_to_html(buffer), 'ab')


@requires_gtk
class PlainTextTests(unittest.TestCase):

    def test_pasted_text_takes_no_formatting(self):
        from gi.repository import Gtk

        from retain.widgets import html_text

        buffer = Gtk.TextBuffer()
        html_text.html_to_buffer('<b>bold</b>', buffer)
        buffer.place_cursor(buffer.get_iter_at_offset(2))
        html_text.insert_plain_text(buffer, 'X')
        self.assertEqual(html_text.buffer_to_html(buffer), '<b>bo</b>X<b>ld</b>')

    def test_pasted_text_replaces_the_selection_and_keeps_lines(self):
        from gi.repository import Gtk

        from retain.widgets import html_text

        buffer = Gtk.TextBuffer()
        html_text.html_to_buffer('one two', buffer)
        buffer.select_range(buffer.get_iter_at_offset(4), buffer.get_end_iter())
        html_text.insert_plain_text(buffer, 'a\r\nb <i>c</i>')
        self.assertEqual(html_text.buffer_to_html(buffer), 'one a<br>b &lt;i&gt;c&lt;/i&gt;')


@requires_gtk
class CanRoundTripTests(unittest.TestCase):

    def test_supported_subset(self):
        from retain.widgets import html_text

        for html in ('', 'plain', '<b>a</b><i>b</i><u>c</u>', 'a<br>b', '<div>a</div>',
                     '<p>a</p>', 'x<sub>2</sub><sup>3</sup>', '<code>f</code>',
                     '<img src="a.png">', '[sound:a.mp3]', 'a &amp; b&nbsp;c'):
            self.assertTrue(html_text.can_round_trip(html), html)

    def test_richer_html_is_kept_as_html(self):
        from retain.widgets import html_text

        for html in ('<table><tr><td>x</td></tr></table>', '<span style="color: red">x</span>',
                     '<ul><li>x</li></ul>', '<div style="text-align: center">x</div>',
                     '<img src="a.png" width="200">', '<a href="x">x</a>', '<!-- c -->x',
                     '<b class="x">x</b>', '<img>'):
            self.assertFalse(html_text.can_round_trip(html), html)
