# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The image occlusion editor widget: sizing, the shapes round trip, and the editing
driven through the gesture and key handlers with canvas coordinates (the canvas is never
allocated here, so the image is laid out at its natural size: 200×100 px at (0, 0))."""

import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401  (registers retain/ and isolates settings)
from tests.gtk import requires_gtk


def write_png(path, width, height):
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0x4080c0ff)
    pixbuf.savev(path, 'png', [], [])


@requires_gtk
class OcclusionEditorTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from retain.widgets import occlusion_editor

        cls.module = occlusion_editor

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='retain-test-occlusion-')
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.image = f'{self.directory}/image.png'
        write_png(self.image, 200, 100)
        self.editor = self.module.RetainOcclusionEditor()
        self.editor.set_image(self.image)
        self.changes = 0
        self.editor.connect('changed', self._count)

    def _count(self, _editor):
        self.changes += 1

    def drag(self, x, y, dx, dy):
        self.editor._begin_drag(x, y)
        self.editor._update_drag(dx, dy)
        self.editor._end_drag()

    def fractions(self, shape):
        return tuple(round(shape[key], 3) for key in ('left', 'top', 'width', 'height'))

    def test_set_image_sets_natural_size(self):
        from gi.repository import Gtk

        canvas = self.editor.canvas
        self.assertEqual(self.editor.props.image_path, self.image)
        self.assertEqual(canvas.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 200)
        self.assertEqual(canvas.measure(Gtk.Orientation.VERTICAL, 200)[1], 100)
        self.assertEqual(canvas.measure(Gtk.Orientation.VERTICAL, 400)[1], 200)
        tall = f'{self.directory}/tall.png'
        write_png(tall, 300, 1200)
        self.editor.props.image_path = tall
        self.assertEqual(canvas.measure(Gtk.Orientation.VERTICAL, -1)[1], 600)
        self.assertEqual(canvas.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 150)
        self.editor.set_image(None)
        self.assertIsNone(self.editor.props.image_path)
        self.assertEqual(canvas.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 0)

    def test_shapes_round_trip_through_the_occlusion_field(self):
        from retain import notetypes

        text = ('{{c1::image-occlusion:rect:left=0.1:top=0.2:width=0.3:height=0.4}}<br>'
                '{{c1::image-occlusion:ellipse:left=0.5:top=0.5:rx=0.1:ry=0.05:oi=1}}<br>'
                '{{c2::image-occlusion:polygon:points=0.1,0.1 0.2,0.1 0.2,0.2}}<br>'
                '{{c0::image-occlusion:text:left=0.6:top=0.1:text=Label:scale=1}}<br>')
        shapes = notetypes.occlusion_shapes(text)
        self.editor.set_shapes(shapes)
        self.assertEqual(notetypes.occlusion_field(self.editor.get_shapes()), text)
        self.assertEqual(self.changes, 0)
        self.assertTrue(self.editor.props.occlude_all)  # a shape had oi=1
        self.editor.get_shapes()[0]['left'] = 0.9  # a copy
        self.assertEqual(self.editor.get_shapes()[0]['left'], 0.1)

    def test_set_shapes_renumbers_ordinals_in_order(self):
        self.editor.set_shapes([
            {'ordinal': 4, 'shape': 'rect', 'left': 0, 'top': 0, 'width': 0.1, 'height': 0.1},
            {'ordinal': 2, 'shape': 'rect', 'left': 0.5, 'top': 0, 'width': 0.1, 'height': 0.1},
            {'ordinal': 4, 'shape': 'rect', 'left': 0, 'top': 0.5, 'width': 0.1, 'height': 0.1},
        ])
        self.assertEqual([shape['ordinal'] for shape in self.editor.get_shapes()], [1, 2, 1])

    def test_drag_draws_a_rect(self):
        self.drag(20, 10, 60, 30)
        shapes = self.editor.get_shapes()
        self.assertEqual(len(shapes), 1)
        self.assertEqual(shapes[0]['shape'], 'rect')
        self.assertEqual(shapes[0]['ordinal'], 1)
        self.assertEqual(self.fractions(shapes[0]), (0.1, 0.1, 0.3, 0.3))
        self.assertFalse(shapes[0]['occlude_inactive'])
        self.assertEqual(self.editor.selected, 0)
        self.assertTrue(self.editor.delete_button.get_sensitive())
        self.assertEqual(self.changes, 1)

    def test_drag_backwards_and_past_the_image_is_normalised_and_clamped(self):
        self.drag(100, 50, -150, -80)
        self.assertEqual(self.fractions(self.editor.get_shapes()[0]), (0, 0, 0.5, 0.5))

    def test_drag_draws_an_ellipse_in_ellipse_mode(self):
        self.editor.props.mode = 'ellipse'
        self.assertEqual(self.editor.toggle_group.get_active_name(), 'ellipse')
        self.drag(100, 50, 40, 20)
        shape = self.editor.get_shapes()[0]
        self.assertEqual(shape['shape'], 'ellipse')
        self.assertEqual(self.fractions(shape), (0.5, 0.5, 0.2, 0.2))
        self.assertAlmostEqual(shape['rx'], 0.1)
        self.assertAlmostEqual(shape['ry'], 0.1)
        self.editor.toggle_group.set_active_name('rect')
        self.assertEqual(self.editor.props.mode, 'rect')

    def test_tiny_drag_is_discarded(self):
        self.drag(20, 10, 5, 30)
        self.assertEqual(self.editor.get_shapes(), [])
        self.assertIsNone(self.editor.selected)
        self.assertEqual(self.changes, 0)

    def test_group_with_previous_shares_the_ordinal(self):
        self.drag(10, 10, 30, 30)
        self.editor.props.group_with_previous = True
        self.assertTrue(self.editor.group_button.get_active())
        self.drag(100, 10, 30, 30)
        self.editor.props.group_with_previous = False
        self.drag(10, 60, 30, 30)
        self.assertEqual([shape['ordinal'] for shape in self.editor.get_shapes()], [1, 1, 2])

    def test_drag_on_a_shape_moves_it(self):
        self.drag(20, 10, 60, 30)
        self.editor.select(None)
        self.drag(50, 25, 20, 10)  # from inside the shape
        self.assertEqual(self.fractions(self.editor.get_shapes()[0]), (0.2, 0.2, 0.3, 0.3))
        self.assertEqual(self.editor.selected, 0)
        self.assertEqual(self.changes, 2)

    def test_drag_on_a_corner_handle_resizes(self):
        self.drag(20, 10, 60, 30)  # selected, with handles at its corners
        self.drag(80, 40, 20, 20)  # the bottom-right one
        self.assertEqual(self.fractions(self.editor.get_shapes()[0]), (0.1, 0.1, 0.4, 0.5))
        self.drag(20, 10, 10, 10)  # the top-left one, inwards
        self.assertEqual(self.fractions(self.editor.get_shapes()[0]), (0.15, 0.2, 0.35, 0.4))

    def test_click_on_empty_image_deselects(self):
        self.drag(20, 10, 60, 30)
        self.editor._begin_drag(150, 80)
        self.editor._end_drag()
        self.assertIsNone(self.editor.selected)
        self.assertEqual(len(self.editor.get_shapes()), 1)

    def test_delete_removes_the_selected_shape_and_renumbers(self):
        from gi.repository import Gdk

        self.drag(10, 10, 30, 30)
        self.drag(100, 10, 30, 30)
        self.drag(10, 60, 30, 30)
        self.editor.select(1)
        self.assertTrue(self.editor._key(Gdk.KEY_Delete))
        self.assertEqual([self.fractions(shape)[:2] for shape in self.editor.get_shapes()],
                         [(0.05, 0.1), (0.05, 0.6)])
        self.assertEqual([shape['ordinal'] for shape in self.editor.get_shapes()], [1, 2])
        self.assertIsNone(self.editor.selected)
        self.assertFalse(self.editor.delete_button.get_sensitive())
        self.assertEqual(self.changes, 4)
        self.assertFalse(self.editor._key(Gdk.KEY_Delete))  # nothing selected
        self.editor.select(0)
        self.assertTrue(self.editor.delete_selected())
        self.assertEqual(len(self.editor.get_shapes()), 1)

    def test_arrow_keys_nudge_and_escape_deselects(self):
        from gi.repository import Gdk

        self.drag(20, 10, 60, 30)
        self.assertTrue(self.editor._key(Gdk.KEY_Right))
        self.assertTrue(self.editor._key(Gdk.KEY_Down, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(self.fractions(self.editor.get_shapes()[0]), (0.105, 0.2, 0.3, 0.3))
        self.assertEqual(self.changes, 3)
        self.assertTrue(self.editor._key(Gdk.KEY_Escape))
        self.assertIsNone(self.editor.selected)
        self.assertFalse(self.editor._key(Gdk.KEY_Escape))
        self.assertFalse(self.editor._key(Gdk.KEY_Left))  # nothing selected

    def test_occlude_all_sets_every_shape(self):
        self.drag(10, 10, 30, 30)
        self.editor.props.occlude_all = True
        self.assertTrue(self.editor.occlude_switch.get_active())
        self.drag(100, 10, 30, 30)
        self.assertTrue(all(shape['occlude_inactive'] for shape in self.editor.get_shapes()))
        self.assertEqual(self.changes, 3)
        self.editor.occlude_switch.set_active(False)
        self.assertFalse(any(shape['occlude_inactive'] for shape in self.editor.get_shapes()))
        self.assertFalse(self.editor.props.occlude_all)

    def test_imported_polygon_and_text_move_but_do_not_resize(self):
        self.editor.set_shapes([
            {'ordinal': 1, 'shape': 'polygon', 'points': [(0.1, 0.1), (0.3, 0.1), (0.2, 0.3)],
             'left': 0.1, 'top': 0.1, 'width': 0.2, 'height': 0.2},
            {'ordinal': 0, 'shape': 'text', 'left': 0.6, 'top': 0.5, 'text': 'Hi',
             'scale': 1.0},
        ])
        self.drag(40, 15, 20, 10)  # inside the triangle
        polygon = self.editor.get_shapes()[0]
        self.assertEqual(self.fractions(polygon), (0.2, 0.2, 0.2, 0.2))
        self.assertEqual([(round(x, 3), round(y, 3)) for x, y in polygon['points']],
                         [(0.2, 0.2), (0.4, 0.2), (0.3, 0.4)])
        self.assertEqual(self.editor.canvas._handles(0), [])
        self.drag(122, 52, -20, 0)  # on the label
        self.assertEqual(self.fractions(self.editor.get_shapes()[1])[:2], (0.5, 0.5))
        self.assertEqual(self.editor.canvas._handles(1), [])
        self.assertEqual(self.changes, 2)

    def test_canvas_is_focusable_and_named(self):
        from gi.repository import Gtk

        canvas = self.editor.canvas
        self.assertTrue(canvas.get_focusable())
        self.assertEqual(canvas.get_accessible_role(), Gtk.AccessibleRole.GROUP)

    def test_snapshot_draws_every_shape_within_the_canvas(self):
        from gi.repository import Gsk, Gtk

        self.editor.set_shapes([
            {'ordinal': 1, 'shape': 'rect', 'left': 0.1, 'top': 0.1, 'width': 0.2,
             'height': 0.2, 'angle': 15.0},
            {'ordinal': 2, 'shape': 'ellipse', 'left': 0.5, 'top': 0.1, 'width': 0.2,
             'height': 0.2},
            {'ordinal': 3, 'shape': 'polygon', 'points': [(0.1, 0.6), (0.3, 0.6), (0.2, 0.9)]},
            {'ordinal': 0, 'shape': 'text', 'left': 0.6, 'top': 0.6, 'text': 'Label'},
        ])
        self.editor.select(1)
        snapshot = Gtk.Snapshot()
        self.editor.canvas.do_snapshot(snapshot)
        node = snapshot.to_node()
        self.assertEqual(node.get_node_type(), Gsk.RenderNodeType.CONTAINER_NODE)
        kinds = set()
        for index in range(node.get_n_children()):
            child = node.get_child(index)
            kinds.add(child.get_node_type())
            bounds = child.get_bounds()
            # Every node lies on the 200×100 image (the stroke's width and the handles aside):
            # a Gsk.RoundedRect initialised the wrong way draws nowhere, with huge bounds,
            # and a polygon without a box put its number at the corner.
            self.assertGreaterEqual(bounds.origin.x, -5)
            self.assertGreaterEqual(bounds.origin.y, -5)
            self.assertLessEqual(bounds.origin.x + bounds.size.width, 205)
            self.assertLessEqual(bounds.origin.y + bounds.size.height, 105)
        self.assertLessEqual({Gsk.RenderNodeType.TEXTURE_NODE, Gsk.RenderNodeType.COLOR_NODE,
                              Gsk.RenderNodeType.BORDER_NODE, Gsk.RenderNodeType.FILL_NODE,
                              Gsk.RenderNodeType.STROKE_NODE, Gsk.RenderNodeType.TEXT_NODE},
                             kinds)


if __name__ == '__main__':
    unittest.main()
