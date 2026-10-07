# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A card's sounds: the files its `[sound:…]` tags name, played one after the other.

    player = Player()
    player.play([path, path])   # a list of file paths, in order; replaces what was playing
    player.stop()
    player.available            # False when GStreamer is missing: play() then does nothing

GStreamer's playbin does the playing; without GStreamer (gi cannot import Gst) the player
logs once and stays silent, so the review page works the same. Gst.init() runs once, the
first time a Player is made.
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

    def __init__(self):
        self.available = _init()
        self._queue = []
        self._playbin = None
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

    def play(self, paths):
        """Play the files at `paths` (str or pathlib.Path), in order."""
        self.stop()
        if not self.available:
            return
        self._queue = [str(path) for path in paths]
        self._play_next()

    def stop(self):
        self._queue = []
        if self._playbin is not None:
            self._playbin.set_state(Gst.State.NULL)

    def _play_next(self):
        if not self._queue:
            return
        path = self._queue.pop(0)
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
