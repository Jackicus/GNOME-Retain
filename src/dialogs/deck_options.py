# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Deck Options: the preset a deck uses, as a preferences dialog much shorter than Anki's.

    dialog = present(app, parent, deck_id)
    dialog.save()                       # what the close does: one undo step when changed
    parse_steps('1m 10m 1h'), format_steps([1, 10, 60])
    parse_parameters('0.21, 1.29, …'), format_parameters(params)
    reviews_estimate(retention, params)  # the change in reviews against 90%, in percent
    workload_text(simulation), easy_days_summary(values, balancing)

The Preset row lists the collection's presets (deck_config.py's DeckConfig rows); choosing
one assigns it to the deck at once (collection.set_deck_config), and its menu adds (a copy
of the shown one), renames or deletes a preset through deck_name.NameDialog. Everything else
edits the shown preset: the daily limits, the workload (Load Balancing, and Easy Days: a
row per weekday, Normal, Reduced or Minimum, which need the balancer), the study mode (flip,
type or choose; answers.py), the desired retention (a scale with a plain sentence and an
estimate of the reviews a day it costs: workload.simulate() over the preset's cards for a
year, in a thread, restarted a moment after the settings it reads change; the forgetting
curve's ratio against 90% when the decks have no cards), Optimize Parameters (optimizer.suggest in a
thread over the revlog of every deck on the preset, cancelled by closing the dialog, stored
on success) and, under Advanced, the steps, ordering, burying, leeches, the maximum interval
and the FSRS parameters themselves. An entry that cannot be parsed turns red and is left out
of the save. The edits are saved when the dialog closes, as one undo step, with a toast.
"""

import logging
import re
import threading
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gio, Gtk

from .. import fsrs, optimizer, workload
from ..collection import CollectionError
from ..deck_config import DEFAULT_ID
from ..widgets.util import connect_weak
from .deck_name import NameDialog, watch_dialog

log = logging.getLogger(__name__)

STEP = re.compile(r'^(\d+(?:\.\d+)?)\s*([mhd]?)$', re.IGNORECASE)
UNITS = {'': 1, 'm': 1, 'h': 60, 'd': 1440}
NEW_ORDERS = ('added', 'random')
NEW_MIXES = ('mix', 'after', 'before')
LEECH_ACTIONS = ('tag', 'suspend')
STUDY_MODES = ('flip', 'type', 'choice')
REFERENCE_RETENTION = 0.9
EASY_DAY_CHOICES = (workload.NORMAL, workload.REDUCED, workload.MINIMUM)
ESTIMATE_DELAY_MS = 250  # after the last change, before the workload is simulated again


# -- parsing and text ------------------------------------------------------------------------

def parse_steps(text):
    """Minutes from '1m 10m 1h 2d' (bare numbers are minutes; commas allowed). ValueError
    for anything else."""
    steps = []
    for token in text.replace(',', ' ').split():
        match = STEP.match(token)
        if match is None:
            raise ValueError(token)
        value = float(match.group(1)) * UNITS[match.group(2).lower()]
        if value <= 0:
            raise ValueError(token)
        steps.append(int(value) if value == int(value) else value)
    return steps


def format_steps(steps):
    parts = []
    for minutes in steps:
        if minutes >= 1440 and minutes % 1440 == 0:
            parts.append(f'{int(minutes // 1440)}d')
        elif minutes >= 60 and minutes % 60 == 0:
            parts.append(f'{int(minutes // 60)}h')
        else:
            parts.append(f'{minutes:g}m')
    return ' '.join(parts)


def parse_parameters(text):
    """The 21 FSRS parameters from comma- or space-separated numbers; None when blank;
    ValueError when they are not a parameter set."""
    tokens = text.replace(',', ' ').split()
    if not tokens:
        return None
    return fsrs.normalize_parameters(tokens)


def format_parameters(params):
    if not params:
        return ''
    return ', '.join(f'{value:.4f}'.rstrip('0').rstrip('.') for value in params)


def reviews_estimate(retention, params):
    """How many more (positive) or fewer (negative) reviews, in percent, a retention costs
    against 90%, by the forgetting curve: an interval scales with r**(1/decay) - 1, and the
    reviews with its inverse."""
    decay = fsrs.decay(params)

    def interval(r):
        return r ** (1 / decay) - 1

    return (interval(REFERENCE_RETENTION) / interval(retention) - 1) * 100


def estimate_text(retention, params):
    percent = round(reviews_estimate(retention, params))
    if abs(percent) < 2:
        return _('About as many reviews as at 90%')
    if percent > 0:
        return _('About {percent}% more reviews than at 90%').format(percent=percent)
    return _('About {percent}% fewer reviews than at 90%').format(percent=-percent)


def weekday_names():
    return (_('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'),
            _('Saturday'), _('Sunday'))


def easy_days_summary(values, balancing=True):
    """The Easy Days row's subtitle: which weekdays get fewer reviews."""
    if not balancing:
        return _('Needs load balancing')
    lighter = [name for name, value in zip(weekday_names(), values, strict=True)
               if value != workload.NORMAL]
    if not lighter:
        return _('The same load every day')
    return _('Fewer reviews on {days}').format(days=', '.join(lighter))


