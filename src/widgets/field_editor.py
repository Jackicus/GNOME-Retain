# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""One field of a note in the editor: its name, a formatting toolbar shown while the field
has the focus, and a text view that grows with the text.

    editor = FieldEditor('Front', media=collection.media, cloze=False)
    editor.set_html('<b>hola</b>'); editor.get_html()
    editor.clear(); editor.is_empty(); editor.grab_focus()
    editor.command('bold' | 'italic' | 'underline' | 'cloze' | 'cloze-same' | 'attach')
    editor.attach_path('/home/me/cat.jpg')      # copies it into the media folder, inserts it
    editor.set_hint('Makes 3 cards')            # a caption under the field ('' hides it)
    signals: changed, focus-left

The text view holds rich text through html_text's tags and anchors (a picture shows as a
thumbnail at most THUMBNAIL_HEIGHT tall, a sound as a chip). A field whose HTML the editor
could not hold as rich text (html_text.can_round_trip) is edited as raw HTML, monospaced,
under a banner saying so, and nothing in it is lost. Tab moves to the next field (the view's
accepts-tab is off); the toolbar's buttons take no focus, so the view keeps it when one is
clicked. Pasting keeps the text and drops the formatting; an image on the clipboard is added
to the media folder as a PNG. Bold, italic and underline toggle the selection, or what is
typed next when there is none. `connect_weak` is here for the dialog too: a child's signal
bound to the parent's method would keep the parent alive through C.
"""

import logging
import time
import weakref
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .. import template
from . import html_text
from .util import connect_weak

log = logging.getLogger(__name__)

THUMBNAIL_HEIGHT = 240
THUMBNAIL_WIDTH = 320  # fits the field at 360 px
BUTTON_SIZE = 32


def media_kind(name):
    """'img' for a picture file's name, else 'sound' (Anki plays video through [sound:] too)."""
    content_type, _uncertain = Gio.content_type_guess(name, None)
    return 'img' if content_type and content_type.startswith('image/') else 'sound'


class Thumbnail(GObject.Object, Gdk.Paintable):
    """A texture drawn no taller than THUMBNAIL_HEIGHT (nor wider than THUMBNAIL_WIDTH): a
    Gtk.Picture over it asks for the scaled size, where the texture's would be its own. (The
    picture must not shrink: a text view gives an anchored child its minimum size.)"""

    def __init__(self, texture):
        super().__init__()
        self.texture = texture
        width, height = texture.get_width(), texture.get_height()
        scale = min(1.0, THUMBNAIL_HEIGHT / max(height, 1), THUMBNAIL_WIDTH / max(width, 1))
        self.width = max(1, round(width * scale))
        self.height = max(1, round(height * scale))

    def do_get_intrinsic_width(self):
        return self.width

    def do_get_intrinsic_height(self):
        return self.height

    def do_get_intrinsic_aspect_ratio(self):
        return self.width / self.height

    def do_get_flags(self):
        return Gdk.PaintableFlags.SIZE | Gdk.PaintableFlags.CONTENTS

    def do_snapshot(self, snapshot, width, height):
        self.texture.snapshot(snapshot, width, height)


