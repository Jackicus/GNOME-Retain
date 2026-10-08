# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""sync.py and the graves behind it: two or three collections on temporary paths syncing
through a temporary folder, as a file-sync tool would keep it the same on every computer.
The clock is invented (time.time patched), so 'newer' is certain and no two ids collide."""

import json
import pathlib
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from tests.support import add_basic, add_cloze
from retain import schema, sync
from retain.collection import Collection, card_key
from retain.deck_config import DeckConfig
from retain.scheduler import Scheduler

PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(24))
OTHER_PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(24, 0, -1))


class Clock:
    """time.time for the tests: moves a millisecond each time it is read, and on with
    advance()."""

    def __init__(self):
        self.now = 1_780_000_000.0

    def __call__(self):
        self.now += 0.001
        return self.now

    def advance(self, seconds=10):
        self.now += seconds


class SyncTestCase(unittest.TestCase):

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix='retain-sync-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.folder = self.root / 'shared'
        self.folder.mkdir()
        self.clock = Clock()
        patcher = mock.patch('time.time', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.devices = {}

    def device(self, name):
        """A collection of its own, as on another computer."""
        collection = Collection(self.root / name / 'collection.sqlite', backups=False)
        self.addCleanup(collection.close)
        self.devices[name] = collection
        return collection

    def sync(self, name, **kwargs):
        self.clock.advance()
        kwargs.setdefault('backup', False)
        result = sync.sync(self.devices[name], self.folder, name, **kwargs)
        self.clock.advance()
        return result

    def sync_all(self, *names):
        """Each device syncs, then each again: what a few rounds of syncing come to."""
        names = names or tuple(self.devices)
        for name in names + names:
            self.sync(name)

    def assertSameCollections(self, *names):
        names = names or tuple(self.devices)
        states = [state(self.devices[name]) for name in names]
        for other in states[1:]:
            self.assertEqual(states[0], other)


def state(collection):
    """What a collection holds, by identities every device shares."""
    db = collection.db
    notetypes = {row['id']: row['name'] for row in db.execute('SELECT id, name FROM notetypes')}
    decks = {row['id']: row['name'] for row in db.execute('SELECT id, name FROM decks')}
    notes = {row['guid']: (notetypes[row['notetype_id']], row['fields'], row['tags'],
                           row['marked'])
             for row in db.execute('SELECT * FROM notes')}
    cards = {card_key(row['guid'], row['ord']): (
        decks[row['deck_id']], row['state'], row['due'], row['reps'], row['flag'],
        row['suspended'], round(row['stability'], 6))
        for row in db.execute('SELECT cards.*, notes.guid FROM cards JOIN notes '
                              'ON notes.id = cards.note_id')}
    reviews = sorted((row['id'], row['rating']) for row in db.execute('SELECT * FROM revlog'))
    presets = sorted((row['name'], row['data']) for row in db.execute(
        'SELECT name, data FROM deck_configs'))
    return {'notetypes': sorted(notetypes.values()), 'decks': sorted(decks.values()),
            'notes': notes, 'cards': cards, 'reviews': reviews, 'presets': presets}


def note_by_guid(collection, guid):
    row = collection.db.execute('SELECT id FROM notes WHERE guid = ?', (guid,)).fetchone()
    return collection.note(row[0]) if row else None


class MergeTest(SyncTestCase):

    def test_a_new_device_gets_everything_and_keeps_the_first_ones_note_types(self):
        laptop = self.device('laptop')
        deck = laptop.add_deck('Spanish::Verbs')
        add_basic(laptop, deck, 'hablar', 'to speak', ['verbs'])
        add_cloze(laptop, deck, '{{c1::comer}} is to eat')
        basic = laptop.notetype_by_name('Basic')
        basic.css = '.card { color: teal; }'
        laptop.update_notetype(basic)
        self.sync('laptop')
        self.clock.advance(3600)
        desktop = self.device('desktop')  # made later: its stock types are untouched
        result = self.sync('desktop')
        self.assertEqual(result.notes_added, 2)
        self.assertEqual(result.cards_added, 2)
        self.assertEqual(result.sources, ['laptop'])
        self.assertEqual(desktop.notetype_by_name('Basic').css, '.card { color: teal; }')
        self.assertEqual(desktop.notetype_by_name('Basic').id, basic.id)  # one id now
        self.assertIsNotNone(desktop.deck_by_name('Spanish::Verbs'))
        self.sync('laptop')
        self.assertEqual(laptop.notetype_by_name('Basic').css, '.card { color: teal; }')
        self.assertSameCollections()
        self.assertEqual(sync.describe_result(result),
                         'Synced: 2 cards, 2 notes, 2 decks, 1 note type from laptop')

    def test_edits_on_both_sides_merge(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        add_basic(laptop, laptop.decks()[0], 'uno', 'one')
        add_basic(desktop, desktop.decks()[0], 'dos', 'two')
        self.sync_all()
        self.assertEqual(laptop.note_count(), 2)
        self.assertEqual(desktop.note_count(), 2)
        self.assertSameCollections()

    def test_a_newer_edit_wins(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_basic(laptop, laptop.decks()[0], 'tres', 'three')
        self.sync_all()
        theirs = note_by_guid(desktop, note.guid)
        theirs.fields = ['tres', 'THREE (desktop)']
        desktop.update_note(theirs)
        self.clock.advance(60)
        ours = laptop.note(note.id)
        ours.fields = ['tres', 'three (laptop, later)']
        laptop.update_note(ours)
        self.sync_all()
        self.assertEqual(note_by_guid(desktop, note.guid).fields[1], 'three (laptop, later)')
        self.assertEqual(laptop.note(note.id).fields[1], 'three (laptop, later)')
        self.assertSameCollections()

    def test_a_tie_is_decided_alike_on_both_sides(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_basic(laptop, laptop.decks()[0], 'cuatro', 'four')
        self.sync_all()
        for collection, text in ((laptop, 'left'), (desktop, 'right')):
            record = note_by_guid(collection, note.guid)
            record.fields = ['cuatro', text]
            collection.update_note(record)
            collection.db.execute('UPDATE notes SET modified = 5 WHERE guid = ?', (note.guid,))
        self.sync_all()
        self.assertSameCollections()

    def test_reviews_from_both_sides_are_all_kept(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_basic(laptop, laptop.decks()[0], 'cinco', 'five')
        self.sync_all()
        card = laptop.cards_of_note(note.id)[0]
        Scheduler(laptop).answer(card, 3, now=self.clock())
        self.clock.advance(120)
        their_card = desktop.cards_of_note(note_by_guid(desktop, note.guid).id)[0]
        after = Scheduler(desktop).answer(their_card, 1, now=self.clock())
        self.sync_all()
        for collection in (laptop, desktop):
            ratings = [review['rating'] for review in collection.db.execute(
                'SELECT rating FROM revlog ORDER BY id')]
            self.assertEqual(ratings, [3, 1])
        # The later answer's scheduling is the card's on both.
        self.assertEqual(laptop.card(card.id).state, after.state)
        self.assertGreater(laptop.card(card.id).modified, card.modified)
        self.assertSameCollections()

    def test_new_decks_note_types_and_presets_travel(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        preset = laptop.add_deck_config(DeckConfig(name='Hard Words', new_per_day=5))
        deck = laptop.add_deck('Latin')
        laptop.set_deck_config(deck.id, preset.id)
        vocab = laptop.notetype_by_name('Basic').copy()
        vocab.id, vocab.name, vocab.css = None, 'Vocabulary', '.card { color: navy; }'
        laptop.add_notetype(vocab)
        laptop.add_note(vocab.id, deck.id, ['amo', 'I love'])
        self.sync_all()
        theirs = desktop.deck_by_name('Latin')
        self.assertIsNotNone(theirs)
        self.assertEqual(desktop.config_for_deck(theirs.id).name, 'Hard Words')
        self.assertEqual(desktop.config_for_deck(theirs.id).new_per_day, 5)
        self.assertEqual(desktop.notetype_by_name('Vocabulary').css, '.card { color: navy; }')
        self.assertEqual(desktop.note_count(), 1)
        self.assertSameCollections()
        # A preset changed on the other side comes back.
        config = desktop.config_for_deck(theirs.id)
        config.new_per_day = 9
        desktop.update_deck_config(config)
        self.sync_all()
        self.assertEqual(laptop.deck_config(preset.id).new_per_day, 9)

    def test_decks_made_apart_with_the_same_name_become_one(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        add_basic(laptop, laptop.add_deck('French'), 'chat', 'cat')
        self.clock.advance()
        add_basic(desktop, desktop.add_deck('French'), 'chien', 'dog')
        self.sync_all()
        self.assertEqual(laptop.deck_by_name('French').id, desktop.deck_by_name('French').id)
        self.assertEqual(laptop.deck_card_count(laptop.deck_by_name('French').id), 2)
        self.assertSameCollections()

    def test_a_renamed_field_carries_the_notes_across(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_basic(laptop, laptop.decks()[0], 'seis', 'six')
        self.sync_all()
        basic = desktop.notetype_by_name('Basic')
        basic.fields = [{'name': 'Back', 'description': ''}, {'name': 'Front',
                                                               'description': ''}]
        desktop.update_notetype(basic)
        self.sync_all()
        self.assertEqual(laptop.notetype_by_name('Basic').field_names(), ['Back', 'Front'])
        self.assertEqual(laptop.note(note.id).fields, ['six', 'seis'])
        self.assertSameCollections()

    def test_syncing_twice_is_idempotent(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        add_basic(laptop, laptop.decks()[0], 'siete', 'seven')
        add_basic(desktop, desktop.add_deck('Other'), 'ocho', 'eight')
        self.sync_all()
        before = {name: state(collection) for name, collection in self.devices.items()}
        for name in ('laptop', 'desktop'):
            result = self.sync(name)
            self.assertFalse(result.changed(), result)
            self.assertFalse(result.written)
            self.assertEqual(result.merged, 0)
            self.assertEqual(sync.describe_result(result),
                             'Synced: nothing new from other devices')
        # Merging an unchanged snapshot again (the seen list forgotten) changes nothing.
        laptop.set(sync.SEEN_KEY, {})
        result = self.sync('laptop')
        self.assertEqual(result.merged, 1)
        self.assertFalse(result.changed(), result)
        self.assertEqual(before, {name: state(collection)
                                  for name, collection in self.devices.items()})

    def test_three_devices_converge(self):
        a, b, c = self.device('a'), self.device('b'), self.device('c')
        add_basic(a, a.decks()[0], 'nueve', 'nine')
        add_basic(b, b.decks()[0], 'diez', 'ten')
        add_basic(c, c.add_deck('C deck'), 'once', 'eleven')
        self.sync_all()
        self.assertEqual(a.note_count(), 3)
        self.assertSameCollections()


class DeletionTest(SyncTestCase):

    def test_a_deletion_propagates_and_does_not_come_back(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_basic(laptop, laptop.decks()[0], 'doce', 'twelve')
        keep = add_basic(laptop, laptop.decks()[0], 'trece', 'thirteen')
        self.sync_all()
        laptop.remove_notes([note.id])
        self.assertEqual([grave['key'] for grave in laptop.graves('note')], [note.guid])
        self.sync('laptop')
        result = self.sync('desktop')
        self.assertEqual(result.notes_removed, 1)
        self.assertEqual(result.cards_removed, 1)
        self.assertIsNone(note_by_guid(desktop, note.guid))
        self.assertIsNotNone(note_by_guid(desktop, keep.guid))
        # The desktop's old snapshot still has it, the laptop's grave keeps it out.
        self.sync_all()
        self.assertIsNone(note_by_guid(laptop, note.guid))
        self.assertIsNone(note_by_guid(desktop, note.guid))
        self.assertEqual(desktop.graves('note')[0]['key'], note.guid)
        self.assertSameCollections()

    def test_an_edit_after_the_deletion_brings_it_back_everywhere(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_basic(laptop, laptop.decks()[0], 'catorce', 'fourteen')
        self.sync_all()
        laptop.remove_notes([note.id])
        self.clock.advance(60)
        theirs = note_by_guid(desktop, note.guid)
        theirs.fields = ['catorce', 'fourteen, kept']
        desktop.update_note(theirs)
        self.sync_all()
        self.assertEqual(note_by_guid(laptop, note.guid).fields[1], 'fourteen, kept')
        self.assertSameCollections()

    def test_an_undone_deletion_leaves_no_grave(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_basic(laptop, laptop.decks()[0], 'quince', 'fifteen')
        deck = laptop.add_deck('Spare')
        self.sync_all()
        laptop.remove_notes([note.id])
        laptop.remove_deck(deck.id)
        self.assertTrue(laptop.graves())
        laptop.undo()
        laptop.undo()
        self.assertEqual(laptop.graves(), [])
        self.sync_all()
        self.assertIsNotNone(note_by_guid(desktop, note.guid))
        self.assertIsNotNone(desktop.deck_by_name('Spare'))

    def test_undo_puts_an_older_grave_back(self):
        laptop = self.device('laptop')
        note = add_basic(laptop, laptop.decks()[0], 'dieciséis', 'sixteen')
        laptop.db.execute("INSERT INTO graves (kind, key, name, deleted) "
                          "VALUES ('note', ?, '', 7)", (note.guid,))
        laptop.remove_notes([note.id])
        self.assertGreater(laptop.graves('note')[0]['deleted'], 7)
        laptop.undo()
        self.assertEqual(laptop.graves('note')[0]['deleted'], 7)

    def test_a_deleted_card_propagates(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        note = add_cloze(laptop, laptop.decks()[0], '{{c1::uno}} y {{c2::dos}}')
        self.sync_all()
        record = laptop.note(note.id)
        record.fields[0] = '{{c1::uno}} y dos'
        laptop.update_note(record)
        self.assertEqual([grave['key'] for grave in laptop.graves('card')],
                         [card_key(note.guid, 1)])
        self.sync_all()
        theirs = note_by_guid(desktop, note.guid)
        self.assertEqual([card.ord for card in desktop.cards_of_note(theirs.id)], [0])
        self.assertSameCollections()

    def test_a_deleted_deck_note_type_and_preset_propagate(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        deck = laptop.add_deck('Gone::Child')
        add_basic(laptop, deck, 'diecisiete', 'seventeen')
        preset = laptop.add_deck_config(DeckConfig(name='Short'))
        laptop.set_deck_config(laptop.deck_by_name('Gone').id, preset.id)
        extra = laptop.notetype_by_name('Basic').copy()
        extra.id, extra.name = None, 'Extra'
        laptop.add_notetype(extra)
        self.sync_all()
        self.assertIsNotNone(desktop.deck_by_name('Gone::Child'))
        laptop.remove_deck(laptop.deck_by_name('Gone').id)
        laptop.remove_notetype(extra.id)
        laptop.remove_deck_config(preset.id)
        self.sync_all()
        self.assertIsNone(desktop.deck_by_name('Gone'))
        self.assertIsNone(desktop.deck_by_name('Gone::Child'))
        self.assertIsNone(desktop.notetype_by_name('Extra'))
        self.assertNotIn('Short', [config.name for config in desktop.deck_configs()])
        self.assertEqual(desktop.note_count(), 0)
        self.assertSameCollections()

    def test_a_card_added_after_its_deck_was_deleted_moves_to_default(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        deck = laptop.add_deck('Moving')
        note = add_basic(laptop, deck, 'dieciocho', 'eighteen')
        self.sync_all()
        laptop.remove_deck(deck.id)
        self.clock.advance(60)
        # A review is no edit of the note: the deletion wins over it.
        theirs = desktop.cards_of_note(note_by_guid(desktop, note.guid).id)[0]
        Scheduler(desktop).answer(theirs, 3, now=self.clock())
        later = add_basic(desktop, desktop.deck_by_name('Moving'), 'diecinueve', 'nineteen')
        self.sync_all()
        for collection in (laptop, desktop):
            self.assertIsNone(collection.deck_by_name('Moving'))
            self.assertIsNone(note_by_guid(collection, note.guid))
            card = collection.cards_of_note(note_by_guid(collection, later.guid).id)[0]
            self.assertEqual(collection.deck(card.deck_id).name, 'Default')
        self.assertSameCollections()


class MediaTest(SyncTestCase):

    def test_media_travels_both_ways(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        name = laptop.media.add_bytes(PNG, 'cat.png')
        add_basic(laptop, laptop.decks()[0], f'<img src="{name}">', 'cat')
        other = desktop.media.add_bytes(OTHER_PNG, 'dog.png')
        self.sync_all()
        self.assertEqual(desktop.media.path('cat.png').read_bytes(), PNG)
        self.assertEqual(laptop.media.path(other).read_bytes(), OTHER_PNG)
        self.assertEqual((self.folder / 'media' / 'cat.png').read_bytes(), PNG)
        self.assertEqual(sorted(laptop.media.names()), sorted(desktop.media.names()))

    def test_a_name_clash_keeps_both_files(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        laptop.media.add_bytes(PNG, 'map.png')
        mine = add_basic(laptop, laptop.decks()[0], '<img src="map.png">', 'laptop map')
        desktop.media.add_bytes(OTHER_PNG, 'map.png')
        theirs = add_basic(desktop, desktop.decks()[0], '<img src="map.png">', 'desktop map')
        self.sync('laptop')
        result = self.sync('desktop')
        self.assertEqual(result.media_renamed, 1)
        self.sync_all()
        for collection in (laptop, desktop):
            self.assertEqual(note_by_guid(collection, mine.guid).fields[0],
                             '<img src="map.png">')
            self.assertEqual(note_by_guid(collection, theirs.guid).fields[0],
                             '<img src="map-2.png">')
            self.assertEqual(collection.media.path('map.png').read_bytes(), PNG)
            self.assertEqual(collection.media.path('map-2.png').read_bytes(), OTHER_PNG)
        self.assertSameCollections()

    def test_the_file_sync_tools_files_are_not_media(self):
        laptop = self.device('laptop')
        (self.folder / 'media').mkdir()
        (self.folder / 'media' / '.syncthing.cat.png.tmp').write_bytes(PNG)
        (self.folder / 'media' / 'cat.sync-conflict-20260101-000000-ABCDEFG.png').write_bytes(
            PNG)
        self.sync('laptop')
        self.assertEqual(laptop.media.names(), [])


class SnapshotTest(SyncTestCase):

    def test_the_folder_holds_one_snapshot_and_one_card_per_device(self):
        laptop = self.device('laptop')
        add_basic(laptop, laptop.decks()[0], 'diecinueve', 'nineteen')
        result = self.sync('laptop')
        self.assertTrue(result.written)
        own = sync.device_id(laptop)
        self.assertEqual(json.loads((self.folder / 'retain-sync.json').read_text()),
                         {'format': sync.FORMAT})
        files = sorted(path.name for path in (self.folder / 'devices').iterdir())
        self.assertEqual(files, [f'{own}.json', f'{own}.sqlite'])
        snapshot = sqlite3.connect(self.folder / 'devices' / f'{own}.sqlite')
        try:
            self.assertEqual(snapshot.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            self.assertEqual(snapshot.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 1)
            keys = [row[0] for row in snapshot.execute('SELECT key FROM config')]
            self.assertFalse([key for key in keys if key.startswith('sync_')])
        finally:
            snapshot.close()
        desktop = self.device('desktop')
        self.sync('desktop')
        listed = sync.devices(self.folder, sync.device_id(desktop))
        self.assertEqual([device['name'] for device in listed], ['laptop'])
        self.assertGreater(listed[0]['synced'], 0)

    def test_a_corrupt_or_partial_snapshot_is_skipped(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        add_basic(laptop, laptop.decks()[0], 'veinte', 'twenty')
        add_basic(desktop, desktop.decks()[0], 'veintiuno', 'twenty-one')
        self.sync('laptop')
        devices = self.folder / 'devices'
        snapshot = devices / f'{sync.device_id(laptop)}.sqlite'
        data = snapshot.read_bytes()
        (devices / 'partial.sqlite').write_bytes(data[:len(data) // 2])
        (devices / 'junk.sqlite').write_bytes(b'not a database at all' * 100)
        (devices / 'empty.sqlite').write_bytes(b'')
        result = self.sync('desktop')
        self.assertEqual(result.merged, 1)
        self.assertEqual(len(result.problems), 3, result.problems)
        self.assertEqual(desktop.note_count(), 2)
        self.assertTrue(sync.describe_result(result).endswith('; 3 devices skipped'))

    def test_a_newer_folder_format_is_refused(self):
        self.device('laptop')
        (self.folder / 'retain-sync.json').write_text(json.dumps({'format': sync.FORMAT + 1}))
        with self.assertRaises(sync.SyncError):
            self.sync('laptop')

    def test_a_missing_folder_is_an_error(self):
        self.device('laptop')
        self.folder.rmdir()
        with self.assertRaises(sync.SyncError):
            self.sync('laptop')

    def test_a_backup_is_made_before_merging(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        add_basic(laptop, laptop.decks()[0], 'veintidós', 'twenty-two')
        self.sync('laptop', backup=True)
        backups = desktop.path.parent / 'backups'
        self.assertFalse(backups.exists())  # nothing to merge yet on the laptop
        self.sync('desktop', backup=True)
        self.assertEqual(len(list(backups.glob('collection-*.sqlite'))), 1)

    def test_a_copied_collection_gets_a_device_id_of_its_own(self):
        laptop = self.device('laptop')
        own = sync.device_id(laptop)
        self.assertEqual(sync.device_id(laptop), own)
        copy_path = self.root / 'copy' / 'collection.sqlite'
        copy_path.parent.mkdir()
        laptop.copy_to(copy_path)
        copy = Collection(copy_path, backups=False)
        self.addCleanup(copy.close)
        self.assertNotEqual(sync.device_id(copy), own)


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def get_string(self, key):
        return self.values.get(key, '')


class RunnerTest(SyncTestCase):
    """SyncRunner: the thread, and what it does to the main collection after."""

    def run_until_finished(self, runner):
        from gi.repository import GLib

        loop = GLib.MainLoop()
        finished = []

        def on_finished(_runner, result, error):
            finished.append((result, error))
            loop.quit()

        runner.connect('finished', on_finished)
        self.assertTrue(runner.start())
        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()
        self.assertEqual(len(finished), 1)
        return finished[0]

    def test_a_sync_in_a_thread_clears_the_undo_stack_and_reports(self):
        laptop, desktop = self.device('laptop'), self.device('desktop')
        add_basic(laptop, laptop.decks()[0], 'veinticinco', 'twenty-five')
        self.sync('laptop')
        add_basic(desktop, desktop.decks()[0], 'veintiséis', 'twenty-six')
        self.assertTrue(desktop.can_undo())
        changes = []
        desktop.connect('changed', lambda _collection, kind: changes.append(kind))
        runner = sync.SyncRunner(FakeSettings(**{'sync-folder': str(self.folder),
                                                 'sync-device-name': 'Desk'}), desktop)
        result, error = self.run_until_finished(runner)
        self.assertEqual(error, '')
        self.assertEqual(result.notes_added, 1)
        self.assertFalse(desktop.can_undo())
        self.assertIn('notes', changes)
        self.assertEqual(desktop.note_count(), 2)
        self.assertIsNotNone(desktop.get(sync.LAST_KEY))
        names = [device['name'] for device in sync.devices(self.folder,
                                                            sync.device_id(laptop))]
        self.assertEqual(names, ['Desk'])

    def test_a_failure_is_reported_as_a_sentence(self):
        laptop = self.device('laptop')
        runner = sync.SyncRunner(FakeSettings(**{'sync-folder': str(self.root / 'nowhere')}),
                                 laptop)
        result, error = self.run_until_finished(runner)
        self.assertIsNone(result)
        self.assertIn('is not there', error)

    def test_without_a_folder_nothing_starts(self):
        runner = sync.SyncRunner(FakeSettings(), self.device('laptop'))
        self.assertFalse(runner.start())
        self.assertFalse(runner.running)


class MigrationTest(unittest.TestCase):

    def test_an_existing_collection_gets_the_graves_table(self):
        directory = tempfile.mkdtemp(prefix='retain-migrate-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = pathlib.Path(directory) / 'collection.sqlite'
        collection = Collection(path, backups=False)
        deck = collection.add_deck('Old')
        collection.close()
        db = sqlite3.connect(path)
        db.execute('DROP TABLE graves')
        db.execute("UPDATE config SET value = '1' WHERE key = 'schema_version'")
        db.commit()
        db.close()
        with mock.patch.object(schema, 'SCHEMA', schema.SCHEMA.split(
                'CREATE TABLE IF NOT EXISTS graves')[0]):
            collection = Collection(path, backups=False)
        try:
            self.assertEqual(collection.get('schema_version'), schema.VERSION)
            self.assertEqual(collection.graves(), [])
            collection.remove_deck(deck.id)
            self.assertEqual([grave['name'] for grave in collection.graves('deck')], ['Old'])
        finally:
            collection.close()

    def test_a_new_collections_stock_objects_are_untouched(self):
        directory = tempfile.mkdtemp(prefix='retain-migrate-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        collection = Collection(pathlib.Path(directory) / 'collection.sqlite', backups=False)
        try:
            for table in ('notetypes', 'decks', 'deck_configs'):
                self.assertEqual(collection.db.execute(
                    f'SELECT MAX(modified) FROM {table}').fetchone()[0], 0, table)
        finally:
            collection.close()


class ModifiedTest(SyncTestCase):
    """Every change a sync must carry moves the row's modified time."""

    def test_marking_and_a_deleted_preset_move_modified(self):
        laptop = self.device('laptop')
        note = add_basic(laptop, laptop.decks()[0], 'veintitrés', 'twenty-three')
        self.clock.advance()
        laptop.set_marked([note.id], True)
        self.assertGreater(laptop.note(note.id).modified, note.modified)
        preset = laptop.add_deck_config(DeckConfig(name='Brief'))
        deck = laptop.add_deck('Brief deck')
        laptop.set_deck_config(deck.id, preset.id)
        before = laptop.deck(deck.id).modified
        self.clock.advance()
        laptop.remove_deck_config(preset.id)
        self.assertGreater(laptop.deck(deck.id).modified, before)

    def test_a_note_types_changed_fields_move_its_notes(self):
        laptop = self.device('laptop')
        note = add_basic(laptop, laptop.decks()[0], 'veinticuatro', 'twenty-four')
        self.clock.advance()
        basic = laptop.notetype_by_name('Basic')
        basic.fields = basic.fields + [{'name': 'Extra', 'description': ''}]
        laptop.update_notetype(basic)
        self.assertGreater(laptop.note(note.id).modified, note.modified)


if __name__ == '__main__':
    unittest.main()
