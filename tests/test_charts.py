# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""widgets/charts.py: the pure helpers, and each chart measured and drawn in a window."""

import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import pump, requires_gtk, wait_for


@requires_gtk
class HelperTest(unittest.TestCase):
    """The streaks, the colour levels and the axis ticks."""

    @classmethod
    def setUpClass(cls):
        from retain.widgets import charts

        cls.charts = charts

    def test_current_streak_ends_today_or_yesterday(self):
        counts = {8: 1, 9: 2, 10: 1}
        self.assertEqual(self.charts.current_streak(counts, 10), 3)
        self.assertEqual(self.charts.current_streak(counts, 11), 3)
        self.assertEqual(self.charts.current_streak(counts, 12), 0)
        self.assertEqual(self.charts.current_streak({}, 10), 0)
        self.assertEqual(self.charts.current_streak({10: 0, 9: 4}, 10), 1)

    def test_longest_streak_is_the_longest_run(self):
        self.assertEqual(self.charts.longest_streak({1: 1, 2: 1, 3: 1, 5: 1, 6: 1}), 3)
        self.assertEqual(self.charts.longest_streak({}), 0)
        self.assertEqual(self.charts.longest_streak({4: 0, 5: 1, 6: 0}), 1)

    def test_quantile_steps_and_levels(self):
        self.assertEqual(self.charts.quantile_steps([]), [])
        self.assertEqual(self.charts.quantile_steps([5, 0]), [5, 5, 5, 5])
        thresholds = self.charts.quantile_steps(range(1, 101))
        self.assertEqual(len(thresholds), 4)
        self.assertEqual(thresholds, sorted(thresholds))
        self.assertEqual(self.charts.level_of(0, thresholds), 0)
        self.assertEqual(self.charts.level_of(1, thresholds), 1)
        self.assertEqual(self.charts.level_of(50, thresholds), 3)
        self.assertEqual(self.charts.level_of(100, thresholds), 5)
        self.assertEqual(self.charts.level_of(3, []), 1)

    def test_nice_ticks_round_the_axis(self):
        self.assertEqual(self.charts.nice_ticks(0), (1, 1))
        self.assertEqual(self.charts.nice_ticks(37), (10, 40))
        self.assertEqual(self.charts.nice_ticks(7), (2, 8))
        self.assertEqual(self.charts.nice_ticks(100, target=2), (50, 100))
        for maximum in (1, 3, 12, 99, 101, 999, 4321):
            step, top = self.charts.nice_ticks(maximum)
            self.assertGreaterEqual(top, maximum)
            self.assertLessEqual(top / step, 5)

    def test_colour_names_and_tints(self):
        accent = self.charts.colour('accent')
        self.assertEqual(accent.alpha, 1.0)
        tinted = self.charts.colour('success:0.5')
        self.assertAlmostEqual(tinted.alpha, 0.5)
        self.assertAlmostEqual(self.charts.colour('neutral').alpha, 0.25)


@requires_gtk
class ChartTest(unittest.TestCase):
    """Each chart takes data, measures a sane size and draws without raising."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        from retain.widgets import charts

        cls.Gtk = Gtk
        cls.charts = charts

    def show(self, widget, width=400):
        """Put a chart in a window and wait until it has a size."""
        window = self.Gtk.Window(default_width=width, default_height=300)
        window.set_child(widget)
        window.present()
        self.addCleanup(window.destroy)
        self.assertTrue(wait_for(lambda: widget.get_width() > 0))
        return window

    def draw(self, widget, expect_node=True):
        """Draw the chart through its own do_snapshot (a WidgetPaintable would only
        show the last frame) and check that it drew something, unless told not to."""
        snapshot = self.Gtk.Snapshot()
        widget.do_snapshot(snapshot)
        node = snapshot.to_node()
        if expect_node:
            self.assertIsNotNone(node)
        return node

    def test_heatmap_measures_cells_and_draws(self):
        heatmap = self.charts.Heatmap()
        today = 739000
        heatmap.set_data({today: 3, today - 1: 1, today - 40: 9}, today)
        minimum, natural = heatmap.measure(self.Gtk.Orientation.HORIZONTAL, -1)[:2]
        self.assertGreater(natural, minimum)
        self.assertGreaterEqual(minimum, 53 * (heatmap.MIN_CELL + heatmap.GAP))
        height = heatmap.measure(self.Gtk.Orientation.VERTICAL, minimum)[1]
        self.assertGreaterEqual(height, 7 * (heatmap.MIN_CELL + heatmap.GAP))
        self.show(heatmap, width=900)
        self.draw(heatmap)
        # The cell under today's square is today; outside the grid there is none.
        left, top = heatmap._gutters()
        cell = heatmap._cell_for_width(heatmap.get_width())
        step = cell + heatmap.GAP
        column = heatmap.columns() - 1
        row = (today - heatmap.start()) % 7
        x = left + column * step + cell / 2
        y = top + row * step + cell / 2
        self.assertEqual(heatmap._cell_at(x, y), today)
        self.assertIsNone(heatmap._cell_at(left + (column + 2) * step, y))
        self.assertIsNone(heatmap._cell_at(0, 0))

    def test_bar_chart_stacks_and_lines(self):
        chart = self.charts.BarChart()
        chart.set_data([[3, 1], [0, 0], [5, 2]], ['accent', 'warning'], labels=['a', None, 'c'],
                       line=[1, None, 10], line_max=10, tooltips=['A', 'B', 'C'])
        self.assertEqual(chart.maximum(), 7)
        minimum, natural = chart.measure(self.Gtk.Orientation.VERTICAL, 300)[:2]
        self.assertEqual((minimum, natural), (chart.NATURAL_HEIGHT, chart.NATURAL_HEIGHT))
        self.show(chart)
        self.draw(chart)
        chart.set_data([], [])
        self.assertIsNone(self.draw(chart, expect_node=False))

    def test_donut_split_bar_and_dot(self):
        donut = self.charts.Donut()
        donut.set_data([(3, 'accent'), (0, 'warning'), (5, 'success')], centre='8',
                       caption='cards')
        self.assertEqual(len(donut.segments), 2)
        self.assertEqual(donut.measure(self.Gtk.Orientation.HORIZONTAL, -1)[:2],
                         (donut.SIZE, donut.SIZE))
        self.show(donut)
        self.draw(donut)
        donut.set_data([(1, 'accent')])  # a single share: the whole ring
        self.draw(donut)
        donut.set_data([])
        self.draw(donut)
        bar = self.charts.SplitBar()
        bar.set_data([(1, 'error'), (2, 'warning'), (7, 'success'), (0, 'accent')])
        self.show(bar)
        self.draw(bar)
        dot = self.charts.Dot('warning')
        self.assertEqual(dot.colour_name, 'warning')
        self.show(dot)
        self.draw(dot)

    def test_description_is_kept_and_style_changes_redraw(self):
        from gi.repository import Adw

        chart = self.charts.BarChart()
        chart.set_description('Reviews per day: 3 on average')
        self.assertEqual(chart.description, 'Reviews per day: 3 on average')
        self.show(chart)
        self.assertEqual(len(chart._style_handlers), 2)
        manager = Adw.StyleManager.get_default()
        manager.notify('dark')
        pump()
        chart.unparent()
        self.assertEqual(chart._style_handlers, [])
