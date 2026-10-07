# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The charts of the Statistics page: widgets that draw their data in do_snapshot.

    heatmap = Heatmap(); heatmap.set_data(counts, today)   # {day_number: reviews}
    chart = BarChart(); chart.set_data(bars, colours, labels=…, line=…, tooltips=…)
    donut = Donut(); donut.set_data([(count, colour), …], centre='123', caption='cards')
    bar = SplitBar(); bar.set_data([(count, colour), …])
    Dot(colour)                                             # a legend's coloured dot
    chart.set_description(text)                             # what a screen reader hears

A colour is a name: 'accent' (the user's accent colour, from the style manager), 'success',
'warning' and 'error' (libadwaita's palette, a light and a dark value each, switched on the
style manager's `dark`) or 'neutral' (the foreground colour, faint). Text uses the widget's
own colour and font (the `caption` size), so the charts follow the theme; each one redraws
when the style manager's dark or accent-color changes while it is mapped. Every chart is an
image to a screen reader, with the description the page sets.

A chart measures its own size (no layout manager, so do_measure is honoured): a bar chart
or a donut asks for a natural height and stretches to the width it is given; the heatmap
never draws a cell smaller than MIN_CELL, so it asks for that width at least and the page
scrolls it when the window is narrower.

The pure helpers are the page's arithmetic over a heatmap: current_streak(), longest_streak(),
quantile_steps() and level_of() (the colour steps), and nice_ticks() for an axis.
"""

import bisect
import math
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango

from .. import days

# libadwaita's success, warning and error background colours, light then dark.
PALETTE = {
    'success': ('#2ec27e', '#26a269'),
    'warning': ('#e5a50a', '#cd9309'),
    'error': ('#e01b24', '#c01c28'),
}
HEATMAP_ALPHAS = (0.25, 0.4, 0.6, 0.8, 1.0)  # the accent at each level above zero
STEPS = len(HEATMAP_ALPHAS)
ZERO_ALPHA = 0.08  # the foreground colour for a day without a review
AXIS_ALPHA = 0.55
GRID_ALPHA = 0.12
LINE_WIDTH = 2.0


# -- colours and pure helpers -------------------------------------------------------------

def colour(name, foreground=None):
    """The Gdk.RGBA of a colour name for the current style, 'name:alpha' for a lighter
    tint ('success:0.5'); 'neutral' is the foreground, faint."""
    name, _sep, alpha = name.partition(':')
    manager = Adw.StyleManager.get_default()
    if name == 'accent':
        rgba = manager.get_accent_color_rgba()
    elif name == 'neutral':
        rgba = with_alpha(foreground or Gdk.RGBA(), 0.25)
    else:
        rgba = Gdk.RGBA()
        rgba.parse(PALETTE[name][1 if manager.get_dark() else 0])
    return with_alpha(rgba, float(alpha)) if alpha else rgba


def with_alpha(rgba, alpha):
    result = Gdk.RGBA()
    result.red, result.green, result.blue, result.alpha = rgba.red, rgba.green, rgba.blue, alpha
    return result


def current_streak(counts, today):
    """Consecutive days with a count, ending today or (today being empty) yesterday."""
    day = today if counts.get(today, 0) > 0 else today - 1
    streak = 0
    while counts.get(day, 0) > 0:
        streak += 1
        day -= 1
    return streak


def longest_streak(counts):
    """The longest run of consecutive days with a count."""
    longest = run = 0
    previous = None
    for day in sorted(day for day, count in counts.items() if count > 0):
        run = run + 1 if previous is not None and day == previous + 1 else 1
        longest = max(longest, run)
        previous = day
    return longest


def quantile_steps(values, steps=STEPS):
    """The thresholds that split the positive values into `steps` quantile levels:
    `steps - 1` values, ascending (empty when there are no values)."""
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return []
    return [ordered[min(len(ordered) - 1, len(ordered) * step // steps)]
            for step in range(1, steps)]


def level_of(value, thresholds):
    """0 for nothing, else 1 to len(thresholds) + 1 by the thresholds passed."""
    if value <= 0:
        return 0
    return 1 + bisect.bisect_right(thresholds, value)


def nice_ticks(maximum, target=4):
    """(step, top) for an axis up to `maximum`: a round step giving about `target`
    gridlines, and the first multiple of it at or above the maximum."""
    if maximum <= 0:
        return 1, 1
    raw = maximum / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if maximum / step <= target + 0.5:
            break
    if step >= 1:
        step = int(step)
    top = math.ceil(maximum / step - 1e-9) * step
    return step, top


def format_count(value):
    """A tick label: whole numbers with thousands separators, else one decimal."""
    if float(value).is_integer():
        return f'{int(value):,}'
    return f'{value:.1f}'


def date_label(day, pattern='%e %b'):
    """A day number as a short local date ('3 Mar')."""
    date = days.date_of(day)
    moment = GLib.DateTime.new_local(date.year, date.month, date.day, 0, 0, 0)
    return moment.format(pattern).strip()


def _rect(x, y, width, height):
    return Graphene.Rect().init(x, y, width, height)


def _rounded(bounds, radius, top_only=False):
    corner = Graphene.Size().init(radius, radius)
    square = Graphene.Size().init(0, 0)
    rounded = Gsk.RoundedRect()
    rounded.init(bounds, corner, corner, square if top_only else corner,
                 square if top_only else corner)
    return rounded


# -- the base ---------------------------------------------------------------------------

class Chart(Gtk.Widget):
    """A widget drawn in do_snapshot, an image to a screen reader; see the module."""

    MIN_WIDTH = 120
    NATURAL_WIDTH = 240
    NATURAL_HEIGHT = 160
    ROLE = Gtk.AccessibleRole.IMG

    def __init__(self, **kwargs):
        super().__init__(accessible_role=self.ROLE, **kwargs)
        self.add_css_class('caption')
        self._description = ''
        self._style_handlers = []

    # -- data and description --

    @property
    def description(self):
        """The accessible description last set."""
        return self._description

    def set_description(self, text):
        self._description = text or ''
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [self._description])
        self.queue_draw()

    # -- size --

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.MIN_WIDTH, max(self.MIN_WIDTH, self.NATURAL_WIDTH), -1, -1
        return self.NATURAL_HEIGHT, self.NATURAL_HEIGHT, -1, -1

    # -- the style --

    def do_map(self):
        Gtk.Widget.do_map(self)
        manager = Adw.StyleManager.get_default()
        self._style_handlers = [manager.connect(f'notify::{name}', self._on_style_changed)
                                for name in ('dark', 'accent-color')]

    def do_unmap(self):
        manager = Adw.StyleManager.get_default()
        for handler in self._style_handlers:
            manager.disconnect(handler)
        self._style_handlers = []
        Gtk.Widget.do_unmap(self)

    def _on_style_changed(self, *_args):
        self.queue_draw()

    # -- drawing helpers --

    def do_snapshot(self, snapshot):
        self.draw(snapshot, self.get_width(), self.get_height())

    def draw(self, snapshot, width, height):
        """Draw the chart into `width` × `height`; the subclasses do."""

    def foreground(self):
        return self.get_color()

    def dim(self, alpha=AXIS_ALPHA):
        return with_alpha(self.get_color(), alpha)

    def colour(self, name):
        return colour(name, self.get_color())

    def layout(self, text, scale=None, bold=False):
        layout = self.create_pango_layout(text)
        if scale or bold:
            attributes = Pango.AttrList()
            if scale:
                attributes.insert(Pango.attr_scale_new(scale))
            if bold:
                attributes.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            layout.set_attributes(attributes)
        return layout

    def text_size(self, text):
        return self.layout(text).get_pixel_size()

    def draw_text(self, snapshot, layout, x, y, rgba=None, align='left'):
        width, _height = layout.get_pixel_size()
        if align == 'center':
            x -= width / 2
        elif align == 'right':
            x -= width
        snapshot.save()
        snapshot.translate(Graphene.Point().init(round(x), round(y)))
        snapshot.append_layout(layout, rgba or self.foreground())
        snapshot.restore()

    @staticmethod
    def fill(snapshot, x, y, width, height, rgba, radius=0, top_only=False):
        bounds = _rect(x, y, width, height)
        if radius <= 0 or width < 2 * radius:
            snapshot.append_color(rgba, bounds)
            return
        snapshot.push_rounded_clip(_rounded(bounds, radius, top_only))
        snapshot.append_color(rgba, bounds)
        snapshot.pop()

    @staticmethod
    def stroke_polyline(snapshot, points, rgba, width=LINE_WIDTH):
        if len(points) < 2:
            if points:
                x, y = points[0]
                snapshot.append_color(rgba, _rect(x - width, y - width, 2 * width, 2 * width))
            return
        builder = Gsk.PathBuilder.new()
        builder.move_to(*points[0])
        for point in points[1:]:
            builder.line_to(*point)
        stroke = Gsk.Stroke.new(width)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(builder.to_path(), stroke, rgba)


# -- the heatmap --------------------------------------------------------------------------

class Heatmap(Chart):
    """A year of days as a calendar of squares, a week per column, Monday at the top."""

    __gtype_name__ = 'RetainHeatmap'

    CELL = 13
    MIN_CELL = 8
    GAP = 2
    WEEKS = 53
    DAYS = 365

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.counts = {}
        self.today = None
        self.thresholds = []
        self.set_has_tooltip(True)
        self.connect('query-tooltip', self._on_query_tooltip)

    def set_data(self, counts, today):
        self.counts = dict(counts)
        self.today = today
        self.thresholds = quantile_steps(self.counts.values())
        self.queue_resize()
        self.queue_draw()

    # -- geometry --

    def first_day(self):
        return self.today - self.DAYS + 1

    def start(self):
        """The Monday of the first column."""
        first = self.first_day()
        return first - days.date_of(first).weekday()

    def columns(self):
        if self.today is None:
            return self.WEEKS
        return (self.today - self.start()) // 7 + 1

    def _gutters(self):
        """(left, top): the room for the weekday initials and the month names."""
        width, height = self.text_size('W')
        return width + 8, height + 4

    def _cell_for_width(self, width):
        left, _top = self._gutters()
        cell = (width - left) // self.WEEKS - self.GAP
        return max(self.MIN_CELL, min(self.CELL, int(cell)))

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        left, top = self._gutters()
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum = left + self.WEEKS * (self.MIN_CELL + self.GAP)
            natural = left + self.WEEKS * (self.CELL + self.GAP)
            return minimum, natural, -1, -1
        cell = self._cell_for_width(for_size) if for_size >= 0 else self.CELL
        height = top + 7 * (cell + self.GAP)
        return height, height, -1, -1

    def _cell_at(self, x, y):
        """The day number under a point, or None."""
        if self.today is None:
            return None
        left, top = self._gutters()
        cell = self._cell_for_width(self.get_width())
        step = cell + self.GAP
        column = int((x - left) // step)
        row = int((y - top) // step)
        if column < 0 or row < 0 or row > 6 or column >= self.columns():
            return None
        day = self.start() + column * 7 + row
        if day < self.first_day() or day > self.today:
            return None
        return day

    # -- drawing --

    def draw(self, snapshot, width, height):
        if self.today is None:
            return
        left, top = self._gutters()
        cell = self._cell_for_width(width)
        step = cell + self.GAP
        foreground = self.foreground()
        dim = self.dim()
        accent = self.colour('accent')
        zero = with_alpha(foreground, ZERO_ALPHA)
        start = self.start()
        first = self.first_day()
        radius = 2 if cell >= 10 else 1
        # The month names: at each column whose Monday starts a new month, unless the
        # next one follows within three columns.
        labels = []
        previous_month = None
        for column in range(self.columns()):
            date = days.date_of(start + column * 7)
            if date.month != previous_month:
                if previous_month is not None or column == 0:
                    labels.append((column, date))
                previous_month = date.month
        for index, (column, date) in enumerate(labels):
            if index + 1 < len(labels) and labels[index + 1][0] - column < 3:
                continue
            if column == 0 and start + 7 < first:
                continue
            self.draw_text(snapshot, self.layout(date_label(date.toordinal(), '%b')),
                           left + column * step, 0, dim)
        # The weekday initials beside Monday, Wednesday and Friday.
        for row in (0, 2, 4):
            label = self.layout(date_label(start + row, '%a')[:1])
            _w, h = label.get_pixel_size()
            self.draw_text(snapshot, label, 0, top + row * step + (cell - h) / 2, dim)
        # The cells.
        for column in range(self.columns()):
            for row in range(7):
                day = start + column * 7 + row
                if day < first or day > self.today:
                    continue
                level = level_of(self.counts.get(day, 0), self.thresholds)
                rgba = zero if level == 0 else with_alpha(accent, HEATMAP_ALPHAS[level - 1])
                x = left + column * step
                y = top + row * step
                self.fill(snapshot, x, y, cell, cell, rgba, radius)
                if day == self.today:
                    outline = _rounded(_rect(x - 1, y - 1, cell + 2, cell + 2), radius + 1)
                    snapshot.append_border(outline, [1.5] * 4, [foreground] * 4)

    def _on_query_tooltip(self, _widget, x, y, keyboard, tooltip):
        if keyboard:
            return False
        day = self._cell_at(x, y)
        if day is None:
            return False
        count = self.counts.get(day, 0)
        date = date_label(day, '%e %B %Y')
        if count == 0:
            tooltip.set_text(_('No reviews on {date}').format(date=date))
        else:
            tooltip.set_text(ngettext('{n} review on {date}', '{n} reviews on {date}',
                                      count).format(n=f'{count:,}', date=date))
        return True


# -- bars -------------------------------------------------------------------------------

class BarChart(Chart):
    """Stacked bars with gridlines, an optional line on its own scale, and x labels.

        set_data(bars, colours, labels=None, line=None, line_colour='success',
                 line_max=None, line_format=None, tooltips=None)

    `bars` is a list of stacks, each a list of heights (one per colour in `colours`);
    `labels` a string or None per bar; `line` a value or None (a gap) per bar, scaled to
    `line_max` (its own maximum by default) and labelled at the right by `line_format`;
    `tooltips` a string per bar.
    """

    __gtype_name__ = 'RetainBarChart'

    NATURAL_HEIGHT = 180
    TOP = 8
    GAP = 2

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bars = []
        self.colours = []
        self.labels = None
        self.line = None
        self.line_colour = 'success'
        self.line_max = None
        self.line_format = None
        self.tooltips = None
        self.set_has_tooltip(True)
        self.connect('query-tooltip', self._on_query_tooltip)

    def set_data(self, bars, colours, labels=None, line=None, line_colour='success',
                 line_max=None, line_format=None, tooltips=None):
        self.bars = [list(stack) for stack in bars]
        self.colours = list(colours)
        self.labels = labels
        self.line = line
        self.line_colour = line_colour
        self.line_max = line_max
        self.line_format = line_format or format_count
        self.tooltips = tooltips
        self.queue_draw()

    def maximum(self):
        return max((sum(stack) for stack in self.bars), default=0)

    def _frame(self, width, height):
        """(left, right, top, bottom) of the plot area, and the ticks."""
        step, top_value = nice_ticks(self.maximum())
        ticks = [tick * step for tick in range(int(round(top_value / step)) + 1)]
        left = max(self.text_size(format_count(tick))[0] for tick in ticks) + 8
        right = 0
        if self.line is not None:
            right = max(self.text_size(self.line_format(value))[0]
                        for value in self._line_ticks()) + 8
        bottom = height
        if self.labels and any(label for label in self.labels):
            bottom = height - self.text_size('0')[1] - 4
        return left, width - right, self.TOP, bottom, ticks, top_value

    def _line_ticks(self):
        """The line's own scale: round ticks up to its maximum (`line_max` when set)."""
        if self.line_max is not None:
            maximum = self.line_max
        else:
            maximum = max((value for value in self.line if value is not None), default=0)
        step, top = nice_ticks(maximum, target=2)
        return [tick * step for tick in range(int(round(top / step)) + 1)]

    def _slot(self, left, right):
        count = max(1, len(self.bars))
        return (right - left) / count

    def draw(self, snapshot, width, height):
        if not self.bars:
            return
        left, right, top, bottom, ticks, top_value = self._frame(width, height)
        dim = self.dim()
        grid = self.dim(GRID_ALPHA)
        plot_height = bottom - top
        # Gridlines and their labels.
        for tick in ticks:
            y = bottom - plot_height * tick / top_value
            snapshot.append_color(grid, _rect(left, round(y), right - left, 1))
            label = self.layout(format_count(tick))
            _w, h = label.get_pixel_size()
            self.draw_text(snapshot, label, left - 6, y - h / 2, dim, align='right')
        # The bars.
        slot = self._slot(left, right)
        gap = self.GAP if slot >= 4 else 0
        bar_width = max(1, slot - gap)
        radius = 2 if bar_width >= 4 else 0
        colours = [self.colour(name) for name in self.colours]
        for index, stack in enumerate(self.bars):
            x = left + index * slot + gap / 2
            total = sum(stack)
            if total <= 0:
                continue
            stack_height = plot_height * total / top_value
            snapshot.push_rounded_clip(
                _rounded(_rect(x, bottom - stack_height, bar_width, stack_height), radius,
                         top_only=True))
            y = bottom
            for value, rgba in zip(stack, colours, strict=True):
                if value <= 0:
                    continue
                segment = plot_height * value / top_value
                y -= segment
                inset = 1 if segment > 2 and y > bottom - stack_height + 1 else 0
                snapshot.append_color(rgba, _rect(x, y + inset, bar_width, segment - inset))
            snapshot.pop()
        # The line, on its own scale, with its labels at the right.
        if self.line is not None:
            ticks = self._line_ticks()
            line_top = ticks[-1] or 1
            rgba = self.colour(self.line_colour)
            run = []
            for index, value in enumerate(self.line):
                if value is None:
                    self.stroke_polyline(snapshot, run, rgba)
                    run = []
                    continue
                run.append((left + index * slot + slot / 2,
                            bottom - plot_height * min(value, line_top) / line_top))
            self.stroke_polyline(snapshot, run, rgba)
            for value in ticks:
                label = self.layout(self.line_format(value))
                _w, h = label.get_pixel_size()
                y = bottom - plot_height * value / line_top
                self.draw_text(snapshot, label, right + 6, y - h / 2, dim)
        # The x labels, skipping any that would overlap the one before.
        if self.labels:
            last_right = -math.inf
            for index, text in enumerate(self.labels):
                if not text:
                    continue
                label = self.layout(text)
                w, _h = label.get_pixel_size()
                x = left + index * slot + slot / 2
                x = min(max(x, left + w / 2), right - w / 2)
                if x - w / 2 < last_right + 8:
                    continue
                last_right = x + w / 2
                self.draw_text(snapshot, label, x, bottom + 4, dim, align='center')

    def _on_query_tooltip(self, _widget, x, y, keyboard, tooltip):
        if keyboard or not self.tooltips or not self.bars:
            return False
        left, right, top, bottom, _ticks, _top = self._frame(self.get_width(),
                                                             self.get_height())
        if x < left or x >= right or y < top or y > bottom:
            return False
        index = int((x - left) // self._slot(left, right))
        if index < 0 or index >= len(self.tooltips) or not self.tooltips[index]:
            return False
        tooltip.set_text(self.tooltips[index])
        return True


# -- the donut --------------------------------------------------------------------------

class Donut(Chart):
    """A ring of shares with a number in the middle."""

    __gtype_name__ = 'RetainDonut'

    SIZE = 168
    THICKNESS = 22
    MIN_WIDTH = SIZE
    NATURAL_WIDTH = SIZE
    NATURAL_HEIGHT = SIZE

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.segments = []
        self.centre = ''
        self.caption = ''

    def set_data(self, segments, centre='', caption=''):
        self.segments = [(value, name) for value, name in segments if value > 0]
        self.centre = centre
        self.caption = caption
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.SIZE, self.SIZE, -1, -1

    def draw(self, snapshot, width, height):
        cx, cy = width / 2, height / 2
        outer = min(width, height) / 2 - 1
        inner = outer - self.THICKNESS
        total = sum(value for value, _name in self.segments)
        if total <= 0:
            self._ring(snapshot, cx, cy, outer, inner, with_alpha(self.foreground(), ZERO_ALPHA))
        elif len(self.segments) == 1:
            self._ring(snapshot, cx, cy, outer, inner, self.colour(self.segments[0][1]))
        else:
            gap = self.GAP_PX / outer  # a sliver of the card between neighbours
            angle = 0.0
            for value, name in self.segments:
                sweep = 2 * math.pi * value / total
                start = angle + gap / 2
                end = angle + sweep - gap / 2
                if end > start:
                    self._segment(snapshot, cx, cy, outer, inner, start, end,
                                  self.colour(name))
                angle += sweep
        if self.centre:
            number = self.layout(self.centre, scale=2.2, bold=True)
            _w, number_height = number.get_pixel_size()
            caption = self.layout(self.caption) if self.caption else None
            caption_height = caption.get_pixel_size()[1] if caption else 0
            y = cy - (number_height + caption_height) / 2
            self.draw_text(snapshot, number, cx, y, align='center')
            if caption:
                self.draw_text(snapshot, caption, cx, y + number_height, self.dim(),
                               align='center')

    GAP_PX = 2.5

    @staticmethod
    def _ring(snapshot, cx, cy, outer, inner, rgba):
        builder = Gsk.PathBuilder.new()
        builder.add_circle(Graphene.Point().init(cx, cy), outer)
        builder.add_circle(Graphene.Point().init(cx, cy), inner)
        snapshot.append_fill(builder.to_path(), Gsk.FillRule.EVEN_ODD, rgba)

    @staticmethod
    def _segment(snapshot, cx, cy, outer, inner, start, end, rgba):
        """A ring segment from angle `start` to `end` (radians, clockwise from the top)."""
        def point(radius, angle):
            return cx + radius * math.sin(angle), cy - radius * math.cos(angle)

        large = end - start > math.pi
        builder = Gsk.PathBuilder.new()
        builder.move_to(*point(outer, start))
        builder.svg_arc_to(outer, outer, 0, large, True, *point(outer, end))
        builder.line_to(*point(inner, end))
        builder.svg_arc_to(inner, inner, 0, large, False, *point(inner, start))
        builder.close()
        snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING, rgba)


# -- small ones -------------------------------------------------------------------------

class SplitBar(Chart):
    """One horizontal bar split into coloured shares (the answer buttons)."""

    __gtype_name__ = 'RetainSplitBar'

    HEIGHT = 10
    NATURAL_HEIGHT = HEIGHT
    GAP = 1

    def __init__(self, **kwargs):
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER, **kwargs)
        self.shares = []

    def set_data(self, shares):
        self.shares = [(value, name) for value, name in shares if value > 0]
        self.queue_draw()

    def draw(self, snapshot, width, height):
        radius = height / 2
        total = sum(value for value, _name in self.shares)
        snapshot.push_rounded_clip(_rounded(_rect(0, 0, width, height), radius))
        if total <= 0:
            snapshot.append_color(with_alpha(self.foreground(), ZERO_ALPHA),
                                  _rect(0, 0, width, height))
        else:
            x = 0.0
            for index, (value, name) in enumerate(self.shares):
                share = width * value / total
                inset = self.GAP if index else 0
                snapshot.append_color(self.colour(name),
                                      _rect(x + inset, 0, share - inset, height))
                x += share
        snapshot.pop()


class Dot(Chart):
    """A legend's coloured dot (decorative: the label beside it says what it means)."""

    __gtype_name__ = 'RetainDot'

    SIZE = 10
    MIN_WIDTH = SIZE
    NATURAL_WIDTH = SIZE
    NATURAL_HEIGHT = SIZE
    ROLE = Gtk.AccessibleRole.PRESENTATION

    colour_name = GObject.Property(type=str, default='accent')

    def __init__(self, colour_name='accent', **kwargs):
        super().__init__(valign=Gtk.Align.CENTER, **kwargs)
        self.colour_name = colour_name
        self.connect('notify::colour-name', lambda *_args: self.queue_draw())

    def do_measure(self, orientation, for_size):
        return self.SIZE, self.SIZE, -1, -1

    def draw(self, snapshot, width, height):
        size = min(width, height)
        self.fill(snapshot, (width - size) / 2, (height - size) / 2, size, size,
                  self.colour(self.colour_name), size / 2)
