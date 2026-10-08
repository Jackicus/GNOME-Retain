# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""AnkiConnect's API on localhost, so that card-mining tools (Yomitan, asbplayer, …) that
talk to Anki's AnkiConnect add-on add notes to Retain unchanged.

    api = Api(collection, browse=None, edit=None, added=None, key='')
    reply = api.handle({'action': 'deckNames', 'version': 6})   # a JSON-ready value
    server = Server(api, port=PORT)       # serves on 127.0.0.1 from a thread
    server.start()                        # raises OSError when the port is taken
    server.stop()
    origin_allowed(origin) -> bool        # the Origin header policy

The protocol is AnkiConnect's (version 6): a POST of `{"action", "version", "params", "key"}`
answered with `{"result", "error"}`; a request of version 4 or lower (Yomitan sends 2) is
answered with the bare result on success, and `{"result": null, "error": "…"}` on failure,
as AnkiConnect does. `multi` runs a list of such requests and answers a list of replies.
With a `key` set, every action but `requestPermission` must carry it.

The actions: version, requestPermission, apiReflect, sync (does nothing), deckNames,
deckNamesAndIds, createDeck, modelNames, modelNamesAndIds, modelFieldNames, findNotes,
findCards, notesInfo, cardsInfo, canAddNotes, canAddNotesWithErrorDetail, addNote, addNotes,
updateNoteFields, storeMediaFile (base64 `data` only: no paths or URLs), suspend, unsuspend,
areSuspended, guiBrowse and guiEditNote (through the `browse(query)` and `edit(note_id)`
callbacks), multi. A note's fields are matched to the note type's by name, ignoring case,
and unknown ones are dropped, as AnkiConnect does; `audio`, `video` and `picture` entries
with base64 `data` are stored and referenced in the fields they name. A duplicate is a note
of the same type (any type with `checkAllModels`) whose first field, HTML stripped, matches;
`duplicateScope: "deck"` limits that to the deck (and its subdecks with `checkChildren`).
Error messages are AnkiConnect's English (clients match on them), not translated. Each
added note is its own undo step; `added(note_ids)` is called after an action that
added any.

