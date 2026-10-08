# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""widgets/speech.py's Speaker and widgets/audio.py's Player with speech in their queue,
over a fake backend (no voice has to be installed)."""

from tests import ROOT  # noqa: F401  (registers src/ as retain)

import unittest

from retain import speech


class FakeBackend:
    name = 'fake'

    def __init__(self, voices, loaded=True):
        self._voices = voices
        self.loaded = loaded
        self.spoken = []
        self.stopped = 0
        self.waiting = []  # done callbacks of what is "being spoken"

    def voices(self):
        return list(self._voices)

    def speak(self, voice, lang, speed, text, done):
        self.spoken.append((voice.name, lang, speed, text))
        self.waiting.append(done)

    def stop(self):
        self.stopped += 1
        self.waiting = []

    def finish(self):
        self.waiting.pop(0)()


def voice(name, *languages):
    return speech.Voice('fake', name.lower(), name, list(languages), None)


def tts(lang, text, speed=1.0, voices=()):
    return ('tts', (lang, list(voices), speed, text))


class SpeakerTests(unittest.TestCase):

    def make(self, loaded=True):
        from retain.widgets.speech import Speaker

        self.backend = FakeBackend([voice('Amy', 'en-US'), voice('JTalk', 'ja-JP')], loaded)
        speaker = Speaker(backends=[self.backend])
        speaker.fallback_lang = 'en_GB'
        return speaker

    def test_speaks_in_the_voice_of_the_language(self):
        speaker = self.make()
        self.assertTrue(speaker.speak('ja_JP', '猫', lambda: None, speed=0.8))
        self.assertTrue(speaker.speak('', 'cat', lambda: None))
        self.assertEqual(self.backend.spoken, [('JTalk', 'ja_JP', 0.8, '猫'),
                                               ('Amy', '', 1.0, 'cat')])

    def test_no_voice_for_the_language_is_said(self):
        speaker = self.make()
        self.assertFalse(speaker.speak('ko_KR', '안녕', lambda: None))
        self.assertEqual(self.backend.spoken, [])

    def test_waits_for_the_voices_to_load(self):
        speaker = self.make(loaded=False)
        missing, done = [], []
        self.assertTrue(speaker.speak('ja', '猫', lambda: done.append(1)))
        self.assertTrue(speaker.speak('ko', '안녕', lambda: done.append(2), missing=missing.append))
        self.assertEqual(self.backend.spoken, [])
        self.backend.loaded = True
        speaker._on_loaded()
        self.assertEqual([item[3] for item in self.backend.spoken], ['猫'])
        self.assertEqual((missing, done), (['ko'], [2]))

    def test_without_backends_nothing_speaks(self):
        from retain.widgets.speech import Speaker

        speaker = Speaker(backends=[])
        self.assertFalse(speaker.has_backend)
        self.assertFalse(speaker.speak('en', 'cat', lambda: None))


class PlayerTests(unittest.TestCase):

    def setUp(self):
        from retain.widgets.audio import Player
        from retain.widgets.speech import Speaker

        self.backend = FakeBackend([voice('Amy', 'en-US'), voice('JTalk', 'ja-JP')])
        self.missing = []
        self.player = Player(speaker=Speaker(backends=[self.backend]),
                             missing_voice=self.missing.append)

    def test_speech_is_spoken_one_item_after_the_other(self):
        self.player.play([tts('ja', '猫'), tts('en', 'cat')])
        self.assertEqual([item[3] for item in self.backend.spoken], ['猫'])
        self.backend.finish()
        self.assertEqual([item[3] for item in self.backend.spoken], ['猫', 'cat'])

    def test_a_language_without_a_voice_is_reported_and_skipped(self):
        self.player.play([tts('ko', '안녕'), tts('en', 'hello')])
        self.assertEqual(self.missing, ['ko'])
        self.assertEqual([item[3] for item in self.backend.spoken], ['hello'])

    def test_stop_drops_the_rest(self):
        self.player.play([tts('ja', '猫'), tts('en', 'cat')])
        done = self.backend.waiting[0]
        self.player.stop()
        done()  # a late "finished" from the speech that was cancelled
        self.assertEqual([item[3] for item in self.backend.spoken], ['猫'])
        self.assertEqual(self.backend.stopped, 2)  # play()'s own stop, then this one

    def test_nothing_to_speak_with_at_all(self):
        from retain.widgets.audio import Player
        from retain.widgets.speech import Speaker

        missing = []
        player = Player(speaker=Speaker(backends=[]), missing_voice=missing.append)
        player.play([tts('ja', '猫')])
        self.assertEqual(missing, [None])


if __name__ == '__main__':
    unittest.main()
