# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""deck_config.py: a preset's defaults, its row, and Anki's dconf in and out."""

import json
import unittest

from tests import ROOT  # noqa: F401
from retain import fsrs, workload
from retain.deck_config import DEFAULT_ID, FIELDS, LEECH_TAG, DeckConfig

# An invented FSRS-5 set (19 numbers) and FSRS-4.5 set (17), all within the bounds.
PARAMS_19 = [0.4, 1.2, 3.1, 15.7, 7.2, 0.5, 1.4, 0.01, 1.5, 0.1, 1.0, 1.9, 0.1, 0.3, 2.3, 0.2,
             2.9, 0.5, 0.6]
PARAMS_17 = PARAMS_19[:17]


class DefaultsTest(unittest.TestCase):

    def test_a_new_preset_has_ankis_defaults(self):
        config = DeckConfig(name='Default')
        self.assertIsNone(config.id)
        self.assertEqual(config.name, 'Default')
        for key, default in FIELDS.items():
            self.assertEqual(getattr(config, key), default, key)
        self.assertEqual(config.new_per_day, 20)
        self.assertEqual(config.reviews_per_day, 200)
        self.assertEqual(config.learning_steps, [1, 10])
        self.assertEqual(config.relearning_steps, [10])
        self.assertIsNone(config.fsrs_parameters)
        self.assertGreater(config.modified, 0)
        self.assertEqual(LEECH_TAG, 'leech')
        self.assertEqual(DEFAULT_ID, 1)

    def test_list_defaults_are_copies(self):
        one = DeckConfig()
        one.learning_steps.append(60)
        self.assertEqual(DeckConfig().learning_steps, [1, 10])
        self.assertEqual(FIELDS['learning_steps'], [1, 10])

    def test_values_override_the_defaults_and_none_means_default(self):
        config = DeckConfig(name='Fast', new_per_day=5, learning_steps=[2], new_mix=None)
        self.assertEqual(config.new_per_day, 5)
        self.assertEqual(config.learning_steps, [2])
        self.assertEqual(config.new_mix, 'mix')

    def test_an_unknown_setting_is_an_error(self):
        with self.assertRaises(TypeError):
            DeckConfig(name='Odd', steps=[1])

    def test_parameters_are_the_defaults_until_fitted(self):
        config = DeckConfig()
        self.assertEqual(config.parameters(), list(fsrs.DEFAULT_PARAMETERS))
        self.assertIsNot(config.parameters(), fsrs.DEFAULT_PARAMETERS)
        config.fsrs_parameters = PARAMS_19
        fitted = config.parameters()
        self.assertEqual(len(fitted), 21)
        self.assertEqual(fitted[:19], PARAMS_19)


class RowTest(unittest.TestCase):

    def test_to_row_and_from_row_round_trip(self):
        config = DeckConfig(id=7, name='Verbs', modified=1234, new_per_day=12,
                            reviews_per_day=99, desired_retention=0.85,
                            learning_steps=[2.5, 15], relearning_steps=[5, 20],
                            new_order='random', new_mix='after', bury_siblings=False,
                            leech_threshold=5, leech_action='suspend', maximum_interval=365,
                            fsrs_parameters=PARAMS_19)
        row = config.to_row()
        self.assertEqual(sorted(row), ['data', 'id', 'modified', 'name'])
        self.assertEqual(row['id'], 7)
        self.assertEqual(row['modified'], 1234)
        self.assertEqual(set(json.loads(row['data'])), set(FIELDS))
        back = DeckConfig.from_row(row)
        self.assertEqual(back.to_row(), row)
        for key in FIELDS:
            self.assertEqual(getattr(back, key), getattr(config, key), key)
        self.assertEqual(back.name, 'Verbs')

    def test_from_row_fills_missing_keys_and_ignores_unknown_ones(self):
        row = {'id': 3, 'name': 'Old', 'modified': 10,
               'data': json.dumps({'new_per_day': 7, 'from_the_future': True})}
        config = DeckConfig.from_row(row)
        self.assertEqual(config.new_per_day, 7)
        self.assertEqual(config.reviews_per_day, 200)
        self.assertFalse(hasattr(config, 'from_the_future'))

    def test_copy_is_independent(self):
        config = DeckConfig(id=2, name='A', learning_steps=[1, 10])
        clone = config.copy()
        clone.learning_steps.append(30)
        clone.name = 'B'
        self.assertEqual(config.learning_steps, [1, 10])
        self.assertEqual(config.name, 'A')
        self.assertEqual(clone.id, 2)


class AnkiTest(unittest.TestCase):

    def test_from_anki_reads_steps_limits_leech_and_parameters(self):
        dconf = {
            'name': 'Imported Verbs',
            'new': {'delays': [2, 20], 'perDay': 15, 'order': 0, 'bury': False},
            'rev': {'perDay': 150, 'maxIvl': 1000},
            'lapse': {'delays': [5], 'leechFails': 4, 'leechAction': 0},
            'desiredRetention': 0.88,
            'fsrsParams6': list(fsrs.DEFAULT_PARAMETERS),
        }
        config = DeckConfig.from_anki(dconf)
        self.assertEqual(config.name, 'Imported Verbs')
        self.assertEqual(config.learning_steps, [2.0, 20.0])
        self.assertEqual(config.relearning_steps, [5.0])
        self.assertEqual(config.new_per_day, 15)
        self.assertEqual(config.reviews_per_day, 150)
        self.assertEqual(config.new_order, 'random')
        self.assertFalse(config.bury_siblings)
        self.assertEqual(config.leech_threshold, 4)
        self.assertEqual(config.leech_action, 'suspend')
        self.assertEqual(config.maximum_interval, 1000)
        self.assertAlmostEqual(config.desired_retention, 0.88)
        self.assertEqual(config.fsrs_parameters, list(fsrs.DEFAULT_PARAMETERS))

    def test_from_anki_defaults_and_tag_action(self):
        config = DeckConfig.from_anki({}, name='Given')
        self.assertEqual(config.name, 'Given')
        self.assertEqual(config.learning_steps, [1.0, 10.0])
        self.assertEqual(config.relearning_steps, [10.0])
        self.assertEqual(config.new_per_day, 20)
        self.assertEqual(config.new_order, 'added')
        self.assertTrue(config.bury_siblings)
        self.assertEqual(config.leech_action, 'tag')
        self.assertIsNone(config.fsrs_parameters)
        self.assertEqual(DeckConfig.from_anki({}).name, 'Imported')
        tagged = DeckConfig.from_anki({'lapse': {'leechAction': 1}, 'new': {'order': 1}})
        self.assertEqual(tagged.leech_action, 'tag')
        self.assertEqual(tagged.new_order, 'added')

    def test_from_anki_upgrades_older_parameter_sets(self):
        five = DeckConfig.from_anki({'fsrsParams5': PARAMS_19})
        self.assertEqual(len(five.fsrs_parameters), 21)
        self.assertEqual(five.fsrs_parameters, fsrs.normalize_parameters(PARAMS_19))
        self.assertEqual(five.fsrs_parameters[:19], PARAMS_19)
        weights = DeckConfig.from_anki({'fsrsWeights': PARAMS_17})
        self.assertEqual(len(weights.fsrs_parameters), 21)
        self.assertEqual(weights.fsrs_parameters, fsrs.normalize_parameters(PARAMS_17))
        six = DeckConfig.from_anki({'fsrsParams6': list(fsrs.DEFAULT_PARAMETERS),
                                    'fsrsParams5': PARAMS_19})
        self.assertEqual(six.fsrs_parameters, list(fsrs.DEFAULT_PARAMETERS))
        empty = DeckConfig.from_anki({'fsrsParams6': [], 'fsrsWeights': []})
        self.assertIsNone(empty.fsrs_parameters)

    def test_from_anki_clamps_the_retention(self):
        self.assertAlmostEqual(DeckConfig.from_anki({'desiredRetention': 0.5}).desired_retention,
                               0.7)
        self.assertAlmostEqual(DeckConfig.from_anki({'desiredRetention': 1.0}).desired_retention,
                               0.99)

    def test_to_anki_writes_a_dconf_entry(self):
        config = DeckConfig(id=5, name='Verbs', modified=99, new_per_day=12, reviews_per_day=120,
                            learning_steps=[1, 10], relearning_steps=[10, 30],
                            new_order='random', bury_siblings=False, leech_threshold=6,
                            leech_action='suspend', maximum_interval=500,
                            desired_retention=0.85, fsrs_parameters=PARAMS_19)
        dconf = config.to_anki()
        self.assertEqual(dconf['id'], 5)
        self.assertEqual(dconf['name'], 'Verbs')
        self.assertEqual(dconf['mod'], 99)
        self.assertEqual(dconf['new']['delays'], [1, 10])
        self.assertEqual(dconf['new']['perDay'], 12)
        self.assertEqual(dconf['new']['order'], 0)
        self.assertFalse(dconf['new']['bury'])
        self.assertEqual(dconf['rev']['perDay'], 120)
        self.assertEqual(dconf['rev']['maxIvl'], 500)
        self.assertEqual(dconf['lapse']['delays'], [10, 30])
        self.assertEqual(dconf['lapse']['leechFails'], 6)
        self.assertEqual(dconf['lapse']['leechAction'], 0)
        self.assertAlmostEqual(dconf['desiredRetention'], 0.85)
        self.assertEqual(dconf['fsrsParams6'], PARAMS_19)
        self.assertFalse(dconf['dyn'])
        self.assertEqual(DeckConfig(name='New').to_anki()['id'], DEFAULT_ID)
        self.assertEqual(DeckConfig(name='New').to_anki()['fsrsParams6'], [])

    def test_to_anki_and_from_anki_round_trip(self):
        config = DeckConfig(id=5, name='Verbs', new_per_day=12, reviews_per_day=120,
                            learning_steps=[2.0, 15.0], relearning_steps=[5.0],
                            new_order='random', bury_siblings=False, leech_threshold=6,
                            leech_action='suspend', maximum_interval=500,
                            desired_retention=0.85, fsrs_parameters=list(fsrs.DEFAULT_PARAMETERS),
                            load_balancing=False, easy_days=EASY_DAYS)
        back = DeckConfig.from_anki(config.to_anki())
        for key in FIELDS:
            self.assertEqual(getattr(back, key), getattr(config, key), key)


