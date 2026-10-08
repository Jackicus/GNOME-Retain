# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Manage Note Types: the note types, their fields, their card templates and their styling.

    dialog = present(app, parent, notetype_id=None)   # on a type's page when one is given
    page = dialog.show_notetype(notetype_id)          # a NoteTypePage, pushed
    dialog.add_notetype(name, source) / rename_notetype(id, name) / confirm_delete(id)
    page.add_field(name), .rename_field(i, name), .move_field(i, j), .remove_field(i),
        .set_sort_field(i), .add_template(), .rename_template(i, name),
        .move_template(i, j), .remove_template(i)      each True when it was stored
    template_page = page.show_template(i)             # a TemplatePage, pushed
    template_page.set_side('front' | 'back' | 'styling'); .buffers[side]; .save()

One Adw.Dialog (notetypes.blp) holding an Adw.NavigationView: the list of types (each with
its note count; Add Note Type makes one from a stock kind or as a copy of a type), a type's
page (notetype_page.blp: its fields and card templates, each row with a menu), and a card
template's page (template_page.blp: the front, the back and the type's shared CSS in a
monospace editor, GtkSourceView 5 with HTML or CSS highlighting when it can be imported, a
Gtk.TextView otherwise, and a RetainCardView preview of the card, from the type's latest
note or from sample text).

A change to the fields or the templates' list is stored at once (notetypes.py's editing
methods on a copy, then Collection.update_notetype, which brings the notes and cards into
line), one undo step each, with a toast that says what went (a field's contents in how many
notes, a template's cards) and an Undo button: the toasts are the dialog's own, over its
pages. Only deleting a whole note type asks first (an Adw.AlertDialog with its note count).
A template's text and the CSS are stored when its page is left or the dialog closes, one
undo step; while a template has a problem (notetypes.NoteType.template_problem: an unclosed
conditional, an unknown field, a front without a field or a cloze) a banner says so with a
Revert button, the page cannot be left and the dialog cannot close, so nothing broken is
stored and nothing typed is lost. Pages refresh from the collection's `changed` signal (an
undo); a page whose type or template is gone is popped.
"""

import logging
from gettext import gettext as _
from gettext import ngettext

import gi

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import notetypes, template  # noqa: E402
from ..collection import CollectionError  # noqa: E402
from ..widgets.card_view import RetainCardView  # noqa: E402, F401  (template_page.ui uses it)
from ..widgets.util import connect_weak  # noqa: E402
from .deck_name import NameDialog, watch_dialog  # noqa: E402

log = logging.getLogger(__name__)

GtkSource = None
try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
except (ImportError, ValueError):
    log.info('GtkSourceView 5 is not available: templates are edited as plain text')

PREVIEW_DELAY = 250  # ms after the last keystroke
SIDES = ('front', 'back', 'styling')
ERRORS = (CollectionError, notetypes.NoteTypeError)


def _menu_button(menu, tooltip):
    button = Gtk.MenuButton(icon_name='view-more-symbolic', menu_model=menu,
                            valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    button.add_css_class('flat')
    return button


def _go_next():
    return Gtk.Image(icon_name='go-next-symbolic', accessible_role=Gtk.AccessibleRole.PRESENTATION)


def _item(menu, label, action, value):
    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(action, value)
    menu.append_item(item)


def _clear(group, rows):
    for row in rows:
        group.remove(row)
    rows.clear()


@Gtk.Template(resource_path='/io/github/jackicus/Retain/notetypes.ui')
class NoteTypesDialog(Adw.Dialog):
    """See the module."""

    __gtype_name__ = 'RetainNoteTypesDialog'

    toast_overlay = Gtk.Template.Child()
    navigation_view = Gtk.Template.Child()
    add_button = Gtk.Template.Child()
    list_group = Gtk.Template.Child()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.collection = app.collection
        self.last_toast = None
        self._rows = []
        group = Gio.SimpleActionGroup()
        for name, callback in (('rename', self._on_rename), ('delete', self._on_delete)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('x'))
            connect_weak(action, 'activate', callback)
            group.add_action(action)
        self.insert_action_group('notetypes', group)
        connect_weak(self.add_button, 'clicked', self._on_add)
        connect_weak(self.collection, 'changed', self._on_changed)
        connect_weak(self, 'close-attempt', self._on_close_attempt)
        connect_weak(self, 'closed', self._on_closed)
        self.refresh()

    # -- the list ---------------------------------------------------------------------------

    def refresh(self):
        _clear(self.list_group, self._rows)
        for notetype in self.collection.notetypes():
            count = self.collection.notetype_use(notetype.id)
            row = Adw.ActionRow(title=notetype.name, use_markup=False, activatable=True,
                                subtitle=ngettext('{n} note', '{n} notes', count).format(n=count))
            menu = Gio.Menu()
            value = GLib.Variant('x', notetype.id)
            _item(menu, _('_Rename…'), 'notetypes.rename', value)
            _item(menu, _('_Delete…'), 'notetypes.delete', value)
            row.add_suffix(_menu_button(menu, _('More')))
            row.add_suffix(_go_next())
            row.notetype_id = notetype.id
            connect_weak(row, 'activated', self._on_row_activated)
            self.list_group.add(row)
            self._rows.append(row)

    def row_for(self, notetype_id):
        return next((row for row in self._rows if row.notetype_id == notetype_id), None)

    def _on_row_activated(self, row):
        self.show_notetype(row.notetype_id)

    def _on_changed(self, _collection, kind):
        if kind not in ('notetypes', 'notes'):
            return
        self.refresh()
        for page in list(self._pages()):
            page.reload()

    def _pages(self):
        stack = self.navigation_view.get_navigation_stack()
        for index in range(stack.get_n_items()):
            page = stack.get_item(index)
            if isinstance(page, (NoteTypePage, TemplatePage)):
                yield page

    def show_notetype(self, notetype_id):
        """Push a type's page (after popping to the list)."""
        if self.collection.notetype(notetype_id) is None:
            return None
        self.navigation_view.pop_to_tag('list')
        page = NoteTypePage(self, notetype_id)
        self.navigation_view.push(page)
        return page

    def toast(self, text, undo=False):
        """A toast over the dialog's pages; with `undo`, its button undoes the last step."""
        toast = Adw.Toast(title=text, timeout=0 if undo else 5)
        if undo:
            toast.set_button_label(_('Undo'))
            toast.connect('button-clicked', lambda *_args: self.app.undo())
        self.toast_overlay.add_toast(toast)
        self.last_toast = toast
        return toast

    # -- adding, renaming, deleting ---------------------------------------------------------

    def sources(self):
        """What a new type can start from: [(label, NoteType)], the stock kinds first."""
        found = [(stock.name, stock) for stock in notetypes.all_stock()]
        for notetype in self.collection.notetypes():
            found.append((_('Copy of “{name}”').format(name=notetype.name), notetype))
        return found

    def add_notetype(self, name, source):
        """Store a new type made from `source` (a NoteType) and open its page."""
        notetype = self.collection.new_notetype(source, name)
        self.toast(_('Note type “{name}” added').format(name=notetype.name), undo=True)
        self.show_notetype(notetype.id)
        return notetype

    def _on_add(self, _button):
        sources = self.sources()
        row = Adw.ComboRow(title=_('Based On'), use_subtitle=False,
                           model=Gtk.StringList.new([label for label, _source in sources]))

        def apply(name):
            index = row.get_selected()
            self.add_notetype(name, sources[index][1] if 0 <= index < len(sources)
                              else notetypes.stock('basic'))
            return None

        dialog = NameDialog(_('Add Note Type'), _('_Add'), apply)
        group = dialog.entry.get_ancestor(Adw.PreferencesGroup)
        group.add(row)
        dialog.present_over(self)
        return dialog

    def rename_notetype(self, notetype_id, name):
        old = self.collection.notetype(notetype_id)
        self.collection.rename_notetype(notetype_id, name)
        if old is not None and old.name != name.strip():
            self.toast(_('Note type renamed to “{name}”').format(name=name.strip()), undo=True)

    def _on_rename(self, _action, value):
        notetype = self.collection.notetype(value.get_int64())
        if notetype is None:
            return None

        def apply(name):
            self.rename_notetype(notetype.id, name)
            return None

        dialog = NameDialog(_('Rename Note Type'), _('_Rename'), apply, text=notetype.name)
        dialog.entry.select_region(0, -1)
        return dialog.present_over(self)

    def confirm_delete(self, notetype_id):
        """Ask before deleting a type with its notes (the alert), or say why it cannot go."""
        notetype = self.collection.notetype(notetype_id)
        if notetype is None:
            return None
        if len(self.collection.notetypes()) <= 1:
            self.toast(_('The last note type cannot be deleted'))
            return None
        count = self.collection.notetype_use(notetype_id)
        dialog = Adw.AlertDialog(
            heading=_('Delete “{name}”?').format(name=notetype.name),
            body=ngettext('This deletes its {n} note and the note’s cards. You can undo this.',
                          'This deletes its {n} notes and their cards. You can undo this.',
                          count).format(n=count))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('delete', _('_Delete'))
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        ref = self.weak_ref()

        def on_response(_dialog, response):
            owner = ref()
            if owner is None or response != 'delete':
                return
            try:
                owner.collection.remove_notetype(notetype_id)
            except CollectionError as error:
                owner.app.report(error)
                return
            owner.navigation_view.pop_to_tag('list')
            owner.toast(_('Note type deleted'), undo=True)

        dialog.connect('response', on_response)
        dialog.present(self)
        return dialog

    def _on_delete(self, _action, value):
        self.confirm_delete(value.get_int64())

    # -- closing ----------------------------------------------------------------------------

    def update_can_close(self):
        self.set_can_close(all(page.problem is None for page in self._pages()
                               if isinstance(page, TemplatePage)))

    def _on_close_attempt(self, _dialog):
        for page in self._pages():
            if isinstance(page, TemplatePage) and page.problem is not None:
                self.navigation_view.pop_to_page(page)

    def _on_closed(self, _dialog):
        for page in list(self._pages()):
            if isinstance(page, TemplatePage):
                page.stop_preview()
                page.save()


