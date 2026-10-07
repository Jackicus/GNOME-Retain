# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A deck's page: what is due, Study Now, the description, the subdecks and the options.

    page = DeckPage(deck_id)            # page.deck_id names the deck
    page.refresh()                      # from the collection (also on map and on `changed`)
    plain_text(html)                    # a description without its tags

The header bar's More menu carries the window's deck actions (win.deck-rename, -options,
-export, -delete, which act on this page's deck when the sidebar's menu is not open) and
two of the page's own: deck.edit-description (an alert dialog with a text view, saved
through collection.update_deck) and deck.unbury (collection.unbury_deck; enabled while the
deck has buried cards). Study Now is window.study(deck_id); with nothing due the primary
button becomes Study More… (dialogs/custom_study.py), which otherwise sits under it. The
Options row summarises the deck's preset and opens dialogs/deck_options.py.
"""

import html
import re
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk

from . import app
from .today import CHANGE_KINDS, REFRESH_DELAY_MS, fill_deck_list

BLOCK_TAGS = re.compile(r'<\s*(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>', re.IGNORECASE)
TAGS = re.compile(r'<[^>]+>')


def plain_text(text):
    """A deck description as plain text: block ends become line breaks, other tags go,
    entities are unescaped, and blank lines collapse."""
    if not text:
        return ''
    text = BLOCK_TAGS.sub('\n', text)
    text = TAGS.sub('', text)
    text = html.unescape(text)
    lines = [' '.join(line.split()) for line in text.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def options_summary(config):
    """'Default · 20 new/day · 90% retention'."""
    return _('{preset} · {new} new/day · {retention}% retention').format(
        preset=config.name, new=config.new_per_day,
        retention=round(config.desired_retention * 100))


@Gtk.Template(resource_path='/io/github/jackicus/Retain/deck.ui')
class DeckPage(Adw.NavigationPage):
    __gtype_name__ = 'RetainDeckPage'

    path_label = Gtk.Template.Child()
    new_label = Gtk.Template.Child()
    learning_label = Gtk.Template.Child()
    due_label = Gtk.Template.Child()
    nothing_label = Gtk.Template.Child()
    study_button = Gtk.Template.Child()
    more_button = Gtk.Template.Child()
    description_card = Gtk.Template.Child()
    description_label = Gtk.Template.Child()
    subdecks_group = Gtk.Template.Child()
    subdecks_list = Gtk.Template.Child()
    options_row = Gtk.Template.Child()

    def __init__(self, deck_id):
        self.deck_id = deck_id
        super().__init__(tag=f'deck-{deck_id}')
        self._changed_handler = None
        self._refresh_pending = None
        self._nothing_due = False
        self._add_actions()
        self.study_button.connect('clicked', self._on_study)
        self.more_button.connect('clicked', self._on_study_more)
        self.options_row.connect('activated', self._on_options)
        self.subdecks_list.connect('row-activated', self._on_subdeck_activated)
        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)
        Gtk.Widget.set_focusable(self, False)
        self.refresh()

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        self.edit_description_action = Gio.SimpleAction.new('edit-description', None)
        self.edit_description_action.connect('activate', self._on_edit_description)
        group.add_action(self.edit_description_action)
        self.unbury_action = Gio.SimpleAction.new('unbury', None)
        self.unbury_action.connect('activate', self._on_unbury)
        group.add_action(self.unbury_action)
        self.insert_action_group('deck', group)

    # -- lifetime ----------------------------------------------------------------------------

    def _on_map(self, *_args):
        if self._changed_handler is None:
            self._changed_handler = app().collection.connect('changed', self._on_changed)
        self.refresh()

    def _on_unmap(self, *_args):
        if self._changed_handler is not None:
            app().collection.disconnect(self._changed_handler)
            self._changed_handler = None
        if self._refresh_pending is not None:
            GLib.source_remove(self._refresh_pending)
            self._refresh_pending = None

    def _on_changed(self, _collection, kind):
        if kind in CHANGE_KINDS and self._refresh_pending is None:
            self._refresh_pending = GLib.timeout_add(REFRESH_DELAY_MS, self._refresh_later)

    def _refresh_later(self):
        self._refresh_pending = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    # -- content -----------------------------------------------------------------------------

    def refresh(self):
        application = app()
        collection = application.collection
        deck = collection.deck(self.deck_id)
        if deck is None:
            return  # deleted: the window drops the page
        self.set_title(deck.basename)
        self.path_label.set_text(deck.name.replace('::', ' › '))
        self.path_label.set_visible(deck.level > 0)
        new, learning, due = application.scheduler.counts(self.deck_id)
        self.new_label.set_text(str(new))
        self.learning_label.set_text(str(learning))
        self.due_label.set_text(str(due))
        self._nothing_due = new + learning + due == 0
        self.nothing_label.set_visible(self._nothing_due)
        self.study_button.set_label(_('Study _More…') if self._nothing_due else _('_Study Now'))
        self.more_button.set_visible(not self._nothing_due)
        description = plain_text(deck.description)
        self.description_label.set_text(description)
        self.description_card.set_visible(bool(description))
        node = self._node(application.scheduler.tree())
        children = node.children if node else []
        self.subdecks_group.set_visible(bool(children))
        fill_deck_list(self.subdecks_list, children, self._study, base_level=deck.level + 1)
        self.options_row.set_subtitle(options_summary(collection.config_for_deck(self.deck_id)))
        buried = collection.find_cards(f'deck:"{deck.name}" is:buried')
        self.unbury_action.set_enabled(bool(buried))

    def _node(self, roots):
        for root in roots:
            for node in root.walk():
                if node.deck.id == self.deck_id:
                    return node
        return None

    # -- actions -----------------------------------------------------------------------------

    def _window(self):
        return self.get_root()

    def _study(self, deck_id):
        window = self._window()
        if window is not None and hasattr(window, 'study'):
            window.study(deck_id)

    def _on_study(self, *_args):
        if self._nothing_due:
            self._on_study_more()
        else:
            self._study(self.deck_id)

    def _on_study_more(self, *_args):
        from ..dialogs import custom_study

        custom_study.present(app(), self._window(), self.deck_id)

    def _on_options(self, *_args):
        from ..dialogs import deck_options

        deck_options.present(app(), self._window(), self.deck_id)

    def _on_subdeck_activated(self, _list, row):
        window = self._window()
        if window is not None and hasattr(window, 'show_root'):
            window.show_root(f'deck:{row.deck_id}')

    def _on_unbury(self, *_args):
        app().collection.unbury_deck(self.deck_id)
        app().toast(_('Cards unburied'), undo=True)

    def _on_edit_description(self, *_args):
        self.present_description_dialog()

    def present_description_dialog(self):
        """Edit Description: an alert dialog with a text view; Save stores it."""
        from ..dialogs.deck_name import watch_dialog

        collection = app().collection
        deck = collection.deck(self.deck_id)
        if deck is None:
            return None
        dialog = Adw.AlertDialog(heading=_('Edit Description'),
                                 body=_('Shown on the deck’s page, above its subdecks.'))
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        view.get_buffer().set_text(plain_text(deck.description))
        scrolled = Gtk.ScrolledWindow(child=view, min_content_height=120,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.add_css_class('card')
        dialog.set_extra_child(scrolled)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('save', _('_Save'))
        dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('save')
        dialog.set_close_response('cancel')
        dialog.view = view

        def on_response(_dialog, response):
            if response == 'save':
                buffer = view.get_buffer()
                text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
                current = collection.deck(self.deck_id)
                if current is not None and current.description != text.strip():
                    current.description = text.strip()
                    collection.update_deck(current)
                    app().toast(_('Description saved'), undo=True)

        dialog.connect('response', on_response)
        window = self._window()
        watch_dialog(dialog, window)
        dialog.present(window)
        return dialog
