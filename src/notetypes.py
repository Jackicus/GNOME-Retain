# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Note types: the fields and card templates a note is made of, and the stock ones.

The stock types are Anki's, with the same field names, templates and CSS, so a note made here
and exported to Anki looks the same there, and an Anki note imported here keeps working. The
app's own card style overrides the stock colours and fonts at render time. Image occlusion is
Anki's too (a cloze type whose Occlusion field holds the shapes), but the app draws the
shapes itself and ignores the template's script.

    NoteType(name='', kind='standard', fields=None, templates=None, css=DEFAULT_CSS,
             sort_field=0, id=None, original_id=None, modified=None)
        .id             None until stored; `.kind` standard, cloze or occlusion
        .fields         [{'name': …, 'description': …}, …]
        .templates      [{'name': …, 'qfmt': …, 'afmt': …}, …]
        .field_names() -> [str]
        .to_row() -> dict          a notetypes row (schema.py), fields and templates as JSON
        NoteType.from_row(row)     from a sqlite3.Row or a dict with the columns
        .copy()                    a deep copy (with the same id: set it to None for a clone)
        editing, on a copy that Collection.update_notetype() then stores:
        .add_field(name), .rename_field(i, name), .move_field(i, j), .remove_field(i),
        .set_sort_field(i), .add_template(name=None), .rename_template(i, name),
        .move_template(i, j), .remove_template(i)    NoteTypeError for an edit refused
        .template_problem(i) -> str or None          why a card template would not work
    NoteTypeError                  an edit refused, its message a sentence
    clean_field_name(name) -> str  a field name as Anki keeps it
    STOCK_KINDS                    basic, basic-reversed, basic-optional-reversed,
                                   basic-typed, cloze, occlusion
    stock(kind) -> NoteType        a fresh stock type, its name translated
    all_stock() -> [NoteType]      every stock type, in STOCK_KINDS order
    is_image_occlusion(notetype) -> bool
    occlusion_shapes(text) -> [dict]   the shapes in an Occlusion field
    occlusion_field(shapes) -> str     the inverse

The editing methods keep `field_ords` and `template_ords` (each field's and template's index
in the stored type, None for one added), so the collection knows what moved, went or came,
as Anki's ordinals tell it. Renaming a field rewrites the templates' references to it
(template.rename_fields); deleting one removes them.

