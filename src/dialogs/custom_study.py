# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Study More: a session beyond the day's queue of a deck.

    dialog = present(app, parent, deck_id)
    dialog.choose('forgotten'); dialog.start()       # what Start does for the chosen row

The rows are the kinds scheduler.custom_session() knows, each with its amount: more new
cards or more reviews (today's limits are raised), the cards forgotten in the last days,
the cards due within days, every card of a tag of the deck, or every card of the deck (the
last two crams: nothing is scheduled). A radio button in front of each row says which one
Start runs: the session goes to window.study(deck_id, session), and a session without
cards toasts No Cards Match and keeps the dialog open.
"""

from gettext import gettext as _

from gi.repository import Adw, Gtk

from .deck_name import watch_dialog

KINDS = ('new', 'review', 'forgotten', 'ahead', 'tag', 'all')


@Gtk.Template(resource_path='/io/github/jackicus/Retain/custom_study.ui')
class CustomStudyDialog(Adw.Dialog):
    __gtype_name__ = 'RetainCustomStudyDialog'

    new_row = Gtk.Template.Child()
    review_row = Gtk.Template.Child()
    forgotten_row = Gtk.Template.Child()
    ahead_row = Gtk.Template.Child()
    tag_row = Gtk.Template.Child()
    all_row = Gtk.Template.Child()
    start_button = Gtk.Template.Child()

    def __init__(self, app, deck_id, window=None):
        super().__init__()
        self.app = app
        self.deck_id = deck_id
        self.window = window  # the app's window: a dialog's own root may be a window of its own
        self.rows = {'new': self.new_row, 'review': self.review_row,
                     'forgotten': self.forgotten_row, 'ahead': self.ahead_row,
                     'tag': self.tag_row, 'all': self.all_row}
        self.checks = {}
        group = None
        for kind in KINDS:
            row = self.rows[kind]
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, can_focus=False)
            check.update_property([Gtk.AccessibleProperty.LABEL], [row.get_title()])
            if group is not None:
                check.set_group(group)
            group = group or check
            row.add_prefix(check)
            row.set_activatable_widget(check)
            check.connect('toggled', self._on_toggled, kind)
            self.checks[kind] = check
        self.tags = self._deck_tags()
        self.tag_row.set_model(Gtk.StringList.new(self.tags))
        if not self.tags:
            self.tag_row.set_sensitive(False)
            self.tag_row.set_subtitle(_('No tags in this deck'))
        self.start_button.connect('clicked', lambda *_args: self.start())
        self.set_default_widget(self.start_button)
        self.set_focus(self.start_button)
        self.choose('new')

    def _deck_tags(self):
        """The tags of the deck's notes, sorted."""
        collection = self.app.collection
        deck = collection.deck(self.deck_id)
        if deck is None:
            return []
        tags = set()
        for note in collection.notes(collection.find_notes(f'deck:"{deck.name}"')):
            tags.update(note.tags)
        return sorted(tags, key=str.lower)

    def choose(self, kind):
        self.checks[kind].set_active(True)

    @property
    def chosen(self):
        return next(kind for kind in KINDS if self.checks[kind].get_active())

    def _on_toggled(self, check, kind):
        if check.get_active():
            self.start_button.set_sensitive(kind != 'tag' or bool(self.tags))

    def amount(self, kind=None):
        """The chosen row's amount: a count, a number of days, a tag, or None."""
        kind = kind or self.chosen
        if kind in ('new', 'review', 'forgotten', 'ahead'):
            return int(self.rows[kind].get_value())
        if kind == 'tag':
            index = self.tag_row.get_selected()
            return self.tags[index] if 0 <= index < len(self.tags) else None
        return None

    def start(self):
        """Run the chosen study: a session for the review page, or a toast when empty."""
        kind = self.chosen
        amount = self.amount(kind)
        if kind == 'tag' and amount is None:
            self.app.toast(_('No cards match'))
            return None
        session = self.app.scheduler.custom_session(self.deck_id, kind, amount)
        if sum(session.counts()) == 0:
            self.app.toast(_('No cards match'))
            return None
        window = self.window or self.get_root()
        self.close()
        if window is not None and hasattr(window, 'study'):
            window.study(self.deck_id, session)
        return session


def present(app, parent, deck_id):
    dialog = CustomStudyDialog(app, deck_id, window=parent)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog
