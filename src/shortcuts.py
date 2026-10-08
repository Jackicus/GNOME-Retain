# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Every keyboard shortcut, in one place: the application accelerators (main.py sets them),
the review page's keys (pages/review.py handles them itself: a bare key is never an
application accelerator, GTK would run it before the focused entry saw it), the browser's and
the editor's, and the sections the Keyboard Shortcuts dialog shows (dialogs/shortcuts.py).

    ACCELS                  {action name: [accelerators]} for Gtk.Application
    REVIEW, BROWSE, EDITOR  {key name: accelerator} the pages bind themselves
    sections()              [(title, [(description, accelerator or key)])] for the dialog
    accelerator(key)        the accelerator string of a key name, in any table
"""

from gettext import gettext as _

ACCELS = {
    'app.add': ['<primary>n'],
    'app.new-deck': ['<primary><shift>n'],
    'app.import': ['<primary>o'],
    'app.export': ['<primary><shift>e'],
    'app.undo': ['<primary>z'],
    'app.preferences': ['<primary>comma'],
    'app.shortcuts': ['<primary>question'],
    'app.quit': ['<primary>q'],
    'win.search': ['<primary>f'],
    'win.today': ['<primary>1'],
    'win.browse': ['<primary>2'],
    'win.stats': ['<primary>3'],
    'win.study': ['<primary>Return'],
    'win.back': ['<alt>Left'],
    'win.toggle-sidebar': ['F9'],
}

# The review page's keys (pages/review.py).
REVIEW = {
    'show-answer': 'space',
    'show-answer-alt': 'Return',
    'again': '1',
    'hard': '2',
    'good': '3',
    'easy': '4',
    'edit': 'e',
    'suspend': 's',
    'bury': 'b',
    'mark': 'm',
    'flag': 'f',
    'info': 'i',
    'replay-audio': 'r',
    'read-aloud': 'v',
    'delete': 'Delete',
    'flag-1': '<primary>1',
    'flag-2': '<primary>2',
    'flag-3': '<primary>3',
    'flag-4': '<primary>4',
}

# The browser's keys (pages/browse.py), on its table.
BROWSE = {
    'edit': 'Return',
    'delete': 'Delete',
    'suspend': '<primary>j',
    'mark': '<primary>k',
    'change-deck': '<primary>d',
    'add-tags': '<primary><shift>t',
    'select-all': '<primary>a',
}

# The note editor's keys (dialogs/add_edit.py).
EDITOR = {
    'save': '<primary>Return',
    'cloze': '<primary><shift>c',
    'cloze-same': '<primary><shift><alt>c',
    'bold': '<primary>b',
    'italic': '<primary>i',
    'underline': '<primary>u',
    'attach': '<primary><shift>a',
    'next-field': 'Tab',
}


def accelerator(key):
    """The accelerator of a key name: 'app.add' or 'again'."""
    for table in (ACCELS, REVIEW, BROWSE, EDITOR):
        if key in table:
            value = table[key]
            return ' '.join(value) if isinstance(value, list) else value
    raise KeyError(key)


def sections():
    """The Keyboard Shortcuts dialog's sections: (title, [(what it does, accelerator)])."""
    return [
        (_('General'), [
            (_('Add Cards'), accelerator('app.add')),
            (_('New Deck'), accelerator('app.new-deck')),
            (_('Import'), accelerator('app.import')),
            (_('Export'), accelerator('app.export')),
            (_('Undo'), accelerator('app.undo')),
            (_('Search Cards'), accelerator('win.search')),
            (_('Study the Selected Deck'), accelerator('win.study')),
            (_('Today'), accelerator('win.today')),
            (_('Browse'), accelerator('win.browse')),
            (_('Statistics'), accelerator('win.stats')),
            (_('Show or Hide the Sidebar'), accelerator('win.toggle-sidebar')),
            (_('Go Back'), accelerator('win.back')),
            (_('Preferences'), accelerator('app.preferences')),
            (_('Keyboard Shortcuts'), accelerator('app.shortcuts')),
            (_('Quit'), accelerator('app.quit')),
        ]),
        (_('Reviewing'), [
            (_('Show Answer, or Good'), 'space Return'),
            (_('Again'), REVIEW['again']),
            (_('Hard'), REVIEW['hard']),
            (_('Good'), REVIEW['good']),
            (_('Easy'), REVIEW['easy']),
            (_('Edit Card'), REVIEW['edit']),
            (_('Suspend Card'), REVIEW['suspend']),
            (_('Bury Card'), REVIEW['bury']),
            (_('Mark Note'), REVIEW['mark']),
            (_('Flag Card'), REVIEW['flag']),
            (_('Set a Flag'), '<primary>1 <primary>2 <primary>3 <primary>4'),
            (_('Card Information'), REVIEW['info']),
            (_('Replay Sound'), REVIEW['replay-audio']),
            (_('Read Aloud'), REVIEW['read-aloud']),
            (_('Delete Note'), REVIEW['delete']),
        ]),
        (_('Browsing'), [
            (_('Edit Note'), BROWSE['edit']),
            (_('Delete Notes'), BROWSE['delete']),
            (_('Suspend Cards'), BROWSE['suspend']),
            (_('Mark Notes'), BROWSE['mark']),
            (_('Change Deck'), BROWSE['change-deck']),
            (_('Add Tags'), BROWSE['add-tags']),
            (_('Select All'), BROWSE['select-all']),
        ]),
        (_('Editing'), [
            (_('Save and Add'), EDITOR['save']),
            (_('Cloze Deletion'), EDITOR['cloze']),
            (_('Cloze Deletion, Same Number'), EDITOR['cloze-same']),
            (_('Bold'), EDITOR['bold']),
            (_('Italic'), EDITOR['italic']),
            (_('Underline'), EDITOR['underline']),
            (_('Attach a Picture or Sound'), EDITOR['attach']),
            (_('Next Field'), EDITOR['next-field']),
        ]),
    ]