A shape is a dict with 'ordinal' (the cloze number; 0 for a text label, which makes no card),
'shape' (rect, ellipse, polygon or text), 'left', 'top', 'width' and 'height' (fractions of
the image's size), 'fill' ('#rrggbb' or None), 'angle' (degrees, 0 when unrotated) and
'occlude_inactive' (whether the other cards' shapes are hidden too). An ellipse also has 'rx'
and 'ry', a polygon 'points' ([(x, y), …]), a text label 'text' and 'scale'. Anki writes
each as `{{cN::image-occlusion:SHAPE:key=value:key=value…}}` with `\\:` and `\\\\` escapes in
the values.
"""

import copy
import json
import re
import time
import unicodedata
from gettext import gettext as _

from . import template

DEFAULT_CSS = (
    '.card {\n'
    '    font-family: arial;\n'
    '    font-size: 20px;\n'
    '    text-align: center;\n'
    '    color: black;\n'
    '    background-color: white;\n'
    '}\n'
)

CLOZE_CSS = DEFAULT_CSS + (
    '\n'
    '.cloze {\n'
    '    font-weight: bold;\n'
    '    color: blue;\n'
    '}\n'
    '.nightMode .cloze {\n'
    '    color: lightblue;\n'
    '}\n'
)

IMAGE_OCCLUSION_CSS = CLOZE_CSS + (
    '#image-occlusion-canvas {\n'
    '    --inactive-shape-color: #ffeba2;\n'
    '    --active-shape-color: #ff8e8e;\n'
    '    --inactive-shape-border: 1px #212121;\n'
    '    --active-shape-border: 1px #212121;\n'
    '}\n'
    '\n'
    '.image-occlusion-container {\n'
    '    position: relative;\n'
    '}\n'
)

IMAGE_OCCLUSION_FIELDS = ['Occlusion', 'Image', 'Header', 'Back Extra', 'Comments']

STOCK_KINDS = ('basic', 'basic-reversed', 'basic-optional-reversed', 'basic-typed', 'cloze',
               'occlusion')

_FRONT_SIDE = '{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}'
_BACK_SIDE = '{{FrontSide}}\n\n<hr id=answer>\n\n{{Front}}'

_OCCLUSION_HEAD = (
    '{{#Header}}<div>{{Header}}</div>{{/Header}}\n'
    '<div style="display: none">{{cloze:Occlusion}}</div>\n'
    '<div id="image-occlusion-container">\n'
    '    {{Image}}\n'
    '    <canvas id="image-occlusion-canvas"></canvas>\n'
    '</div>\n'
)
_OCCLUSION_SCRIPT = (
    '<script>\n'
    'try {\n'
    '    anki.imageOcclusion.setup();\n'
    '} catch (exc) {\n'
    '    document.getElementById("err").innerHTML = `Error loading image occlusion. '
    'Is your Anki version up to date?<br><br>${exc}`;\n'
    '}\n'
    '</script>\n'
)
_OCCLUSION_QFMT = _OCCLUSION_HEAD + _OCCLUSION_SCRIPT
_OCCLUSION_AFMT = (_OCCLUSION_HEAD + '{{#Back Extra}}<div>{{Back Extra}}</div>{{/Back Extra}}\n'
                   + _OCCLUSION_SCRIPT)


RESERVED_NAMES = ('FrontSide',) + template.SPECIAL_FIELDS


class NoteTypeError(ValueError):
    """An edit a note type cannot take: the message is a sentence for the user."""


def clean_field_name(name):
    """A field name as Anki keeps it: NFC, trimmed, without `: { } "` and without a leading
    `# / ^` (which templates would read as a conditional)."""
    name = unicodedata.normalize('NFC', name or '')
    name = re.sub(r'[:{}"]', '', name).strip()
    return name.lstrip('#/^').strip()


class NoteType:
    """A note type: a name, fields, card templates and CSS (see the module docstring)."""

    def __init__(self, name='', kind='standard', fields=None, templates=None, css=DEFAULT_CSS,
                 sort_field=0, id=None, original_id=None, modified=None):
        self.id = id
        self.name = name
        self.kind = kind
        self.fields = [dict(field) for field in fields or []]
        self.templates = [dict(template) for template in templates or []]
        self.css = css
        self.sort_field = sort_field
        self.original_id = original_id
        self.modified = int(time.time()) if modified is None else modified
        # Where each field and template was in the stored type (None: added since), kept
        # by the editing methods for Collection.update_notetype(); not stored.
        self.field_ords = list(range(len(self.fields)))
        self.template_ords = list(range(len(self.templates)))

    def __repr__(self):
        return f'NoteType({self.name!r}, kind={self.kind!r}, id={self.id!r})'

    def field_names(self):
        """The fields' names, in order."""
        return [field['name'] for field in self.fields]

    def to_row(self):
        """The notetypes row, fields and templates as JSON strings."""
        return {
            'id': self.id,
            'name': self.name,
            'kind': self.kind,
            'fields': json.dumps(self.fields),
            'templates': json.dumps(self.templates),
            'css': self.css,
            'sort_field': self.sort_field,
            'original_id': self.original_id,
            'modified': self.modified,
        }

    @classmethod
    def from_row(cls, row):
        """A NoteType from a notetypes row (a sqlite3.Row or a dict with the columns)."""
        return cls(
            id=row['id'],
            name=row['name'],
            kind=row['kind'],
            fields=json.loads(row['fields']),
            templates=json.loads(row['templates']),
            css=row['css'],
            sort_field=row['sort_field'],
            original_id=row['original_id'],
            modified=row['modified'],
        )

    def copy(self):
        """A deep copy, with the same id; set it to None for a new type."""
        twin = NoteType(
            id=self.id, name=self.name, kind=self.kind, fields=copy.deepcopy(self.fields),
            templates=copy.deepcopy(self.templates), css=self.css, sort_field=self.sort_field,
            original_id=self.original_id, modified=self.modified)
        twin.field_ords = list(self.field_ords)
        twin.template_ords = list(self.template_ords)
        return twin

    def mark_stored(self):
        """Forget the edits' history: the fields and templates are as stored now."""
        self.field_ords = list(range(len(self.fields)))
        self.template_ords = list(range(len(self.templates)))

    # -- editing (Collection.update_notetype() stores the result) -----------------------------

    @property
    def has_templates_to_edit(self):
        """Whether templates can be added and removed: a standard type's (a cloze type has
        its one)."""
        return self.kind not in ('cloze', 'occlusion')

    def _field_name(self, name, index=None):
        name = clean_field_name(name)
        if not name:
            raise NoteTypeError(_('A field needs a name.'))
        if name in RESERVED_NAMES:
            raise NoteTypeError(_('“{name}” is a name templates use for something else.')
                                .format(name=name))
        for other, field in enumerate(self.fields):
            if other != index and field['name'].lower() == name.lower():
                raise NoteTypeError(_('A field called “{name}” already exists.')
                                    .format(name=field['name']))
        return name

    def add_field(self, name):
        """Append a field (every note gets it empty); its index."""
        self.fields.append({'name': self._field_name(name), 'description': ''})
        self.field_ords.append(None)
        return len(self.fields) - 1

    def rename_field(self, index, name):
        """Rename a field, and every template's references to it."""
        name = self._field_name(name, index)
        old = self.fields[index]['name']
        if name != old:
            self.fields[index]['name'] = name
            self._rewrite_templates({old: name})

    def move_field(self, index, new_index):
        """Move a field to `new_index` (its contents move with it in every note)."""
        new_index = max(0, min(new_index, len(self.fields) - 1))
        sort = self.fields[self.sort_field] if 0 <= self.sort_field < len(self.fields) else None
        self.fields.insert(new_index, self.fields.pop(index))
        self.field_ords.insert(new_index, self.field_ords.pop(index))
        if sort is not None:
            self.sort_field = next(i for i, field in enumerate(self.fields) if field is sort)

    def remove_field(self, index):
        """Delete a field, its contents in every note, and the templates' references to it
        (a front left with no field gets the first one, as Anki does)."""
        if len(self.fields) <= 1:
            raise NoteTypeError(_('A note type needs at least one field.'))
        name = self.fields.pop(index)['name']
        self.field_ords.pop(index)
        if self.sort_field == index:
            self.sort_field = 0
        elif self.sort_field > index:
            self.sort_field -= 1
        self._rewrite_templates({name: None})

    def set_sort_field(self, index):
        """The field the browser sorts by and shows first."""
        self.sort_field = index

    def _rewrite_templates(self, renames):
        first = self.fields[0]['name']
        cloze = not self.has_templates_to_edit
        for card in self.templates:
            card['qfmt'] = template.rename_fields(card['qfmt'], renames)
            card['afmt'] = template.rename_fields(card['afmt'], renames)
            try:
                if cloze:
                    if not template.cloze_fields_in(card['qfmt']):
                        card['qfmt'] += '{{cloze:' + first + '}}'
                    if not template.cloze_fields_in(card['afmt']):
                        card['afmt'] += '{{cloze:' + first + '}}'
                elif not template.field_names_in(card['qfmt']):
                    card['qfmt'] += '{{' + first + '}}'
            except template.TemplateError:
                pass  # left for template_problem() to report

    def _template_name(self, name, index=None):
        name = (name or '').strip()
        if not name:
            raise NoteTypeError(_('A card template needs a name.'))
        for other, card in enumerate(self.templates):
            if other != index and card['name'].lower() == name.lower():
                raise NoteTypeError(_('A card template called “{name}” already exists.')
                                    .format(name=card['name']))
        return name

    def new_template_name(self):
        """'Card N', the first N free."""
        names = {card['name'].lower() for card in self.templates}
        number = len(self.templates) + 1
        while _('Card {number}').format(number=number).lower() in names:
            number += 1
        return _('Card {number}').format(number=number)

    def add_template(self, name=None):
        """Append a card template, a reverse card to start from (the second field asked,
        the first on the back) so it makes no duplicate of the first card; its index. The
        cards it calls for are made when the type is stored."""
        if not self.has_templates_to_edit:
            raise NoteTypeError(_('A cloze note type has one card template.'))
        name = self._template_name(name or self.new_template_name())
        names = self.field_names()
        front = names[1] if len(names) > 1 else names[0]
        self.templates.append(_template(
            name, '{{' + front + '}}', '{{FrontSide}}\n\n<hr id=answer>\n\n{{' + names[0] + '}}'))
        self.template_ords.append(None)
        return len(self.templates) - 1

    def rename_template(self, index, name):
        self.templates[index]['name'] = self._template_name(name, index)

    def move_template(self, index, new_index):
        """Move a card template (its cards follow it)."""
        new_index = max(0, min(new_index, len(self.templates) - 1))
        self.templates.insert(new_index, self.templates.pop(index))
        self.template_ords.insert(new_index, self.template_ords.pop(index))

    def remove_template(self, index):
        """Delete a card template; its cards go when the type is stored."""
        if not self.has_templates_to_edit:
            raise NoteTypeError(_('A cloze note type has one card template.'))
        if len(self.templates) <= 1:
            raise NoteTypeError(_('A note type needs at least one card template.'))
        self.templates.pop(index)
        self.template_ords.pop(index)

    def template_problem(self, index):
        """What stops a card template from working, as a sentence, or None: a conditional
        left open or closed twice, a field that does not exist, a front without a field (a
        standard type's) or without a cloze (a cloze type's), as Anki checks them."""
        card = self.templates[index]
        names = set(self.field_names())
        sides = ((_('The front'), card['qfmt']), (_('The back'), card['afmt']))
        for side, text in sides:
            try:
                referenced = template.field_names_in(text)
            except template.TemplateError as error:
                if error.problem == 'unopened':
                    return _('{side} closes {{{{/{key}}}}}, which is not open.').format(
                        side=side, key=error.key)
                return _('{side} opens {{{{#{key}}}}} and does not close it.').format(
                    side=side, key=error.key)
            unknown = sorted(name for name in referenced if name not in names)
            if unknown:
                return _('{side} names “{field}”, which is not a field.').format(
                    side=side, field=unknown[0])
        first = self.fields[0]['name'] if self.fields else ''
        if not self.has_templates_to_edit:
            if not template.cloze_fields_in(card['qfmt']):
                return _('The front needs a cloze, such as {{{{cloze:{field}}}}}.').format(
                    field=first)
        elif not template.field_names_in(card['qfmt']):
            return _('The front needs a field, such as {{{{{field}}}}}.').format(field=first)
        return None


def _fields(*names):
    return [{'name': name, 'description': ''} for name in names]


def _template(name, qfmt, afmt):
    return {'name': name, 'qfmt': qfmt, 'afmt': afmt}


def stock(kind):
    """A fresh stock note type (`kind` is one of STOCK_KINDS), not yet stored."""
    if kind == 'basic':
        return NoteType(
            _('Basic'), fields=_fields('Front', 'Back'),
            templates=[_template('Card 1', '{{Front}}', _FRONT_SIDE)])
    if kind == 'basic-reversed':
        return NoteType(
            _('Basic (and reversed card)'), fields=_fields('Front', 'Back'),
            templates=[_template('Card 1', '{{Front}}', _FRONT_SIDE),
                       _template('Card 2', '{{Back}}', _BACK_SIDE)])
    if kind == 'basic-optional-reversed':
        return NoteType(
            _('Basic (optional reversed card)'), fields=_fields('Front', 'Back', 'Add Reverse'),
            templates=[_template('Card 1', '{{Front}}', _FRONT_SIDE),
                       _template('Card 2', '{{#Add Reverse}}{{Back}}{{/Add Reverse}}',
                                 _BACK_SIDE)])
    if kind == 'basic-typed':
        return NoteType(
            _('Basic (type in the answer)'), fields=_fields('Front', 'Back'),
            templates=[_template('Card 1', '{{Front}}\n\n{{type:Back}}',
                                 '{{Front}}\n\n<hr id=answer>\n\n{{type:Back}}')])
    if kind == 'cloze':
        return NoteType(
            _('Cloze'), kind='cloze', fields=_fields('Text', 'Back Extra'),
            templates=[_template('Cloze', '{{cloze:Text}}', '{{cloze:Text}}<br>\n{{Back Extra}}')],
            css=CLOZE_CSS)
    if kind == 'occlusion':
        return NoteType(
            _('Image Occlusion'), kind='occlusion', fields=_fields(*IMAGE_OCCLUSION_FIELDS),
            templates=[_template('Image Occlusion', _OCCLUSION_QFMT, _OCCLUSION_AFMT)],
            css=IMAGE_OCCLUSION_CSS)
    raise ValueError(f'unknown stock note type {kind!r}')


def all_stock():
    """Every stock note type, in STOCK_KINDS order."""
    return [stock(kind) for kind in STOCK_KINDS]


def is_image_occlusion(notetype):
    """Whether `notetype` is image occlusion: the app's own kind, or an Anki image occlusion
    type (a cloze type whose first fields are IMAGE_OCCLUSION_FIELDS) as imported."""
    if notetype.kind == 'occlusion':
        return True
    count = len(IMAGE_OCCLUSION_FIELDS)
    return notetype.kind == 'cloze' and notetype.field_names()[:count] == IMAGE_OCCLUSION_FIELDS


# --- The Occlusion field ----------------------------------------------------------------------

_SHAPE_RE = re.compile(r'\{\{c(\d+)::image-occlusion:(.*?)\}\}', re.DOTALL)
_FLOAT_KEYS = ('left', 'top', 'width', 'height', 'rx', 'ry', 'scale', 'angle')
# The keys Anki writes for each shape, in its order; anything else follows them.
_SHAPE_KEYS = {
    'rect': ('left', 'top', 'width', 'height'),
    'ellipse': ('left', 'top', 'rx', 'ry'),
    'polygon': ('points',),
    'text': ('left', 'top', 'text', 'scale'),
}
_KNOWN_KEYS = {'ordinal', 'shape', 'fill', 'angle', 'occlude_inactive', 'left', 'top', 'width',
               'height', 'rx', 'ry', 'points', 'text', 'scale'}


def _split_unescaped(text, separator):
    """Split `text` on `separator` outside `\\` escapes, the escapes left in the parts."""
    parts = []
    current = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text):
            current.append(text[index:index + 2])
            index += 2
            continue
        if char == separator:
            parts.append(''.join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append(''.join(current))
    return parts


def _unescape(value):
    return re.sub(r'\\(.)', r'\1', value, flags=re.DOTALL)


def _escape(value):
    return value.replace('\\', '\\\\').replace(':', '\\:')


def _float(value):
    try:
        return float(value)
    except ValueError:
        return 0.0


def _points(value):
    points = []
    for pair in value.split():
        x, _separator, y = pair.partition(',')
        points.append((_float(x), _float(y)))
    return points


def occlusion_shapes(text):
    """The shapes in an Occlusion field (see the module docstring); malformed entries are
    skipped, unknown keys kept as strings."""
    shapes = []
    for match in _SHAPE_RE.finditer(text):
        parts = _split_unescaped(match.group(2), ':')
        kind = parts[0].strip()
        if kind not in _SHAPE_KEYS:
            continue
        shape = {'ordinal': int(match.group(1)), 'shape': kind, 'left': 0.0, 'top': 0.0,
                 'width': 0.0, 'height': 0.0, 'fill': None, 'angle': 0.0,
                 'occlude_inactive': False}
        if kind == 'ellipse':
            shape.update(rx=0.0, ry=0.0)
        elif kind == 'polygon':
            shape['points'] = []
        elif kind == 'text':
            shape.update(text='', scale=1.0)
        for part in parts[1:]:
            key, separator, raw = part.partition('=')
            if not separator:
                continue
            key = key.strip()
            value = _unescape(raw)
            if key in _FLOAT_KEYS:
                shape[key] = _float(value)
            elif key == 'oi':
                shape['occlude_inactive'] = value.strip() == '1'
            elif key == 'fill':
                shape['fill'] = value or None
            elif key == 'points':
                shape['points'] = _points(value)
            elif key == 'text':
                shape['text'] = value
            else:
                shape[key] = value
        if kind == 'ellipse':
            if not shape['width']:
                shape['width'] = shape['rx'] * 2
            if not shape['height']:
                shape['height'] = shape['ry'] * 2
        elif kind == 'polygon' and shape['points'] and not shape['width']:
            xs = [x for x, _y in shape['points']]
            ys = [y for _x, y in shape['points']]
            shape.update(left=min(xs), top=min(ys), width=max(xs) - min(xs),
                         height=max(ys) - min(ys))
        shapes.append(shape)
    return shapes


def _number(value):
    """A float with up to four decimals, trailing zeros trimmed: 0.25, 0.3333, 1."""
    text = f'{float(value):.4f}'.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


def occlusion_field(shapes):
    """The Occlusion field text for `shapes` (as occlusion_shapes() returns them), one
    `{{cN::image-occlusion:…}}<br>` per shape."""
    lines = []
    for shape in shapes:
        kind = shape['shape']
        keys = _SHAPE_KEYS[kind]
        parts = [f'{{{{c{shape["ordinal"]}::image-occlusion', kind]
        for key in keys:
            parts.append(f'{key}={_value(key, shape.get(key))}')
        if shape.get('occlude_inactive'):
            parts.append('oi=1')
        if shape.get('fill'):
            parts.append(f'fill={_escape(str(shape["fill"]))}')
        if shape.get('angle'):
            parts.append(f'angle={_number(shape["angle"])}')
        for key, value in shape.items():  # the unknown keys kept by occlusion_shapes()
            if key not in _KNOWN_KEYS and value is not None:
                parts.append(f'{key}={_value(key, value)}')
        lines.append(':'.join(parts) + '}}<br>')
    return ''.join(lines)


def _value(key, value):
    if key == 'points':
        return ' '.join(f'{_number(x)},{_number(y)}' for x, y in value or [])
    if key in _FLOAT_KEYS:
        return _number(value or 0)
    return _escape(str(value if value is not None else ''))