# Saturday reduced, Sunday minimum.
EASY_DAYS = [workload.NORMAL] * 5 + [workload.REDUCED, workload.MINIMUM]


class WorkloadSettingsTest(unittest.TestCase):

    def test_balancing_is_on_and_every_day_normal_by_default(self):
        config = DeckConfig()
        self.assertTrue(config.load_balancing)
        self.assertEqual(config.easy_days, [1.0] * 7)
        config.easy_days[0] = 0.0
        self.assertEqual(DeckConfig().easy_days, [1.0] * 7)

    def test_the_row_round_trips(self):
        config = DeckConfig(id=3, name='Weekdays', load_balancing=False, easy_days=EASY_DAYS)
        back = DeckConfig.from_row(config.to_row())
        self.assertFalse(back.load_balancing)
        self.assertEqual(back.easy_days, EASY_DAYS)
        # A row stored before the settings existed reads as the defaults.
        row = config.to_row()
        data = json.loads(row['data'])
        del data['load_balancing'], data['easy_days']
        row['data'] = json.dumps(data)
        older = DeckConfig.from_row(row)
        self.assertTrue(older.load_balancing)
        self.assertEqual(older.easy_days, [1.0] * 7)

    def test_to_anki_writes_ankis_fields(self):
        dconf = DeckConfig(easy_days=EASY_DAYS, load_balancing=False).to_anki()
        self.assertEqual(dconf['easyDaysPercentages'], [1.0] * 5 + [0.5, 0.0])
        self.assertIs(dconf['loadBalancerEnabled'], False)
        json.dumps(dconf)

    def test_from_anki_reads_easy_days_as_anki_does(self):
        config = DeckConfig.from_anki({'easyDaysPercentages': [1, 1, 0.7, 1, 1, 0.5, 0]})
        self.assertEqual(config.easy_days, [1.0, 1.0, 0.5, 1.0, 1.0, 0.5, 0.0])
        self.assertTrue(config.load_balancing)  # Anki keeps its switch in the collection
        for odd in ([], [1, 0], None, 'x'):
            self.assertEqual(DeckConfig.from_anki({'easyDaysPercentages': odd}).easy_days,
                             [1.0] * 7)
        self.assertFalse(DeckConfig.from_anki({'loadBalancerEnabled': False}).load_balancing)


if __name__ == '__main__':
    unittest.main()
