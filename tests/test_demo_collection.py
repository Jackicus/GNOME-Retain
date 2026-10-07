# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""scripts/demo_collection.py builds the invented collection the screenshots and --demo
show: decks with history, media that exists, today's queue left for the user."""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT
from retain.collection import Collection
from retain.scheduler import Scheduler

SCRIPT = ROOT / 'scripts' / 'demo_collection.py'


def build(directory, seed=1):
    subprocess.run([sys.executable, str(SCRIPT), '--small', '--seed', str(seed),
                    '--data-dir', str(directory)], check=True, capture_output=True)


class DemoCollectionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.directory = pathlib.Path(tempfile.mkdtemp(prefix='retain-demo-test-'))
        build(cls.directory)
        cls.collection = Collection(cls.directory / 'collection.sqlite', backups=False)

    @classmethod
    def tearDownClass(cls):
        cls.collection.close()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def test_the_decks_are_there_with_history(self):
        names = [deck.name for deck in self.collection.decks()]
        for name in ('Spanish', 'Spanish::Vocabulary', 'Spanish::Grammar', 'Geography',
                     'Biology::Cell', 'Biology::Anatomy', 'Japanese', 'Programming'):
            self.assertIn(name, names)
        self.assertGreaterEqual(self.collection.card_count(), 300)
        reviews = self.collection.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0]
        self.assertGreaterEqual(reviews, 500)
        self.assertTrue(self.collection.get('demo'))

    def test_media_referenced_exists(self):
        referenced = self.collection.referenced_media()
        self.assertTrue(referenced)
        self.assertEqual(self.collection.media.missing(referenced), [])

    def test_spanish_has_work_today_and_mature_cards(self):
        spanish = self.collection.deck_by_name('Spanish')
        new, _learning, due = Scheduler(self.collection).counts(spanish.id)
        self.assertGreater(new, 0)
        self.assertGreaterEqual(due, 20)
        mature = self.collection.db.execute(
            "SELECT COUNT(*) FROM cards WHERE state = 'review' AND interval >= 21").fetchone()[0]
        self.assertGreater(mature, 0)

    def test_a_seed_is_deterministic(self):
        other = pathlib.Path(tempfile.mkdtemp(prefix='retain-demo-test-'))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        build(other)
        copy = Collection(other / 'collection.sqlite', backups=False)
        try:
            self.assertEqual(copy.card_count(), self.collection.card_count())
            counts = [c.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0]
                      for c in (copy, self.collection)]
            self.assertEqual(counts[0], counts[1])
        finally:
            copy.close()


if __name__ == '__main__':
    unittest.main()
