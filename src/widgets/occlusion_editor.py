# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The image occlusion editor: an image with the shapes that hide parts of it, drawn and
edited in place (the note editor's Occlusion field).

    RetainOcclusionEditor()        a vertical box: a small toolbar over the canvas
        .set_image(path)           the image file (None clears it); also the `image-path`
                                   property
        .get_shapes() -> [dict]    as notetypes.occlusion_shapes() returns them, ordinals
                                   1..n in creation order (grouped shapes share one)
        .set_shapes(shapes)        replaces them (no `changed`)
        .mode                      'rect' or 'ellipse': what a drag on empty image draws
        .occlude_all               every shape hidden on each card's question (Anki's
                                   "Hide All, Guess One"), not just the card's own; it is
                                   `occlude_inactive` on every shape
        .group_with_previous       a new shape takes the previous one's ordinal, so one
                                   card hides both
        .selected                  the selected shape's index, or None
        .select(index), .delete_selected()
        changed                    emitted after every edit
        .canvas                    the drawing widget (focusable, named "Occlusions")

Shapes are kept as fractions of the image's size (notetypes.py's dicts), so they survive the
image being shown at another size; the canvas draws the image scaled to fit, centred, and
asks for the image's size capped at MAX_HEIGHT. A drag on empty image draws a shape in the
current mode (one under MIN_SHAPE px is dropped); a drag on a shape moves it; a drag on a
corner handle of the selected shape resizes it; a click selects; Delete or BackSpace removes
the selection; Escape deselects; the arrow keys nudge by 1 px (10 with Shift). Imported
polygons and text labels are drawn (a rotated shape rotated) and can be moved and deleted,
not reshaped. The canvas's _begin_drag(x, y), _update_drag(dx, dy), _end_drag() and
_key(keyval, state) are what its gestures and key controller call, in canvas pixels; the
editor exposes the same names for tests.
"""

import copy
import logging
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango

log = logging.getLogger(__name__)

MAX_HEIGHT = 600        # the canvas's natural height is the image's, capped at this
MIN_SHAPE = 8           # a drawn shape narrower or shorter than this, in px, is discarded
HANDLE = 8              # the corner handles' size in px
HANDLE_REACH = 6        # how far from a handle's centre a press still takes it
FILL_ALPHA = 0.45
BORDER = 2.0
MODES = ('rect', 'ellipse')
TEXT_SIZE = 18          # a text label's font size in px, times its scale
KAPPA = 0.5522847498    # a quarter ellipse's cubic Bézier control distance


class RetainOcclusionEditor(Gtk.Box):
    """The toolbar and the canvas (see the module docstring)."""

    __gtype_name__ = 'RetainOcclusionEditor'
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, **kwargs):
        self._image_path = None
        self._syncing_switch = False
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, **kwargs)
        self.canvas = _Canvas()
        self.canvas.on_changed = lambda: self.emit('changed')
        self.canvas.on_selection_changed = self._on_selection_changed
        self.append(self._build_toolbar())
        self.append(self.canvas)

    def _build_toolbar(self):
        toolbar = Gtk.Box(spacing=6)
        self.toggle_group = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        self.toggle_group.add(Adw.Toggle(name='rect', icon_name='checkbox-symbolic',
                                         tooltip=_('Rectangle')))
        self.toggle_group.add(Adw.Toggle(name='ellipse', icon_name='media-record-symbolic',
                                         tooltip=_('Ellipse')))
        self.toggle_group.connect('notify::active-name', self._on_toggle_group)
        toolbar.append(self.toggle_group)

        self.group_button = Gtk.ToggleButton(icon_name='insert-link-symbolic',
                                             tooltip_text=_('Group with Previous'),
                                             valign=Gtk.Align.CENTER)
        self.group_button.update_property([Gtk.AccessibleProperty.LABEL],
                                          [_('Group with Previous')])
        self.group_button.connect('toggled', self._on_group_toggled)
        toolbar.append(self.group_button)

        self.occlude_switch = Gtk.Switch(valign=Gtk.Align.CENTER, margin_start=6)
        label = Gtk.Label(label=_('Occlude All'), mnemonic_widget=self.occlude_switch)
        label.set_tooltip_text(_('Hide every shape on each card, not just its own'))
        self.occlude_switch.update_property([Gtk.AccessibleProperty.LABEL], [_('Occlude All')])
        self.occlude_switch.connect('notify::active', self._on_occlude_switch)
        toolbar.append(label)
        toolbar.append(self.occlude_switch)

        self.delete_button = Gtk.Button(icon_name='user-trash-symbolic',
                                        tooltip_text=_('Delete Shape'), sensitive=False,
                                        halign=Gtk.Align.END, hexpand=True,
                                        valign=Gtk.Align.CENTER)
        self.delete_button.update_property([Gtk.AccessibleProperty.LABEL], [_('Delete Shape')])
        self.delete_button.connect('clicked', lambda _button: self.delete_selected())
        toolbar.append(self.delete_button)
        return toolbar

    # --- The image and the shapes -------------------------------------------------------------

    def set_image(self, path):
        """Show the image at `path` (None clears it); the shapes stay."""
        path = str(path) if path else None
        texture = None
        if path is not None:
            try:
                texture = Gdk.Texture.new_from_filename(path)
            except GLib.Error as error:
                log.warning('cannot load %s: %s', path, error.message)
        self.canvas.set_texture(texture)
        if path != self._image_path:
            self._image_path = path
            self.notify('image-path')

    @GObject.Property(type=str)
    def image_path(self):
        return self._image_path

    @image_path.setter
    def image_path(self, path):
        self.set_image(path)

    def get_shapes(self):
        """Copies of the shapes, as notetypes.occlusion_shapes() returns them."""
        return copy.deepcopy(self.canvas.shapes)

    def set_shapes(self, shapes):
        """Replace the shapes with copies of `shapes` (notetypes.occlusion_shapes() dicts)."""
        self.canvas.set_shapes(shapes)
        self._sync_occlude_switch()

    @GObject.Property(type=str, default='rect')
    def mode(self):
        return self.canvas.mode

    @mode.setter
    def mode(self, mode):
        if mode not in MODES:
            raise ValueError(f'unknown occlusion mode {mode!r}')
        if mode == self.canvas.mode:
            return
        self.canvas.mode = mode
        self.toggle_group.set_active_name(mode)

    @GObject.Property(type=bool, default=False)
    def occlude_all(self):
        return self.canvas.occlude_inactive

    @occlude_all.setter
    def occlude_all(self, value):
        self.occlude_switch.set_active(bool(value))

    @GObject.Property(type=bool, default=False)
    def group_with_previous(self):
        return self.canvas.group_with_previous

    @group_with_previous.setter
    def group_with_previous(self, value):
        self.group_button.set_active(bool(value))

    @property
    def selected(self):
        return self.canvas.selected

    def select(self, index):
        self.canvas.select(index)

    def delete_selected(self):
        """Remove the selected shape; True when there was one."""
        return self.canvas.delete_selected()

    # --- The toolbar --------------------------------------------------------------------------

    def _on_toggle_group(self, group, _pspec):
        name = group.get_active_name()
        if name in MODES and name != self.canvas.mode:
            self.canvas.mode = name
            self.notify('mode')

    def _on_group_toggled(self, button):
        self.canvas.group_with_previous = button.get_active()
        self.notify('group-with-previous')

    def _on_occlude_switch(self, switch, _pspec):
        if self._syncing_switch:
            return
        if self.canvas.set_occlude_inactive(switch.get_active()):
            self.notify('occlude-all')

    def _sync_occlude_switch(self):
        """Show the shapes' flag (on when any has it) without stamping it on them all."""
        self._syncing_switch = True
        try:
            self.occlude_switch.set_active(self.canvas.occlude_inactive)
        finally:
            self._syncing_switch = False

    def _on_selection_changed(self):
        self.delete_button.set_sensitive(self.canvas.selected is not None)

    # The gesture handlers, for tests: canvas pixels (see the module docstring).

    def _begin_drag(self, x, y):
        self.canvas._begin_drag(x, y)

    def _update_drag(self, dx, dy):
        self.canvas._update_drag(dx, dy)

    def _end_drag(self):
        self.canvas._end_drag()

    def _key(self, keyval, state=0):
        return self.canvas._key(keyval, state)