@Gtk.Template(resource_path='/io/github/jackicus/Retain/notetype_page.ui')
class NoteTypePage(Adw.NavigationPage):
    """A note type's fields and card templates (see the module)."""

    __gtype_name__ = 'RetainNoteTypePage'

    fields_group = Gtk.Template.Child()
    templates_group = Gtk.Template.Child()
    add_field_button = Gtk.Template.Child()
    add_template_button = Gtk.Template.Child()

    def __init__(self, dialog, notetype_id):
        super().__init__()
        self.dialog = dialog
        self.app = dialog.app
        self.collection = dialog.collection
        self.notetype_id = notetype_id
        self.notetype = None
        self.field_rows = []
        self.template_rows = []
        group = Gio.SimpleActionGroup()
        for name, callback in (
                ('rename-field', self._on_rename_field), ('field-up', self._on_field_up),
                ('field-down', self._on_field_down), ('sort-field', self._on_sort_field),
                ('delete-field', self._on_delete_field),
                ('rename-template', self._on_rename_template),
                ('template-up', self._on_template_up),
                ('template-down', self._on_template_down),
                ('delete-template', self._on_delete_template)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('i'))
            connect_weak(action, 'activate', callback)
            group.add_action(action)
        self.insert_action_group('notetype', group)
        connect_weak(self.add_field_button, 'clicked', self._on_add_field)
        connect_weak(self.add_template_button, 'clicked', self._on_add_template)
        self.reload()

    def reload(self):
        """Show the type as stored; pop when it is gone."""
        self.notetype = self.collection.notetype(self.notetype_id)
        if self.notetype is None:
            self.dialog.navigation_view.pop_to_tag('list')
            return
        self.set_title(self.notetype.name)
        self._fill_fields()
        self._fill_templates()

    def _fill_fields(self):
        _clear(self.fields_group, self.field_rows)
        names = self.notetype.field_names()
        for index, name in enumerate(names):
            sort = index == self.notetype.sort_field
            row = Adw.ActionRow(title=name, use_markup=False,
                                subtitle=_('Sorts the browser') if sort else '')
            menu = Gio.Menu()
            value = GLib.Variant('i', index)
            _item(menu, _('_Rename…'), 'notetype.rename-field', value)
            if index > 0:
                _item(menu, _('Move _Up'), 'notetype.field-up', value)
            if index < len(names) - 1:
                _item(menu, _('Move _Down'), 'notetype.field-down', value)
            if not sort:
                _item(menu, _('_Sort by This Field'), 'notetype.sort-field', value)
            if len(names) > 1:
                section = Gio.Menu()
                _item(section, _('_Delete'), 'notetype.delete-field', value)
                menu.append_section(None, section)
            row.add_suffix(_menu_button(menu, _('Field Options')))
            self.fields_group.add(row)
            self.field_rows.append(row)

    def _fill_templates(self):
        _clear(self.templates_group, self.template_rows)
        editable = self.notetype.has_templates_to_edit
        self.add_template_button.set_visible(editable)
        self.templates_group.set_title(_('Card Templates') if editable else _('Card Template'))
        self.templates_group.set_description(
            '' if editable else _('A cloze note makes a card for each cloze number'))
        count = len(self.notetype.templates)
        for index, card in enumerate(self.notetype.templates):
            if not editable and index > 0:
                break
            used = self.collection.template_use(self.notetype_id, index) if editable else \
                self.collection.notetype_use(self.notetype_id)
            subtitle = ngettext('{n} card', '{n} cards', used).format(n=used) if editable \
                else _('Front, back and styling')
            row = Adw.ActionRow(title=card['name'], subtitle=subtitle, use_markup=False,
                                activatable=True)
            if editable:
                menu = Gio.Menu()
                value = GLib.Variant('i', index)
                _item(menu, _('_Rename…'), 'notetype.rename-template', value)
                if index > 0:
                    _item(menu, _('Move _Up'), 'notetype.template-up', value)
                if index < count - 1:
                    _item(menu, _('Move _Down'), 'notetype.template-down', value)
                if count > 1:
                    section = Gio.Menu()
                    _item(section, _('_Delete'), 'notetype.delete-template', value)
                    menu.append_section(None, section)
                row.add_suffix(_menu_button(menu, _('Card Template Options')))
            row.add_suffix(_go_next())
            row.template_index = index
            connect_weak(row, 'activated', self._on_template_activated)
            self.templates_group.add(row)
            self.template_rows.append(row)

    # -- storing an edit ----------------------------------------------------------------------

    def _store(self, edit, label, message):
        """Apply `edit(notetype)` to a copy of the stored type and store it, one undo step
        called `label`; the toast says `message` (a function of the stored type, or a
        string). The error, as a sentence, when refused; else None."""
        notetype = self.collection.notetype(self.notetype_id)
        if notetype is None:
            return _('The note type no longer exists.')
        try:
            edit(notetype)
            self.collection.update_notetype(notetype, label=label)
        except ERRORS as error:
            return str(error)
        self.dialog.toast(message, undo=True)
        self.reload()
        return None

    def _apply(self, edit, label, message):
        """_store, its error toasted; True when stored."""
        error = self._store(edit, label, message)
        if error:
            self.dialog.toast(error)
        return error is None

    def add_field(self, name):
        name = notetypes.clean_field_name(name)
        return self._apply(lambda nt: nt.add_field(name), _('Add Field'),
                           _('Field “{name}” added').format(name=name))

    def rename_field(self, index, name):
        name = notetypes.clean_field_name(name)
        if name == self.notetype.fields[index]['name']:
            return True
        return self._apply(lambda nt: nt.rename_field(index, name), _('Rename Field'),
                           _('Field renamed to “{name}”').format(name=name))

    def move_field(self, index, new_index):
        return self._apply(lambda nt: nt.move_field(index, new_index), _('Move Field'),
                           _('Field moved'))

    def set_sort_field(self, index):
        name = self.notetype.fields[index]['name']
        return self._apply(lambda nt: nt.set_sort_field(index), _('Change Sort Field'),
                           _('The browser sorts by “{name}”').format(name=name))

    def remove_field(self, index):
        name = self.notetype.fields[index]['name']
        used = self.collection.field_use(self.notetype_id, index)
        if used:
            message = ngettext('Field “{name}” deleted with its text in {n} note',
                               'Field “{name}” deleted with its text in {n} notes',
                               used).format(name=name, n=used)
        else:
            message = _('Field “{name}” deleted').format(name=name)
        return self._apply(lambda nt: nt.remove_field(index), _('Delete Field'), message)

    def add_template(self):
        name = self.notetype.new_template_name()
        stored = self._apply(lambda nt: nt.add_template(name), _('Add Card Template'),
                             _('Card template “{name}” added').format(name=name))
        if stored:
            self.show_template(len(self.notetype.templates) - 1)
        return stored

    def rename_template(self, index, name):
        name = name.strip()
        return self._apply(lambda nt: nt.rename_template(index, name),
                           _('Rename Card Template'),
                           _('Card template renamed to “{name}”').format(name=name))

    def move_template(self, index, new_index):
        return self._apply(lambda nt: nt.move_template(index, new_index),
                           _('Move Card Template'), _('Card template moved'))

    def remove_template(self, index):
        name = self.notetype.templates[index]['name']
        used = self.collection.template_use(self.notetype_id, index)
        message = ngettext('Card template “{name}” deleted with its {n} card',
                           'Card template “{name}” deleted with its {n} cards',
                           used).format(name=name, n=used)
        return self._apply(lambda nt: nt.remove_template(index), _('Delete Card Template'),
                           message)

    def show_template(self, index):
        if not 0 <= index < len(self.notetype.templates):
            return None
        page = TemplatePage(self.dialog, self.notetype_id, index)
        self.dialog.navigation_view.push(page)
        return page

    # -- the rows' actions ------------------------------------------------------------------

    def _ask_name(self, title, text, apply):
        dialog = NameDialog(title, _('_Rename') if text else _('_Add'), apply, text=text)
        if text:
            dialog.entry.select_region(0, -1)
        return dialog.present_over(self.dialog)

    def _on_add_field(self, _button):
        return self._ask_name(_('Add Field'), '', lambda name: self._store(
            lambda nt: nt.add_field(name), _('Add Field'),
            _('Field “{name}” added').format(name=notetypes.clean_field_name(name))))

    def _on_rename_field(self, _action, value):
        index = value.get_int32()
        return self._ask_name(_('Rename Field'), self.notetype.fields[index]['name'],
                              lambda name: self._store(
                                  lambda nt: nt.rename_field(index, name), _('Rename Field'),
                                  _('Field renamed to “{name}”').format(
                                      name=notetypes.clean_field_name(name))))

    def _on_field_up(self, _action, value):
        self.move_field(value.get_int32(), value.get_int32() - 1)

    def _on_field_down(self, _action, value):
        self.move_field(value.get_int32(), value.get_int32() + 1)

    def _on_sort_field(self, _action, value):
        self.set_sort_field(value.get_int32())

    def _on_delete_field(self, _action, value):
        self.remove_field(value.get_int32())

    def _on_add_template(self, _button):
        self.add_template()

    def _on_rename_template(self, _action, value):
        index = value.get_int32()
        return self._ask_name(_('Rename Card Template'), self.notetype.templates[index]['name'],
                              lambda name: self._store(
                                  lambda nt: nt.rename_template(index, name.strip()),
                                  _('Rename Card Template'),
                                  _('Card template renamed to “{name}”').format(
                                      name=name.strip())))

    def _on_template_up(self, _action, value):
        self.move_template(value.get_int32(), value.get_int32() - 1)

    def _on_template_down(self, _action, value):
        self.move_template(value.get_int32(), value.get_int32() + 1)

    def _on_delete_template(self, _action, value):
        self.remove_template(value.get_int32())

    def _on_template_activated(self, row):
        self.show_template(row.template_index)


