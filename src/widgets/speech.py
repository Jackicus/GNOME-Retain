# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Speaking text: the system's voices through Spiel, else Speech Dispatcher.

    speaker = get_speaker()                 # one for the app, made on first use
    speaker.speak(lang, text, done, voices=(), speed=1.0, missing=None) -> False: no voice
    speaker.stop()
    speaker.voices()                        # [speech.Voice] of every backend, once loaded
    speaker.has_backend                     # False when neither Spiel nor speechd imports

Spiel (libspiel, GNOME's speech framework) is tried first: its providers (espeak-ng, Piper)
are D-Bus services. Speech Dispatcher (the `speechd` module) is the second: most
distributions run it, and its Open JTalk module is today's way to Japanese that reads kanji.
Either may be missing; with neither, nothing is spoken and speak() says so.

The voice is chosen here (speech.choose_voice), never by the backend: Spiel would read a
Japanese word with whatever voice comes first when no Japanese one exists. speak() answers
False when no voice suits the language, so the caller can say what to install. `done()` is
called on the main loop once the text was spoken, cancelled or failed; a stop() leaves the
callbacks of what it cancelled uncalled.

Both backends load their voices in the background (Spiel asynchronously, Speech Dispatcher
in a thread, as it may start its daemon); until they are known, speak() waits for them.
"""

import logging
import threading

import gi
from gi.repository import GLib

from .. import speech

log = logging.getLogger(__name__)

try:
    gi.require_version('Spiel', '1.0')
    from gi.repository import Spiel
except (ImportError, ValueError):  # pragma: no cover - depends on the machine
    Spiel = None

try:
    import speechd
except ImportError:  # pragma: no cover - depends on the machine
    speechd = None

# Speech Dispatcher output modules whose voices report no language of their own.
MODULE_LANGUAGES = {'openjtalk': ['ja-JP']}
LOAD_TIMEOUT = 5  # seconds speak() waits for the voices before giving up


def _user_language():
    for name in GLib.get_language_names():
        if name not in ('C', 'POSIX') and not name.startswith('C.'):
            return name.split('.')[0].split('@')[0]
    return 'en'


class SpielBackend:
    name = 'spiel'

    def __init__(self, on_loaded):
        self.speaker = None
        self.loaded = False
        self._on_loaded = on_loaded
        self._callbacks = {}  # utterance -> done
        Spiel.Speaker.new(None, self._on_ready)

    def _on_ready(self, _source, result):
        try:
            self.speaker = Spiel.Speaker.new_finish(result)
        except GLib.Error as error:
            log.info('Spiel is not available: %s', error.message)
        else:
            for signal in ('utterance-finished', 'utterance-canceled'):
                self.speaker.connect(signal, self._on_finished)
            self.speaker.connect('utterance-error', self._on_error)
        self.loaded = True
        self._on_loaded()

    def voices(self):
        if self.speaker is None:
            return []
        model = self.speaker.props.voices
        found = []
        for index in range(model.get_n_items()):
            voice = model.get_item(index)
            found.append(speech.Voice(self.name, voice.props.identifier, voice.props.name,
                                      list(voice.props.languages or []), voice))
        return found

    def speak(self, voice, lang, speed, text, done):
        utterance = Spiel.Utterance(text=text, voice=voice.handle,
                                    language=speech.language_tag(lang),
                                    rate=min(max(speed, 0.1), 10.0))
        self._callbacks[utterance] = done
        self.speaker.speak(utterance)

    def stop(self):
        self._callbacks.clear()
        if self.speaker is not None:
            self.speaker.cancel()

    def _on_finished(self, _speaker, utterance):
        done = self._callbacks.pop(utterance, None)
        if done is not None:
            done()

    def _on_error(self, _speaker, utterance, error):
        log.warning('Spiel could not speak: %s', error.message if error else 'unknown error')
        self._on_finished(_speaker, utterance)


class SpeechdBackend:
    name = 'speechd'

    def __init__(self, on_loaded):
        self.client = None
        self.loaded = False
        self._voices = []
        self._on_loaded = on_loaded
        self._generation = 0  # bumped by stop(): callbacks of older speech are dropped
        threading.Thread(target=self._load, name='retain-speechd', daemon=True).start()

    def _load(self):
        voices, client = [], None
        try:
            client = speechd.SSIPClient('retain')
            for module in client.list_output_modules() or []:
                client.set_output_module(module)
                for name, language, _variant in client.list_synthesis_voices() or []:
                    languages = MODULE_LANGUAGES.get(module) or (
                        [language] if language and language != 'none' else [])
                    voices.append(speech.Voice(self.name, f'{module}/{name}', name,
                                               languages, (module, name)))
        except Exception as error:  # noqa: BLE001 - no daemon, no socket: no backend
            log.info('Speech Dispatcher is not available: %s', error)
            if client is not None:
                client.close()
            client, voices = None, []
        GLib.idle_add(self._loaded, client, voices)

    def _loaded(self, client, voices):
        self.client, self._voices, self.loaded = client, voices, True
        self._on_loaded()
        return GLib.SOURCE_REMOVE

    def voices(self):
        return list(self._voices)

    def speak(self, voice, lang, speed, text, done):
        module, name = voice.handle
        generation = self._generation

        def on_event(_event_type, **_kwargs):  # Speech Dispatcher's thread
            GLib.idle_add(finish)

        def finish():
            if generation == self._generation:
                done()
            return GLib.SOURCE_REMOVE

        try:
            self.client.set_output_module(module)
            self.client.set_synthesis_voice(name)
            self.client.set_rate(int(min(max((speed - 1.0) * 100, -100), 100)))
            self.client.speak(text, callback=on_event, event_types=(
                speechd.CallbackType.END, speechd.CallbackType.CANCEL))
        except Exception:  # noqa: BLE001 - the daemon went away
            log.warning('Speech Dispatcher could not speak', exc_info=True)
            GLib.idle_add(finish)

    def stop(self):
        self._generation += 1
        if self.client is not None:
            try:
                self.client.cancel()
            except Exception:  # noqa: BLE001
                log.debug('cancelling Speech Dispatcher', exc_info=True)


class Speaker:

    def __init__(self, backends=None):
        self._pending = []  # speak() calls waiting for the voices
        if backends is None:
            backends = []
            if Spiel is not None:
                backends.append(SpielBackend(self._on_loaded))
            if speechd is not None:
                backends.append(SpeechdBackend(self._on_loaded))
        self.backends = backends
        self.fallback_lang = _user_language()

    @property
    def has_backend(self):
        return bool(self.backends)

    @property
    def loaded(self):
        return all(backend.loaded for backend in self.backends)

    def voices(self):
        found = []
        for backend in self.backends:
            found.extend(backend.voices())
        return found

    def voice_for(self, lang, preferred=()):
        return speech.choose_voice(self.voices(), lang, preferred, self.fallback_lang)

    def speak(self, lang, text, done, voices=(), speed=1.0, missing=None):
        """Speak `text` in `lang`; False (and `done` never called) when no voice suits it.
        While the voices are still loading, the call waits for them and answers True; if
        no voice suits it then, `missing(lang)` is called, then `done()`."""
        if not text.strip() or not self.backends:
            return False
        if not self.loaded:
            self._pending.append((lang, text, done, voices, speed, missing))
            GLib.timeout_add_seconds(LOAD_TIMEOUT, self._give_up)
            return True
        voice = self.voice_for(lang, voices)
        if voice is None:
            return False
        backend = next(each for each in self.backends if each.name == voice.backend)
        backend.speak(voice, lang, speed, text, done)
        return True

    def stop(self):
        self._pending = []
        for backend in self.backends:
            backend.stop()

    def _on_loaded(self):
        if not self.loaded:
            return
        pending, self._pending = self._pending, []
        for lang, text, done, voices, speed, missing in pending:
            if not self.speak(lang, text, done, voices, speed):
                if missing is not None:
                    missing(lang)
                done()

    def _give_up(self):
        if self._pending:
            log.info('speech voices did not load in time')
            pending, self._pending = self._pending, []
            for item in pending:
                item[2]()
        return GLib.SOURCE_REMOVE


_speaker = None


def get_speaker():
    global _speaker
    if _speaker is None:
        _speaker = Speaker()
    return _speaker
