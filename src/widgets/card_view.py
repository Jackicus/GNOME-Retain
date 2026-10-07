# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The card face: one side of a card, rendered from its HTML.

    view = RetainCardView()
    view.set_card(question_html, answer_html, css, media_uri, occlusion=None, scale=1.0)
    view.show_question(); view.show_answer()
    view.set_typed_result(html)        # replaces the answer's {{type:…}} span (typed_answer_diff)
    view.connect('ready', …)           # the side shown has been rendered

WebKitGTK 6.0 renders the card when it can be imported: one WebView for the widget's life,
transparent over the libadwaita card behind it, with card.css (the gresource's) as a user
style sheet prefixed by the libadwaita colours of the moment as CSS variables (the accent,
the card's text colour, success/warning/error: read from the widget's style, again whenever
Adw.StyleManager turns dark or light), the note type's own CSS in a `<style>` of the document,
and `<body class="card nightMode">` in dark, as Anki marks it. The document's base URI is the
media folder (`file://…/media/`), so an `<img src="name">` loads; `[sound:…]` tags become a
small note mark (the page plays them, widgets/audio.py); `scale` is the view's zoom. MathJax
is not bundled: `\\(…\\)` and `\\[…\\]` stay as written, no network is used. JavaScript is on
(cloze and other templates use it); the view takes no keyboard focus, so the page's keys
reach its controller. For an image occlusion note, `occlusion` is {'shapes': [...]
(notetypes.occlusion_shapes), 'ordinal': N}: a script in the document lays a div over the
image for each shape, masked (the accent, opaque) or outlined, by side and ordinal.

Without WebKit (not importable, or RETAIN_NO_WEBKIT=1 in the environment) a Gtk.Label shows
the HTML as Pango markup, through html_to_markup(): bold, italic, underline, breaks and
paragraphs, sub- and superscript, code, images as "[image]", entities decoded.
"""

import html as html_module
import json
import logging
import os
import pathlib
import re
from gettext import gettext as _
from xml.sax.saxutils import escape

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

log = logging.getLogger(__name__)

CARD_CSS_RESOURCE = '/io/github/jackicus/Retain/card.css'

WebKit = None
if not os.environ.get('RETAIN_NO_WEBKIT'):
    try:
        gi.require_version('WebKit', '6.0')
        from gi.repository import WebKit
    except (ImportError, ValueError):
        log.info('WebKitGTK 6.0 is not available: cards are shown as plain text')

_TYPE_ANSWER = re.compile(r'<span class="retain-type-answer"[^>]*>\s*</span>')
_TYPE_RESULT = re.compile(r'<span class="retain-type-answer-result"[^>]*>\s*</span>')
_SOUND = re.compile(r'\[sound:([^\]]+)\]')

# Lays the occlusion shapes over the image, in pixels of the image as shown, again when the
# view is resized. window.RETAIN_OCCLUSION holds the shapes, the side and the ordinal.
OCCLUSION_SCRIPT = r"""
(function () {
    var data = window.RETAIN_OCCLUSION;
    var container = document.getElementById('image-occlusion-container');
    if (!data || !container) { return; }
    var img = container.querySelector('img');
    if (!img) { return; }
    var layer = document.createElement('div');
    layer.className = 'retain-occlusions';
    container.appendChild(layer);
    function bounds(shape) {
        if (shape.shape === 'polygon') {
            var xs = shape.points.map(function (p) { return p[0]; });
            var ys = shape.points.map(function (p) { return p[1]; });
            var left = Math.min.apply(null, xs), top = Math.min.apply(null, ys);
            return [left, top, Math.max.apply(null, xs) - left, Math.max.apply(null, ys) - top];
        }
        return [shape.left, shape.top, shape.width, shape.height];
    }
    function place() {
        layer.innerHTML = '';
        var w = img.offsetWidth, h = img.offsetHeight, x0 = img.offsetLeft, y0 = img.offsetTop;
        data.shapes.forEach(function (shape) {
            var div = document.createElement('div');
            var classes = ['retain-occlusion', shape.shape];
            var active = shape.ordinal === data.ordinal;
            if (shape.shape === 'text') {
                div.textContent = shape.text || '';
                classes.push('label');
                div.style.fontSize = ((shape.scale || 1) * 100) + '%';
            } else if (shape.ordinal === 0) {
                return;
            } else {
                var masked = data.side === 'question'
                    ? (active || shape.occlude_inactive)
                    : (!active && shape.occlude_inactive);
                classes.push(masked ? 'masked' : 'outlined');
                classes.push(active ? 'active' : 'inactive');
            }
            div.className = classes.join(' ');
            var box = bounds(shape);
            div.style.left = (x0 + box[0] * w) + 'px';
            div.style.top = (y0 + box[1] * h) + 'px';
            if (box[2] > 0 && box[3] > 0) {
                div.style.width = (box[2] * w) + 'px';
                div.style.height = (box[3] * h) + 'px';
            }
            if (shape.shape === 'polygon' && box[2] > 0 && box[3] > 0) {
                div.style.clipPath = 'polygon(' + shape.points.map(function (p) {
                    return ((p[0] - box[0]) / box[2] * 100) + '% ' +
                           ((p[1] - box[1]) / box[3] * 100) + '%';
                }).join(', ') + ')';
                div.style.borderRadius = '0';
            }
            if (shape.angle) { div.style.transform = 'rotate(' + shape.angle + 'deg)'; }
            layer.appendChild(div);
        });
    }
    if (img.complete) { place(); } else { img.addEventListener('load', place); }
    window.addEventListener('resize', place);
})();
"""


def _card_css():
    """The text of card.css: the gresource's, else the source tree's (tests without a build)."""
    try:
        data = Gio.resources_lookup_data(CARD_CSS_RESOURCE, Gio.ResourceLookupFlags.NONE)
        return data.get_data().decode('utf-8')
    except GLib.Error:
        path = pathlib.Path(__file__).resolve().parent.parent / 'card.css'
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            log.warning('card.css is missing: the card face is unstyled')
            return ''


def _rgba_css(rgba, alpha=None):
    alpha = rgba.alpha if alpha is None else alpha

    def channel(value):  # a mixed colour can leave the sRGB range
        return max(0, min(255, int(round(value * 255))))

    return (f'rgba({channel(rgba.red)}, {channel(rgba.green)}, {channel(rgba.blue)}, '
            f'{max(0.0, min(1.0, alpha)):.2f})')


def _mix(fg, bg, amount):
    """`amount` of fg over bg, opaque."""
    mixed = Gdk.RGBA()
    mixed.red = fg.red * amount + bg.red * (1 - amount)
    mixed.green = fg.green * amount + bg.green * (1 - amount)
    mixed.blue = fg.blue * amount + bg.blue * (1 - amount)
    mixed.alpha = 1.0
    return mixed


class _Probes(Gtk.Box):
    """Invisible labels whose computed colours are libadwaita's: the card's text, and the
    success, warning and error colours (their style classes), read with get_color()."""

    def __init__(self):
        super().__init__(visible=False)
        self._labels = {}
        for name in ('', 'success', 'warning', 'error'):
            label = Gtk.Label()
            if name:
                label.add_css_class(name)
            self.append(label)
            self._labels[name] = label

    def colour(self, name=''):
        return self._labels[name].get_color()


class RetainCardView(Adw.Bin):
    __gtype_name__ = 'RetainCardView'

    __gsignals__ = {
        'ready': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(vexpand=True)
        self._question = ''
        self._answer = ''
        self._css = ''
        self._base_uri = None
        self._occlusion = None
        self._scale = 1.0
        self._side = 'question'
        self._typed = None
        self._loaded = False
        self._styled = False  # the user style sheet has been added
        self._web = None
        self._label = None
        self._card_css = _card_css()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._probes = _Probes()
        box.append(self._probes)
        if WebKit is not None:
            self._web = self._make_web_view()
            box.append(self._web)
        else:
            self._label = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                    selectable=True, justify=Gtk.Justification.CENTER,
                                    xalign=0.5, use_markup=True, vexpand=True,
                                    margin_top=18, margin_bottom=18, margin_start=24,
                                    margin_end=24)
            box.append(self._label)
        self.set_child(box)
        self.add_css_class('card-view')
        style = Adw.StyleManager.get_default()
        # Held weakly: the style manager outlives the view (see widgets/util.py's note).
        ref = self.weak_ref()
        handler = None

        def on_dark(manager, _pspec):
            nonlocal handler
            view = ref()
            if view is None:
                if handler is not None and manager.handler_is_connected(handler):
                    manager.disconnect(handler)
                return
            view._apply_style()

        handler = style.connect('notify::dark', on_dark)

    @property
    def uses_webkit(self):
        return self._web is not None

    @property
    def side(self):
        return self._side

    # -- the WebKit view -------------------------------------------------------------------

    def _make_web_view(self):
        settings = WebKit.Settings(
            enable_javascript=True, enable_developer_extras=False,
            allow_file_access_from_file_urls=True, allow_universal_access_from_file_urls=False,
            enable_smooth_scrolling=True)
        self._manager = WebKit.UserContentManager()
        web = WebKit.WebView(user_content_manager=self._manager, settings=settings,
                             vexpand=True, hexpand=True)
        web.set_background_color(Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=0.0))
        web.set_can_focus(False)
        web.set_focusable(False)
        ref = self.weak_ref()

        def on_load_changed(_web, event):
            view = ref()
            if view is not None and event == WebKit.LoadEvent.FINISHED:
                view._loaded = True
                view.emit('ready')

        def on_decide_policy(_web, decision, kind):
            # The card is a document, not a browser: a link opens outside.
            if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
                action = decision.get_navigation_action()
                if action.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED:
                    uri = action.get_request().get_uri()
                    decision.ignore()
                    try:
                        Gio.AppInfo.launch_default_for_uri(uri, None)
                    except GLib.Error as error:
                        log.warning('opening %s: %s', uri, error.message)
                    return True
            return False

        web.connect('load-changed', on_load_changed)
        web.connect('decide-policy', on_decide_policy)
        web.connect('context-menu', lambda *_args: True)
        return web

    def _palette(self):
        """The CSS variables of the moment (see card.css)."""
        style = Adw.StyleManager.get_default()
        dark = style.get_dark()
        accent = style.get_accent_color_rgba()
        fg = self._probes.colour()
        card_bg = Gdk.RGBA()
        card_bg.parse('#1e1e1e' if dark else '#ffffff')  # only mixed with, never shown
        font = Pango.FontDescription.from_string(
            Gtk.Settings.get_default().props.gtk_font_name or 'Sans 11')
        family = font.get_family() or 'sans-serif'
        return {
            'accent-color': _rgba_css(accent),
            'accent-bg-color': _rgba_css(accent, 1.0),
            'card-fg-color': _rgba_css(fg),
            'dim-color': _rgba_css(fg, 0.55),  # as libadwaita's .dimmed: the text, faded
            'border-color': _rgba_css(fg, 0.15),
            'success-color': _rgba_css(self._probes.colour('success')),
            'warning-color': _rgba_css(self._probes.colour('warning')),
            'error-color': _rgba_css(self._probes.colour('error')),
            'occlusion-inactive-color': _rgba_css(_mix(fg, card_bg, 0.45)),
            'card-font-family': f'"{family}", sans-serif',
        }

    def _apply_style(self):
        if self._web is None:
            return
        variables = ' '.join(f'--{name}: {value};' for name, value in self._palette().items())
        sheet = WebKit.UserStyleSheet(f':root {{ {variables} }}\n' + self._card_css,
                                      WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                      WebKit.UserStyleLevel.USER, None, None)
        self._manager.remove_all_style_sheets()
        self._manager.add_style_sheet(sheet)
        self._styled = True
        if self._loaded:
            self._load()  # the body's nightMode class follows the style

    # -- the public API -------------------------------------------------------------------

    def set_card(self, question_html, answer_html, css, media_uri, occlusion=None, scale=1.0):
        """A card's two sides; `media_uri` is the media folder as a file:// URI with its
        trailing slash (None for no media); `occlusion` {'shapes': […], 'ordinal': N}."""
        self._question = question_html or ''
        self._answer = answer_html or ''
        self._css = css or ''
        self._base_uri = media_uri
        self._occlusion = occlusion
        self._scale = scale or 1.0
        self._typed = None
        if self._web is not None:
            self._web.set_zoom_level(self._scale)

    def show_question(self):
        self._side = 'question'
        self._load()

    def show_answer(self):
        self._side = 'answer'
        self._load()

    def set_typed_result(self, html):
        """The comparison to show where the answer's type-answer span is (and nothing where
        the question's was)."""
        self._typed = html
        if self._side == 'answer':
            self._load()

    def set_scale(self, scale):
        self._scale = scale or 1.0
        if self._web is not None:
            self._web.set_zoom_level(self._scale)
        elif self._label is not None:
            self._load()

    # -- rendering --------------------------------------------------------------------------

    def _side_html(self):
        html = self._question if self._side == 'question' else self._answer
        html = _TYPE_ANSWER.sub('', html)
        html = _TYPE_RESULT.sub(self._typed or '', html)
        return _SOUND.sub(lambda match: _sound_mark(match[1]), html)

    def _load(self):
        if self._web is not None:
            if not self._styled:
                self._apply_style()
            self._web.load_html(self.document(), self._base_uri)
        elif self._label is not None:
            markup = html_to_markup(self._side_html())
            if self._occlusion:
                markup = (markup + '\n' if markup else '') + escape(_('[image]'))
            self._label.set_markup(markup)
            self._label.set_attributes(_scaled(self._scale))
            self.emit('ready')

    def document(self):
        """The HTML document of the side shown (what the WebKit view loads)."""
        dark = Adw.StyleManager.get_default().get_dark()
        body_class = 'card nightMode' if dark else 'card'
        scripts = ''
        if self._occlusion:
            data = {'shapes': self._occlusion.get('shapes', []),
                    'ordinal': self._occlusion.get('ordinal', 1), 'side': self._side}
            payload = json.dumps(data).replace('</', '<\\/')
            scripts = (f'<script>window.RETAIN_OCCLUSION = {payload};</script>'
                       f'<script>{OCCLUSION_SCRIPT}</script>')
        return (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width">'
                f'<style>{self._css}</style></head>'
                f'<body class="{body_class}"><div id="qa">{self._side_html()}</div>'
                f'{scripts}</body></html>')


def _sound_mark(name):
    title = html_module.escape(name, quote=True)
    return f'<span class="retain-sound" title="{title}">&#9834;</span>'


def _scaled(scale):
    attributes = Pango.AttrList()
    if scale and scale != 1.0:
        attributes.insert(Pango.attr_scale_new(scale))
    return attributes


# -- the fallback: HTML to Pango markup -----------------------------------------------------

_TOKEN = re.compile(r'<!--.*?-->|<[^>]*>|[^<]+', re.DOTALL)
_DROP = re.compile(r'<(script|style)\b.*?</\1\s*>', re.DOTALL | re.IGNORECASE)
_CLASS = re.compile(r'class\s*=\s*["\']?([^"\'>\s]+)', re.IGNORECASE)
_TAG_NAME = re.compile(r'</?\s*([a-zA-Z][a-zA-Z0-9]*)')

# HTML tag -> Pango tag.
_INLINE = {'b': 'b', 'strong': 'b', 'i': 'i', 'em': 'i', 'u': 'u', 'ins': 'u', 's': 's',
           'del': 's', 'strike': 's', 'sub': 'sub', 'sup': 'sup', 'code': 'tt', 'tt': 'tt',
           'kbd': 'tt', 'pre': 'tt', 'samp': 'tt'}
_BLOCK = {'div', 'p', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'table',
          'blockquote', 'section', 'article', 'header', 'footer', 'pre', 'hr'}
_SPAN_CLASSES = {'cloze': 'b', 'typeGood': 'b', 'typeBad': 's', 'typeMissed': 'u'}


def html_to_markup(html):
    """A card side's HTML as Pango markup for a Gtk.Label: b, i, u, s, sub, sup and code
    (and the cloze and typed-answer spans as bold, struck or underlined), br and the block
    elements as line breaks, `<img>` as "[image]", `<hr>` as a rule, entities decoded, other
    tags dropped. The tags are balanced whatever the HTML did."""
    html = _DROP.sub('', html or '')
    html = _SOUND.sub('♪', html)
    out = []
    stack = []  # open Pango tags

    def newline(count=1):
        text = ''.join(out)
        stripped = text.rstrip(' \t')
        if stripped != text:
            out[:] = [stripped]
        trailing = len(stripped) - len(stripped.rstrip('\n'))
        if stripped and trailing < count:
            out.append('\n' * (count - trailing))

    for match in _TOKEN.finditer(html):
        token = match[0]
        if token.startswith('<!--'):
            continue
        if not token.startswith('<'):
            out.append(escape(html_module.unescape(token).replace('\xa0', ' ')))
            continue
        name_match = _TAG_NAME.match(token)
        if name_match is None:
            continue
        name = name_match[1].lower()
        closing = token.startswith('</')
        if name == 'br':
            out.append('\n')
        elif name == 'img':
            out.append(escape(_('[image]')))
        elif name == 'hr':
            newline()
            out.append('———\n')
        elif name in _INLINE or name in ('span', 'font'):
            # Every opening tag of these is pushed (a span without a Pango tag as None), so
            # its closing tag pops the right one.
            if closing:
                _pop(stack, out)
            elif not token.endswith('/>'):
                tag = _INLINE.get(name)
                if name in ('span', 'font'):
                    classes = _CLASS.search(token)
                    tag = next((_SPAN_CLASSES[c] for c in classes[1].split()
                                if c in _SPAN_CLASSES), None) if classes else None
                if tag is not None:
                    out.append(f'<{tag}>')
                stack.append(tag)
        if name in _BLOCK and name != 'hr':
            newline(2 if name in ('p', 'blockquote') and closing else 1)
    while stack:
        _pop(stack, out)
    text = ''.join(out)
    return re.sub(r'\n{3,}', '\n\n', text).strip('\n ')


def _pop(stack, out):
    """Close the innermost tag (a span without a Pango tag is a None entry)."""
    if not stack:
        return
    tag = stack.pop()
    if tag is not None:
        out.append(f'</{tag}>')
