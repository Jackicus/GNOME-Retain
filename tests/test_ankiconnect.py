# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""ankiconnect.py: the AnkiConnect protocol, the actions a card-mining tool uses, the
origin policy, and the HTTP server answering through the main loop."""

from tests import ROOT  # noqa: F401  (registers src/ as retain)

import base64
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from tests.support import add_basic, temporary_collection

from retain.ankiconnect import Api, Server, host_allowed, origin_allowed

PNG = base64.b64encode(b'\x89PNG\r\n\x1a\n invented picture').decode()
MP3 = base64.b64encode(b'ID3 invented sound').decode()


def note(front, back='', deck='Japanese', model='Basic', **extra):
    return {'deckName': deck, 'modelName': model, 'fields': {'Front': front, 'Back': back},
            'tags': ['mined'], **extra}


class ProtocolTests(unittest.TestCase):

    def setUp(self):
        self.context = temporary_collection()
        self.collection = self.context.__enter__()
        self.collection.add_deck('Japanese')
        self.api = Api(self.collection)

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def test_old_versions_get_the_bare_result(self):
        self.assertEqual(self.api.handle({'action': 'version', 'version': 2}), 6)
        self.assertEqual(self.api.handle({'action': 'version'}), 6)

    def test_version_6_wraps_the_result(self):
        self.assertEqual(self.api.handle({'action': 'version', 'version': 6}),
                         {'result': 6, 'error': None})

    def test_errors_carry_a_message_at_every_version(self):
        for version in (2, 6):
            reply = self.api.handle({'action': 'noSuchAction', 'version': version})
            self.assertEqual(reply, {'result': None, 'error': 'unsupported action'})
        reply = self.api.handle({'action': 'deckNames', 'params': {'wrong': 1}, 'version': 6})
        self.assertIn('invalid parameters', reply['error'])

    def test_multi_answers_each_action_at_its_own_version(self):
        reply = self.api.handle({'action': 'multi', 'version': 6, 'params': {'actions': [
            {'action': 'version'}, {'action': 'deckNames', 'version': 6},
            {'action': 'modelFieldNames', 'params': {'modelName': 'Nope'}}]}})
        results = reply['result']
        self.assertEqual(results[0], 6)
        self.assertIn('Japanese', results[1]['result'])
        self.assertEqual(results[2]['error'], 'model was not found: Nope')

    def test_a_key_is_required_once_set(self):
        api = Api(self.collection, key='invented-key')
        reply = api.handle({'action': 'deckNames', 'version': 6})
        self.assertEqual(reply['error'], 'valid api key must be provided')
        reply = api.handle({'action': 'deckNames', 'version': 6, 'key': 'invented-key'})
        self.assertIsNone(reply['error'])
        reply = api.handle({'action': 'requestPermission', 'version': 6})
        self.assertEqual(reply['result'],
                         {'permission': 'granted', 'requireApiKey': True, 'version': 6})

    def test_api_reflect_lists_the_actions_asked_about(self):
        reply = self.api.handle({'action': 'apiReflect', 'version': 6, 'params': {
            'scopes': ['actions'], 'actions': ['guiEditNote', 'removeEverything']}})
        self.assertEqual(reply['result'], {'scopes': ['actions'], 'actions': ['guiEditNote']})


class MiningTests(unittest.TestCase):
    """The calls Yomitan makes, in its order."""

    def setUp(self):
        self.context = temporary_collection()
        self.collection = self.context.__enter__()
        self.deck = self.collection.add_deck('Japanese')
        self.added = []
        self.api = Api(self.collection, added=self.added.extend)

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def call(self, action, **params):
        reply = self.api.handle({'action': action, 'version': 6, 'params': params})
        self.assertIsNone(reply['error'], reply['error'])
        return reply['result']

    def test_decks_and_note_types(self):
        self.assertIn('Japanese', self.call('deckNames'))
        self.assertIn('Basic', self.call('modelNames'))
        self.assertEqual(self.call('modelFieldNames', modelName='basic'), ['Front', 'Back'])
        self.assertEqual(self.call('deckNamesAndIds')['Japanese'], self.deck.id)

    def test_add_a_note_and_find_it_again(self):
        self.assertEqual(self.call('canAddNotes', notes=[note('猫')]), [True])
        note_id = self.call('addNote', note=note('猫', 'cat'))
        self.assertEqual(self.added, [note_id])
        stored = self.collection.note(note_id)
        self.assertEqual(stored.fields, ['猫', 'cat'])
        self.assertEqual(stored.tags, ['mined'])
        # Yomitan's duplicate query: the deck, then the first field by name.
        self.assertEqual(self.call('findNotes', query='"deck:Japanese" "front:猫"'), [note_id])
        info = self.call('notesInfo', notes=[note_id, 999])
        self.assertEqual(info[0]['modelName'], 'Basic')
        self.assertEqual(info[0]['fields']['Back'], {'value': 'cat', 'order': 1})
        self.assertEqual(len(info[0]['cards']), 1)
        self.assertEqual(info[1], {})

    def test_each_added_note_is_an_undo_step(self):
        note_id = self.call('addNote', note=note('犬', 'dog'))
        self.assertIsNotNone(self.collection.note(note_id))
        self.collection.undo()
        self.assertIsNone(self.collection.note(note_id))

    def test_field_names_ignore_case_and_unknown_ones_are_dropped(self):
        note_id = self.call('addNote', note={
            'deckName': 'japanese', 'modelName': 'Basic',
            'fields': {'front': '鳥', 'BACK': 'bird', 'Reading': 'とり'}})
        self.assertEqual(self.collection.note(note_id).fields, ['鳥', 'bird'])

    def test_why_a_note_cannot_be_added(self):
        add_basic(self.collection, self.deck, '<b>魚</b>', 'fish')
        details = self.call('canAddNotesWithErrorDetail', notes=[
            note('魚'), note(''), note('木', deck='Nowhere'), note('木', model='Nope'),
            note('魚', options={'allowDuplicate': True})])
        self.assertEqual(details, [
            {'canAdd': False, 'error': 'cannot create note because it is a duplicate'},
            {'canAdd': False, 'error': 'cannot create note because it is empty'},
            {'canAdd': False, 'error': 'deck was not found: Nowhere'},
            {'canAdd': False, 'error': 'model was not found: Nope'},
            {'canAdd': True},
        ])
        cloze = {'deckName': 'Japanese', 'modelName': 'Cloze', 'fields': {'Text': 'no cloze'}}
        self.assertEqual(self.call('canAddNotesWithErrorDetail', notes=[cloze])[0]['error'],
                         'cannot create note because it would make no card')
        reply = self.api.handle({'action': 'addNote', 'version': 6,
                                 'params': {'note': note('魚')}})
        self.assertEqual(reply['error'], 'cannot create note because it is a duplicate')

    def test_a_deck_scope_only_counts_duplicates_in_that_deck(self):
        other = self.collection.add_deck('Other')
        add_basic(self.collection, other, '山', 'mountain')
        in_deck = {'duplicateScope': 'deck',
                   'duplicateScopeOptions': {'deckName': 'Japanese'}}
        self.assertEqual(self.call('canAddNotes', notes=[note('山', options=in_deck)]), [True])
        self.assertEqual(self.call('canAddNotes', notes=[note('山')]), [False])
        child = self.collection.add_deck('Japanese::Words')
        add_basic(self.collection, child, '川', 'river')
        with_children = {'duplicateScope': 'deck', 'duplicateScopeOptions': {
            'deckName': 'Japanese', 'checkChildren': True}}
        self.assertEqual(self.call('canAddNotes', notes=[note('川', options=in_deck),
                                                         note('川', options=with_children)]),
                         [True, False])

    def test_add_notes_answers_none_for_the_ones_it_could_not_add(self):
        ids = self.call('addNotes', notes=[note('一'), note('一'), note('二')])
        self.assertIsInstance(ids[0], int)
        self.assertIsNone(ids[1])
        self.assertIsInstance(ids[2], int)
        self.collection.undo()  # the batch is one step
        self.assertEqual(self.collection.note_count(), 0)

    def test_store_media_and_refer_to_it(self):
        name = self.call('storeMediaFile', filename='yomitan_neko.png', data=PNG)
        self.assertEqual(name, 'yomitan_neko.png')
        self.assertTrue(self.collection.media.exists(name))
        reply = self.api.handle({'action': 'storeMediaFile', 'version': 6, 'params': {
            'filename': 'x.png', 'path': '/etc/passwd'}})
        self.assertIn('only base64 data', reply['error'])

    def test_pictures_and_sounds_attached_to_a_note(self):
        note_id = self.call('addNote', note=note('猫', 'cat', picture=[
            {'filename': 'cat.png', 'data': PNG, 'fields': ['Back']}], audio=[
            {'filename': 'neko.mp3', 'data': MP3, 'fields': ['front']}]))
        front, back = self.collection.note(note_id).fields
        self.assertEqual(back, 'cat<img src="cat.png">')
        self.assertEqual(front, '猫[sound:neko.mp3]')

    def test_update_note_fields(self):
        note_id = self.call('addNote', note=note('月', 'moon'))
        self.call('updateNoteFields', note={'id': note_id, 'fields': {'Back': 'moon; month'}})
        self.assertEqual(self.collection.note(note_id).fields, ['月', 'moon; month'])

    def test_cards_info_and_suspending(self):
        note_id = self.call('addNote', note=note('火', 'fire'))
        card_id = self.call('findCards', query=f'nid:{note_id}')[0]
        info = self.call('cardsInfo', cards=[card_id])[0]
        self.assertEqual((info['note'], info['deckName'], info['type'], info['queue']),
                         (note_id, 'Japanese', 0, 0))
        self.assertIn('火', info['question'])
        self.call('suspend', cards=[card_id])
        self.assertEqual(self.call('areSuspended', cards=[card_id, 1]), [True, None])
        self.assertEqual(self.call('cardsInfo', cards=[card_id])[0]['queue'], -1)

    def test_the_window_is_asked_to_browse_and_edit(self):
        browsed, edited = [], []
        api = Api(self.collection, browse=browsed.append, edit=edited.append)
        note_id = self.call('addNote', note=note('水', 'water'))
        reply = api.handle({'action': 'guiBrowse', 'params': {'query': f'nid:{note_id}'}})
        self.assertEqual(len(reply), 1)
        api.handle({'action': 'guiEditNote', 'params': {'note': note_id}})
        self.assertEqual((browsed, edited), ([f'nid:{note_id}'], [note_id]))


class PolicyTests(unittest.TestCase):

    def test_web_pages_are_refused_and_extensions_allowed(self):
        self.assertTrue(origin_allowed(None))
        self.assertTrue(origin_allowed('chrome-extension://invented'))
        self.assertTrue(origin_allowed('moz-extension://invented-uuid'))
        self.assertTrue(origin_allowed('http://localhost'))
        self.assertFalse(origin_allowed('https://example.com'))
        self.assertFalse(origin_allowed('http://localhost.example.com'))
        self.assertFalse(origin_allowed('null'))

    def test_only_the_loopback_host(self):
        self.assertTrue(host_allowed('127.0.0.1:8765'))
        self.assertTrue(host_allowed('localhost:8765'))
        self.assertTrue(host_allowed(None))
        self.assertFalse(host_allowed('rebound.example.com:8765'))


class ServerTests(unittest.TestCase):
    """A real round trip: the request in a thread, the main loop iterated here."""

    def setUp(self):
        self.context = temporary_collection()
        self.collection = self.context.__enter__()
        self.collection.add_deck('Japanese')
        self.server = Server(Api(self.collection), port=0)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.context.__exit__(None, None, None)

    def post(self, body, headers=None):
        from gi.repository import GLib

        url = f'http://127.0.0.1:{self.server.port}'
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                         headers={'Content-Type': 'application/json',
                                                  **(headers or {})})
        box = {}

        def send():
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    box['status'] = response.status
                    box['body'] = json.loads(response.read())
                    box['allow'] = response.headers.get('Access-Control-Allow-Origin')
            except urllib.error.HTTPError as error:
                box['status'] = error.code
                error.close()

        thread = threading.Thread(target=send)
        thread.start()
        deadline = time.monotonic() + 10
        while thread.is_alive() and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        thread.join()
        return box

    def test_a_request_is_answered_on_the_main_loop(self):
        reply = self.post({'action': 'addNote', 'version': 6, 'params': {'note': note('本')}})
        self.assertEqual(reply['status'], 200)
        self.assertIsNone(reply['body']['error'])
        self.assertEqual(self.collection.note(reply['body']['result']).fields[0], '本')

    def test_an_extension_gets_its_origin_back(self):
        reply = self.post({'action': 'version', 'version': 2},
                          {'Origin': 'chrome-extension://invented'})
        self.assertEqual((reply['status'], reply['body'], reply['allow']),
                         (200, 6, 'chrome-extension://invented'))

    def test_a_web_page_is_refused(self):
        reply = self.post({'action': 'deckNames', 'version': 6},
                          {'Origin': 'https://example.com'})
        self.assertEqual(reply['status'], 403)

    def test_the_port_in_use_is_an_error(self):
        second = Server(Api(self.collection), port=self.server.port)
        with self.assertRaises(OSError):
            second.start()


if __name__ == '__main__':
    unittest.main()