Who may call: the server listens on 127.0.0.1 only and refuses a request whose Host is not
the loopback address (DNS rebinding) or whose Origin is a web page's. A browser sends the
Origin of the page making the request, so a web site cannot reach the API; browser
extensions (chrome-extension://, moz-extension://, safari-web-extension://), http://localhost
and clients that send no Origin (scripts, local apps) can.

Threads: the server answers in a thread of its own and hands each request to the main loop
(GLib.idle_add), where the collection lives, waiting for the reply; Api itself runs on the
main thread and never sees a socket.
"""

import base64
import binascii
import http.server
import json
import logging
import threading
import urllib.parse
from gettext import gettext as _

from gi.repository import GLib

from . import search, template
from .collection import CollectionError

log = logging.getLogger(__name__)

PORT = 8765
HOST = '127.0.0.1'
VERSION = 6
MAX_BODY = 64 * 1024 * 1024    # bytes in one request (media comes base64-encoded)
REPLY_TIMEOUT = 30             # seconds the server waits for the main loop
EXTENSION_SCHEMES = ('chrome-extension://', 'moz-extension://', 'safari-web-extension://')
LOCAL_ORIGINS = ('http://localhost', 'http://127.0.0.1')
CARD_TYPES = {'new': 0, 'learning': 1, 'review': 2, 'relearning': 3}


class ApiError(Exception):
    """An action that cannot be done; the message is AnkiConnect's wording."""


def origin_allowed(origin):
    """Whether a request with this Origin header may use the API (None: no header)."""
    if origin is None:
        return True
    origin = origin.strip().lower()
    return origin.startswith(EXTENSION_SCHEMES) or origin in LOCAL_ORIGINS


def host_allowed(host):
    """Whether a Host header names the loopback address (not a rebound domain)."""
    if not host:
        return True
    name = urllib.parse.urlsplit(f'//{host}').hostname or ''
    return name in ('127.0.0.1', 'localhost', '::1')


class Api:

    def __init__(self, collection, browse=None, edit=None, added=None, key=''):
        self.collection = collection
        self.browse = browse
        self.edit = edit
        self.added = added
        self.key = key or ''
        self._added = []

    # -- the protocol --------------------------------------------------------------------

    def handle(self, request):
        """One request (a decoded JSON object) to its reply; `added` is told afterwards."""
        self._added = []
        reply = self._handle(request)
        if self._added and self.added is not None:
            self.added(list(self._added))
        return reply

    def _handle(self, request):
        version = 4
        try:
            if not isinstance(request, dict):
                raise ApiError('request must be an object')
            action = request.get('action', '')
            version = request.get('version', 4)
            params = request.get('params') or {}
            if self.key and action != 'requestPermission' and request.get('key') != self.key:
                raise ApiError('valid api key must be provided')
            method = ACTIONS.get(action)
            if method is None:
                raise ApiError('unsupported action')
            if not isinstance(params, dict):
                raise ApiError('params must be an object')
            result = method(self, **params)
        except TypeError as error:
            return _failure(f'invalid parameters: {error}')
        except (ApiError, CollectionError, search.SearchError) as error:
            return _failure(str(error))
        except Exception as error:  # noqa: BLE001 - a reply, never a crash of the server
            log.warning('AnkiConnect action failed', exc_info=True)
            return _failure(str(error))
        if isinstance(version, int) and version <= 4:
            return result
        return {'result': result, 'error': None}

    # -- information ---------------------------------------------------------------------

    def version(self):
        return VERSION

    def request_permission(self):
        return {'permission': 'granted', 'requireApiKey': bool(self.key), 'version': VERSION}

    def api_reflect(self, scopes=None, actions=None):
        names = sorted(ACTIONS)
        if actions is not None:
            names = [name for name in actions if name in ACTIONS]
        return {'scopes': ['actions'], 'actions': names}

    def sync(self):
        return None

    # -- decks and note types ------------------------------------------------------------

    def deck_names(self):
        return [deck.name for deck in self.collection.decks()]

    def deck_names_and_ids(self):
        return {deck.name: deck.id for deck in self.collection.decks()}

    def create_deck(self, deck):
        existing = self.collection.deck_by_name(deck)
        if existing is not None:
            return existing.id
        return self.collection.add_deck(deck).id

    def model_names(self):
        return [notetype.name for notetype in self.collection.notetypes()]

    def model_names_and_ids(self):
        return {notetype.name: notetype.id for notetype in self.collection.notetypes()}

    def model_field_names(self, modelName):
        return self._notetype(modelName).field_names()

    # -- finding -------------------------------------------------------------------------

    def find_notes(self, query):
        return self.collection.find_notes(query)

    def find_cards(self, query):
        return self.collection.find_cards(query)

    def notes_info(self, notes=None, query=None):
        if notes is None:
            notes = self.collection.find_notes(query or '')
        infos = []
        for note_id in notes:
            note = self.collection.note(note_id)
            if note is None:
                infos.append({})
                continue
            notetype = self.collection.notetype(note.notetype_id)
            infos.append({
                'noteId': note.id,
                'modelName': notetype.name,
                'tags': list(note.tags),
                'fields': _fields_info(notetype, note),
                'cards': [card.id for card in self.collection.cards_of_note(note.id)],
                'mod': note.modified,
            })
        return infos

    def cards_info(self, cards):
        infos = []
        for card_id in cards:
            card = self.collection.card(card_id)
            if card is None:
                infos.append({})
                continue
            note = self.collection.note(card.note_id)
            notetype = self.collection.notetype(note.notetype_id)
            deck = self.collection.deck(card.deck_id)
            question, answer = self.collection.render_card(card, note, notetype)
            infos.append({
                'cardId': card.id,
                'note': note.id,
                'deckName': deck.name if deck else '',
                'modelName': notetype.name,
                'fieldOrder': card.ord,
                'ord': card.ord,
                'fields': _fields_info(notetype, note),
                'question': question,
                'answer': answer,
                'css': notetype.css,
                'type': CARD_TYPES.get(card.state, 0),
                'queue': _queue(card),
                'due': card.due,
                'interval': round(card.interval or 0),
                'reps': card.reps,
                'lapses': card.lapses,
                'left': card.left,
                'flags': card.flag,
                'mod': card.modified,
            })
        return infos

    # -- adding and changing -------------------------------------------------------------

    def can_add_notes(self, notes):
        return [detail['canAdd'] for detail in self.can_add_notes_with_error_detail(notes)]

    def can_add_notes_with_error_detail(self, notes):
        details = []
        for note in notes:
            try:
                self._prepare(note)
            except (ApiError, CollectionError) as error:
                details.append({'canAdd': False, 'error': str(error)})
            else:
                details.append({'canAdd': True})
        return details

    def add_note(self, note):
        notetype, deck, fields, tags = self._prepare(note)
        fields = self._attach_media(note, notetype, fields)
        added = self.collection.add_note(notetype.id, deck.id, fields, tags)
        self._added.append(added.id)
        return added.id

    def add_notes(self, notes):
        """AnkiConnect's addNotes: a list of ids, None where a note could not be added.
        The notes added are one undo step."""
        ids = [None] * len(notes)
        prepared = []
        for index, note in enumerate(notes):
            try:
                prepared.append((index, note, self._prepare(note)))
            except (ApiError, CollectionError) as error:
                log.info('addNotes: note %d was not added: %s', index, error)
        if not prepared:
            return ids
        with self.collection.undoable(_('Add Notes')):
            for index, note, (notetype, deck, fields, tags) in prepared:
                options = note.get('options') or {}
                if not options.get('allowDuplicate', False) and self._is_duplicate(
                        notetype, fields[0], options):
                    continue  # a duplicate of a note added earlier in this batch
                fields = self._attach_media(note, notetype, fields)
                added = self.collection.add_note(notetype.id, deck.id, fields, tags)
                self._added.append(added.id)
                ids[index] = added.id
        return ids

    def update_note_fields(self, note):
        stored = self.collection.note(note.get('id'))
        if stored is None:
            raise ApiError('note was not found: {}'.format(note.get('id')))
        notetype = self.collection.notetype(stored.notetype_id)
        fields = _assign_fields(notetype, list(stored.fields), note.get('fields') or {})
        stored.fields = self._attach_media(note, notetype, fields)
        self.collection.update_note(stored)
        return None

    def store_media_file(self, filename, data=None, path=None, url=None,
                         deleteExisting=True, skipHash=None):
        if data is None:
            raise ApiError('only base64 data can be stored, not a path or a URL')
        return self.collection.media.add_bytes(_decode(data), filename)

    def suspend(self, cards):
        with self.collection.undoable(_('Suspend')):
            self.collection.suspend(cards)
        return True

    def unsuspend(self, cards):
        with self.collection.undoable(_('Unsuspend')):
            self.collection.unsuspend(cards)
        return True

    def are_suspended(self, cards):
        found = {card.id: card for card in self.collection.cards(cards)}
        return [bool(found[card_id].suspended) if card_id in found else None
                for card_id in cards]

    # -- the window ----------------------------------------------------------------------

    def gui_browse(self, query, reorderCards=None):
        found = self.collection.find_cards(query)
        if self.browse is not None:
            self.browse(query)
        return found

    def gui_edit_note(self, note):
        if self.collection.note(note) is None:
            raise ApiError(f'note was not found: {note}')
        if self.edit is not None:
            self.edit(note)
        return None

    def multi(self, actions):
        return [self._handle(action) for action in actions]

    # -- helpers -------------------------------------------------------------------------

    def _notetype(self, name):
        notetype = self.collection.notetype_by_name(name or '')
        if notetype is None:
            raise ApiError(f'model was not found: {name}')
        return notetype

    def _prepare(self, note):
        """Check a note as addNote would: (notetype, deck, fields, tags) or ApiError."""
        if not isinstance(note, dict):
            raise ApiError('note must be an object')
        notetype = self._notetype(note.get('modelName'))
        deck = self.collection.deck_by_name(note.get('deckName') or '')
        if deck is None:
            raise ApiError('deck was not found: {}'.format(note.get('deckName')))
        fields = _assign_fields(notetype, [''] * len(notetype.fields), note.get('fields') or {})
        if not template.strip_html(fields[0]).strip() and not _has_media_for(note, notetype, 0):
            raise ApiError('cannot create note because it is empty')
        named = dict(zip(notetype.field_names(), fields, strict=False))
        if not template.cards_for_note(notetype.kind, notetype.templates, named):
            raise ApiError('cannot create note because it would make no card')
        options = note.get('options') or {}
        if not options.get('allowDuplicate', False) and self._is_duplicate(
                notetype, fields[0], options):
            raise ApiError('cannot create note because it is a duplicate')
        tags = [tag for tag in note.get('tags') or [] if isinstance(tag, str) and tag.strip()]
        return notetype, deck, fields, tags

    def _is_duplicate(self, notetype, first_field, options):
        scope = options.get('duplicateScope')
        scope_options = options.get('duplicateScopeOptions') or {}
        types = (self.collection.notetypes() if scope_options.get('checkAllModels')
                 else [notetype])
        found = []
        for each in types:
            found.extend(self.collection.find_duplicates(each.id, first_field))
        if not found or scope != 'deck':
            return bool(found)
        deck = self.collection.deck_by_name(scope_options.get('deckName') or '')
        if deck is None:
            return True
        decks = ({deck.id} if not scope_options.get('checkChildren', False)
                 else set(self.collection.deck_and_children(deck.id)))
        return any(card.deck_id in decks
                   for note_id in found for card in self.collection.cards_of_note(note_id))

    def _attach_media(self, note, notetype, fields):
        """Store the note's audio, video and picture entries and add them to their fields."""
        names = [name.lower() for name in notetype.field_names()]
        for kind in ('audio', 'video', 'picture'):
            entries = note.get(kind) or []
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries:
                if entry.get('data') is None:
                    raise ApiError('only base64 data can be stored, not a path or a URL')
                name = self.collection.media.add_bytes(_decode(entry['data']),
                                                       entry.get('filename') or 'file')
                reference = (f'<img src="{name}">' if kind == 'picture' else f'[sound:{name}]')
                for field in entry.get('fields') or []:
                    if field.lower() in names:
                        index = names.index(field.lower())
                        fields[index] += reference
        return fields


ACTIONS = {
    'version': Api.version,
    'requestPermission': Api.request_permission,
    'apiReflect': Api.api_reflect,
    'sync': Api.sync,
    'deckNames': Api.deck_names,
    'deckNamesAndIds': Api.deck_names_and_ids,
    'createDeck': Api.create_deck,
    'modelNames': Api.model_names,
    'modelNamesAndIds': Api.model_names_and_ids,
    'modelFieldNames': Api.model_field_names,
    'findNotes': Api.find_notes,
    'findCards': Api.find_cards,
    'notesInfo': Api.notes_info,
    'cardsInfo': Api.cards_info,
    'canAddNotes': Api.can_add_notes,
    'canAddNotesWithErrorDetail': Api.can_add_notes_with_error_detail,
    'addNote': Api.add_note,
    'addNotes': Api.add_notes,
    'updateNoteFields': Api.update_note_fields,
    'storeMediaFile': Api.store_media_file,
    'suspend': Api.suspend,
    'unsuspend': Api.unsuspend,
    'areSuspended': Api.are_suspended,
    'guiBrowse': Api.gui_browse,
    'guiEditNote': Api.gui_edit_note,
    'multi': Api.multi,
}


def _failure(message):
    return {'result': None, 'error': message}


def _fields_info(notetype, note):
    """AnkiConnect's {name: {'value', 'order'}} for a note's fields."""
    pairs = zip(notetype.field_names(), note.fields, strict=False)
    return {name: {'value': value, 'order': order} for order, (name, value) in enumerate(pairs)}


def _queue(card):
    """Anki's queue number: -1 suspended, -3 buried, else by state."""
    if card.suspended:
        return -1
    if card.buried:
        return -3
    return {'new': 0, 'learning': 1, 'relearning': 1, 'review': 2}.get(card.state, 0)


def _assign_fields(notetype, values, given):
    """`given` ({name: value}) onto `values` by the note type's field names, ignoring case."""
    names = [name.lower() for name in notetype.field_names()]
    for name, value in given.items():
        if isinstance(name, str) and name.lower() in names and isinstance(value, str):
            values[names.index(name.lower())] = value
    return values


def _has_media_for(note, notetype, index):
    """Whether one of the note's media entries goes into field `index`."""
    wanted = notetype.field_names()[index].lower()
    for kind in ('audio', 'video', 'picture'):
        entries = note.get(kind) or []
        for entry in [entries] if isinstance(entries, dict) else entries:
            if any(isinstance(f, str) and f.lower() == wanted for f in entry.get('fields') or []):
                return True
    return False


def _decode(data):
    try:
        return base64.b64decode(data, validate=False)
    except (binascii.Error, TypeError, ValueError) as error:
        raise ApiError('invalid base64 data') from error


# The server


class Server:
    """AnkiConnect's HTTP endpoint on 127.0.0.1, answering through `api` on the main loop.
    `dispatch(function, request)` runs the function on the main thread and returns its
    value; by default through GLib.idle_add."""

    def __init__(self, api, port=PORT, dispatch=None):
        self.api = api
        self.port = port
        self.dispatch = dispatch or _dispatch_to_main_loop
        self._httpd = None
        self._thread = None

    def start(self):
        server = self

        class Handler(_Handler):
            owner = server

        self._httpd = http.server.HTTPServer((HOST, self.port), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name='retain-ankiconnect', daemon=True)
        self._thread.start()
        log.info('AnkiConnect API listening on %s:%d', HOST, self.port)

    def stop(self):
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        self._httpd = self._thread = None
        log.info('AnkiConnect API stopped')

    @property
    def running(self):
        return self._httpd is not None


class _Handler(http.server.BaseHTTPRequestHandler):
    owner = None  # the Server, set on a subclass
    server_version = 'Retain-AnkiConnect'

    def log_message(self, format, *args):
        log.debug('%s %s', self.address_string(), format % args)

    def _allowed(self):
        origin = self.headers.get('Origin')
        if not host_allowed(self.headers.get('Host')) or not origin_allowed(origin):
            log.info('AnkiConnect request refused: Origin %s, Host %s', origin,
                     self.headers.get('Host'))
            self._send(403, b'forbidden', 'text/plain')
            return False
        return True

    def _send(self, status, body, content_type='application/json'):
        self.send_response(status)
        origin = self.headers.get('Origin')
        if origin and origin_allowed(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._allowed():
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin') or '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        if self._allowed():
            self._send(200, f'AnkiConnect v.{VERSION}'.encode(), 'text/plain')

    def do_POST(self):
        if not self._allowed():
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._send(413, b'request too large', 'text/plain')
            return
        body = self.rfile.read(length)
        try:
            request = json.loads(body or b'{}')
        except ValueError:
            reply = _failure('request is not valid JSON')
        else:
            try:
                reply = self.owner.dispatch(self.owner.api.handle, request)
            except TimeoutError:
                reply = _failure('Retain did not answer in time')
        self._send(200, json.dumps(reply, ensure_ascii=False).encode())


def _dispatch_to_main_loop(function, request):
    """Run function(request) on the main loop and wait for its value."""
    done = threading.Event()
    box = {}

    def run():
        try:
            box['value'] = function(request)
        except Exception as error:  # noqa: BLE001 - reported to the client
            log.warning('AnkiConnect request failed', exc_info=True)
            box['value'] = _failure(str(error))
        done.set()
        return GLib.SOURCE_REMOVE

    GLib.idle_add(run)
    if not done.wait(REPLY_TIMEOUT):
        raise TimeoutError
    return box['value']
