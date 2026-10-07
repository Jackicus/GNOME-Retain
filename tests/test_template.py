# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

import unittest

from tests import ROOT  # noqa: F401
from retain import template

HINT_LINK = (
    '<a class=hint href="#" onclick="this.style.display=\'none\';'
    'document.getElementById(\'hint1\').style.display=\'block\';return false;">')


class FieldTest(unittest.TestCase):

    def test_fields_are_substituted(self):
        question, answer = template.render(
            '{{Front}}?', '{{FrontSide}}<hr id=answer>{{Back}}',
            {'Front': 'capital of <b>Peru</b>', 'Back': 'Lima'})
        self.assertEqual(question, 'capital of <b>Peru</b>?')
        self.assertEqual(answer, 'capital of <b>Peru</b>?<hr id=answer>Lima')

    def test_an_unknown_field_shows_a_placeholder(self):
        self.assertEqual(template.render_side('{{Nope}}', {'Front': 'x'}), '{unknown field Nope}')

    def test_specials_come_from_the_special_dict(self):
        rendered = template.render_side(
            '{{Deck}}|{{Subdeck}}|{{Tags}}|{{Card}}|{{Type}}|{{CardFlag}}', {},
            special={'Deck': 'Geography::Capitals', 'Subdeck': 'Capitals', 'Tags': 'a b'})
        self.assertEqual(rendered, 'Geography::Capitals|Capitals|a b|||')

    def test_whitespace_in_handles_and_legacy_delimiters(self):
        rendered = template.render_side('{{ Front }} <% Back %>', {'Front': 'a', 'Back': 'b'})
        self.assertEqual(rendered, 'a b')


class ConditionalTest(unittest.TestCase):

    def test_positive_conditional(self):
        tpl = '{{Front}}{{#Extra}}<br>{{Extra}}{{/Extra}}'
        self.assertEqual(template.render_side(tpl, {'Front': 'a', 'Extra': 'more'}), 'a<br>more')
        self.assertEqual(template.render_side(tpl, {'Front': 'a', 'Extra': ''}), 'a')

    def test_negative_conditional(self):
        tpl = '{{^Extra}}nothing extra{{/Extra}}'
        self.assertEqual(template.render_side(tpl, {'Extra': ''}), 'nothing extra')
        self.assertEqual(template.render_side(tpl, {'Extra': 'x'}), '')
        self.assertEqual(template.render_side(tpl, {}), 'nothing extra')

    def test_conditionals_nest(self):
        tpl = '{{#A}}a{{#B}}b{{/B}}{{^B}}no b{{/B}}{{/A}}'
        self.assertEqual(template.render_side(tpl, {'A': '1', 'B': '2'}), 'ab')
        self.assertEqual(template.render_side(tpl, {'A': '1', 'B': ''}), 'ano b')
        self.assertEqual(template.render_side(tpl, {'A': '', 'B': '2'}), '')

    def test_a_field_of_breaks_only_is_empty(self):
        tpl = '{{#Extra}}shown{{/Extra}}'
        self.assertEqual(template.render_side(tpl, {'Extra': '<br> <br/>\n<div></div>'}), '')
        self.assertEqual(template.render_side(tpl, {'Extra': '<br>x'}), 'shown')

    def test_a_cloze_conditional_follows_the_card(self):
        tpl = '{{#c2}}second{{/c2}}{{^c2}}other{{/c2}}'
        self.assertEqual(template.render_side(tpl, {}, ord=1, kind='cloze'), 'second')
        self.assertEqual(template.render_side(tpl, {}, ord=0, kind='cloze'), 'other')

    def test_unbalanced_conditionals_are_errors(self):
        with self.assertRaises(template.TemplateError):
            template.render_side('{{#A}}x', {'A': '1'})
        with self.assertRaises(template.TemplateError):
            template.render_side('x{{/A}}', {'A': '1'})


class FrontSideTest(unittest.TestCase):

    def test_frontside_loses_its_sound_tags(self):
        question, answer = template.render(
            '{{Word}}[sound:word.mp3]', '{{FrontSide}}<hr>{{Meaning}}',
            {'Word': 'hola', 'Meaning': 'hello'})
        self.assertEqual(question, 'hola[sound:word.mp3]')
        self.assertEqual(answer, 'hola<hr>hello')

    def test_render_side_takes_the_question(self):
        answer = template.render_side(
            '{{FrontSide}}|{{Back}}', {'Back': 'b'}, question='q[sound:a.ogg]', side='answer')
        self.assertEqual(answer, 'q|b')
        self.assertEqual(template.render_side('{{FrontSide}}x', {}), 'x')