def workload_text(simulation):
    """A simulation as one sentence: reviews a day over the year, and what they buy."""
    average = sum(simulation.reviews) / max(1, len(simulation.reviews))
    # Whole reviews from ten a day; under that one decimal, so a small deck shows a change.
    if average >= 10:
        reviews = f'{round(average):,}'
    else:
        reviews = f'{average:.1f}'.removesuffix('.0')
    return ngettext(
        'About {reviews} review a day over the next year, with {remembered} of {cards} '
        'cards remembered at its end',
        'About {reviews} reviews a day over the next year, with {remembered} of {cards} '
        'cards remembered at its end', 1 if reviews == '1' else 2).format(
            reviews=reviews, remembered=f'{round(simulation.memorized):,}',
            cards=f'{round(simulation.cards):,}')


def retention_sentence(retention):
    return _('Aim to remember {percent}% of cards when they come up. '
             'Higher means more reviews.').format(percent=round(retention * 100))


# -- the dialog ------------------------------------------------------------------------------

@Gtk.Template(resource_path='/io/github/jackicus/Retain/deck_options.ui')
class DeckOptionsDialog(Adw.PreferencesDialog):
    __gtype_name__ = 'RetainDeckOptionsDialog'

    preset_row = Gtk.Template.Child()
    preset_menu_button = Gtk.Template.Child()
    new_per_day_row = Gtk.Template.Child()
    reviews_per_day_row = Gtk.Template.Child()
    study_mode_row = Gtk.Template.Child()
    retention_value = Gtk.Template.Child()
    retention_scale = Gtk.Template.Child()
    retention_adjustment = Gtk.Template.Child()
    retention_subtitle = Gtk.Template.Child()
    retention_estimate = Gtk.Template.Child()
    load_balancing_row = Gtk.Template.Child()
    easy_days_row = Gtk.Template.Child()
    optimize_row = Gtk.Template.Child()
    optimize_stack = Gtk.Template.Child()
    optimize_button = Gtk.Template.Child()
    optimize_progress = Gtk.Template.Child()
    advanced_row = Gtk.Template.Child()
    learning_steps_row = Gtk.Template.Child()
    relearning_steps_row = Gtk.Template.Child()
    new_order_row = Gtk.Template.Child()
    new_mix_row = Gtk.Template.Child()
    bury_row = Gtk.Template.Child()
    leech_threshold_row = Gtk.Template.Child()
    leech_action_row = Gtk.Template.Child()
    maximum_interval_row = Gtk.Template.Child()
    parameters_row = Gtk.Template.Child()
    parameters_reset_button = Gtk.Template.Child()

    def __init__(self, app, deck_id):
        super().__init__()
        self.app = app
        self.collection = app.collection
        self.deck_id = deck_id
        self.config = None  # the preset shown, as stored
        self._configs = []  # the presets, in the combo's order
        self._loading = False
        self._stop = threading.Event()  # set when the dialog closes: the optimizer stops
        self._optimizing = False
        self._review_count = 0
        self._snapshot = None  # the preset's cards as workload.simulate() takes them
        self._estimate_source = None  # the pending restart of the simulation
        self._estimate_cancel = threading.Event()  # set to drop the running simulation
        self._add_actions()
        self.easy_day_rows = []
        for name in weekday_names():
            row = Adw.ComboRow(title=name, use_markup=False, model=Gtk.StringList.new(
                [_('Normal'), _('Reduced'), _('Minimum')]))
            connect_weak(row, 'notify::selected', self._on_workload_changed)
            self.easy_days_row.add_row(row)
            self.easy_day_rows.append(row)
        connect_weak(self.load_balancing_row, 'notify::active', self._on_workload_changed)
        for row in (self.new_per_day_row, self.reviews_per_day_row):
            connect_weak(row, 'notify::value', self._on_workload_changed)
        self.preset_row.set_model(Gtk.StringList())
        self.preset_row.connect('notify::selected', self._on_preset_selected)
        self.retention_adjustment.connect('value-changed', self._on_retention_changed)
        self.retention_scale.update_property([Gtk.AccessibleProperty.LABEL],
                                             [_('Desired Retention')])
        self.optimize_button.connect('clicked', self._on_optimize)
        self.learning_steps_row.connect('changed', self._on_steps_changed)
        self.relearning_steps_row.connect('changed', self._on_steps_changed)
        self.parameters_row.connect('changed', self._on_parameters_changed)
        self.parameters_reset_button.connect('clicked',
                                             lambda *_args: self.parameters_row.set_text(''))
        self.connect('closed', self._on_closed)
        self.reload()

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        self._preset_actions = {}
        for name, callback in (('add', self._on_add_preset), ('rename', self._on_rename_preset),
                               ('delete', self._on_delete_preset)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            group.add_action(action)
            self._preset_actions[name] = action
        self.insert_action_group('preset', group)

    # -- loading -----------------------------------------------------------------------------

    def reload(self):
        """Show the deck's preset as the collection has it."""
        self.config = self.collection.config_for_deck(self.deck_id)
        self._configs = self.collection.deck_configs()
        self._loading = True
        try:
            names = [config.name for config in self._configs]
            model = self.preset_row.get_model()
            # The model is replaced only when the names changed: swapping it from inside
            # the row's own selection notification upsets the row.
            if [model.get_string(i) for i in range(model.get_n_items())] != names:
                self.preset_row.set_model(Gtk.StringList.new(names))
            index = next((i for i, config in enumerate(self._configs)
                          if config.id == self.config.id), 0)
            self.preset_row.set_selected(index)
            self._load_fields(self.config)
        finally:
            self._loading = False
        users = self._decks_on_preset()
        self.preset_row.set_subtitle(
            ngettext('Used by {n} deck', 'Used by {n} decks', len(users)).format(n=len(users)))
        self._preset_actions['delete'].set_enabled(self.config.id != DEFAULT_ID)
        self._load_review_count()
        self._snapshot = None
        self._queue_estimate()

    def _load_fields(self, config):
        self.new_per_day_row.set_value(config.new_per_day)
        self.reviews_per_day_row.set_value(config.reviews_per_day)
        self.load_balancing_row.set_active(bool(config.load_balancing))
        for row, value in zip(self.easy_day_rows, workload.normalize_easy_days(config.easy_days),
                              strict=True):
            row.set_selected(EASY_DAY_CHOICES.index(value))
        self._on_workload_changed()
        self.study_mode_row.set_selected(_index(STUDY_MODES, config.study_mode))
        self.retention_adjustment.set_value(config.desired_retention)
        self._on_retention_changed()
        self.learning_steps_row.set_text(format_steps(config.learning_steps))
        self.relearning_steps_row.set_text(format_steps(config.relearning_steps))
        self.new_order_row.set_selected(_index(NEW_ORDERS, config.new_order))
        self.new_mix_row.set_selected(_index(NEW_MIXES, config.new_mix))
        self.bury_row.set_active(bool(config.bury_siblings))
        self.leech_threshold_row.set_value(config.leech_threshold)
        self.leech_action_row.set_selected(_index(LEECH_ACTIONS, config.leech_action))
        self.maximum_interval_row.set_value(config.maximum_interval)
        self.parameters_row.set_text(format_parameters(config.fsrs_parameters))
        self._on_steps_changed()
        self._on_parameters_changed()

    def _decks_on_preset(self):
        """The ids of the decks using the shown preset (a deck whose preset is gone counts
        as the default's)."""
        known = {config.id for config in self._configs}
        found = []
        for deck in self.collection.decks():
            config_id = deck.config_id if deck.config_id in known else DEFAULT_ID
            if config_id == self.config.id:
                found.append(deck.id)
        return found

    def _revlog_rows(self):
        """The reviews of the cards now in the decks on the preset, oldest first, as dicts a
        thread can read."""
        deck_ids = self._decks_on_preset()
        if not deck_ids:
            return []
        marks = ','.join('?' * len(deck_ids))
        rows = self.collection.db.execute(
            f'SELECT revlog.id, revlog.card_id, revlog.rating, revlog.state, revlog.kind '
            f'FROM revlog JOIN cards ON cards.id = revlog.card_id '
            f'WHERE cards.deck_id IN ({marks}) ORDER BY revlog.id', deck_ids)
        return [dict(row) for row in rows]

    def _load_review_count(self):
        rows = self._revlog_rows()
        histories = optimizer.items_from_revlog(rows, self.collection.day_start_hour)
        self._review_count = optimizer.loss_bearing_reviews(histories)
        enough = self._review_count >= optimizer.MINIMUM_REVIEWS
        self.optimize_row.set_sensitive(enough and not self._optimizing)
        if enough:
            self.optimize_row.set_subtitle(
                ngettext('Fit the memory model to your {n} review',
                         'Fit the memory model to your {n} reviews',
                         self._review_count).format(n=self._review_count))
        else:
            self.optimize_row.set_subtitle(
                _('Needs at least {minimum} reviews ({n} so far)').format(
                    minimum=optimizer.MINIMUM_REVIEWS, n=self._review_count))

    # -- reading back ------------------------------------------------------------------------

    def _on_retention_changed(self, *_args):
        retention = self.retention_adjustment.get_value()
        self.retention_value.set_text(f'{round(retention * 100)}%')
        self.retention_subtitle.set_text(retention_sentence(retention))
        self._queue_estimate()

    def _on_workload_changed(self, *_args):
        balancing = self.load_balancing_row.get_active()
        self.easy_days_row.set_sensitive(balancing)
        if not balancing:
            self.easy_days_row.set_expanded(False)
        self.easy_days_row.set_subtitle(easy_days_summary(self._easy_days(), balancing))
        self._queue_estimate()

    def _easy_days(self):
        return [EASY_DAY_CHOICES[row.get_selected()] for row in self.easy_day_rows]

    # -- the workload estimate ---------------------------------------------------------------

    def _queue_estimate(self):
        """Simulate the workload again in a moment (a slider drag restarts it once)."""
        if self._loading or self.config is None or self._stop.is_set():
            return
        if self._estimate_source is not None:
            GLib.source_remove(self._estimate_source)
        self._estimate_source = GLib.timeout_add(ESTIMATE_DELAY_MS, self._start_estimate)

    def _start_estimate(self):
        self._estimate_source = None
        self._estimate_cancel.set()  # a simulation still running is out of date
        if self._stop.is_set() or self.config is None:
            return GLib.SOURCE_REMOVE
        if self._snapshot is None:
            self._snapshot = workload.snapshot(self.collection, self._decks_on_preset())
        cards, new_cards = self._snapshot
        config = self.collect()
        if not cards and not new_cards:
            self.retention_estimate.set_text(
                estimate_text(config.desired_retention, config.parameters()))
            return GLib.SOURCE_REMOVE
        cancel = self._estimate_cancel = threading.Event()
        stop = self._stop
        arguments = dict(
            new_cards=new_cards, new_per_day=config.new_per_day,
            reviews_per_day=config.reviews_per_day, maximum_interval=config.maximum_interval,
            learning_steps=len(config.learning_steps),
            relearning_steps=len(config.relearning_steps),
            load_balancing=config.load_balancing, easy_days=list(config.easy_days),
            today=self.collection.today())
        params = config.parameters()
        retention = config.desired_retention

        def run():
            try:
                result = workload.simulate(
                    cards, params, retention,
                    should_stop=lambda: cancel.is_set() or stop.is_set(), **arguments)
            except Exception:
                log.exception('simulating the workload')
                result = None
            if result is not None:
                GLib.idle_add(self._on_estimated, cancel, result)

        threading.Thread(target=run, name='retain-workload', daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _on_estimated(self, cancel, result):
        if not cancel.is_set() and not self._stop.is_set():
            self.retention_estimate.set_text(workload_text(result))
        return GLib.SOURCE_REMOVE

    def _on_steps_changed(self, *_args):
        for row in (self.learning_steps_row, self.relearning_steps_row):
            _mark_valid(row, _valid(parse_steps, row.get_text()))

    def _on_parameters_changed(self, *_args):
        text = self.parameters_row.get_text()
        _mark_valid(self.parameters_row, _valid(parse_parameters, text))
        self.parameters_reset_button.set_visible(bool(text.strip()))

    def collect(self):
        """The preset as the dialog shows it: a copy of the stored one with every valid
        field read back (an invalid entry keeps the stored value)."""
        config = self.config.copy()
        config.new_per_day = int(self.new_per_day_row.get_value())
        config.reviews_per_day = int(self.reviews_per_day_row.get_value())
        config.load_balancing = self.load_balancing_row.get_active()
        config.easy_days = self._easy_days()
        config.study_mode = STUDY_MODES[self.study_mode_row.get_selected()]
        config.desired_retention = round(self.retention_adjustment.get_value(), 2)
        for row, field in ((self.learning_steps_row, 'learning_steps'),
                           (self.relearning_steps_row, 'relearning_steps')):
            try:
                setattr(config, field, parse_steps(row.get_text()))
            except ValueError:
                pass
        config.new_order = NEW_ORDERS[self.new_order_row.get_selected()]
        config.new_mix = NEW_MIXES[self.new_mix_row.get_selected()]
        config.bury_siblings = self.bury_row.get_active()
        config.leech_threshold = int(self.leech_threshold_row.get_value())
        config.leech_action = LEECH_ACTIONS[self.leech_action_row.get_selected()]
        config.maximum_interval = int(self.maximum_interval_row.get_value())
        try:
            config.fsrs_parameters = parse_parameters(self.parameters_row.get_text())
        except ValueError:
            pass
        return config

    def save(self):
        """Store the edits (one undo step, with a toast); False when nothing changed."""
        if self.config is None or self.collection.deck_config(self.config.id) is None:
            return False
        config = self.collect()
        if config.to_json() == self.config.to_json() and config.name == self.config.name:
            return False
        self.collection.update_deck_config(config)
        self.config = config
        self.app.toast(_('Deck options saved'), undo=True)
        return True

    def _on_closed(self, *_args):
        self._stop.set()
        if self._estimate_source is not None:
            GLib.source_remove(self._estimate_source)
            self._estimate_source = None
        self.save()

    # -- presets -----------------------------------------------------------------------------

    def _on_preset_selected(self, *_args):
        if self._loading:
            return
        index = self.preset_row.get_selected()
        if index >= len(self._configs) or self._configs[index].id == self.config.id:
            return
        self.save()
        self.collection.set_deck_config(self.deck_id, self._configs[index].id)
        self.reload()

    def _on_add_preset(self, *_args):
        self.save()
        shown = self.collect()

        def apply(name):
            if any(config.name.lower() == name.lower() for config in self._configs):
                return _('A preset called “{name}” already exists.').format(name=name)
            new = shown.copy()
            new.id = None
            new.name = name
            self.collection.add_deck_config(new)
            self.collection.set_deck_config(self.deck_id, new.id)
            self.app.toast(_('Preset “{name}” created').format(name=name), undo=True)
            self.reload()
            return None

        NameDialog(_('New Preset'), _('C_reate'), apply, text=self.config.name,
                   hint=_('Starts as a copy of the settings shown')).present_over(self)

    def _on_rename_preset(self, *_args):
        def apply(name):
            if any(config.name.lower() == name.lower() and config.id != self.config.id
                   for config in self._configs):
                return _('A preset called “{name}” already exists.').format(name=name)
            config = self.collect()
            config.name = name
            self.collection.update_deck_config(config)
            self.reload()
            return None

        NameDialog(_('Rename Preset'), _('_Rename'), apply, text=self.config.name).present_over(
            self)

    def _on_delete_preset(self, *_args):
        try:
            self.collection.remove_deck_config(self.config.id)
        except CollectionError as error:
            self.app.report(error)
            return
        self.app.toast(_('Preset deleted'), undo=True)
        self.reload()

    # -- optimizing --------------------------------------------------------------------------

    def _on_optimize(self, *_args):
        if self._optimizing:
            return
        self.save()
        rows = self._revlog_rows()
        current = self.config.fsrs_parameters
        day_start_hour = self.collection.day_start_hour
        stop = self._stop
        self._optimizing = True
        self.optimize_row.set_sensitive(True)
        self.optimize_stack.set_visible_child_name('working')
        self.optimize_progress.set_text('0%')
        self.optimize_row.set_subtitle(_('Fitting the memory model to your reviews…'))

        def progress(fraction, loss):
            GLib.idle_add(self._on_progress, fraction, loss)

        def run():
            try:
                result = optimizer.suggest(rows, current, day_start_hour, progress=progress,
                                           should_stop=stop.is_set)
            except optimizer.NotEnoughData as error:
                GLib.idle_add(self._on_optimize_failed, str(error))
            except Exception as error:
                log.exception('optimizing')
                GLib.idle_add(self._on_optimize_failed, str(error))
            else:
                GLib.idle_add(self._on_optimized, result)

        threading.Thread(target=run, name='retain-optimizer', daemon=True).start()

    def _on_progress(self, fraction, _loss):
        if self._optimizing and not self._stop.is_set():
            self.optimize_progress.set_text(f'{round(fraction * 100)}%')
        return GLib.SOURCE_REMOVE

    def _finish_optimizing(self):
        self._optimizing = False
        self.optimize_stack.set_visible_child_name('button')

    def _on_optimize_failed(self, message):
        self._finish_optimizing()
        if not self._stop.is_set():
            self.optimize_row.set_subtitle(
                _('Could not optimize: {error}').format(error=message))
        return GLib.SOURCE_REMOVE

    def _on_optimized(self, result):
        self._finish_optimizing()
        if self._stop.is_set():
            return GLib.SOURCE_REMOVE  # closed meanwhile: the fit was cut short
        params, before, after = result
        if self.collection.deck_config(self.config.id) is None:
            return GLib.SOURCE_REMOVE
        config = self.collect()
        config.fsrs_parameters = list(params)
        self.collection.update_deck_config(config)
        self.config = config
        self._loading = True
        try:
            self.parameters_row.set_text(format_parameters(config.fsrs_parameters))
        finally:
            self._loading = False
        self._on_retention_changed()
        self.optimize_row.set_subtitle(
            _('Parameters updated (loss {before:.3f} → {after:.3f})').format(
                before=before, after=after))
        self.app.toast(_('Parameters updated'), undo=True)
        return GLib.SOURCE_REMOVE


def _index(choices, value):
    return choices.index(value) if value in choices else 0


def _valid(parse, text):
    try:
        parse(text)
    except ValueError:
        return False
    return True


def _mark_valid(row, valid):
    if valid:
        row.remove_css_class('error')
    else:
        row.add_css_class('error')


def present(app, parent, deck_id):
    dialog = DeckOptionsDialog(app, deck_id)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog
