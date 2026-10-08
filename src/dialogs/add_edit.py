# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The note editor: Add Cards, and Edit Note.

    dialog = present_add(app, parent, deck_id=None, notetype_id=None)
    dialog = present_edit(app, parent, note_id)       # None when the note is gone

One Adw.Dialog (add_edit.blp): Cancel and Add (or Save) in the header bar, then the note
type and the deck as combo rows, one widgets/field_editor.py FieldEditor per field of the
type, and the tags. Adding keeps the dialog open: the fields clear, the type, deck and tags
stay, a toast with Undo says how many cards were made, and a CollectionError (an empty front,
a cloze type without a cloze) shows in the banner at the top instead of a toast. The first
field is checked for duplicates (collection.find_duplicates) when it loses the focus or
DUPLICATE_DELAY after it last changed; Show closes the dialog and opens the browser on the
notes. A cloze type says under its text how many cards it makes. An image occlusion type
has a row to choose the image and the RetainOcclusionEditor (widgets/occlusion_editor.py)
over it; without that widget the Occlusion field is a plain field. The last type and deck
used are kept in the collection's config ('last_notetype', 'last_deck'); the last tags are
remembered for the next add only with the config 'sticky_tags'. The editor's keys are
shortcuts.EDITOR, handled by the dialog's own key controller before the text views see them.
When adding, a button on the Type row opens Manage Note Types (dialogs/notetypes.py) on the
chosen type; when it closes, the types and the fields are read again, what was typed kept.
"""

import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .. import notetypes, template
from ..collection import CollectionError
from ..shortcuts import EDITOR
from ..widgets.field_editor import FieldEditor, media_kind
from ..widgets.util import connect_weak

log = logging.getLogger(__name__)

DUPLICATE_DELAY = 400  # ms after the first field last changed
_last_tags = []  # the tags of the last note added, for 'sticky_tags'


class DeckItem(GObject.Object):
    """A deck in the Deck row's list: its full name (parents joined with ›) for the row, its
    own name and depth for the indented list."""

    __gtype_name__ = 'RetainAddEditDeckItem'

    name = GObject.Property(type=str, default='')
    title = GObject.Property(type=str, default='')
    level = GObject.Property(type=int, default=0)
    deck_id = GObject.Property(type=GObject.TYPE_INT64, default=0)


@Gtk.Template(resource_path='/io/github/jackicus/Retain/add_edit.ui')
class AddEditDialog(Adw.Dialog):
    """See the module. `note` is the note to edit, or None to add."""

    __gtype_name__ = 'RetainAddEditDialog'

    cancel_button = Gtk.Template.Child()
    primary_button = Gtk.Template.Child()
    error_banner = Gtk.Template.Child()
    duplicate_banner = Gtk.Template.Child()
    type_row = Gtk.Template.Child()
    deck_row = Gtk.Template.Child()
    fields_box = Gtk.Template.Child()
    tags_row = Gtk.Template.Child()

    def __init__(self, app, parent, note=None, deck_id=None, notetype_id=None):
        super().__init__()
        self.app = app
        self.collection = app.collection
        self.parent_window = parent
        self.note = note
        self.editors = []  # the FieldEditors, in field order (image occlusion: see below)
        self.occlusion = None  # {'editor': widget or None, 'image': name, …} for occlusion
        self.notetype = None
        self._notetypes = []
        self._decks = []
        self._duplicate_ids = []
        self._duplicate_timer = 0
        self._building = False
        self._keys = [(name, Gtk.accelerator_parse(accel)) for name, accel in EDITOR.items()]

        if note is not None:
            self.set_title(_('Edit Note'))
            self.primary_button.set_label(_('_Save'))
        self._fill_types(note.notetype_id if note else notetype_id)
        if note is None:
            manage = Gtk.Button(icon_name='document-edit-symbolic', valign=Gtk.Align.CENTER,
                                tooltip_text=_('Manage Note Types'))
            manage.add_css_class('flat')
            self.type_row.add_suffix(manage)
            connect_weak(manage, 'clicked', self._on_manage_types)
        self._fill_decks(self._initial_deck(deck_id))
        self._fill_tags()
        self._build_fields()

        connect_weak(self.cancel_button, 'clicked', self._on_cancel)
        connect_weak(self.primary_button, 'clicked', self._on_primary)
        connect_weak(self.type_row, 'notify::selected', self._on_type_changed)
        connect_weak(self.duplicate_banner, 'button-clicked', self._on_show_duplicates)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        connect_weak(keys, 'key-pressed', self._on_key_pressed)
        self.add_controller(keys)
        self.connect('map', self._on_map)
        self.connect('closed', self._on_closed)

    # -- the rows ---------------------------------------------------------------------------

    def _fill_types(self, wanted_id):
        self._notetypes = self.collection.notetypes()
        self.type_row.set_model(Gtk.StringList.new([n.name for n in self._notetypes]))
        if wanted_id is None:
            wanted_id = self.collection.get('last_notetype')
        ids = [n.id for n in self._notetypes]
        self.type_row.set_selected(ids.index(wanted_id) if wanted_id in ids else 0)
        self.type_row.set_sensitive(self.note is None)

    def _initial_deck(self, deck_id):
        if self.note is not None:
            cards = self.collection.cards_of_note(self.note.id)
            if cards:
                return cards[0].deck_id
        if deck_id is not None:
            return deck_id
        return self.collection.get('last_deck')

    def _fill_decks(self, wanted_id):
        store = Gio.ListStore.new(DeckItem)
        self._decks = []
        for root in self.collection.deck_tree():
            for node in root.walk():
                item = DeckItem(name=node.deck.name.replace('::', ' › '),
                                title=node.deck.basename, level=node.level,
                                deck_id=node.deck.id)
                store.append(item)
                self._decks.append(node.deck)
        self.deck_row.set_expression(Gtk.PropertyExpression.new(DeckItem, None, 'name'))
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', _setup_deck_item)
        factory.connect('bind', _bind_deck_item)
        self.deck_row.set_list_factory(factory)
        self.deck_row.set_model(store)
        ids = [deck.id for deck in self._decks]
        self.deck_row.set_selected(ids.index(wanted_id) if wanted_id in ids else 0)

    def _fill_tags(self):
        if self.note is not None:
            self.tags_row.set_text(' '.join(self.note.tags))
        elif self.collection.get('sticky_tags', False):
            self.tags_row.set_text(' '.join(_last_tags))

    def selected_notetype(self):
        index = self.type_row.get_selected()
        return self._notetypes[index] if 0 <= index < len(self._notetypes) else None

    def selected_deck_id(self):
        index = self.deck_row.get_selected()
        return self._decks[index].id if 0 <= index < len(self._decks) else None

    def tags(self):
        return self.tags_row.get_text().split()

    # -- the fields -------------------------------------------------------------------------

    def _build_fields(self):
        """One editor per field of the selected type (filled from the note when editing)."""
        self._building = True
        try:
            while (child := self.fields_box.get_first_child()) is not None:
                self.fields_box.remove(child)
            self.editors = []
            self.occlusion = None
            self.notetype = self.selected_notetype()
            if self.notetype is None:
                return
            values = self.note.fields if self.note is not None else []
            if notetypes.is_image_occlusion(self.notetype):
                self._build_occlusion_fields(values)
            else:
                for index, name in enumerate(self.notetype.field_names()):
                    self._add_editor(name, values[index] if index < len(values) else '')
            if self.editors:
                connect_weak(self.editors[0], 'changed', self._on_first_field_changed)
                connect_weak(self.editors[0], 'focus-left', self._on_first_field_left)
            if self.notetype.kind in template.CLOZE_KINDS and self.editors:
                connect_weak(self.editors[0], 'changed', self._update_cloze_hint)
                self._update_cloze_hint(self.editors[0])
        finally:
            self._building = False
        self.error_banner.set_revealed(False)
        self.duplicate_banner.set_revealed(False)

    def _add_editor(self, name, value):
        editor = FieldEditor(name, media=self.collection.media,
                             cloze=self.notetype.kind in template.CLOZE_KINDS)
        editor.set_html(value)
        self.fields_box.append(editor)
        self.editors.append(editor)
        return editor

    def _build_occlusion_fields(self, values):
        """Image occlusion: an image row, the occlusion editor over the image (a plain field
        when widgets/occlusion_editor.py has not landed), then the other fields as usual."""
        names = self.notetype.field_names()
        by_name = dict(zip(names, values, strict=False))
        image_html = by_name.get('Image', '')
        references = template.media_references(image_html)
        image = references[0] if references else None
        occlusion = {'image': image, 'editor': None, 'raw': None, 'others': {}}

        group = Adw.PreferencesGroup()
        row = Adw.ActionRow(title=_('Image'), subtitle=image or _('No image chosen'),
                            activatable=True)
        button = Gtk.Button(label=_('Choose…'), valign=Gtk.Align.CENTER)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        group.add(row)
        self.fields_box.append(group)
        occlusion['row'] = row
        connect_weak(button, 'clicked', self._on_choose_image)

        try:
            from ..widgets.occlusion_editor import RetainOcclusionEditor
        except ImportError:
            RetainOcclusionEditor = None
        if RetainOcclusionEditor is not None:
            editor = RetainOcclusionEditor()
            editor.set_visible(image is not None)
            if image is not None:
                editor.set_image(str(self.collection.media.path(image)))
                editor.set_shapes(notetypes.occlusion_shapes(by_name.get('Occlusion', '')))
            self.fields_box.append(editor)
            occlusion['editor'] = editor
        else:
            occlusion['raw'] = self._add_editor('Occlusion', by_name.get('Occlusion', ''))
        self.occlusion = occlusion
        for name in names:
            if name not in ('Occlusion', 'Image'):
                occlusion['others'][name] = self._add_editor(name, by_name.get(name, ''))

    def _on_choose_image(self, _button):
        dialog = Gtk.FileDialog(title=_('Choose an Image'), modal=True)
        pictures = Gtk.FileFilter(name=_('Pictures'))
        pictures.add_pixbuf_formats()
        dialog.set_default_filter(pictures)
        root = self.get_root()
        ref = self.weak_ref()
        dialog.open(root if isinstance(root, Gtk.Window) else None, None,
                    lambda d, result: _finish_choose(d, result, ref))

    def set_occlusion_image(self, path):
        """Use a picture file for the image occlusion note: copied into the media folder."""
        if self.occlusion is None:
            return
        name = self.collection.media.add_file(path)
        self.occlusion['image'] = name
        self.occlusion['row'].set_subtitle(name)
        editor = self.occlusion['editor']
        if editor is not None:
            editor.set_image(str(self.collection.media.path(name)))
            editor.set_visible(True)

    def field_values(self):
        """The fields' HTML, in the note type's order."""
        if self.occlusion is None:
            return [editor.get_html() for editor in self.editors]
        values = {}
        occlusion = self.occlusion
        if occlusion['editor'] is not None:
            values['Occlusion'] = notetypes.occlusion_field(occlusion['editor'].get_shapes())
        else:
            values['Occlusion'] = occlusion['raw'].get_html()
        image = occlusion['image']
        values['Image'] = f'<img src="{image}">' if image else ''
        for name, editor in occlusion['others'].items():
            values[name] = editor.get_html()
        return [values.get(name, '') for name in self.notetype.field_names()]

    def clear_fields(self):
        for editor in self.editors:
            editor.clear()
        if self.occlusion is not None:
            self.occlusion['image'] = None
            self.occlusion['row'].set_subtitle(_('No image chosen'))
            editor = self.occlusion['editor']
            if editor is not None:
                editor.set_shapes([])
                editor.set_image(None)
                editor.set_visible(False)

    def focus_first_field(self):
        if self.editors:
            self.editors[0].grab_focus()

    def _update_cloze_hint(self, editor):
        count = len(template.cloze_numbers(editor.get_html()))
        if count:
            editor.set_hint(ngettext('Makes {n} card', 'Makes {n} cards', count).format(n=count))
        else:
            editor.set_hint(_('Select a word and choose Cloze to make a card'))

    # -- duplicates -------------------------------------------------------------------------

    def _on_first_field_changed(self, _editor):
        if self._duplicate_timer:
            GLib.source_remove(self._duplicate_timer)
        ref = self.weak_ref()
        self._duplicate_timer = GLib.timeout_add(DUPLICATE_DELAY, lambda: _check_later(ref))

    def _on_first_field_left(self, _editor):
        self.check_duplicates()

    def check_duplicates(self):
        """Look for notes of the type with this first field; the banner follows."""
        if self._duplicate_timer:
            GLib.source_remove(self._duplicate_timer)
            self._duplicate_timer = 0
        if self.notetype is None or not self.editors or self.occlusion is not None:
            return []
        first = self.editors[0].get_html()
        except_note = self.note.id if self.note is not None else None
        self._duplicate_ids = self.collection.find_duplicates(self.notetype.id, first,
                                                              except_note=except_note)
        self.duplicate_banner.set_revealed(bool(self._duplicate_ids))
        return self._duplicate_ids

    def _on_show_duplicates(self, _banner):
        ids = self._duplicate_ids
        window = self.parent_window
        self.close()
        if window is None or not ids:
            return
        if hasattr(window, 'show_root'):
            window.show_root('browse')
        page = window.navigation_view.get_visible_page() if hasattr(
            window, 'navigation_view') else None
        if page is not None and hasattr(page, 'search'):
            page.search(' or '.join(f'nid:{note_id}' for note_id in ids))

    # -- adding and saving ----------------------------------------------------------------

    def _on_primary(self, _button=None):
        if self.note is None:
            self.add()
        else:
            self.save()

    def add(self):
        """Add the note; True when it was added (else the banner says why not)."""
        global _last_tags
        notetype = self.notetype
        deck_id = self.selected_deck_id()
        if notetype is None or deck_id is None:
            self._show_error(_('Choose a note type and a deck first.'))
            return False
        tags = self.tags()
        try:
            note = self.collection.add_note(notetype.id, deck_id, self.field_values(), tags)
        except CollectionError as error:
            self._show_error(str(error))
            return False
        count = len(self.collection.cards_of_note(note.id))
        self.app.toast(ngettext('Card added', '{n} cards added', count).format(n=count),
                       undo=True)
        if self.collection.get('last_notetype') != notetype.id:
            self.collection.set('last_notetype', notetype.id)
        if self.collection.get('last_deck') != deck_id:
            self.collection.set('last_deck', deck_id)
        _last_tags = tags
        self.error_banner.set_revealed(False)
        self.duplicate_banner.set_revealed(False)
        self.clear_fields()
        self.focus_first_field()
        return True

    def save(self):
        """Store the edited note (its cards follow) and close."""
        note = self.note
        note.fields = self.field_values()
        note.tags = self.tags()
        deck_id = self.selected_deck_id()
        try:
            with self.collection.undoable(_('Edit Note')):
                self.collection.update_note(note, deck_id=deck_id)
                cards = self.collection.cards_of_note(note.id)
                moved = [card.id for card in cards if card.deck_id != deck_id]
                if deck_id is not None and moved:
                    self.collection.set_deck(moved, deck_id)
        except CollectionError as error:
            self._show_error(str(error))
            return False
        self.app.toast(_('Note saved'), undo=True)
        self.close()
        return True

    def _show_error(self, message):
        self.error_banner.set_title(message)
        self.error_banner.set_revealed(True)

    # -- keys and lifetime ----------------------------------------------------------------

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        mask = state & Gtk.accelerator_get_default_mod_mask()
        lower = Gdk.keyval_to_lower(keyval)
        for name, (ok, key, mods) in self._keys:
            if not ok or lower != Gdk.keyval_to_lower(key) or mask != mods:
                continue
            if name == 'save':
                self._on_primary()
                return Gdk.EVENT_STOP
            if name == 'next-field':
                return Gdk.EVENT_PROPAGATE  # the text views pass Tab on themselves
            editor = self.focused_editor()
            if editor is not None and editor.command(name):
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def focused_editor(self):
        widget = self.get_focus()
        while widget is not None:
            if isinstance(widget, FieldEditor):
                return widget
            widget = widget.get_parent()
        return None

    def _on_cancel(self, _button):
        self.close()

    def _on_manage_types(self, _button):
        from . import notetypes as manage

        notetype = self.selected_notetype()
        dialog = manage.present(self.app, self, notetype.id if notetype else None)
        connect_weak(dialog, 'closed', self._on_types_managed)
        return dialog

    def _on_types_managed(self, _dialog):
        """The types may have changed: list them again, and rebuild the fields with what was
        typed in them kept by field name."""
        notetype = self.selected_notetype()
        typed = {}
        if notetype is not None and self.occlusion is None:
            typed = dict(zip(notetype.field_names(), self.field_values(), strict=False))
        self._building = True
        try:
            self._fill_types(notetype.id if notetype else None)
        finally:
            self._building = False
        self._build_fields()
        if self.occlusion is None and self.notetype is not None:
            for name, editor in zip(self.notetype.field_names(), self.editors, strict=False):
                if typed.get(name):
                    editor.set_html(typed[name])

    def _on_type_changed(self, *_args):
        if not self._building:
            self._build_fields()
            self.focus_first_field()

    def _on_map(self, *_args):
        parent = self.parent_window
        if hasattr(parent, 'set_dialog_open'):
            parent.set_dialog_open(True)
        ref = self.weak_ref()
        GLib.idle_add(lambda: _focus_later(ref))

    def _on_closed(self, *_args):
        if self._duplicate_timer:
            GLib.source_remove(self._duplicate_timer)
            self._duplicate_timer = 0
        parent = self.parent_window
        if hasattr(parent, 'set_dialog_open'):
            parent.set_dialog_open(False)


def _setup_deck_item(_factory, list_item):
    list_item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))


def _bind_deck_item(_factory, list_item):
    item = list_item.get_item()
    label = list_item.get_child()
    label.set_label(item.title)
    label.set_margin_start(18 * item.level)


def _check_later(ref):
    dialog = ref()
    if dialog is not None:
        dialog._duplicate_timer = 0
        dialog.check_duplicates()
    return GLib.SOURCE_REMOVE


def _focus_later(ref):
    dialog = ref()
    if dialog is not None and dialog.get_mapped():
        dialog.focus_first_field()
    return GLib.SOURCE_REMOVE


def _finish_choose(file_dialog, result, ref):
    try:
        file = file_dialog.open_finish(result)
    except GLib.Error as error:
        if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
            log.warning('choosing an image: %s', error)
        return
    dialog = ref()
    if dialog is not None and file is not None and file.get_path():
        path = file.get_path()
        if media_kind(path) == 'img':
            dialog.set_occlusion_image(path)


def present_add(app, parent, deck_id=None, notetype_id=None):
    dialog = AddEditDialog(app, parent, deck_id=deck_id, notetype_id=notetype_id)
    dialog.present(parent)
    return dialog


def present_edit(app, parent, note_id):
    note = app.collection.note(note_id)
    if note is None:
        app.report(CollectionError(_('The note no longer exists.')))
        return None
    dialog = AddEditDialog(app, parent, note=note)
    dialog.present(parent)
    return dialog