class FilterTest(unittest.TestCase):

    def test_hint(self):
        rendered = template.render_side('{{hint:Mnemonic}}', {'Mnemonic': 'think <i>big</i>'})
        self.assertEqual(
            rendered,
            HINT_LINK + 'Mnemonic</a><div id="hint1" class=hint style="display: none">'
            'think <i>big</i></div>')
        self.assertEqual(template.render_side('{{hint:Mnemonic}}', {'Mnemonic': ' '}), '')

    def test_hint_ids_are_distinct_across_the_answer(self):
        _, answer = template.render('{{hint:A}}', '{{FrontSide}}{{hint:B}}', {'A': 'a', 'B': 'b'})
        self.assertIn('id="hint1"', answer)
        self.assertIn('id="hint2"', answer)

    def test_text_strips_html(self):
        self.assertEqual(template.render_side('{{text:Front}}', {'Front': '<b>a</b> &amp; b'}),
                         'a & b')

    def test_type_placeholders(self):
        question, answer = template.render(
            '{{Front}}{{type:Back}}', '{{type:Back}}', {'Front': 'q', 'Back': 'a'})
        self.assertEqual(question, 'q<span class="retain-type-answer" data-field="Back"></span>')
        self.assertEqual(answer,
                         '<span class="retain-type-answer-result" data-field="Back"></span>')
        question, answer = template.render(
            '{{type:cloze:Text}}', '{{type:cloze:Text}}', {'Text': '{{c1::x}}'}, kind='cloze')
        self.assertEqual(
            question,
            '<span class="retain-type-answer" data-field="Text" data-cloze="1"></span>')
        self.assertEqual(
            answer,
            '<span class="retain-type-answer-result" data-field="Text" data-cloze="1"></span>')

    def test_filter_chains_apply_right_to_left(self):
        rendered = template.render_side(
            '{{text:cloze:Text}}', {'Text': '<b>{{c1::bold}}</b> text'}, kind='cloze')
        self.assertEqual(rendered, '[...] text')
        rendered = template.render_side(
            '{{hint:text:Note}}', {'Note': '<i>plain</i>'})
        self.assertEqual(rendered, HINT_LINK + 'Note</a><div id="hint1" class=hint '
                         'style="display: none">plain</div>')

    def test_tts_and_unknown_filters_give_the_plain_field(self):
        fields = {'Front': 'hello'}
        self.assertEqual(template.render_side('{{tts en_US:Front}}', fields), 'hello')
        self.assertEqual(template.render_side('{{tts-voices:}}', fields), '')
        self.assertEqual(template.render_side('{{shout:Front}}', fields), 'hello')

    def test_furigana(self):
        fields = {'Reading': '日本語[にほんご]を 話[はな]す [sound:a.mp3]'}
        self.assertEqual(
            template.render_side('{{furigana:Reading}}', fields),
            '<ruby>日本語<rt>にほんご</rt></ruby>を<ruby>話<rt>はな</rt></ruby>す [sound:a.mp3]')
        self.assertEqual(template.render_side('{{kana:Reading}}', fields),
                         'にほんごをはなす [sound:a.mp3]')
        self.assertEqual(template.render_side('{{kanji:Reading}}', fields),
                         '日本語を話す [sound:a.mp3]')


class ClozeTest(unittest.TestCase):

    FIELDS = {'Text': 'The {{c1::sun}} is a {{c2::star::kind of thing}}.'}

    def test_card_one(self):
        question, answer = template.render(
            '{{cloze:Text}}', '{{cloze:Text}}', self.FIELDS, ord=0, kind='cloze')
        self.assertEqual(
            question,
            'The <span class="cloze" data-cloze="sun" data-ordinal="1">[...]</span> is a '
            '<span class="cloze-inactive" data-ordinal="2">star</span>.')
        self.assertEqual(
            answer,
            'The <span class="cloze" data-ordinal="1">sun</span> is a '
            '<span class="cloze-inactive" data-ordinal="2">star</span>.')

    def test_card_two_shows_the_hint(self):
        question, answer = template.render(
            '{{cloze:Text}}', '{{cloze:Text}}', self.FIELDS, ord=1, kind='cloze')
        self.assertEqual(
            question,
            'The <span class="cloze-inactive" data-ordinal="1">sun</span> is a '
            '<span class="cloze" data-cloze="star" data-ordinal="2">[kind of thing]</span>.')
        self.assertEqual(
            answer,
            'The <span class="cloze-inactive" data-ordinal="1">sun</span> is a '
            '<span class="cloze" data-ordinal="2">star</span>.')

    def test_nested_clozes(self):
        fields = {'Text': '{{c1::a {{c2::b}}}}'}
        self.assertEqual(
            template.render_side('{{cloze:Text}}', fields, ord=0, kind='cloze'),
            '<span class="cloze" data-cloze="a &lt;span class=&quot;cloze-inactive&quot; '
            'data-ordinal=&quot;2&quot;&gt;b&lt;/span&gt;" data-ordinal="1">[...]</span>')
        self.assertEqual(
            template.render_side('{{cloze:Text}}', fields, ord=1, kind='cloze'),
            '<span class="cloze-inactive" data-ordinal="1">a <span class="cloze" '
            'data-cloze="b" data-ordinal="2">[...]</span></span>')
        self.assertEqual(
            template.render_side('{{cloze:Text}}', fields, ord=1, kind='cloze', side='answer'),
            '<span class="cloze-inactive" data-ordinal="1">a <span class="cloze" '
            'data-ordinal="2">b</span></span>')

    def test_multi_ordinal_clozes_are_active_on_each_card(self):
        fields = {'Text': '{{c1,2::x}} {{c3::y}}'}
        for ord in (0, 1):
            self.assertEqual(
                template.render_side('{{cloze:Text}}', fields, ord=ord, kind='cloze'),
                f'<span class="cloze" data-cloze="x" data-ordinal="{ord + 1}">[...]</span> '
                '<span class="cloze-inactive" data-ordinal="3">y</span>')
        self.assertEqual(
            template.render_side('{{cloze:Text}}', fields, ord=2, kind='cloze'),
            '<span class="cloze-inactive" data-ordinal="1">x</span> '
            '<span class="cloze" data-cloze="y" data-ordinal="3">[...]</span>')

    def test_a_card_without_its_cloze_shows_the_text(self):
        self.assertEqual(
            template.render_side('{{cloze:Text}}', {'Text': 'a {{c1::b}}'}, ord=4, kind='cloze'),
            'a <span class="cloze-inactive" data-ordinal="1">b</span>')

    def test_cloze_only(self):
        fields = {'Text': '{{c1::a}} and {{c1::b::hint}} not {{c2::c}}'}
        self.assertEqual(template.render_side('{{cloze-only:Text}}', fields, kind='cloze'),
                         '..., hint')
        self.assertEqual(
            template.render_side('{{cloze-only:Text}}', fields, kind='cloze', side='answer'),
            'a, b')

    def test_image_occlusion_clozes_do_not_crash(self):
        text = '{{c1::image-occlusion:rect:left=10.5:top=20:width=30:height=40:oi=1}}'
        rendered = template.render_side('{{cloze:Occlusion}}', {'Occlusion': text},
                                        kind='occlusion')
        self.assertEqual(
            rendered,
            '<span class="cloze" data-cloze="image-occlusion:rect:left=10.5:top=20:width=30:'
            'height=40:oi=1" data-ordinal="1">[...]</span>')
        self.assertEqual(template.cloze_numbers(text), {1})

    def test_cloze_numbers(self):
        self.assertEqual(template.cloze_numbers('{{c1::a}} {{c3::b {{c2::c}}}} {{c0::z}}'),
                         {1, 2, 3})
        self.assertEqual(template.cloze_numbers('{{c1,4::a}}'), {1, 4})
        self.assertEqual(template.cloze_numbers('no cloze {{c1::unclosed'), set())


