# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

import json
import sqlite3
import unittest

from tests import ROOT  # noqa: F401
from retain import notetypes, schema
from retain.notetypes import NoteType


class StockTest(unittest.TestCase):

    def test_basic(self):
        basic = notetypes.stock('basic')
        self.assertEqual(basic.name, 'Basic')
        self.assertEqual(basic.kind, 'standard')
        self.assertEqual(basic.field_names(), ['Front', 'Back'])
        self.assertEqual(len(basic.templates), 1)
        self.assertEqual(basic.templates[0]['name'], 'Card 1')
        self.assertEqual(basic.templates[0]['qfmt'], '{{Front}}')
        self.assertEqual(basic.templates[0]['afmt'],
                         '{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}')
        self.assertEqual(basic.css, notetypes.DEFAULT_CSS)
        self.assertIsNone(basic.id)

    def test_basic_reversed(self):
        reversed_ = notetypes.stock('basic-reversed')
        self.assertEqual(reversed_.name, 'Basic (and reversed card)')
        self.assertEqual(reversed_.field_names(), ['Front', 'Back'])
        self.assertEqual([t['name'] for t in reversed_.templates], ['Card 1', 'Card 2'])
        self.assertEqual(reversed_.templates[1]['qfmt'], '{{Back}}')
        self.assertEqual(reversed_.templates[1]['afmt'],
                         '{{FrontSide}}\n\n<hr id=answer>\n\n{{Front}}')

    def test_basic_optional_reversed(self):
        optional = notetypes.stock('basic-optional-reversed')
        self.assertEqual(optional.name, 'Basic (optional reversed card)')
        self.assertEqual(optional.field_names(), ['Front', 'Back', 'Add Reverse'])
        self.assertEqual(len(optional.templates), 2)
        self.assertEqual(optional.templates[1]['qfmt'],
                         '{{#Add Reverse}}{{Back}}{{/Add Reverse}}')

    def test_basic_typed(self):
        typed = notetypes.stock('basic-typed')
        self.assertEqual(typed.name, 'Basic (type in the answer)')
        self.assertEqual(typed.field_names(), ['Front', 'Back'])
        self.assertEqual(len(typed.templates), 1)
        self.assertEqual(typed.templates[0]['qfmt'], '{{Front}}\n\n{{type:Back}}')
        self.assertEqual(typed.templates[0]['afmt'],
                         '{{Front}}\n\n<hr id=answer>\n\n{{type:Back}}')

    def test_cloze(self):
        cloze = notetypes.stock('cloze')
        self.assertEqual(cloze.name, 'Cloze')
        self.assertEqual(cloze.kind, 'cloze')
        self.assertEqual(cloze.field_names(), ['Text', 'Back Extra'])
        self.assertEqual(len(cloze.templates), 1)
        self.assertEqual(cloze.templates[0]['name'], 'Cloze')
        self.assertEqual(cloze.templates[0]['qfmt'], '{{cloze:Text}}')
        self.assertEqual(cloze.templates[0]['afmt'], '{{cloze:Text}}<br>\n{{Back Extra}}')
        self.assertEqual(cloze.css, notetypes.CLOZE_CSS)
        self.assertEqual(cloze.sort_field, 0)

    def test_image_occlusion(self):
        occlusion = notetypes.stock('occlusion')
        self.assertEqual(occlusion.name, 'Image Occlusion')
        self.assertEqual(occlusion.kind, 'occlusion')
        self.assertEqual(occlusion.field_names(), notetypes.IMAGE_OCCLUSION_FIELDS)
        self.assertEqual(len(occlusion.templates), 1)
        template = occlusion.templates[0]
        self.assertEqual(template['name'], 'Image Occlusion')
        self.assertIn('{{cloze:Occlusion}}', template['qfmt'])
        self.assertIn('<canvas id="image-occlusion-canvas"></canvas>', template['qfmt'])
        self.assertNotIn('{{Back Extra}}', template['qfmt'])
        self.assertIn('{{#Back Extra}}<div>{{Back Extra}}</div>{{/Back Extra}}',
                      template['afmt'])
        self.assertTrue(occlusion.css.startswith(notetypes.CLOZE_CSS))
        self.assertIn('#image-occlusion-canvas', occlusion.css)

    def test_all_stock_in_order(self):
        names = [notetype.name for notetype in notetypes.all_stock()]
        self.assertEqual(names, ['Basic', 'Basic (and reversed card)',
                                 'Basic (optional reversed card)', 'Basic (type in the answer)',
                                 'Cloze', 'Image Occlusion'])
        self.assertEqual(len(notetypes.STOCK_KINDS), len(names))

    def test_css_is_ankis(self):
        self.assertTrue(notetypes.DEFAULT_CSS.startswith('.card {\n    font-family: arial;\n'))
        self.assertTrue(notetypes.CLOZE_CSS.startswith(notetypes.DEFAULT_CSS))
        self.assertIn('.nightMode .cloze', notetypes.CLOZE_CSS)

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            notetypes.stock('mnemonic')