@Gtk.Template(resource_path='/io/github/jackicus/Retain/template_page.ui')
class TemplatePage(Adw.NavigationPage):
    """A card template's front and back and the type's CSS, with a preview (see the
    module)."""

    __gtype_name__ = 'RetainTemplatePage'

    problem_banner = Gtk.Template.Child()
    side_group = Gtk.Template.Child()
    editor_scroll = Gtk.Template.Child()
    preview_caption = Gtk.Template.Child()
    card_view = Gtk.Template.Child()

    def __init__(self, dialog, notetype_id, index):
        super().__init__()
        self.dialog = dialog
        self.app = dialog.app
        self.collection = dialog.collection
        self.notetype_id = notetype_id
        self.index = index
        self.stored = self.collection.notetype(notetype_id)
        self.problem = None
        self._preview_timer = 0
        self._loading = False
        self.set_title(self.stored.templates[index]['name'])
        self.buffers = {side: self._make_buffer(side) for side in SIDES}
        self.view = self._make_view()
        self.editor_scroll.set_child(self.view)
        self._set_texts(self.stored)
        for buffer in self.buffers.values():
            connect_weak(buffer, 'changed', self._on_text_changed)
        connect_weak(self.side_group, 'notify::active-name', self._on_side_changed)
        connect_weak(self.problem_banner, 'button-clicked', self._on_revert)
        connect_weak(self, 'hidden', self._on_hidden)
        style = Adw.StyleManager.get_default()
        connect_weak(style, 'notify::dark', self._on_dark)
        self._on_dark(style)
        self.set_side('front')
        self._check()
        self.render_preview()

    # -- the editor -------------------------------------------------------------------------

    def _make_buffer(self, side):
        if GtkSource is None:
            return Gtk.TextBuffer()
        buffer = GtkSource.Buffer()
        language = GtkSource.LanguageManager.get_default().get_language(
            'css' if side == 'styling' else 'html')
        if language is not None:
            buffer.set_language(language)
        return buffer

    def _make_view(self):
        if GtkSource is not None:
            view = GtkSource.View(auto_indent=True, tab_width=4, insert_spaces_instead_of_tabs=True,
                                  highlight_current_line=False)
        else:
            view = Gtk.TextView()
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for side in ('top', 'bottom', 'left', 'right'):
            getattr(view, f'set_{side}_margin')(12 if side in ('left', 'right') else 9)
        view.add_css_class('template-editor')
        view.update_property([Gtk.AccessibleProperty.LABEL], [_('Front Template')])
        return view

    def _on_dark(self, manager, *_args):
        if GtkSource is None:
            return
        scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(
            'Adwaita-dark' if manager.get_dark() else 'Adwaita')
        if scheme is not None:
            for buffer in self.buffers.values():
                buffer.set_style_scheme(scheme)

    def _set_texts(self, notetype):
        card = notetype.templates[self.index]
        self._loading = True
        try:
            for side, text in zip(SIDES, (card['qfmt'], card['afmt'], notetype.css),
                                  strict=True):
                buffer = self.buffers[side]
                buffer.begin_irreversible_action()  # not undone into the stored text
                buffer.set_text(text)
                buffer.end_irreversible_action()
        finally:
            self._loading = False

    def text(self, side):
        buffer = self.buffers[side]
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    @property
    def side(self):
        return self.side_group.get_active_name() or 'front'

    def set_side(self, side):
        if self.side_group.get_active_name() != side:
            self.side_group.set_active_name(side)
        self.view.set_buffer(self.buffers[side])
        labels = {'front': _('Front Template'), 'back': _('Back Template'),
                  'styling': _('Styling')}
        self.view.update_property([Gtk.AccessibleProperty.LABEL], [labels[side]])
        self._show_side()

    def _on_side_changed(self, *_args):
        self.set_side(self.side)

    def working(self):
        """The stored type with this page's texts in it."""
        notetype = self.stored.copy()
        card = notetype.templates[self.index]
        card['qfmt'] = self.text('front')
        card['afmt'] = self.text('back')
        notetype.css = self.text('styling')
        return notetype

    def changed(self):
        card = self.stored.templates[self.index]
        return (self.text('front'), self.text('back'), self.text('styling')) != (
            card['qfmt'], card['afmt'], self.stored.css)

    def _check(self):
        """Show the template's problem (only when edited: what was stored stays allowed)."""
        self.problem = self.working().template_problem(self.index) if self.changed() else None
        self.problem_banner.set_title(self.problem or '')
        self.problem_banner.set_revealed(self.problem is not None)
        self.set_can_pop(self.problem is None)
        self.dialog.update_can_close()

    def _on_text_changed(self, _buffer):
        if self._loading:
            return
        self._check()
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
        ref = self.weak_ref()

        def render():
            page = ref()
            if page is not None:
                page._preview_timer = 0
                page.render_preview()
            return GLib.SOURCE_REMOVE

        self._preview_timer = GLib.timeout_add(PREVIEW_DELAY, render)

    def _on_revert(self, _banner):
        self._set_texts(self.stored)
        self._check()
        self.render_preview()

    # -- the preview --------------------------------------------------------------------------

    def preview_html(self):
        """The question and answer of the card as the page's texts would render it, from the
        type's latest note (its card for this template when it has one), or from sample
        text; None while a template cannot be parsed."""
        notetype = self.working()
        note = self.collection.sample_note(self.notetype_id)
        cloze = not notetype.has_templates_to_edit
        try:
            if note is not None:
                cards = self.collection.cards_of_note(note.id)
                card = next((c for c in cards if cloze or c.ord == self.index), None)
                if card is not None:
                    return self.collection.render_card(card, note, notetype)
                fields = dict(zip(notetype.field_names(), note.fields, strict=False))
            else:
                fields = self.sample_fields(notetype)
            card = notetype.templates[self.index]
            special = {'Tags': '', 'Type': notetype.name, 'Deck': '', 'Subdeck': '',
                       'Card': card['name'], 'CardFlag': ''}
            return template.render(card['qfmt'], card['afmt'], fields,
                                   ord=0 if cloze else self.index, kind=notetype.kind,
                                   special=special)
        except template.TemplateError:
            return None

    def sample_fields(self, notetype):
        """Each field's name in brackets; a cloze type's cloze fields as a first cloze."""
        try:
            clozes = template.cloze_fields_in(notetype.templates[self.index]['qfmt'])
        except template.TemplateError:
            clozes = set()
        return {name: '{{c1::(' + name + ')}}' if name in clozes else f'({name})'
                for name in notetype.field_names()}

    def render_preview(self):
        rendered = self.preview_html()
        if rendered is None:
            return False
        question, answer = rendered
        note = self.collection.sample_note(self.notetype_id)
        self.preview_caption.set_text(_('Your latest note') if note else _('Sample text'))
        notetype = self.working()
        occlusion = None
        if note is not None and notetypes.is_image_occlusion(notetype):
            names = notetype.field_names()
            index = names.index('Occlusion') if 'Occlusion' in names else 0
            occlusion = {'shapes': notetypes.occlusion_shapes(note.fields[index]
                                                              if index < len(note.fields)
                                                              else ''),
                         'ordinal': 1}
        media_uri = self.collection.media.directory.resolve().as_uri() + '/'
        self.card_view.set_card(question, answer, notetype.css, media_uri, occlusion=occlusion)
        self._show_side()
        return True

    def _show_side(self):
        if self.side == 'front':
            self.card_view.show_question()
        else:
            self.card_view.show_answer()

    # -- storing ----------------------------------------------------------------------------

    def save(self):
        """Store the texts (one undo step, with a toast); False when unchanged or while
        they have a problem."""
        if not self.changed() or self.problem is not None:
            return False
        if self.collection.notetype(self.notetype_id) is None:
            return False
        notetype = self.collection.notetype(self.notetype_id)
        if self.index >= len(notetype.templates):
            return False
        card = notetype.templates[self.index]
        card['qfmt'] = self.text('front')
        card['afmt'] = self.text('back')
        notetype.css = self.text('styling')
        try:
            self.collection.update_notetype(notetype, label=_('Edit Card Template'))
        except CollectionError as error:
            self.dialog.toast(str(error))
            return False
        self.stored = self.collection.notetype(self.notetype_id)
        self.dialog.toast(_('Card template saved'), undo=True)
        return True

    def reload(self):
        """After an undo: follow the stored type, keeping what was typed since."""
        stored = self.collection.notetype(self.notetype_id)
        if stored is None or self.index >= len(stored.templates):
            self.set_can_pop(True)
            self.dialog.navigation_view.pop_to_tag('list')
            return
        unchanged = not self.changed()
        self.stored = stored
        if unchanged:
            self._set_texts(stored)
            self.render_preview()
        self._check()

    def stop_preview(self):
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0

    def _on_hidden(self, _page):
        self.stop_preview()
        self.save()


def present(app, parent, notetype_id=None):
    dialog = NoteTypesDialog(app)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    if notetype_id is not None:
        dialog.show_notetype(notetype_id)
    return dialog