class CardsForNoteTest(unittest.TestCase):

    def test_standard_skips_empty_fronts(self):
        templates = [
            {'qfmt': '{{Front}}', 'afmt': '{{Back}}'},
            {'qfmt': '{{Back}}<br>{{hint:Extra}}', 'afmt': '{{Front}}'},
            {'qfmt': '<img src="logo.png">', 'afmt': 'x'},
        ]
        self.assertEqual(template.cards_for_note(
            'standard', templates, {'Front': 'a', 'Back': '', 'Extra': ''}), [0, 2])
        self.assertEqual(template.cards_for_note(
            'standard', templates, {'Front': '<br>', 'Back': 'b', 'Extra': ''}), [1, 2])
        self.assertEqual(template.cards_for_note(
            'standard', templates, {'Front': '', 'Back': '', 'Extra': 'e'}), [1, 2])

    def test_cloze_follows_the_cloze_fields(self):
        templates = [{'qfmt': '{{cloze:Text}}{{#Extra}}{{cloze:Extra}}{{/Extra}}'}]
        self.assertEqual(template.cards_for_note(
            'cloze', templates, {'Text': '{{c3::a}} {{c1::b}}', 'Extra': '{{c2::c}}'}), [0, 1, 2])
        self.assertEqual(template.cards_for_note(
            'cloze', templates, {'Text': 'no clozes', 'Extra': ''}), [])
        self.assertEqual(template.cards_for_note(
            'occlusion', ['{{cloze:Occlusion}}'], {'Occlusion': '{{c2::image-occlusion:rect}}'}),
            [1])


class StripHtmlTest(unittest.TestCase):

    def test_strip_html(self):
        text = '<div>one<br>two</div><p>three &amp; four&nbsp;five</p>[sound:x.mp3] <b>six</b>'
        self.assertEqual(template.strip_html(text), 'one two three & four five six')
        self.assertEqual(template.strip_html('a [anki:tts lang=en]b[/anki:tts]  <!-- c --> d'),
                         'a d')
        self.assertEqual(template.strip_html('&lt;not a tag&gt;'), '<not a tag>')
        self.assertIs(template.strip_html_media, template.strip_html)

    def test_sound_tags(self):
        text = 'a[sound:one.mp3] b [sound:two.ogg]'
        self.assertEqual(template.sound_tags(text), ['one.mp3', 'two.ogg'])
        self.assertEqual(template.strip_sound_tags(text), 'a b ')


class MediaReferencesTest(unittest.TestCase):

    def test_media_references(self):
        text = (
            '<img src="a.png"> <img src=\'b c.jpg\'> <img alt="x" src=d.gif> '
            '<img src="e%20f.png"> <audio src="g.mp3"></audio> <object data="h.svg"></object> '
            '<video><source src="i.webm"></video> <img src="a.png"> [sound:j.mp3] '
            '<img src="https://example.org/k.png"> <img src="l&amp;m.png">')
        self.assertEqual(
            template.media_references(text),
            ['a.png', 'b c.jpg', 'd.gif', 'e f.png', 'g.mp3', 'h.svg', 'i.webm', 'j.mp3',
             'l&m.png'])


class TypedAnswerTest(unittest.TestCase):

    def test_a_match(self):
        self.assertEqual(template.typed_answer_diff('<b>cat</b>', 'cat'),
                         '<code id=typeans><span class=typeGood>cat</span></code>')

    def test_a_mismatch(self):
        self.assertEqual(
            template.typed_answer_diff('cats', 'cot'),
            '<code id=typeans><span class=typeGood>c</span><span class=typeBad>o</span>'
            '<span class=typeGood>t</span><span class=typeMissed>-</span><br>'
            '<span id=typearrow>&darr;</span><br><span class=typeGood>c</span>'
            '<span class=typeMissed>a</span><span class=typeGood>t</span>'
            '<span class=typeMissed>s</span></code>')

    def test_nothing_typed(self):
        self.assertEqual(template.typed_answer_diff('a &lt; b', ''),
                         '<code id=typeans>a &lt; b</code>')


class FieldNamesTest(unittest.TestCase):

    def test_field_names_in(self):
        tpl = ('{{Front}} {{cloze:Text}} {{hint:Extra}} {{type:Back}} {{text:Notes}} '
               '{{#Image}}{{Image}}{{/Image}} {{^Audio}}-{{/Audio}} {{FrontSide}} {{Tags}} '
               '{{#c1}}{{/c1}} {{tts-voices:}}')
        self.assertEqual(
            template.field_names_in(tpl),
            {'Front', 'Text', 'Extra', 'Back', 'Notes', 'Image', 'Audio'})


if __name__ == '__main__':
    unittest.main()