class NoteTypeTest(unittest.TestCase):

    def test_field_names_and_defaults(self):
        notetype = NoteType('Vocabulary', fields=[{'name': 'Word', 'description': ''},
                                                  {'name': 'Meaning', 'description': 'short'}])
        self.assertEqual(notetype.field_names(), ['Word', 'Meaning'])
        self.assertEqual(notetype.kind, 'standard')
        self.assertEqual(notetype.templates, [])
        self.assertEqual(notetype.css, notetypes.DEFAULT_CSS)
        self.assertIsNone(notetype.original_id)
        self.assertIsInstance(notetype.modified, int)

    def test_row_round_trip(self):
        cloze = notetypes.stock('cloze')
        cloze.id = 7
        cloze.original_id = 1234567890
        cloze.modified = 1700000000
        row = cloze.to_row()
        self.assertEqual(json.loads(row['fields']), cloze.fields)
        self.assertEqual(json.loads(row['templates']), cloze.templates)
        self.assertEqual(row['kind'], 'cloze')
        back = NoteType.from_row(row)
        self.assertEqual(back.to_row(), row)
        self.assertEqual(back.field_names(), ['Text', 'Back Extra'])
        self.assertEqual(back.id, 7)
        self.assertEqual(back.original_id, 1234567890)

    def test_row_round_trip_through_sqlite(self):
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        connection.executescript(schema.SCHEMA)
        row = notetypes.stock('basic-reversed').to_row()
        connection.execute(
            'INSERT INTO notetypes (name, kind, fields, templates, css, sort_field, original_id,'
            ' modified) VALUES (:name, :kind, :fields, :templates, :css, :sort_field,'
            ' :original_id, :modified)', row)
        stored = NoteType.from_row(connection.execute('SELECT * FROM notetypes').fetchone())
        self.assertEqual(stored.id, 1)
        self.assertEqual(stored.name, 'Basic (and reversed card)')
        self.assertEqual(stored.templates, notetypes.stock('basic-reversed').templates)
        self.assertEqual(stored.css, notetypes.DEFAULT_CSS)

    def test_copy_is_independent(self):
        original = notetypes.stock('basic')
        original.id = 3
        duplicate = original.copy()
        duplicate.fields.append({'name': 'Extra', 'description': ''})
        duplicate.templates[0]['qfmt'] = '{{Back}}'
        self.assertEqual(duplicate.id, 3)
        self.assertEqual(original.field_names(), ['Front', 'Back'])
        self.assertEqual(original.templates[0]['qfmt'], '{{Front}}')


class IsImageOcclusionTest(unittest.TestCase):

    def test_kinds(self):
        self.assertTrue(notetypes.is_image_occlusion(notetypes.stock('occlusion')))
        self.assertFalse(notetypes.is_image_occlusion(notetypes.stock('cloze')))
        self.assertFalse(notetypes.is_image_occlusion(notetypes.stock('basic')))

    def test_imported_anki_type(self):
        imported = NoteType(
            'Image Occlusion', kind='cloze',
            fields=[{'name': name, 'description': ''}
                    for name in notetypes.IMAGE_OCCLUSION_FIELDS + ['Source']],
            templates=notetypes.stock('occlusion').templates)
        self.assertTrue(notetypes.is_image_occlusion(imported))
        standard = imported.copy()
        standard.kind = 'standard'
        self.assertFalse(notetypes.is_image_occlusion(standard))


