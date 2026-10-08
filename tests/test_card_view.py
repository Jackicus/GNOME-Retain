# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The card view (widgets/card_view.py): the fallback's HTML to Pango markup conversion
(a pure function), and the widget rendering a card with the fallback label and, when it is
there, with WebKit (RETAIN_NO_WEBKIT=1 leaves WebKit out, as CI does)."""

import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import requires_gtk


def convert(html):
    from retain.widgets.card_view import html_to_markup

    return html_to_markup(html)


class HtmlToMarkupTest(unittest.TestCase):

    def test_inline_tags(self):
        self.assertEqual(convert('<b>bold</b> <i>it</i> <u>u</u> <strong>s</strong> <em>e</em>'),
                         '<b>bold</b> <i>it</i> <u>u</u> <b>s</b> <i>e</i>')
        self.assertEqual(convert('H<sub>2</sub>O x<sup>2</sup> <code>c</code>'),
                         'H<sub>2</sub>O x<sup>2</sup> <tt>c</tt>')

    def test_breaks_and_blocks(self):
        self.assertEqual(convert('one<br>two<br/>three'), 'one\ntwo\nthree')
        self.assertEqual(convert('<div>one</div><div>two</div>'), 'one\ntwo')
        self.assertEqual(convert('<p>one</p><p>two</p>'), 'one\n\ntwo')
        self.assertEqual(convert('a<hr id=answer>b'), 'a\n———\nb')

    def test_images_sounds_and_entities(self):
        self.assertEqual(convert('see <img src="x.png"> &amp; &lt;b&gt; &eacute;&nbsp;'),
                         'see [image] &amp; &lt;b&gt; é')
        self.assertEqual(convert('hola [sound:hola.mp3]'), 'hola ♪')
        self.assertEqual(convert('猫[anki:tts lang=ja_JP]猫[/anki:tts]'), '猫')

    def test_clozes_and_typed_answer_spans(self):
        self.assertEqual(
            convert('<span class="cloze" data-ordinal="1">[...]</span> and '
                    '<span class="cloze-inactive" data-ordinal="2">that</span>'),
            '<b>[...]</b> and that')
        self.assertEqual(
            convert('<code id=typeans><span class=typeGood>go</span>'
                    '<span class=typeBad>x</span><span class=typeMissed>od</span></code>'),
            '<tt><b>go</b><s>x</s><u>od</u></tt>')

    def test_scripts_styles_and_unknown_tags_go(self):
        self.assertEqual(convert('<style>.x{}</style><script>a<b</script><table><tr><td>'
                                 'cell</td></tr></table>'), 'cell')
        self.assertEqual(convert('<!-- note --><font color="red">red</font>'), 'red')

    def test_tags_are_balanced(self):
        self.assertEqual(convert('<b>open <i>both'), '<b>open <i>both</i></b>')
        self.assertEqual(convert('</b>stray</i>'), 'stray')
        self.assertEqual(convert(''), '')
        self.assertEqual(convert(None), '')


@requires_gtk
class CardViewTest(unittest.TestCase):

    def view_without_webkit(self):
        from retain.widgets import card_view

        kept = card_view.WebKit
        card_view.WebKit = None
        try:
            view = card_view.RetainCardView()
        finally:
            card_view.WebKit = kept
        self.addCleanup(view.run_dispose)
        return view

    def test_fallback_renders_both_sides(self):
        view = self.view_without_webkit()
        self.assertFalse(view.uses_webkit)
        ready = []
        view.connect('ready', lambda _view: ready.append(True))
        view.set_card('<b>front</b><span class="retain-type-answer" data-field="Back"></span>',
                      'front<hr id=answer>back<span class="retain-type-answer-result" '
                      'data-field="Back"></span>', '.card { color: red }', None)
        view.show_question()
        self.assertEqual(view._label.get_text(), 'front')
        self.assertEqual(view.side, 'question')
        view.show_answer()
        self.assertIn('back', view._label.get_text())
        view.set_typed_result('<code id=typeans><span class=typeGood>back</span></code>')
        self.assertEqual(view._label.get_text().count('back'), 2)
        self.assertEqual(len(ready), 3)
        view.set_card('q', 'a', '', None, occlusion={'shapes': [], 'ordinal': 1}, scale=1.5)
        view.show_question()
        self.assertEqual(view._label.get_text(), 'q\n[image]')

    def test_document(self):
        from retain.widgets.card_view import OCCLUSION_SCRIPT, RetainCardView

        view = self.view_without_webkit()
        shapes = [{'ordinal': 1, 'shape': 'rect', 'left': 0.1, 'top': 0.2, 'width': 0.3,
                   'height': 0.1, 'fill': None, 'angle': 0.0, 'occlude_inactive': False}]
        view.set_card('<img src="a.png">[sound:a.mp3]', 'a', '.card { color: red }',
                      'file:///invented/media/', occlusion={'shapes': shapes, 'ordinal': 1})
        view.show_question()
        document = view.document()
        self.assertIn('<style>.card { color: red }</style>', document)
        self.assertIn('class="retain-sound"', document)
        self.assertNotIn('[sound:', document)
        self.assertIn('"ordinal": 1', document)
        self.assertIn('"side": "question"', document)
        self.assertIn(OCCLUSION_SCRIPT, document)
        self.assertIn('<body class="card', document)
        self.assertIsInstance(view, RetainCardView)

    def test_webkit_renders_without_raising(self):
        from retain.widgets import card_view

        if card_view.WebKit is None:
            self.skipTest('WebKitGTK 6.0 is not available (or RETAIN_NO_WEBKIT is set)')
        from gi.repository import Gtk

        window = Gtk.Window()
        self.addCleanup(window.destroy)
        view = card_view.RetainCardView()
        window.set_child(view)
        self.assertTrue(view.uses_webkit)
        view.set_card('<b>front</b>', 'back', '', 'file:///invented/media/',
                      occlusion={'shapes': [], 'ordinal': 1}, scale=1.2)
        view.show_question()
        view.show_answer()
        view.set_typed_result('<code id=typeans>back</code>')
        view.set_scale(1.0)
        self.assertEqual(view._web.get_zoom_level(), 1.0)
        view._apply_style()  # the palette resolves on this machine's theme


if __name__ == '__main__':
    unittest.main()
