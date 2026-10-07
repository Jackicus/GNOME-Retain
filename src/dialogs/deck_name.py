# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Naming and deleting a deck.

    present_new_deck(app, parent, parent_deck_id=None)   # New Deck: creates, shows, toasts
    present_rename(app, parent, deck_id)                  # Rename Deck, prefilled
    confirm_delete(app, parent, deck_id)                  # Delete “Spanish”? (an alert)
    NameDialog(title, apply_label, on_apply, text='', hint=None)   # the dialog they share

Each returns the dialog. NameDialog is an Adw.Dialog with a header bar (Cancel, and the
apply button as the default), an entry row and an inline error: `on_apply(name)` returns
None to close the dialog, or the sentence to show in red (a CollectionError's message, which
the dialog catches too). deck_options.py uses it for its presets. Deleting a deck is the one
destructive action that asks first (CLAUDE.md): the alert names the deck and counts its
cards; Delete removes it, tells the window to forget its page and toasts Undo.
"""

from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gtk

from ..collection import CollectionError

class NameDialog(Adw.Dialog):
    """A dialog asking for one name."""

    def __init__(self, title, apply_label, on_apply, text='', hint=None):
        super().__init__(title=title, content_width=420)
        self.on_apply = on_apply
        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        cancel.connect('clicked', lambda *_args: self.close())
        header.pack_start(cancel)
        self.apply_button = Gtk.Button(label=apply_label, use_underline=True)
        self.apply_button.add_css_class('suggested-action')
        self.apply_button.connect('clicked', self._on_apply)
        header.pack_end(self.apply_button)
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                      margin_bottom=24, margin_start=18, margin_end=18)
        group = Adw.PreferencesGroup()
        self.entry = Adw.EntryRow(title=_('Name'), text=text)
        self.entry.connect('entry-activated', self._on_apply)
        self.entry.connect('changed', self._on_changed)
        group.add(self.entry)
        box.append(group)
        if hint:
            hint_label = Gtk.Label(label=hint, xalign=0, wrap=True, margin_start=12)
            hint_label.add_css_class('caption')
            hint_label.add_css_class('dimmed')
            box.append(hint_label)
        self.error_label = Gtk.Label(xalign=0, wrap=True, visible=False, margin_start=12)
        self.error_label.add_css_class('caption')
        self.error_label.add_css_class('error')
        box.append(self.error_label)
        view.set_content(box)
        self.set_child(view)
        self.set_default_widget(self.apply_button)
        self.set_focus(self.entry)
        self._on_changed()

    def _on_changed(self, *_args):
        self.apply_button.set_sensitive(bool(self.entry.get_text().strip()))
        self.show_error(None)

    def show_error(self, message):
        self.error_label.set_text(message or '')
        self.error_label.set_visible(bool(message))
        if message:
            self.entry.add_css_class('error')
        else:
            self.entry.remove_css_class('error')

    def _on_apply(self, *_args):
        name = self.entry.get_text().strip()
        if not name:
            return
        try:
            error = self.on_apply(name)
        except CollectionError as problem:
            error = str(problem)
        if error:
            self.show_error(error)
            self.entry.grab_focus()
        else:
            self.close()

    def present_over(self, window):
        """Present over a window, keeping its keyed actions out of the entry's way."""
        watch_dialog(self, window)
        self.present(window)
        self.entry.grab_focus()
        return self


def watch_dialog(dialog, window):
    """Keyed window actions step aside while the dialog is open (window.set_dialog_open)."""
    if window is None or not hasattr(window, 'set_dialog_open'):
        return
    window.set_dialog_open(True)
    dialog.connect('closed', lambda *_args: window.set_dialog_open(False))


def _show_deck(window, deck_id):
    """Open a deck's page once the sidebar lists it."""
    if window is None:
        return
    controller = getattr(window, 'sidebar_controller', None)
    if controller is not None and hasattr(controller, 'refresh_now'):
        controller.refresh_now()
    if hasattr(window, 'show_root'):
        window.show_root(f'deck:{deck_id}')


def present_new_deck(app, parent, parent_deck_id=None):
    collection = app.collection
    text = ''
    if parent_deck_id is not None:
        parent_deck = collection.deck(parent_deck_id)
        if parent_deck is not None:
            text = parent_deck.name + '::'

    def apply(name):
        if collection.deck_by_name(name) is not None:
            return _('A deck called “{name}” already exists.').format(name=name)
        deck = collection.add_deck(name)
        app.toast(_('Deck “{name}” created').format(name=deck.basename), undo=True)
        _show_deck(parent, deck.id)
        return None

    dialog = NameDialog(_('New Deck'), _('C_reate'), apply, text=text,
                        hint=_('“Spanish::Verbs” makes a subdeck'))
    dialog.entry.set_position(-1)
    return dialog.present_over(parent)


def present_rename(app, parent, deck_id):
    collection = app.collection
    deck = collection.deck(deck_id)
    if deck is None:
        return None

    def apply(name):
        collection.rename_deck(deck_id, name)
        renamed = collection.deck(deck_id)
        if renamed is not None and renamed.name != deck.name:
            app.toast(_('Deck renamed to “{name}”').format(name=renamed.basename), undo=True)
        return None

    dialog = NameDialog(_('Rename Deck'), _('_Rename'), apply, text=deck.name,
                        hint=_('“Spanish::Verbs” moves it under Spanish'))
    dialog.entry.select_region(0, -1)
    return dialog.present_over(parent)


def confirm_delete(app, parent, deck_id):
    collection = app.collection
    deck = collection.deck(deck_id)
    if deck is None:
        return None
    cards = collection.deck_card_count(deck_id)
    subdecks = len(collection.deck_and_children(deck_id)) - 1
    counted = ngettext('its {n} card', 'its {n} cards', cards).format(n=cards)
    if subdecks:
        counted = ngettext('{cards} and {n} subdeck', '{cards} and {n} subdecks',
                           subdecks).format(cards=counted, n=subdecks)
    dialog = Adw.AlertDialog(
        heading=_('Delete “{name}”?').format(name=deck.basename),
        body=_('This deletes {what}. You can undo this.').format(what=counted))
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('delete', _('_Delete'))
    dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response != 'delete':
            return
        try:
            collection.remove_deck(deck_id)
        except CollectionError as error:
            app.report(error)
            return
        if parent is not None and hasattr(parent, 'forget_deck'):
            parent.forget_deck(deck_id)
        app.toast(_('Deck deleted'), undo=True)

    dialog.connect('response', on_response)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog
