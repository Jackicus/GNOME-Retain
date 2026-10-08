# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A card's sounds and speech: the files its `[sound:…]` tags name and the text its
`[anki:tts]` tags (or Read Aloud) give, one after the other, in order.

    player = Player(speaker=None, missing_voice=None)
    player.play([path, ('tts', (lang, voices, speed, text)), …])   # replaces what was playing
    player.stop()
    player.available            # False when GStreamer is missing: files are then skipped

GStreamer's playbin plays the files; without GStreamer (gi cannot import Gst) the player
logs once and skips them, so the review page works the same. Gst.init() runs once, the
first time a Player is made. Speech goes to `speaker` (widgets/speech.py's Speaker, the
app's one by default); `missing_voice(lang)` is told when no voice can read a language,
and the queue moves on.
"""

import logging

import gi

log = logging.getLogger(__name__)

try:
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
except (ImportError, ValueError):  # pragma: no cover - depends on the machine
    Gst = None

_initialised = False


def _init():
    """Gst.init once; False when GStreamer is not there."""
    global _initialised
    if Gst is None:
        return False
    if not _initialised:
        try:
            Gst.init(None)
        except Exception:
            log.warning('GStreamer could not start: card sounds will not play', exc_info=True)
            return False
        _initialised = True
    return True


class Player:

    def __init__(self, speaker=None, missing_voice=None):
        self.available = _init()
        self._queue = []
        self._playbin = None
        self._speaker = speaker
        self._missing_voice = missing_voice
        self._generation = 0  # bumped by stop(): speech finishing after it is ignored
        if not self.available:
            log.info('GStreamer is not available: card sounds will not play')
            return
        self._playbin = Gst.ElementFactory.make('playbin', None)
        if self._playbin is None:
            log.warning('GStreamer has no playbin: card sounds will not play')
            self.available = False
            return
        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        bus.connect('message::eos', self._on_eos)
        bus.connect('message::error', self._on_error)

    def play(self, items):
        """Play files (str or pathlib.Path) and speak ('tts', (lang, voices, speed, text))
        items, in order."""
        self.stop()
        self._queue = [item if isinstance(item, tuple) else str(item) for item in items]
        self._play_next()

    def stop(self):
        self._queue = []
        self._generation += 1
        if self._playbin is not None:
            self._playbin.set_state(Gst.State.NULL)
        if self._speaker is not None:
            self._speaker.stop()

    @property
    def speaker(self):
        if self._speaker is None:
            from .speech import get_speaker

            self._speaker = get_speaker()
        return self._speaker

    def _speak(self, lang, voices, speed, text):
        generation = self._generation

        def done():
            if generation == self._generation:
                self._play_next()

        def missing(language):
            if self._missing_voice is not None:
                self._missing_voice(language)

        if self.speaker.speak(lang, text, done, voices=voices, speed=speed, missing=missing):
            return
        if self.speaker.has_backend:
            missing(lang)
        else:
            missing(None)  # nothing can speak at all
        self._play_next()

    def _play_next(self):
        if not self._queue:
            return
        item = self._queue.pop(0)
        if isinstance(item, tuple):
            _kind, (lang, voices, speed, text) = item
            self._speak(lang, voices, speed, text)
            return
        if not self.available:
            self._play_next()
            return
        path = item
        self._playbin.set_state(Gst.State.NULL)
        self._playbin.set_property('uri', Gst.filename_to_uri(path))
        if self._playbin.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            log.warning('cannot play %s', path)
            self._play_next()

    def _on_eos(self, _bus, _message):
        self._playbin.set_state(Gst.State.NULL)
        self._play_next()

    def _on_error(self, _bus, message):
        error, _debug = message.parse_error()
        log.warning('playing a card sound: %s', error.message)
        self._playbin.set_state(Gst.State.NULL)
        self._play_next()