class OcclusionShapesTest(unittest.TestCase):

    def test_rect(self):
        shapes = notetypes.occlusion_shapes(
            '{{c1::image-occlusion:rect:left=.2:top=.3:width=.1:height=.05}}<br>')
        self.assertEqual(len(shapes), 1)
        rect = shapes[0]
        self.assertEqual(rect['ordinal'], 1)
        self.assertEqual(rect['shape'], 'rect')
        self.assertEqual((rect['left'], rect['top'], rect['width'], rect['height']),
                         (0.2, 0.3, 0.1, 0.05))
        self.assertIsNone(rect['fill'])
        self.assertEqual(rect['angle'], 0)
        self.assertFalse(rect['occlude_inactive'])

    def test_ellipse_with_fill_and_occlude_inactive(self):
        shapes = notetypes.occlusion_shapes(
            '{{c2::image-occlusion:ellipse:left=.5:top=.5:rx=.1:ry=.2:oi=1:fill=#ff0000'
            ':angle=15}}')
        ellipse = shapes[0]
        self.assertEqual(ellipse['ordinal'], 2)
        self.assertEqual(ellipse['shape'], 'ellipse')
        self.assertEqual((ellipse['rx'], ellipse['ry']), (0.1, 0.2))
        self.assertAlmostEqual(ellipse['width'], 0.2)
        self.assertAlmostEqual(ellipse['height'], 0.4)
        self.assertEqual(ellipse['fill'], '#ff0000')
        self.assertEqual(ellipse['angle'], 15)
        self.assertTrue(ellipse['occlude_inactive'])

    def test_polygon_points(self):
        shapes = notetypes.occlusion_shapes(
            '{{c3::image-occlusion:polygon:points=.1,.2 .5,.2 .3,.6}}')
        polygon = shapes[0]
        self.assertEqual(polygon['shape'], 'polygon')
        self.assertEqual(polygon['points'], [(0.1, 0.2), (0.5, 0.2), (0.3, 0.6)])
        self.assertAlmostEqual(polygon['left'], 0.1)
        self.assertAlmostEqual(polygon['top'], 0.2)
        self.assertAlmostEqual(polygon['width'], 0.4)
        self.assertAlmostEqual(polygon['height'], 0.4)

    def test_text_label_with_escaped_colon(self):
        shapes = notetypes.occlusion_shapes(
            '{{c0::image-occlusion:text:left=.1:top=.9:text=Note\\: see\\\\ below:scale=1.5}}')
        label = shapes[0]
        self.assertEqual(label['ordinal'], 0)
        self.assertEqual(label['shape'], 'text')
        self.assertEqual(label['text'], 'Note: see\\ below')
        self.assertEqual(label['scale'], 1.5)
        self.assertEqual((label['left'], label['top']), (0.1, 0.9))

    def test_several_shapes_and_junk(self):
        text = ('{{c1::image-occlusion:rect:left=.1:top=.1:width=.2:height=.2}}<br>'
                'stray text {{c2::image-occlusion:rect:left=.4:top=.1:width=.2:height=.2}}'
                '{{c3::image-occlusion:star:left=0}}')
        shapes = notetypes.occlusion_shapes(text)
        self.assertEqual([shape['ordinal'] for shape in shapes], [1, 2])
        self.assertEqual(notetypes.occlusion_shapes(''), [])

    def test_occlusion_field_format(self):
        rect = {'ordinal': 1, 'shape': 'rect', 'left': 0.23, 'top': 0.1, 'width': 0.4,
                'height': 0.5, 'fill': None, 'angle': 0, 'occlude_inactive': False}
        self.assertEqual(
            notetypes.occlusion_field([rect]),
            '{{c1::image-occlusion:rect:left=0.23:top=0.1:width=0.4:height=0.5}}<br>')
        rect.update(occlude_inactive=True, fill='#00ff00', angle=12.5, left=1 / 3, width=1)
        self.assertEqual(
            notetypes.occlusion_field([rect]),
            '{{c1::image-occlusion:rect:left=0.3333:top=0.1:width=1:height=0.5:oi=1'
            ':fill=#00ff00:angle=12.5}}<br>')
        label = {'ordinal': 0, 'shape': 'text', 'left': 0.5, 'top': 0.5, 'text': 'a:b\\c',
                 'scale': 1}
        self.assertEqual(
            notetypes.occlusion_field([label]),
            '{{c0::image-occlusion:text:left=0.5:top=0.5:text=a\\:b\\\\c:scale=1}}<br>')

    def test_round_trip(self):
        text = ('{{c1::image-occlusion:rect:left=0.2:top=0.3:width=0.1:height=0.05}}<br>'
                '{{c2::image-occlusion:ellipse:left=0.5:top=0.5:rx=0.1:ry=0.2:oi=1'
                ':fill=#ff0000:angle=15}}<br>'
                '{{c3::image-occlusion:polygon:points=0.1,0.2 0.5,0.2 0.3,0.6}}<br>'
                '{{c0::image-occlusion:text:left=0.1:top=0.9:text=Note\\: here:scale=1.5}}<br>')
        shapes = notetypes.occlusion_shapes(text)
        self.assertEqual(len(shapes), 4)
        written = notetypes.occlusion_field(shapes)
        self.assertEqual(written, text)
        self.assertEqual(notetypes.occlusion_shapes(written), shapes)


if __name__ == '__main__':
    unittest.main()
