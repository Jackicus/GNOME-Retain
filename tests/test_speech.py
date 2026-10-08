# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""speech.py: splitting a card side into runs for the voice of each language."""

from tests import ROOT  # noqa: F401  (registers src/ as retain)

import unittest

from retain import speech


class RunsTests(unittest.TestCase):

    def test_japanese_and_english_are_split(self):
        self.assertEqual(speech.runs('猫<br>cat', context='猫[ねこ]'), [('ja', '猫'), ('', 'cat')])

    def test_a_kanji_word_alone_is_japanese_only_with_kana_in_the_note(self):
        self.assertEqual(speech.runs('猫', context='ねこ\x1fcat'), [('ja', '猫')])
        self.assertEqual(speech.runs('猫', context='māo\x1fcat'), [('zh', '猫')])

    def test_furigana_give_the_reading(self):
        self.assertEqual(speech.runs('<ruby>一日<rt>ついたち</rt></ruby>: the 1st'),
                         [('ja', 'ついたち:'), ('', 'the 1st')])

    def test_punctuation_and_digits_stay_in_their_run(self):
        self.assertEqual(speech.runs("三時[さんじ]です。 3 o'clock, please."),
                         [('ja', 'さんじです。 3'), ('', "o'clock, please.")])

    def test_kanji_without_kana_is_chinese(self):
        self.assertEqual(speech.runs('你好 hello'), [('zh', '你好'), ('', 'hello')])

    def test_hangul(self):
        self.assertEqual(speech.runs('안녕하세요'), [('ko', '안녕하세요')])

    def test_nothing_to_say(self):
        self.assertEqual(speech.runs('<img src="a.png">[sound:a.mp3]'), [])

    def test_the_answer_side_without_the_question(self):
        question, answer = '猫', '猫\n\n<hr id=answer>\n\ncat'
        self.assertEqual(speech.side_html(question, answer, 'answer'), '\n\ncat')
        self.assertEqual(speech.side_html(question, answer, 'question'), '猫')
        self.assertEqual(speech.side_html(question, 'no rule', 'answer'), 'no rule')


def voice(name, *languages, backend='spiel'):
    return speech.Voice(backend, name.lower(), name, list(languages), None)


class VoiceTests(unittest.TestCase):

    def setUp(self):
        self.voices = [voice('Amy', 'en-US'), voice('Alan', 'en-GB'), voice('Kyoko', 'ja'),
                       voice('OpenJTalk', 'ja-JP', backend='speechd'), voice('Mei', 'zh-CN')]

    def test_language_tags(self):
        self.assertEqual(speech.language_tag('ja_JP'), 'ja-JP')
        self.assertEqual(speech.language_tag('EN-gb'), 'en-GB')
        self.assertEqual(speech.language_tag('zh_Hant_TW'), 'zh-Hant-TW')
        self.assertEqual(speech.language_tag(''), '')

    def test_an_exact_language_wins_then_the_base_language(self):
        self.assertEqual(speech.choose_voice(self.voices, 'ja_JP').name, 'OpenJTalk')
        self.assertEqual(speech.choose_voice(self.voices, 'ja').name, 'Kyoko')
        self.assertEqual(speech.choose_voice(self.voices, 'en_GB').name, 'Alan')
        self.assertEqual(speech.choose_voice(self.voices, 'en_AU').name, 'Amy')

    def test_never_a_voice_of_another_language(self):
        self.assertIsNone(speech.choose_voice(self.voices, 'ko_KR'))
        self.assertIsNone(speech.choose_voice([voice('Amy', 'en-US')], 'ja_JP'))
        self.assertIsNone(speech.choose_voice([], 'en_US'))

    def test_preferred_voices_by_name(self):
        chosen = speech.choose_voice(self.voices, 'ja_JP', preferred=['Apple_Otoya', 'kyoko'])
        self.assertEqual(chosen.name, 'Kyoko')

    def test_no_language_is_read_in_the_users_own(self):
        self.assertEqual(speech.choose_voice(self.voices, '', fallback_lang='en_GB').name, 'Alan')


if __name__ == '__main__':
    unittest.main()