class _Canvas(Gtk.Widget):
    """The image and the shapes over it, and all the editing; the editor holds the toolbar.
    Reports through `on_changed()` and `on_selection_changed()`."""

    __gtype_name__ = 'RetainOcclusionCanvas'

    def __init__(self):
        super().__init__(hexpand=True, focusable=True, overflow=Gtk.Overflow.HIDDEN,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_('Occlusions')])
        self.texture = None
        self.shapes = []
        self.selected = None
        self.mode = 'rect'
        self.group_with_previous = False
        self.occlude_inactive = False
        self.on_changed = None
        self.on_selection_changed = None
        self._drag = None

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect('drag-begin', lambda _gesture, x, y: self._begin_drag(x, y))
        drag.connect('drag-update', lambda _gesture, dx, dy: self._update_drag(dx, dy))
        drag.connect('drag-end', lambda _gesture, _dx, _dy: self._end_drag())
        self.add_controller(drag)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect('pressed', self._on_pressed)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed',
                     lambda _controller, keyval, _code, state: self._key(keyval, state))
        self.add_controller(keys)

    # --- State ----------------------------------------------------------------------------------

    def set_texture(self, texture):
        self.texture = texture
        self.set_cursor_from_name('crosshair' if texture is not None else None)
        self.queue_resize()

    def set_shapes(self, shapes):
        self.shapes = []
        for shape in shapes:
            shape = copy.deepcopy(shape)
            shape.setdefault('ordinal', 0)
            shape.setdefault('shape', 'rect')
            for key in ('left', 'top', 'width', 'height'):
                shape.setdefault(key, 0.0)
            shape.setdefault('fill', None)
            shape.setdefault('angle', 0.0)
            shape.setdefault('occlude_inactive', False)
            if shape['shape'] == 'ellipse':
                shape['rx'] = shape['width'] / 2
                shape['ry'] = shape['height'] / 2
            elif shape['shape'] == 'polygon':
                points = shape.setdefault('points', [])
                if points and not (shape['width'] and shape['height']):
                    xs = [x for x, _y in points]
                    ys = [y for _x, y in points]
                    shape.update(left=min(xs), top=min(ys), width=max(xs) - min(xs),
                                 height=max(ys) - min(ys))
            self.shapes.append(shape)
        self._renumber()
        self.occlude_inactive = any(shape['occlude_inactive'] for shape in self.shapes)
        self.select(None)
        self.queue_draw()

    def set_occlude_inactive(self, value):
        """Set `occlude_inactive` on every shape; True when something changed."""
        value = bool(value)
        changed = value != self.occlude_inactive
        self.occlude_inactive = value
        for shape in self.shapes:
            if shape['occlude_inactive'] != value:
                shape['occlude_inactive'] = value
                changed = True
        if changed:
            self.queue_draw()
            self._changed()
        return changed

    def select(self, index):
        if index is not None and not 0 <= index < len(self.shapes):
            index = None
        if index == self.selected:
            return
        self.selected = index
        self.queue_draw()
        if self.on_selection_changed is not None:
            self.on_selection_changed()

    def delete_selected(self):
        if self.selected is None:
            return False
        self.shapes.pop(self.selected)
        self._renumber()
        self.select(None)
        self.queue_draw()
        self._changed()
        return True

    def _changed(self):
        if self.on_changed is not None:
            self.on_changed()

    def _renumber(self):
        """Ordinals 1..n in order of first appearance (text labels keep 0)."""
        mapping = {}
        for shape in self.shapes:
            ordinal = shape['ordinal']
            if ordinal > 0 and ordinal not in mapping:
                mapping[ordinal] = len(mapping) + 1
        for shape in self.shapes:
            if shape['ordinal'] > 0:
                shape['ordinal'] = mapping[shape['ordinal']]

    def _next_ordinal(self):
        previous = next((shape['ordinal'] for shape in reversed(self.shapes)
                         if shape['ordinal'] > 0), None)
        if self.group_with_previous and previous is not None:
            return previous
        return (previous or 0) + 1

    # --- Geometry: the image's rectangle in the canvas, and each shape's box in pixels ----------

    def _natural_size(self):
        width, height = self.texture.get_width(), self.texture.get_height()
        scale = min(1.0, MAX_HEIGHT / height) if height else 1.0
        return max(1, round(width * scale)), max(1, round(height * scale))

    def _image_rect(self):
        """(x, y, width, height) of the image as drawn: scaled to fit, centred; at the natural
        size while the canvas is not yet allocated (tests)."""
        texture_width, texture_height = self.texture.get_width(), self.texture.get_height()
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            width, height = self._natural_size()
        scale = min(width / texture_width, height / texture_height)
        drawn_width, drawn_height = texture_width * scale, texture_height * scale
        return (width - drawn_width) / 2, (height - drawn_height) / 2, drawn_width, drawn_height

    def _box(self, shape):
        """The shape's bounding box in canvas pixels (a text label's is its layout's)."""
        image_x, image_y, image_width, image_height = self._image_rect()
        x = image_x + shape['left'] * image_width
        y = image_y + shape['top'] * image_height
        if shape['shape'] == 'text':
            width, height = self._text_layout(shape).get_pixel_size()
            return x, y, width + 4, height + 4
        return x, y, shape['width'] * image_width, shape['height'] * image_height

    def _set_box(self, shape, x, y, width, height):
        """Move and size the shape to a pixel box (a polygon's points follow, a text label's
        size is its own)."""
        image_x, image_y, image_width, image_height = self._image_rect()
        left, top = (x - image_x) / image_width, (y - image_y) / image_height
        if shape['shape'] == 'polygon':
            dx, dy = left - shape['left'], top - shape['top']
            shape['points'] = [(px + dx, py + dy) for px, py in shape.get('points', [])]
        shape['left'], shape['top'] = left, top
        if shape['shape'] != 'text':
            shape['width'], shape['height'] = width / image_width, height / image_height
        if shape['shape'] == 'ellipse':
            shape['rx'], shape['ry'] = shape['width'] / 2, shape['height'] / 2

    def _clamp_point(self, x, y):
        image_x, image_y, image_width, image_height = self._image_rect()
        return (min(max(x, image_x), image_x + image_width),
                min(max(y, image_y), image_y + image_height))

    def _clamp_box(self, x, y, width, height):
        image_x, image_y, image_width, image_height = self._image_rect()
        x = min(max(x, image_x), image_x + max(0, image_width - width))
        y = min(max(y, image_y), image_y + max(0, image_height - height))
        return x, y, width, height

    def _handles(self, index):
        """The selected shape's corner handle centres, when it has handles."""
        if index is None:
            return []
        shape = self.shapes[index]
        if shape['shape'] not in ('rect', 'ellipse'):
            return []
        x, y, width, height = self._box(shape)
        return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]

    def _handle_at(self, x, y):
        for number, (hx, hy) in enumerate(self._handles(self.selected)):
            if abs(x - hx) <= HANDLE_REACH and abs(y - hy) <= HANDLE_REACH:
                return number
        return None

    def _hit(self, x, y):
        """The topmost shape under the point, or None."""
        for index in reversed(range(len(self.shapes))):
            if self._contains(self.shapes[index], x, y):
                return index
        return None

    def _contains(self, shape, x, y):
        box_x, box_y, width, height = self._box(shape)
        if width <= 0 or height <= 0:
            return False
        kind = shape['shape']
        if kind == 'ellipse':
            dx, dy = (x - box_x) / width * 2 - 1, (y - box_y) / height * 2 - 1
            return dx * dx + dy * dy <= 1
        if kind == 'polygon' and shape.get('points'):
            return _polygon_contains(self._polygon_pixels(shape), x, y)
        return box_x <= x <= box_x + width and box_y <= y <= box_y + height

    def _polygon_pixels(self, shape):
        image_x, image_y, image_width, image_height = self._image_rect()
        return [(image_x + px * image_width, image_y + py * image_height)
                for px, py in shape['points']]

    # --- Gestures and keys ----------------------------------------------------------------------

    def _on_pressed(self, _gesture, _n_press, x, y):
        self.grab_focus()
        if self.texture is not None and self._handle_at(x, y) is None:
            self.select(self._hit(x, y))

    def _begin_drag(self, x, y):
        self._drag = None
        if self.texture is None:
            return
        handle = self._handle_at(x, y)
        if handle is not None:
            self._drag = {'kind': 'resize', 'index': self.selected, 'handle': handle,
                          'box': self._box(self.shapes[self.selected]), 'moved': False}
            return
        index = self._hit(x, y)
        if index is not None:
            self.select(index)
            self._drag = {'kind': 'move', 'index': index, 'box': self._box(self.shapes[index]),
                          'moved': False}
            return
        self.select(None)
        image_x, image_y, image_width, image_height = self._image_rect()
        if not (image_x <= x <= image_x + image_width and image_y <= y <= image_y + image_height):
            return
        shape = {'ordinal': self._next_ordinal(), 'shape': self.mode, 'left': 0.0, 'top': 0.0,
                 'width': 0.0, 'height': 0.0, 'fill': None, 'angle': 0.0,
                 'occlude_inactive': self.occlude_inactive}
        if self.mode == 'ellipse':
            shape.update(rx=0.0, ry=0.0)
        self._set_box(shape, x, y, 0, 0)
        self.shapes.append(shape)
        self._drag = {'kind': 'draw', 'index': len(self.shapes) - 1, 'origin': (x, y),
                      'moved': False}

    def _update_drag(self, dx, dy):
        drag = self._drag
        if drag is None:
            return
        shape = self.shapes[drag['index']]
        if drag['kind'] == 'draw':
            x0, y0 = drag['origin']
            x1, y1 = self._clamp_point(x0 + dx, y0 + dy)
            self._set_box(shape, *_normalise(x0, y0, x1, y1))
        elif drag['kind'] == 'move':
            x, y, width, height = drag['box']
            self._set_box(shape, *self._clamp_box(x + dx, y + dy, width, height))
        else:
            x, y, width, height = drag['box']
            corners = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
            moving = corners[drag['handle']]
            anchor = corners[(drag['handle'] + 2) % 4]
            x1, y1 = self._clamp_point(moving[0] + dx, moving[1] + dy)
            self._set_box(shape, *_normalise(anchor[0], anchor[1], x1, y1))
        drag['moved'] = True
        self.queue_draw()

    def _end_drag(self):
        drag, self._drag = self._drag, None
        if drag is None:
            return
        if drag['kind'] == 'draw':
            _x, _y, width, height = self._box(self.shapes[drag['index']])
            if width < MIN_SHAPE or height < MIN_SHAPE:
                self.shapes.pop(drag['index'])
                self._renumber()
                self.queue_draw()
                return
            self.select(drag['index'])
            self._changed()
        elif drag['moved']:
            self._changed()

    def _key(self, keyval, state):
        """The key controller's handler: True when the key was used."""
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete, Gdk.KEY_BackSpace):
            return self.delete_selected()
        if keyval == Gdk.KEY_Escape:
            if self.selected is None:
                return False
            self.select(None)
            return True
        step = 10 if state & Gdk.ModifierType.SHIFT_MASK else 1
        moves = {Gdk.KEY_Left: (-step, 0), Gdk.KEY_KP_Left: (-step, 0),
                 Gdk.KEY_Right: (step, 0), Gdk.KEY_KP_Right: (step, 0),
                 Gdk.KEY_Up: (0, -step), Gdk.KEY_KP_Up: (0, -step),
                 Gdk.KEY_Down: (0, step), Gdk.KEY_KP_Down: (0, step)}
        if keyval not in moves or self.selected is None or self.texture is None:
            return False
        dx, dy = moves[keyval]
        shape = self.shapes[self.selected]
        x, y, width, height = self._box(shape)
        self._set_box(shape, *self._clamp_box(x + dx, y + dy, width, height))
        self.queue_draw()
        self._changed()
        return True

    # --- Layout and drawing ---------------------------------------------------------------------

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if self.texture is None:
            return 0, 0, -1, -1
        width, height = self._natural_size()
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, width, -1, -1
        if for_size < 0:
            return 0, height, -1, -1
        height = min(MAX_HEIGHT, round(height * for_size / width))
        return height, height, -1, -1

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        image_x, image_y, image_width, image_height = self._image_rect()
        snapshot.append_texture(self.texture, Graphene.Rect().init(
            image_x, image_y, image_width, image_height))
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        fill = Gdk.RGBA(accent.red, accent.green, accent.blue, FILL_ALPHA)
        border = Gdk.RGBA(accent.red, accent.green, accent.blue, 1.0)
        for shape in self.shapes:
            self._draw_shape(snapshot, shape, fill, border)
        for hx, hy in self._handles(self.selected):
            self._draw_handle(snapshot, hx, hy, border)

    def do_css_changed(self, change):
        Gtk.Widget.do_css_changed(self, change)
        self.queue_draw()  # the accent colour may have changed

    def _draw_shape(self, snapshot, shape, fill, border):
        x, y, width, height = self._box(shape)
        snapshot.save()
        if shape.get('angle'):
            centre = Graphene.Point().init(x + width / 2, y + height / 2)
            snapshot.translate(centre)
            snapshot.rotate(shape['angle'])
            snapshot.translate(Graphene.Point().init(-centre.x, -centre.y))
        kind = shape['shape']
        if kind == 'text':
            self._draw_text(snapshot, self._text_layout(shape), x + 2, y + 2)
        else:
            if kind == 'rect' or (kind == 'polygon' and not shape.get('points')):
                rect = _rounded_rect(x, y, width, height, 0)
                snapshot.append_color(fill, rect.bounds)
                snapshot.append_border(rect, [BORDER] * 4, [border] * 4)
            else:
                if kind == 'ellipse':
                    path = _ellipse_path(x, y, width, height)
                else:
                    path = _polygon_path(self._polygon_pixels(shape))
                snapshot.append_fill(path, Gsk.FillRule.WINDING, fill)
                snapshot.append_stroke(path, Gsk.Stroke.new(BORDER), border)
            if shape['ordinal'] > 0:
                layout = self.create_pango_layout(str(shape['ordinal']))
                font = Pango.FontDescription.from_string('Bold')
                font.set_absolute_size(max(10, min(22, min(width, height) * 0.5)) * Pango.SCALE)
                layout.set_font_description(font)
                layout_width, layout_height = layout.get_pixel_size()
                self._draw_text(snapshot, layout, x + (width - layout_width) / 2,
                                y + (height - layout_height) / 2)
        snapshot.restore()

    def _draw_text(self, snapshot, layout, x, y):
        """White text over a dark shadow, readable on any image."""
        for offset, colour in ((1, Gdk.RGBA(0, 0, 0, 0.7)), (0, Gdk.RGBA(1, 1, 1, 1))):
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x + offset, y + offset))
            snapshot.append_layout(layout, colour)
            snapshot.restore()

    def _draw_handle(self, snapshot, x, y, border):
        rect = _rounded_rect(x - HANDLE / 2, y - HANDLE / 2, HANDLE, HANDLE, 1)
        snapshot.append_color(Gdk.RGBA(1, 1, 1, 1), rect.bounds)
        snapshot.append_border(rect, [1.0] * 4, [border] * 4)

    def _text_layout(self, shape):
        layout = self.create_pango_layout(shape.get('text') or '')
        font = Pango.FontDescription.from_string('Bold')
        scale = shape.get('scale') or 1.0
        font.set_absolute_size(max(8, TEXT_SIZE * scale) * Pango.SCALE)
        layout.set_font_description(font)
        return layout


def _rounded_rect(x, y, width, height, radius):
    """A Gsk.RoundedRect, initialised in place: what init_from_rect() returns under PyGObject
    is a wrapper over a temporary, with garbage bounds."""
    rect = Gsk.RoundedRect()
    rect.init_from_rect(Graphene.Rect().init(x, y, width, height), radius)
    return rect


def _normalise(x0, y0, x1, y1):
    """The box with the two points as opposite corners."""
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


def _ellipse_path(x, y, width, height):
    rx, ry = width / 2, height / 2
    cx, cy = x + rx, y + ry
    kx, ky = rx * KAPPA, ry * KAPPA
    builder = Gsk.PathBuilder.new()
    builder.move_to(cx + rx, cy)
    builder.cubic_to(cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry)
    builder.cubic_to(cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy)
    builder.cubic_to(cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry)
    builder.cubic_to(cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy)
    builder.close()
    return builder.to_path()


def _polygon_path(points):
    builder = Gsk.PathBuilder.new()
    builder.move_to(*points[0])
    for point in points[1:]:
        builder.line_to(*point)
    builder.close()
    return builder.to_path()


def _polygon_contains(points, x, y):
    """Ray casting: whether the point is inside the polygon."""
    inside = False
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
    return inside