class FieldEditor(Gtk.Box):
    """See the module."""

    __gtype_name__ = 'RetainFieldEditor'

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'focus-left': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, name, media=None, cloze=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.name = name
        self.media = media  # a media.MediaStore, or None (no attaching)
        self.cloze = cloze  # a cloze note type's field: the Cloze button shows
        self.raw = False  # editing the field as HTML
        self._loading = False
        self._pending = None  # (cursor offset, set of tag names) for the next typed text
        self._build()

    # -- building ------------------------------------------------------------------------

    def _build(self):
        header = Gtk.Box(spacing=6, height_request=BUTTON_SIZE)
        self.label = Gtk.Label(label=self.name, xalign=0, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.END)
        self.label.add_css_class('caption')
        self.label.add_css_class('heading')
        header.append(self.label)
        self.toolbar = Gtk.Box(spacing=2, visible=False)
        self.toolbar.add_css_class('field-editor-toolbar')
        self.format_buttons = []
        for command, icon, tooltip in (
                ('bold', 'format-text-bold-symbolic', _('Bold')),
                ('italic', 'format-text-italic-symbolic', _('Italic')),
                ('underline', 'format-text-underline-symbolic', _('Underline'))):
            self.format_buttons.append(self._tool_button(command, icon, tooltip))
        self.cloze_button = self._tool_button('cloze', 'view-conceal-symbolic',
                                              _('Cloze Deletion'))
        self.attach_button = self._tool_button('attach', 'mail-attachment-symbolic',
                                               _('Attach a Picture or Sound'))
        self.attach_button.set_visible(self.media is not None)
        self.cloze_button.set_visible(self.cloze)
        header.append(self.toolbar)
        self.append(header)

        self.banner = Adw.Banner(
            title=_('This field has formatting the editor keeps as HTML'), revealed=False)
        self.append(self.banner)

        self.buffer = Gtk.TextBuffer()
        html_text.ensure_tags(self.buffer)
        self.text_view = Gtk.TextView(buffer=self.buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      accepts_tab=False, left_margin=8, right_margin=8,
                                      top_margin=8, bottom_margin=8, height_request=64)
        self.text_view.add_css_class('field-editor')
        self.text_view.update_property([Gtk.AccessibleProperty.LABEL], [self.name])
        self.append(self.text_view)

        self.hint_label = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.hint_label.add_css_class('caption')
        self.hint_label.add_css_class('dimmed')
        self.append(self.hint_label)

        connect_weak(self.buffer, 'changed', self._on_buffer_changed)
        self.buffer.connect_after('insert-text', self._weak('_on_insert_text'))
        connect_weak(self.buffer, 'mark-set', self._on_mark_set)
        connect_weak(self.text_view, 'paste-clipboard', self._on_paste)
        focus = Gtk.EventControllerFocus()
        connect_weak(focus, 'enter', self._on_focus_enter)
        connect_weak(focus, 'leave', self._on_focus_leave)
        self.text_view.add_controller(focus)
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        connect_weak(drop, 'drop', self._on_drop)
        self.text_view.add_controller(drop)

    def _weak(self, name):
        ref = weakref.ref(self)

        def handler(*args):
            editor = ref()
            if editor is not None:
                return getattr(editor, name)(*args)
            return None

        return handler

    def _tool_button(self, command, icon, tooltip):
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, focusable=False,
                            focus_on_click=False, valign=Gtk.Align.CENTER)
        button.add_css_class('flat')
        button.set_size_request(BUTTON_SIZE, BUTTON_SIZE)
        run = self._weak('command')
        button.connect('clicked', lambda *_args: run(command))
        self.toolbar.append(button)
        return button

    # -- content ----------------------------------------------------------------------------

    def set_html(self, html):
        """Show a field's HTML: as rich text when the editor can hold it, else as HTML."""
        html = html or ''
        self._loading = True
        try:
            self._set_raw(not html_text.can_round_trip(html))
            if self.raw:
                self.buffer.begin_irreversible_action()
                self.buffer.set_text(html)
                self.buffer.end_irreversible_action()
            else:
                media_dir = self.media.directory if self.media is not None else None
                for anchor in html_text.html_to_buffer(html, self.buffer, media_dir):
                    self._place(anchor)
        finally:
            self._loading = False
        self._pending = None
        self.emit('changed')

    def get_html(self):
        if self.raw:
            return self.buffer.get_text(self.buffer.get_start_iter(),
                                        self.buffer.get_end_iter(), True)
        return html_text.buffer_to_html(self.buffer)

    def clear(self):
        self.set_html('')

    def is_empty(self):
        return not template.strip_html(self.get_html()) and not template.media_references(
            self.get_html())

    def set_hint(self, text):
        self.hint_label.set_label(text or '')
        self.hint_label.set_visible(bool(text))

    def grab_focus(self):
        return self.text_view.grab_focus()

    def _set_raw(self, raw):
        self.raw = raw
        self.banner.set_revealed(raw)
        self.text_view.set_monospace(raw)
        for button in self.format_buttons:
            button.set_visible(not raw)

    # -- commands ---------------------------------------------------------------------------

    def command(self, name):
        """Run a toolbar command by name; True when it was one."""
        if name in html_text.TAGS and not self.raw:
            self.toggle_tag(name)
        elif name == 'cloze':
            self.insert_cloze(same=False)
        elif name == 'cloze-same':
            self.insert_cloze(same=True)
        elif name == 'attach':
            self.attach()
        else:
            return False
        return True

    def toggle_tag(self, name):
        """Bold, italic or underline on the selection, or on what is typed next."""
        tag = self.buffer.get_tag_table().lookup(name)
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            inside = start.copy()
            whole = start.has_tag(tag) and (
                not inside.forward_to_tag_toggle(tag) or inside.compare(end) >= 0)
            self.buffer.begin_user_action()
            if whole:
                self.buffer.remove_tag(tag, start, end)
            else:
                self.buffer.apply_tag(tag, start, end)
            self.buffer.end_user_action()
            return
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if self._pending is None or self._pending[0] != cursor.get_offset():
            before = cursor.copy()
            before.backward_char()
            current = {t.get_property('name') for t in before.get_tags()} & set(html_text.TAGS)
            self._pending = (cursor.get_offset(), current)
        self._pending[1].symmetric_difference_update({name})

    def insert_cloze(self, same=False):
        """Wrap the selection in {{cN::…}}: N one past the highest cloze in the field, or
        the highest itself with `same` (1 when there is none); without a selection, an
        empty cloze with the cursor inside."""
        numbers = template.cloze_numbers(self.get_html())
        highest = max(numbers) if numbers else 0
        number = max(highest, 1) if same else highest + 1
        bounds = self.buffer.get_selection_bounds()
        self.buffer.begin_user_action()
        if bounds:
            start, end = bounds
            end_mark = self.buffer.create_mark(None, end, False)
            self.buffer.insert(start, f'{{{{c{number}::')
            end = self.buffer.get_iter_at_mark(end_mark)
            self.buffer.insert(end, '}}')
            self.buffer.delete_mark(end_mark)
        else:
            self.buffer.insert_at_cursor(f'{{{{c{number}::}}}}')
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            cursor.backward_chars(2)
            self.buffer.place_cursor(cursor)
        self.buffer.end_user_action()
        self.text_view.grab_focus()

    def attach(self):
        """Choose a picture or a sound file to add to the field."""
        if self.media is None:
            return
        dialog = Gtk.FileDialog(title=_('Attach a Picture or Sound'), modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        both = Gtk.FileFilter(name=_('Pictures and Sounds'))
        both.add_pixbuf_formats()
        both.add_mime_type('audio/*')
        both.add_mime_type('video/*')
        pictures = Gtk.FileFilter(name=_('Pictures'))
        pictures.add_pixbuf_formats()
        sounds = Gtk.FileFilter(name=_('Sounds'))
        sounds.add_mime_type('audio/*')
        sounds.add_mime_type('video/*')
        for item in (both, pictures, sounds):
            filters.append(item)
        dialog.set_filters(filters)
        dialog.set_default_filter(both)
        root = self.get_root()
        dialog.open(root if isinstance(root, Gtk.Window) else None, None,
                    self._weak('_on_attach_chosen'))

    def _on_attach_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                log.warning('attaching a file: %s', error)
            return
        path = file.get_path() if file is not None else None
        if path:
            self.attach_path(path)
        self.text_view.grab_focus()

    def attach_path(self, path):
        """Copy a file into the media folder and insert it at the cursor; the name used."""
        if self.media is None:
            return None
        try:
            name = self.media.add_file(path)
        except OSError as error:
            log.warning('attaching %s: %s', path, error)
            return None
        self.insert_media(media_kind(name), name)
        return name

    def attach_texture(self, texture):
        """Store a texture (a pasted image) as a PNG in the media folder and insert it."""
        if self.media is None:
            return None
        data = texture.save_to_png_bytes().get_data()
        name = self.media.add_bytes(data, f'paste-{int(time.time())}.png')
        self.insert_media('img', name)
        return name

    def insert_media(self, kind, name):
        """A picture or a sound at the cursor, replacing the selection."""
        self.buffer.begin_user_action()
        self.buffer.delete_selection(True, True)
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if self.raw:
            self.buffer.insert(cursor, html_text.Media(kind, name).to_html())
        else:
            anchor = html_text.insert_media(self.buffer, cursor, kind, name,
                                            self.media.directory if self.media else None)
            self._place(anchor)
        self.buffer.end_user_action()

    def _place(self, anchor):
        """The widget shown at a media anchor: a thumbnail, or a chip for a sound."""
        media = html_text.media_of(anchor)
        if media.kind == 'img':
            widget = None
            if media.path is not None:
                try:
                    texture = Gdk.Texture.new_from_filename(media.path)
                    widget = Gtk.Picture(paintable=Thumbnail(texture), can_shrink=False,
                                         halign=Gtk.Align.START)
                except GLib.Error as error:
                    log.info('no thumbnail for %s: %s', media.name, error)
            if widget is None:
                widget = Gtk.Image(icon_name='image-missing-symbolic', pixel_size=32)
            widget.set_tooltip_text(media.name)
            widget.update_property([Gtk.AccessibleProperty.LABEL], [media.name])
            widget.set_accessible_role(Gtk.AccessibleRole.IMG)
        else:
            widget = Gtk.Button(label=f'♪ {media.name}', focusable=False, focus_on_click=False,
                                tooltip_text=media.name, valign=Gtk.Align.BASELINE)
            widget.add_css_class('pill')
            widget.add_css_class('sound-chip')
            if media.path is not None:
                widget.connect('clicked', lambda *_args, path=media.path: _play(path))
        self.text_view.add_child_at_anchor(widget, anchor)

    # -- signals ---------------------------------------------------------------------------

    def _on_buffer_changed(self, _buffer):
        if not self._loading:
            self.emit('changed')

    def _on_insert_text(self, buffer, location, text, _length):
        # After the default handler: `location` is the end of what was inserted. Typed text
        # takes the formatting the toolbar was set to with no selection.
        if self._pending is None or self.raw or self._loading:
            return
        start_offset, wanted = self._pending
        end = location.get_offset()
        if end - len(text) != start_offset:
            self._pending = None
            return
        start = buffer.get_iter_at_offset(start_offset)
        for name in html_text.TAGS:
            tag = buffer.get_tag_table().lookup(name)
            if name in wanted:
                buffer.apply_tag(tag, start, location)
            else:
                buffer.remove_tag(tag, start, location)
        self._pending = (end, wanted)

    def _on_mark_set(self, buffer, location, mark):
        if mark is buffer.get_insert() and self._pending is not None:
            if location.get_offset() != self._pending[0]:
                self._pending = None

    def _on_paste(self, view):
        view.stop_emission_by_name('paste-clipboard')
        clipboard = view.get_clipboard()
        formats = clipboard.get_formats()
        has_image = formats.contain_gtype(Gdk.Texture) or any(
            mime.startswith('image/') for mime in (formats.get_mime_types() or ()))
        if has_image and self.media is not None:
            clipboard.read_texture_async(None, self._weak('_on_texture_read'))
        else:
            clipboard.read_text_async(None, self._weak('_on_text_read'))

    def _on_texture_read(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as error:
            log.info('pasting an image: %s', error)
            return
        if texture is not None:
            self.attach_texture(texture)

    def _on_text_read(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error as error:
            log.info('pasting text: %s', error)
            return
        if text:
            html_text.insert_plain_text(self.buffer, text)
            self.text_view.scroll_mark_onscreen(self.buffer.get_insert())

    def _on_drop(self, _target, value, _x, _y):
        path = value.get_path() if isinstance(value, Gio.File) else None
        if not path or self.media is None:
            return False
        self.attach_path(path)
        return True

    def _on_focus_enter(self, _controller):
        self.toolbar.set_visible(True)

    def _on_focus_leave(self, _controller):
        self.toolbar.set_visible(False)
        self._pending = None
        self.emit('focus-left')


def _play(path):
    """Play a sound chip's file through widgets/audio.py when it offers play(path)."""
    try:
        from . import audio
    except ImportError:
        return
    play = getattr(audio, 'play', None)
    if play is not None:
        play(path)
