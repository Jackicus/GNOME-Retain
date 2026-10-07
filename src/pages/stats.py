# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Statistics page: stats.py's numbers for one deck or all of them, as charts.

    page = StatsPage(collection=None)   # the app's collection unless one is given (tests)
    page.deck_id                        # the deck chosen in the header bar, None for all
    page.range_days()                   # the period of the charts over time
    page.refresh()                      # query the collection and redraw everything

The chosen deck is kept in the collection's config as 'stats_deck'; the range (1 month, 3
months, 1 year, all time) is the page's own. The page refreshes when it is mapped, when the
deck or the range changes, and, debounced by REFRESH_DELAY_MS, when the collection reports
a change to anything but the config (the deck choice itself is a config change). The
queries (stats.py's) are quick enough for the main thread.
"""

import logging
import math
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from ..stats import Stats, cumulative
from ..widgets import charts
from ..widgets.charts import BarChart, Donut, Dot, Heatmap, SplitBar  # noqa: F401 (template)
from . import app

log = logging.getLogger(__name__)

REFRESH_DELAY_MS = 300
RANGES = {'month': 30, 'quarter': 90, 'year': 365, 'all': None}
HEATMAP_DAYS = 365
FORECAST_LIMIT = 3 * 365  # how far "all" looks ahead
WEEKLY_FROM = 60  # days: a longer range is grouped by week
MONTHLY_FROM = 730  # days: a longer range is grouped by thirty days
STATES = (('new', 'accent', _('New')), ('learning', 'warning', _('Learning')),
          ('review', 'success', _('Review')), ('relearning', 'error', _('Relearning')))
RATINGS = ((1, 'error', _('Again')), (2, 'warning', _('Hard')), (3, 'success', _('Good')),
           (4, 'accent', _('Easy')))
KINDS = (('new', 'accent', _('New')), ('learning', 'warning', _('Learning')),
         ('young', 'success:0.55', _('Young')), ('mature', 'success', _('Mature')),
         ('suspended', 'neutral:0.45', _('Suspended')), ('buried', 'neutral:0.25', _('Buried')))
INTERVAL_LABELS = {
    (0, 1): ('<1d', _('Less than a day')),
    (1, 7): ('1–7d', _('1 to 7 days')),
    (7, 30): ('1–4w', _('1 to 4 weeks')),
    (30, 90): ('1–3mo', _('1 to 3 months')),
    (90, 365): ('3–12mo', _('3 to 12 months')),
    (365, None): ('1y+', _('A year or more')),
}
INDENT = ' '  # an em space per deck level, as the sidebar


class DeckItem(GObject.Object):
    """An entry of the deck chooser: a deck, or every deck (deck_id None)."""

    def __init__(self, name, level=0, deck_id=None):
        super().__init__()
        self.name = name
        self.level = level
        self.deck_id = deck_id


class Tile(Gtk.Box):
    """A boxed number with a caption under it."""

    def __init__(self, caption='', value='', **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3, **kwargs)
        self.add_css_class('card')
        self.add_css_class('stats-tile')
        self.value = Gtk.Label(label=value, xalign=0)
        self.value.add_css_class('title-2')
        self.value.add_css_class('numeric')
        self.caption = Gtk.Label(label=caption, xalign=0, wrap=True)
        self.caption.add_css_class('caption')
        self.caption.add_css_class('dimmed')
        self.append(self.value)
        self.append(self.caption)
        self.update_property([Gtk.AccessibleProperty.LABEL], [caption])

    def set(self, value, caption=None):
        self.value.set_text(str(value))
        if caption is not None:
            self.caption.set_text(caption)
            self.update_property([Gtk.AccessibleProperty.LABEL], [caption])


@Gtk.Template(resource_path='/io/github/jackicus/Retain/stats.ui')
class StatsPage(Adw.NavigationPage):
    __gtype_name__ = 'RetainStatsPage'

    narrow = Gtk.Template.Child()
    header_bar = Gtk.Template.Child()
    bottom_bar = Gtk.Template.Child()
    deck_chooser = Gtk.Template.Child()
    range_group = Gtk.Template.Child()
    scrolled = Gtk.Template.Child()
    tiles = Gtk.Template.Child()
    today_caption = Gtk.Template.Child()
    heatmap = Gtk.Template.Child()
    heatmap_caption = Gtk.Template.Child()
    due_legend = Gtk.Template.Child()
    due_chart = Gtk.Template.Child()
    due_caption = Gtk.Template.Child()
    reviews_legend = Gtk.Template.Child()
    reviews_chart = Gtk.Template.Child()
    reviews_caption = Gtk.Template.Child()
    donut = Gtk.Template.Child()
    counts_legend = Gtk.Template.Child()
    retention_value = Gtk.Template.Child()
    retention_caption = Gtk.Template.Child()
    memorized_caption = Gtk.Template.Child()
    buttons_legend = Gtk.Template.Child()
    buttons_grid = Gtk.Template.Child()
    intervals_chart = Gtk.Template.Child()
    intervals_caption = Gtk.Template.Child()
    difficulty_chart = Gtk.Template.Child()
    difficulty_caption = Gtk.Template.Child()
    hourly_legend = Gtk.Template.Child()
    hourly_chart = Gtk.Template.Child()
    hourly_caption = Gtk.Template.Child()
    totals = Gtk.Template.Child()

    def __init__(self, collection=None):
        super().__init__()
        Gtk.Widget.set_focusable(self, False)
        self.collection = collection if collection is not None else app().collection
        self.deck_id = None
        self.last_stats = None  # the Stats of the last refresh
        self._changed_handler = None
        self._refresh_source = None
        self._filling_decks = False
        self._narrow = False
        self._group_at_bottom = False  # where the range toggles are (the header bar, else)
        self._build_static()
        self.deck_chooser.set_factory(_deck_factory(indent=False))
        self.deck_chooser.set_list_factory(_deck_factory(indent=True))
        self._fill_decks()
        self.deck_chooser.connect('notify::selected', self._on_deck_selected)
        self.range_group.connect('notify::active-name', lambda *_args: self.refresh())
        self.narrow.connect('apply', self._on_narrow, True)
        self.narrow.connect('unapply', self._on_narrow, False)

    def _on_narrow(self, _breakpoint, narrow):
        """The range toggles move to the bottom bar while the page is narrow: from an
        idle, since the breakpoint applies during the layout and a move there leaves the
        frame with the old picture."""
        self._narrow = narrow
        GLib.idle_add(self._place_range_group)

    def _place_range_group(self):
        group = self.range_group
        if self._narrow and not self._group_at_bottom:
            self.header_bar.remove(group)
            group.set_hexpand(True)
            group.set_homogeneous(False)
            self.bottom_bar.append(group)
        elif not self._narrow and self._group_at_bottom:
            self.bottom_bar.remove(group)
            group.set_hexpand(False)
            group.set_homogeneous(True)
            self.header_bar.pack_end(group)
        self._group_at_bottom = self._narrow
        return GLib.SOURCE_REMOVE

    # -- the fixed parts ---------------------------------------------------------------------

    def _build_static(self):
        self.tile_studied = Tile(_('Cards Studied'))
        self.tile_minutes = Tile(_('Minutes'))
        self.tile_correct = Tile(_('Correct'))
        self.tile_new = Tile(_('New Cards'))
        self.tile_streak = Tile(_('Day Streak'))
        for tile in (self.tile_studied, self.tile_minutes, self.tile_correct, self.tile_new,
                     self.tile_streak):
            self.tiles.append(tile)
        self.total_tiles = {}
        for key, caption in (('cards', _('Cards')), ('notes', _('Notes')),
                             ('reviews', _('Reviews')), ('days_studied', _('Days Studied')),
                             ('average_per_day', _('Reviews per Day')),
                             ('minutes_total', _('Time Spent'))):
            tile = Tile(caption)
            self.total_tiles[key] = tile
            self.totals.append(tile)
        _legend(self.due_legend, (('accent', _('Due')), ('success', _('Running total'))))
        _legend(self.reviews_legend, [(colour, name) for _key, colour, name in STATES])
        _legend(self.buttons_legend, [(colour, name) for _rating, colour, name in RATINGS])
        _legend(self.hourly_legend, (('accent', _('Reviews')), ('success', _('Correct'))))
        self.button_bars = {}
        for row, (group, name) in enumerate((('learning', _('Learning')), ('young', _('Young')),
                                             ('mature', _('Mature')))):
            label = Gtk.Label(label=name, xalign=0, width_chars=8)
            label.add_css_class('caption')
            bar = SplitBar()
            bar.set_has_tooltip(True)
            count = Gtk.Label(xalign=1, width_chars=6)
            count.add_css_class('caption')
            count.add_css_class('dimmed')
            count.add_css_class('numeric')
            self.buttons_grid.attach(label, 0, row, 1, 1)
            self.buttons_grid.attach(bar, 1, row, 1, 1)
            self.buttons_grid.attach(count, 2, row, 1, 1)
            self.button_bars[group] = (bar, count)
        self.count_rows = {}
        for key, colour, name in KINDS:
            row = Gtk.Box(spacing=9)
            row.append(Dot(colour))
            label = Gtk.Label(label=name, xalign=0, hexpand=True)
            row.append(label)
            count = Gtk.Label(xalign=1)
            count.add_css_class('numeric')
            count.add_css_class('dimmed')
            row.append(count)
            self.counts_legend.append(row)
            self.count_rows[key] = count

    # -- the deck chooser ----------------------------------------------------------------------

    def _fill_decks(self):
        """The chooser's entries: every deck, with the one 'stats_deck' names selected."""
        self._filling_decks = True
        try:
            store = Gio.ListStore(item_type=DeckItem)
            store.append(DeckItem(_('All Decks')))
            wanted = self.collection.get('stats_deck')
            selected = 0
            for deck in self.collection.decks():
                store.append(DeckItem(deck.basename, deck.level, deck.id))
                if deck.id == wanted:
                    selected = store.get_n_items() - 1
            self.deck_chooser.set_model(store)
            self.deck_chooser.set_selected(selected)
            self.deck_id = store.get_item(selected).deck_id
        finally:
            self._filling_decks = False

    def _on_deck_selected(self, *_args):
        if self._filling_decks:
            return
        item = self.deck_chooser.get_selected_item()
        self.deck_id = item.deck_id if item is not None else None
        if self.collection.get('stats_deck') != self.deck_id:
            self.collection.set('stats_deck', self.deck_id)
        self.refresh()

    # -- following the collection -------------------------------------------------------------

    def do_map(self):
        Adw.NavigationPage.do_map(self)
        if self._changed_handler is None:
            self._changed_handler = self.collection.connect('changed',
                                                            self._on_collection_changed)
        self._refresh_safely()

    def do_unmap(self):
        if self._changed_handler is not None:
            self.collection.disconnect(self._changed_handler)
            self._changed_handler = None
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = None
        Adw.NavigationPage.do_unmap(self)

    def _on_collection_changed(self, _collection, kind):
        if kind == 'config':
            return
        if kind == 'decks':
            self._fill_decks()
        if self._refresh_source is None:
            self._refresh_source = GLib.timeout_add(REFRESH_DELAY_MS, self._refresh_later)

    def _refresh_later(self):
        self._refresh_source = None
        self._refresh_safely()
        return GLib.SOURCE_REMOVE

    def _refresh_safely(self):
        try:
            self.refresh()
        except Exception:
            log.exception('refreshing the statistics')

    # -- the data ------------------------------------------------------------------------------

    def range_days(self):
        """The period of the charts over time, in days (None: all time)."""
        return RANGES.get(self.range_group.get_active_name(), 30)

    def _range_words(self):
        return {'month': _('the last month'), 'quarter': _('the last 3 months'),
                'year': _('the last year'), 'all': _('all time')}.get(
                    self.range_group.get_active_name(), _('the last month'))

    def refresh(self):
        """Query the collection and fill every section."""
        stats = Stats(self.collection, self.deck_id)
        self.last_stats = stats
        totals = stats.totals()
        past_days = self.range_days() or max(30, totals['days_total'])
        self._show_today(stats.today_summary())
        self._show_heatmap(stats.review_heatmap(HEATMAP_DAYS), stats.today)
        self._show_due(stats, stats.today)
        self._show_reviews(stats.reviews_per_day(past_days), past_days)
        self._show_counts(stats.card_counts())
        self._show_retention(stats.retention(past_days), stats.memorized(),
                             stats.answer_buttons(past_days))
        self._show_histograms(stats.interval_histogram(), stats.difficulty_histogram())
        self._show_hourly(stats.hourly_breakdown())
        self._show_totals(totals)

    # 1. Today

    def _show_today(self, summary):
        self.tile_studied.set(_number(summary['reviews']))
        self.tile_minutes.set(_number(round(summary['minutes'])))
        self.tile_correct.set(_percent(summary['correct_percent']))
        self.tile_new.set(_number(summary['new_introduced']))
        self.tile_streak.set(_number(summary['streak_days']))
        self.today_caption.set_text(
            _('Again {again} · Hard {hard} · Good {good} · Easy {easy} · {relearned} '
              'relearned').format(again=_number(summary['again']),
                                  hard=_number(summary['hard']),
                                  good=_number(summary['good']),
                                  easy=_number(summary['easy']),
                                  relearned=_number(summary['relearned'])))

    # 2. The heatmap

    def _show_heatmap(self, counts, today):
        self.heatmap.set_data(counts, today)
        studied = sum(1 for count in counts.values() if count > 0)
        longest = charts.longest_streak(counts)
        current = charts.current_streak(counts, today)
        self.heatmap_caption.set_text(
            _('{studied} studied · longest streak {longest} · current streak {current}')
            .format(studied=_days(studied), longest=_days(longest), current=_days(current)))
        total = sum(counts.values())
        description = _('Reviews over the last year: {total} in all on {studied}').format(
            total=_number(total), studied=_days(studied))
        if counts:
            best = max(counts, key=counts.get)
            description += _(', most on {date} ({n})').format(
                date=charts.date_label(best, '%e %B'), n=_number(counts[best]))
        self.heatmap.set_description(description)

    # 3. Due ahead

    def _show_due(self, stats, today):
        ahead = self.range_days()
        if ahead is None:
            forecast = stats.due_forecast(FORECAST_LIMIT)
            last = max((offset for offset, count in forecast if count), default=0)
            forecast = forecast[:max(30, last) + 1]
        else:
            forecast = stats.due_forecast(ahead)
        group = _group_size(len(forecast))
        grouped = _group(forecast, group)
        total = sum(count for _offset, count in forecast)
        running = [count for _offset, count in cumulative(grouped)]
        labels = []
        tooltips = []
        every = _label_every(len(grouped), group)
        for index, (offset, count) in enumerate(grouped):
            day = today + offset
            if group == 1:
                text = _('Today') if offset == 0 else charts.date_label(day)
                tooltips.append(ngettext('{date}: {n} card due', '{date}: {n} cards due', count)
                                .format(date=_('Today') if offset == 0 else
                                        charts.date_label(day, '%e %B'), n=_number(count)))
            else:
                text = charts.date_label(day)
                tooltips.append(ngettext('From {date}: {n} card due', 'From {date}: {n} cards due',
                                         count).format(date=charts.date_label(day, '%e %B'),
                                                       n=_number(count)))
            labels.append(text if index % every == 0 else None)
        self.due_chart.set_data([[count] for _offset, count in grouped], ['accent'],
                                labels=labels, line=running, line_colour='success',
                                tooltips=tooltips)
        days_ahead = len(forecast)
        tomorrow = forecast[1][1] if len(forecast) > 1 else 0
        average = total / days_ahead if days_ahead else 0
        self.due_caption.set_text(
            _('{today} due today · {tomorrow} due tomorrow · average {average}/day over '
              '{days}').format(today=_number(forecast[0][1]), tomorrow=_number(tomorrow),
                               average=_decimal(average), days=_days(days_ahead)))
        description = _('Cards due over the next {days}: {total} in all, {average} a day '
                        'on average').format(days=_days(days_ahead), total=_number(total),
                                             average=_decimal(average))
        if total:
            offset, count = max(forecast, key=lambda pair: pair[1])
            description += _(', most on {date} ({n})').format(
                date=charts.date_label(today + offset, '%e %B'), n=_number(count))
        self.due_chart.set_description(description)

    # 4. Reviews done

    def _show_reviews(self, per_day, past_days):
        group = _group_size(len(per_day))
        grouped = _group(per_day, group)
        bars = [[counts[key] for key, _colour, _name in STATES] for _day, counts in grouped]
        every = _label_every(len(grouped), group)
        labels = [charts.date_label(day) if index % every == 0 else None
                  for index, (day, _counts) in enumerate(grouped)]
        tooltips = []
        for day, counts in grouped:
            n = sum(counts.values())
            date = charts.date_label(day, '%e %B')
            if group == 1:
                tooltips.append(ngettext('{date}: {n} review', '{date}: {n} reviews', n)
                                .format(date=date, n=_number(n)))
            else:
                tooltips.append(ngettext('From {date}: {n} review', 'From {date}: {n} reviews',
                                         n).format(date=date, n=_number(n)))
        self.reviews_chart.set_data(bars, [colour for _key, colour, _name in STATES],
                                    labels=labels, tooltips=tooltips)
        total = sum(sum(counts.values()) for _day, counts in per_day)
        studied = sum(1 for _day, counts in per_day if sum(counts.values()))
        average = total / studied if studied else 0
        self.reviews_caption.set_text(
            _('{total} reviews over {period} · {studied} studied · average {average}/day '
              'studied').format(total=_number(total), period=self._range_words(),
                                studied=_days(studied), average=_decimal(average)))
        description = _('Reviews per day over {period}: {average} on average').format(
            period=self._range_words(), average=_decimal(average))
        if total:
            day, counts = max(per_day, key=lambda pair: sum(pair[1].values()))
            description += _(', most on {date} ({n})').format(
                date=charts.date_label(day, '%e %B'), n=_number(sum(counts.values())))
        self.reviews_chart.set_description(description)

    # 5. Card counts

    def _show_counts(self, counts):
        self.donut.set_data([(counts[key], colour) for key, colour, _name in KINDS],
                            centre=_number(counts['total']), caption=_('cards'))
        parts = []
        for key, _colour, name in KINDS:
            self.count_rows[key].set_text(_number(counts[key]))
            parts.append(f'{_number(counts[key])} {name.lower()}')
        self.donut.set_description(_('Card counts: {parts}, {total} in all').format(
            parts=', '.join(parts), total=_number(counts['total'])))

    # 6. Retention

    def _show_retention(self, retention, memorized, buttons):
        self.retention_value.set_text(_percent(retention['percent']))
        reviewed = retention['passed'] + retention['failed']
        self.retention_caption.set_text(
            _('Passed {passed} of {total} reviews over {period}').format(
                passed=_number(retention['passed']), total=_number(reviewed),
                period=self._range_words()))
        if memorized['total_reviewed']:
            self.memorized_caption.set_text(
                _('About {cards} of {reviewed} reviewed cards remembered now, {percent} '
                  'average retrievability').format(
                      cards=_number(round(memorized['cards'])),
                      reviewed=_number(memorized['total_reviewed']),
                      percent=_percent(memorized['average_retention'])))
        else:
            self.memorized_caption.set_text(_('No cards reviewed yet'))
        self.retention_value.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_('True retention over {period}').format(period=self._range_words())])
        for group, (bar, count) in self.button_bars.items():
            presses = buttons[group]
            bar.set_data([(presses[rating], colour) for rating, colour, _name in RATINGS])
            total = sum(presses.values())
            count.set_text(_number(total))
            split = ', '.join(f'{name} {_number(presses[rating])}'
                              for rating, _colour, name in RATINGS)
            text = _('{group} cards over {period}: {split}').format(
                group={'learning': _('Learning'), 'young': _('Young'),
                       'mature': _('Mature')}[group], period=self._range_words(), split=split)
            bar.set_tooltip_text(text)
            bar.set_description(text)

    # 7. Intervals and difficulty

    def _show_histograms(self, intervals, difficulty):
        labels = []
        tooltips = []
        parts = []
        for low, high, count in intervals:
            short, long = INTERVAL_LABELS.get((low, high), (_interval_text(low, high),) * 2)
            labels.append(short)
            tooltips.append(ngettext('{range}: {n} card', '{range}: {n} cards', count)
                            .format(range=long, n=_number(count)))
            parts.append(f'{long.lower()} {_number(count)}')
        self.intervals_chart.set_data([[count] for _low, _high, count in intervals],
                                      ['accent'], labels=labels, tooltips=tooltips)
        total = sum(count for _low, _high, count in intervals)
        self.intervals_caption.set_text(ngettext('{n} review card', '{n} review cards', total)
                                        .format(n=_number(total)))
        self.intervals_chart.set_description(
            _('Review cards by interval: {parts}').format(parts=', '.join(parts)))
        labels = [str(low) for low, _high, _count in difficulty]
        tooltips = [ngettext('Difficulty {low} to {high}: {n} card',
                             'Difficulty {low} to {high}: {n} cards', count)
                    .format(low=low, high=high, n=_number(count))
                    for low, high, count in difficulty]
        self.difficulty_chart.set_data([[count] for _low, _high, count in difficulty],
                                       ['warning'], labels=labels, tooltips=tooltips)
        total = sum(count for _low, _high, count in difficulty)
        self.difficulty_caption.set_text(
            ngettext('{n} card with a memory state, difficulty 1 (easy) to 10 (hard)',
                     '{n} cards with a memory state, difficulty 1 (easy) to 10 (hard)', total)
            .format(n=_number(total)))
        self.difficulty_chart.set_description(
            _('Cards by difficulty from 1 to 10: {parts}').format(
                parts=', '.join(f'{low}: {_number(count)}' for low, _high, count in difficulty)))

    # 8. The hour of the day

    def _show_hourly(self, hourly):
        labels = [_hour_label(hour) if hour % 4 == 0 else None for hour, _n, _p in hourly]
        tooltips = []
        for hour, n, percent in hourly:
            text = ngettext('{hour}: {n} review', '{hour}: {n} reviews', n).format(
                hour=_hour_label(hour), n=_number(n))
            if percent is not None:
                text += _(', {percent} correct').format(percent=_percent(percent))
            tooltips.append(text)
        self.hourly_chart.set_data([[n] for _hour, n, _p in hourly], ['accent'], labels=labels,
                                   line=[percent for _hour, _n, percent in hourly],
                                   line_colour='success', line_max=100,
                                   line_format=lambda value: f'{value:.0f}%',
                                   tooltips=tooltips)
        total = sum(n for _hour, n, _p in hourly)
        if total:
            hour, n, percent = max(hourly, key=lambda row: row[1])
            correct = sum(n * (percent or 0) for _hour, n, percent in hourly) / total
            text = _('Most reviews at {hour} ({n}) · {percent} correct over all hours').format(
                hour=_hour_label(hour), n=_number(n), percent=_percent(correct))
        else:
            text = _('No reviews yet')
        self.hourly_caption.set_text(text)
        self.hourly_chart.set_description(_('Reviews by hour of the day: {text}').format(
            text=text.replace(' · ', ', ')))

    # 9. Totals

    def _show_totals(self, totals):
        self.total_tiles['cards'].set(_number(totals['cards']))
        self.total_tiles['notes'].set(_number(totals['notes']))
        self.total_tiles['reviews'].set(_number(totals['reviews']))
        self.total_tiles['days_studied'].set(
            _('{studied} of {total}').format(studied=_number(totals['days_studied']),
                                             total=_number(totals['days_total'])))
        self.total_tiles['average_per_day'].set(_decimal(totals['average_per_day']))
        self.total_tiles['minutes_total'].set(_duration(totals['minutes_total']))


# -- helpers --------------------------------------------------------------------------------

def _deck_factory(indent):
    """A list item factory showing a DeckItem's name, indented by its level in the list."""
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item):
        label = Gtk.Label(xalign=0)
        if not indent:
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(18)
        item.set_child(label)

    def bind(_factory, item):
        deck = item.get_item()
        item.get_child().set_text((INDENT * deck.level if indent else '') + deck.name)

    factory.connect('setup', setup)
    factory.connect('bind', bind)
    return factory


def _legend(box, entries):
    """Fill a box with coloured dots and their names."""
    while (child := box.get_first_child()) is not None:
        box.remove(child)
    for colour, name in entries:
        entry = Gtk.Box(spacing=6)
        entry.append(Dot(colour))
        label = Gtk.Label(label=name)
        label.add_css_class('caption')
        label.add_css_class('dimmed')
        entry.append(label)
        box.append(entry)


def _group_size(count):
    """Days per bar for a range of `count` days: 1, 7 or 30."""
    if count > MONTHLY_FROM:
        return 30
    if count > WEEKLY_FROM:
        return 7
    return 1


def _group(pairs, size):
    """[(key, value)] summed in runs of `size`, keyed by each run's first key; a value
    is a number or a dict of numbers."""
    if size <= 1:
        return list(pairs)
    result = []
    for index in range(0, len(pairs), size):
        run = pairs[index:index + size]
        first = run[0][1]
        if isinstance(first, dict):
            total = {key: sum(value[key] for _k, value in run) for key in first}
        else:
            total = sum(value for _k, value in run)
        result.append((run[0][0], total))
    return result


def _label_every(count, group):
    """A label every this many bars: weekly for days, else about six labels."""
    if group == 1:
        return 7 if count > 14 else max(1, math.ceil(count / 7))
    return max(1, math.ceil(count / 6))


def _number(value):
    return f'{int(value):,}'


def _decimal(value):
    return f'{value:,.1f}' if value < 100 else _number(round(value))


def _percent(value):
    return '—' if value is None else f'{round(value)}%'


def _days(count):
    return ngettext('{n} day', '{n} days', count).format(n=_number(count))


def _duration(minutes):
    if minutes < 60:
        return ngettext('{n} min', '{n} min', round(minutes)).format(n=_number(round(minutes)))
    return _('{n} h').format(n=_decimal(minutes / 60))


def _interval_text(low, high):
    if high is None:
        return _('{low} days or more').format(low=low)
    return _('{low} to {high} days').format(low=low, high=high)


def _hour_label(hour):
    return GLib.DateTime.new_local(2000, 1, 1, hour, 0, 0).format('%H:%M')

