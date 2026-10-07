# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A deck's settings: a preset (deck_configs row) that any number of decks share.

    config = DeckConfig(name='Default')          # Anki's defaults, FSRS on
    config.desired_retention = 0.85
    row = config.to_row(); DeckConfig.from_row(row)
    DeckConfig.from_anki(dconf_dict)             # an imported package's preset

Steps are minutes. `fsrs_parameters` is None for the FSRS-6 defaults, else the 21 fitted
numbers (optimizer.py). `new_mix` says where new cards go among the day's reviews: mixed in,
after them, or before them. `leech_action` is 'tag' (the note gets the leech tag) or 'suspend'.
"""

import json
import time

from . import fsrs

DEFAULT_ID = 1
LEECH_TAG = 'leech'

FIELDS = {
    'new_per_day': 20,
    'reviews_per_day': 200,
    'desired_retention': 0.9,
    'learning_steps': [1, 10],
    'relearning_steps': [10],
    'new_order': 'added',  # or 'random'
    'new_mix': 'mix',  # or 'after', 'before'
    'bury_siblings': True,
    'leech_threshold': 8,
    'leech_action': 'tag',
    'maximum_interval': 36500,
    'fsrs_parameters': None,
}


class DeckConfig:

    def __init__(self, id=None, name='', modified=None, **values):
        self.id = id
        self.name = name
        self.modified = modified or int(time.time())
        for key, default in FIELDS.items():
            value = values.pop(key, None)
            if value is None:
                value = list(default) if isinstance(default, list) else default
            setattr(self, key, value)
        if values:
            raise TypeError(f'unknown deck settings: {", ".join(values)}')

    def parameters(self):
        """The FSRS parameters to schedule with: the fitted ones, else the defaults."""
        if self.fsrs_parameters:
            return fsrs.normalize_parameters(self.fsrs_parameters)
        return list(fsrs.DEFAULT_PARAMETERS)

    def to_json(self):
        return json.dumps({key: getattr(self, key) for key in FIELDS}, sort_keys=True)

    def to_row(self):
        return {'id': self.id, 'name': self.name, 'data': self.to_json(),
                'modified': self.modified}

    @classmethod
    def from_row(cls, row):
        data = json.loads(row['data'])
        known = {key: data.get(key) for key in FIELDS if key in data}
        return cls(id=row['id'], name=row['name'], modified=row['modified'], **known)

    def copy(self):
        return DeckConfig.from_row(self.to_row())

    @classmethod
    def from_anki(cls, dconf, name=None):
        """A preset from an Anki deck configuration dict (a package's dconf entry)."""
        new = dconf.get('new') or {}
        rev = dconf.get('rev') or {}
        lapse = dconf.get('lapse') or {}
        params = dconf.get('fsrsParams6') or None
        if not params:
            older = dconf.get('fsrsParams5') or dconf.get('fsrsWeights') or None
            params = fsrs.normalize_parameters(older) if older else None
        config = cls(
            name=name or dconf.get('name') or 'Imported',
            new_per_day=int(new.get('perDay', FIELDS['new_per_day'])),
            reviews_per_day=int(rev.get('perDay', FIELDS['reviews_per_day'])),
            desired_retention=float(dconf.get('desiredRetention', FIELDS['desired_retention'])),
            learning_steps=[float(step) for step in new.get('delays') or [1, 10]],
            relearning_steps=[float(step) for step in lapse.get('delays') or [10]],
            new_order='random' if new.get('order') == 0 else 'added',
            bury_siblings=bool(new.get('bury', True)),
            leech_threshold=int(lapse.get('leechFails', 8)),
            leech_action='suspend' if lapse.get('leechAction') == 0 else 'tag',
            maximum_interval=int(rev.get('maxIvl', 36500)),
            fsrs_parameters=params,
        )
        config.desired_retention = min(max(config.desired_retention, 0.7), 0.99)
        return config

    def to_anki(self):
        """An Anki deck configuration dict, for export."""
        return {
            'id': self.id or DEFAULT_ID, 'name': self.name, 'mod': self.modified, 'usn': -1,
            'maxTaken': 60, 'autoplay': True, 'timer': 0, 'replayq': True, 'dyn': False,
            'new': {'bury': self.bury_siblings, 'delays': list(self.learning_steps),
                    'initialFactor': 2500, 'ints': [1, 4, 0],
                    'order': 0 if self.new_order == 'random' else 1,
                    'perDay': self.new_per_day, 'separate': True},
            'rev': {'bury': self.bury_siblings, 'ease4': 1.3, 'ivlFct': 1,
                    'maxIvl': self.maximum_interval, 'perDay': self.reviews_per_day,
                    'hardFactor': 1.2, 'minSpace': 1, 'fuzz': 0.05},
            'lapse': {'delays': list(self.relearning_steps),
                      'leechAction': 0 if self.leech_action == 'suspend' else 1,
                      'leechFails': self.leech_threshold, 'minInt': 1, 'mult': 0},
            'desiredRetention': self.desired_retention,
            'fsrsParams6': list(self.fsrs_parameters or []),
        }
