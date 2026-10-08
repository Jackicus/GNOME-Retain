# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The review page: a deck's cards, one at a time, question then answer, then a rating.

    page = ReviewPage(deck_id, session=None)   # window.study() pushes it
    page.deck_id
    page.show_answer(); page.answer(rating)    # what Space/Enter and 1-4 do
    page.next()                                # the session's next card, or the end
    page.undone(label)                         # window.undone(): the answer undone comes back

The session (scheduler.Session) says what comes next: the deck's queue of the day, or the
custom one it was given. A card is rendered by collection.render_card() into a RetainCardView
(widgets/card_view.py) in a libadwaita card; its sounds and `{{tts}}` speech play in order
through widgets/audio.py when auto-play-audio is on (Replay Sound plays them again). Read
Aloud speaks the side shown, each language in its own voice (speech.runs), and does so as
each side is shown with read-aloud on; when no voice reads a language, a banner above the
card says so and explains what to install. A `{{type:…}}` question gets an entry
under the card, whose text the answer side compares (template.typed_answer_diff). The header
shows the deck's name and the remaining counts (widgets/counts.py, from session.counts();
hidden by show-remaining), a flag menu and a More menu; the bottom bar Show Answer, then
Again, Hard, Good and Easy with the interval each gives (scheduler.preview; show-intervals),
Good the coloured one; two-button-mode hides Hard and Easy, which the keys still reach.

The keys (shortcuts.REVIEW) are the page's own controller, in the capture phase, left alone
while an entry has the focus or a popover is open. The review actions (`review.*`, on the
page) are the menus' and the buttons'. An answer, a suspension, a burial, a deletion, a flag
and a mark go through the collection, each one undo step: app.undo() then calls undone()
with the step's label, and the page shows the card again (session.put_back) when the step
was its last answer or removal. A card is a leech when the collection says so during an
answer (its `changed` signal with 'leech'): a toast says it. When the session has no card
left, a status page says the deck is done for today, with Study More… (dialogs/custom_study).

The page listens to the collection and the settings only while mapped, through handlers
held weakly (a pushed page is dropped when popped: nothing may keep it alive).
"""

import contextlib
import html as html_module
import logging
import random
import re
import time
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import answers, notetypes, speech, template
from ..fsrs import AGAIN, EASY, GOOD, HARD
from ..shortcuts import REVIEW
from ..widgets.audio import Player
from ..widgets.card_view import RetainCardView, html_to_markup  # noqa: F401
from ..widgets.counts import RetainCounts, kind_of  # noqa: F401  (the template's child)
from ..widgets.util import connect_weak
from . import app

log = logging.getLogger(__name__)

MAX_DURATION_MS = 60_000  # a card left open longer counts as a minute, as Anki
RATINGS = (AGAIN, HARD, GOOD, EASY)
RATE_KEYS = {'again': AGAIN, 'hard': HARD, 'good': GOOD, 'easy': EASY}
FLAG_KEYS = {'flag-1': 1, 'flag-2': 2, 'flag-3': 3, 'flag-4': 4}
CARD_ACTIONS = ('show-answer', 'rate', 'edit', 'info', 'mark', 'suspend', 'bury', 'delete',
                'replay', 'read-aloud', 'flag')
# Language names for the missing-voice banner; other languages show their tag.
LANGUAGE_NAMES = {'ja': _('Japanese'), 'zh': _('Chinese'), 'ko': _('Korean'),
                  'en': _('English'), 'es': _('Spanish'), 'fr': _('French'),
                  'de': _('German'), 'it': _('Italian'), 'pt': _('Portuguese'),
                  'ru': _('Russian')}
COLLECTION_KINDS = ('cards', 'notes', 'notetypes', 'decks')

_TYPE_SPAN = re.compile(r'<span class="retain-type-answer" data-field="([^"]*)"'
                        r'(?: data-cloze="(\d+)")?')


@Gtk.Template(resource_path='/io/github/jackicus/Retain/review.ui')
class ReviewPage(Adw.NavigationPage):
    __gtype_name__ = 'RetainReviewPage'

    toolbar_view = Gtk.Template.Child()
    voice_banner = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    counts = Gtk.Template.Child()
    flag_button = Gtk.Template.Child()
    more_button = Gtk.Template.Child()
    content_stack = Gtk.Template.Child()
    card_view = Gtk.Template.Child()
    type_entry = Gtk.Template.Child()
    typed_box = Gtk.Template.Child()
    typed_verdict = Gtk.Template.Child()
    typed_diff = Gtk.Template.Child()
    choices_list = Gtk.Template.Child()
    finished_page = Gtk.Template.Child()
    answer_stack = Gtk.Template.Child()
    show_answer_button = Gtk.Template.Child()
    answers_box = Gtk.Template.Child()
    again_button = Gtk.Template.Child()
    hard_button = Gtk.Template.Child()
    good_button = Gtk.Template.Child()
    easy_button = Gtk.Template.Child()
    again_interval = Gtk.Template.Child()
    hard_interval = Gtk.Template.Child()
    good_interval = Gtk.Template.Child()
    easy_interval = Gtk.Template.Child()

    def __init__(self, deck_id, session=None):
        super().__init__()
        self.deck_id = deck_id
        application = app()
        self.collection = application.collection
        self.scheduler = application.scheduler
        self.settings = application.settings
        deck = self.collection.deck(deck_id)
        self.set_title(deck.name if deck else _('Study'))
        self.title_label.set_text(self.get_title())
        self.session = session or self.scheduler.session(deck_id)
        self.card = None
        self.note = None
        self.notetype = None
        self.side = None  # 'question', 'answer', or None once the session is done
        self._question = ''
        self._answer = ''
        self._typed_expected = None  # the text a {{type:…}} question asks for
        self._typed_result = None
        config = self.collection.config_for_deck(deck_id)
        self.mode = config.study_mode if config.study_mode in answers.MODES else 'flip'
        self._card_mode = 'flip'  # the mode this card is asked in (flip when the mode can't)
        self._expected = None     # (field, html) answering the card, in type and choice modes
        self._choices = []        # [(html, correct)] in choice mode, in their order
        self._choices_card = None  # the card id the choices were drawn for
        self._picked = None       # the index of the option chosen
        self._suggested = GOOD    # the grade Space and Enter give on the answer side
        self._shown_at = None
        self._last = None  # (card id, undo label) of the last answer or removal
        self._busy = False  # a change of the page's own making: no reload for it
        self._reload_pending = False
        self._handlers = []  # (object, handler id) connected while mapped
        self._player = Player(missing_voice=self._on_missing_voice)
        self._buttons = {AGAIN: self.again_button, HARD: self.hard_button,
                         GOOD: self.good_button, EASY: self.easy_button}
        self._intervals = {AGAIN: self.again_interval, HARD: self.hard_interval,
                           GOOD: self.good_interval, EASY: self.easy_interval}
        self._add_actions()
        self._add_keys()
        connect_weak(self.type_entry, 'activate', self._on_entry_activate)
        connect_weak(self.voice_banner, 'button-clicked', self._on_voice_help)
        self.settings.bind('show-remaining', self.counts, 'visible', Gio.SettingsBindFlags.GET)
        self._apply_settings()
        self.next()

    # -- lifetime ----------------------------------------------------------------------------

    def do_map(self):
        Adw.NavigationPage.do_map(self)
        if self._handlers:
            return
        for obj, signal, method in (
                (self.collection, 'changed', self._on_collection_changed),
                (self.settings, 'changed', self._on_settings_changed)):
            self._handlers.append((obj, connect_weak(obj, signal, method)))
        self._apply_settings()

    def do_unmap(self):
        for obj, handler in self._handlers:
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self._handlers = []
        self._player.stop()
        Adw.NavigationPage.do_unmap(self)

    def _on_collection_changed(self, _collection, kind):
        if self._busy or kind not in COLLECTION_KINDS or self._reload_pending:
            return
        self._reload_pending = True
        GLib.idle_add(self._reload_idle)

    def _reload_idle(self):
        self._reload_pending = False
        self._reload()
        return GLib.SOURCE_REMOVE

    def _on_settings_changed(self, _settings, key):
        if key in ('show-intervals', 'two-button-mode', 'card-text-scale', 'show-remaining'):
            self._apply_settings()

    def _apply_settings(self):
        two_buttons = self.settings.get_boolean('two-button-mode')
        self.hard_button.set_visible(not two_buttons)
        self.easy_button.set_visible(not two_buttons)
        self.card_view.set_scale(self.settings.get_double('card-text-scale'))
        if self.side == 'answer':
            self._update_intervals()

    @contextlib.contextmanager
    def _quiet(self):
        """A change of the page's own: the collection's signal is not a reason to reload."""
        self._busy = True
        try:
            yield
        finally:
            self._busy = False

    # -- actions and keys --------------------------------------------------------------------

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        self._actions = {}
        for name, callback, parameter in (
                ('show-answer', self._on_show_answer, None),
                ('rate', self._on_rate, GLib.VariantType('i')),
                ('edit', self._on_edit, None), ('info', self._on_info, None),
                ('suspend', self._on_suspend, None), ('bury', self._on_bury, None),
                ('delete', self._on_delete, None), ('replay', self._on_replay, None),
                ('read-aloud', self._on_read_aloud, None),
                ('study-more', self._on_study_more, None)):
            action = Gio.SimpleAction.new(name, parameter)
            connect_weak(action, 'activate', callback)
            group.add_action(action)
            self._actions[name] = action
        mark = Gio.SimpleAction.new_stateful('mark', None, GLib.Variant('b', False))
        connect_weak(mark, 'activate', self._on_mark)
        group.add_action(mark)
        self._actions['mark'] = mark
        mode = Gio.SimpleAction.new_stateful('mode', GLib.VariantType('s'),
                                             GLib.Variant('s', self.mode))
        connect_weak(mode, 'activate', self._on_mode)
        group.add_action(mode)
        self._actions['mode'] = mode
        flag = Gio.SimpleAction.new_stateful('flag', GLib.VariantType('i'), GLib.Variant('i', 0))
        connect_weak(flag, 'activate', self._on_flag)
        group.add_action(flag)
        self._actions['flag'] = flag
        self.insert_action_group('review', group)

    def _add_keys(self):
        self._bindings = []
        for name, accel in REVIEW.items():
            parsed = Gtk.accelerator_parse(accel)
            keyval, mods = parsed[-2], parsed[-1]
            if keyval:
                self._bindings.append((name, keyval, mods))
        controller = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        connect_weak(controller, 'key-pressed', self._on_key)
        self.add_controller(controller)

    def _focus_is_elsewhere(self):
        """Whether the keys belong to someone else: an entry, or an open popover."""
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if focus is None:
            return False
        if isinstance(focus, (Gtk.Text, Gtk.TextView, Gtk.Editable)):
            return True
        return focus.get_ancestor(Gtk.Popover) is not None

    def _on_key(self, _controller, keyval, _keycode, state):
        if self._focus_is_elsewhere():
            return Gdk.EVENT_PROPAGATE
        mods = state & Gtk.accelerator_get_default_mod_mask()
        key = Gdk.keyval_to_lower(keyval)
        if key == Gdk.KEY_KP_Enter:
            key = Gdk.KEY_Return
        elif Gdk.KEY_KP_0 <= key <= Gdk.KEY_KP_9:
            key = Gdk.KEY_0 + (key - Gdk.KEY_KP_0)
        for name, bound_key, bound_mods in self._bindings:
            if key == bound_key and mods == bound_mods:
                return Gdk.EVENT_STOP if self.run_key(name) else Gdk.EVENT_PROPAGATE

        return Gdk.EVENT_PROPAGATE

    def run_key(self, name):
        """What a REVIEW key does; False when it does nothing now."""
        if name in ('show-answer', 'show-answer-alt'):
            if self.side == 'question':
                self.show_answer()
            elif self.side == 'answer':
                self.answer(self._suggested)
            else:
                return False
            return True
        if name in RATE_KEYS:
            if self.side == 'question' and self._card_mode == 'choice':
                return self.pick(RATINGS.index(RATE_KEYS[name]))
            return self.answer(RATE_KEYS[name])
        if self.card is None:
            return False
        if name in FLAG_KEYS:
            self._set_flag(0 if self.card.flag == FLAG_KEYS[name] else FLAG_KEYS[name])
        elif name == 'flag':
            self.flag_button.popup()
        elif name == 'edit':
            self._on_edit()
        elif name == 'suspend':
            self._on_suspend()
        elif name == 'bury':
            self._on_bury()
        elif name == 'mark':
            self._on_mark()
        elif name == 'info':
            self._on_info()
        elif name == 'replay-audio':
            self._on_replay()
        elif name == 'read-aloud':
            self._on_read_aloud()
        elif name == 'delete':
            self._on_delete()
        else:
            return False
        return True

    def _on_entry_activate(self, _entry):
        if self.side == 'question':
            self.show_answer()

    # -- the flow --------------------------------------------------------------------------

    def next(self):
        """Show the session's next card, or the end of the session."""
        self._player.stop()
        try:
            card = self.session.next_card()
        except Exception as error:
            app().report(error)
            card = None
        self.card = card
        if card is None:
            self.side = None
            self.note = self.notetype = None
            self.type_entry.set_visible(False)
            self.content_stack.set_visible_child_name('finished')
            self.toolbar_view.set_reveal_bottom_bars(False)
            self._update_counts()
            self._update_state()
            return
        self._render()
        self.show_question()

    def _render(self):
        """The card's two sides into the view (the side shown is the caller's)."""
        self.note = self.collection.note(self.card.note_id)
        self.notetype = self.collection.notetype(self.note.notetype_id)
        self._question, self._answer = self.collection.render_card(
            self.card, self.note, self.notetype)
        occlusion = None
        if notetypes.is_image_occlusion(self.notetype):
            names = self.notetype.field_names()
            index = names.index('Occlusion') if 'Occlusion' in names else 0
            field = self.note.fields[index] if index < len(self.note.fields) else ''
            occlusion = {'shapes': notetypes.occlusion_shapes(field),
                         'ordinal': self.card.ord + 1}
        media_uri = self.collection.media.directory.resolve().as_uri() + '/'
        self.card_view.set_card(self._question, self._answer, self.notetype.css, media_uri,
                                occlusion=occlusion,
                                scale=self.settings.get_double('card-text-scale'))
        self._typed_expected = self._expected_typed()
        self._prepare_mode()
        self._update_state()

    def _prepare_mode(self):
        """The mode this card is asked in, with its answer and options: flip when the
        mode cannot ask it (a cloze, a long answer, a {{type:}} card, too few options)."""
        self._card_mode = 'flip'
        self._expected = None
        if self.mode == 'flip' or self._typed_expected is not None:
            return
        self._expected = answers.expected_answer(self.notetype, self.note, self.card.ord)
        if self._expected is None:
            return
        if self.mode == 'type':
            self._card_mode = 'type'
            return
        if self._choices_card != self.card.id:
            field, correct = self._expected
            decks = self.collection.deck_and_children(self.deck_id) or [self.card.deck_id]
            wrong = answers.distractors(self.collection, self.note, self.notetype, field,
                                        decks)
            options = [(html, False) for html in wrong] + [(correct, True)]
            random.shuffle(options)
            self._choices = options
            self._choices_card = self.card.id
        if len(self._choices) >= 3:  # the right answer and at least two others
            self._card_mode = 'choice'

    def _expected_typed(self):
        """The text a {{type:…}} span on the question asks for, or None."""
        match = _TYPE_SPAN.search(self._question)
        if match is None:
            return None
        name = html_module.unescape(match[1])
        fields = dict(zip(self.notetype.field_names(), self.note.fields, strict=False))
        if match[2]:
            kind = self.notetype.kind if self.notetype.kind in template.CLOZE_KINDS else 'cloze'
            try:
                return template.render_side('{{cloze-only:' + name + '}}', fields,
                                            ord=self.card.ord, kind=kind, side='answer')
            except template.TemplateError:
                return fields.get(name, '')
        return fields.get(name, '')

    def show_question(self):
        self.side = 'question'
        self._typed_result = None
        self.card_view.show_question()
        typed = self._typed_expected is not None or self._card_mode == 'type'
        self.type_entry.set_text('')
        self.type_entry.set_visible(typed)
        self.typed_box.set_visible(False)
        self._picked = None
        self._fill_choices()
        self._set_suggested(GOOD)
        self.answer_stack.set_visible_child_name('show')
        self.content_stack.set_visible_child_name('study')
        self.toolbar_view.set_reveal_bottom_bars(True)
        self._shown_at = time.monotonic()
        self._update_counts()
        if typed and self.get_mapped():
            self.type_entry.grab_focus()
        self._play_side('question')

    def show_answer(self):
        if self.card is None or self.side != 'question':
            return
        if self._typed_expected is not None:
            self._typed_result = template.typed_answer_diff(self._typed_expected,
                                                            self.type_entry.get_text())
            self.card_view.set_typed_result(self._typed_result)
            self.type_entry.set_visible(False)
        elif self._card_mode == 'type':
            self._show_typed_verdict(self.type_entry.get_text())
            self.type_entry.set_visible(False)
        elif self._card_mode == 'choice':
            self._show_choice_result()
        self.side = 'answer'
        self.card_view.show_answer()
        self._update_intervals()
        self.answer_stack.set_visible_child_name('rate')
        if self._suggested in self._buttons and self.get_mapped():
            self._buttons[self._suggested].grab_focus()
        self._play_side('answer')

    def answer(self, rating):
        """Rate the card shown (its answer side must be showing); True when it was."""
        if self.card is None or self.side != 'answer' or rating not in RATINGS:
            return False
        card = self.card
        elapsed_ms = 0
        if self._shown_at is not None:
            elapsed_ms = min(MAX_DURATION_MS, int((time.monotonic() - self._shown_at) * 1000))
        leech = []
        handler = self.collection.connect(
            'changed', lambda _collection, kind: leech.append(kind) if kind == 'leech' else None)
        try:
            with self._quiet():
                after = self.scheduler.answer(card, rating, duration_ms=elapsed_ms,
                                              cram=self.session.cram)
        except Exception as error:
            app().report(error)
            return False
        finally:
            self.collection.disconnect(handler)
        self.session.answered(after, rating)
        self._last = (card.id, self.collection.undo_label)
        if leech:
            self.toast(_('Card is a leech'))
        self.next()
        return True

    def undone(self, label):
        """An undo happened: when it undid this page's last answer or removal, that card
        comes back, on its question side; otherwise the card shown is read again."""
        if self._last is None or self._last[1] != label:
            self._reload()
            return
        card_id, _label = self._last
        self._last = None
        card = self.collection.card(card_id)
        if card is None:
            self._reload()
            return
        self.session.put_back(card)
        self.card = card
        self._render()
        self.show_question()

    def _reload(self):
        """The card as the collection has it now, on the side shown; the next one when it
        is gone, suspended or buried."""
        if self.card is None:
            if self.side is None and self.session.next_card() is not None:
                self.next()  # something came back for today
            return
        card = self.collection.card(self.card.id)
        if card is None or card.suspended or card.buried:
            self.next()
            return
        self.card = card
        side = self.side
        try:
            self._render()
        except Exception as error:  # the note or its type went away under the card
            app().report(error)
            self.next()
            return
        if side == 'answer':
            if self._typed_result is not None:
                self.card_view.set_typed_result(self._typed_result)
            self.card_view.show_answer()
            self._update_intervals()
        else:
            self.card_view.show_question()
        self._update_counts()

    # -- what the page shows -----------------------------------------------------------------

    def _update_intervals(self):
        if self.card is None:
            return
        show = self.settings.get_boolean('show-intervals')
        try:
            labels = self.scheduler.preview(self.card) if show else {}
        except Exception:
            log.exception('previewing the intervals')
            labels = {}
        for rating, label in self._intervals.items():
            label.set_text(labels.get(rating, ''))
            label.set_visible(show and rating in labels)

    def _update_counts(self):
        try:
            new, learning, due = self.session.counts()
        except Exception:
            log.exception('counting the session')
            new = learning = due = 0
        self.counts.set_counts(new, learning, due, kind_of(self.card))

    def _update_state(self):
        """The actions follow the card: none without one; mark and flag show its state."""
        has_card = self.card is not None
        for name in CARD_ACTIONS:
            self._actions[name].set_enabled(has_card)
        marked = bool(self.note.marked) if self.note is not None else False
        self._actions['mark'].set_state(GLib.Variant('b', marked))
        flag = int(self.card.flag or 0) if has_card else 0
        self._actions['flag'].set_state(GLib.Variant('i', flag))
        if flag:
            self.flag_button.add_css_class('accent')
            self.flag_button.set_tooltip_text(_('Flag Card (flagged)'))
        else:
            self.flag_button.remove_css_class('accent')
            self.flag_button.set_tooltip_text(_('Flag Card'))

    # -- study modes (answers.py) -------------------------------------------------------------

    def _fill_choices(self):
        """The options of a choice card, numbered as the keys that pick them."""
        while (row := self.choices_list.get_row_at_index(0)) is not None:
            self.choices_list.remove(row)
        show = self._card_mode == 'choice' and self.side == 'question'
        self.choices_list.set_visible(show)
        if not show:
            return
        for index, (html, _correct) in enumerate(self._choices):
            row = Adw.ActionRow(title=template.strip_html(html), activatable=True,
                                use_markup=False)
            number = Gtk.Label(label=str(index + 1), width_chars=2)
            number.add_css_class('dimmed')
            number.add_css_class('numeric')
            row.add_prefix(number)
            row.connect('activated', lambda _row, index=index: self.pick(index))
            self.choices_list.append(row)

    def pick(self, index):
        """Choose option `index` (0-based) of a choice card; True when there was one."""
        if self.side != 'question' or self._card_mode != 'choice':
            return False
        if not 0 <= index < len(self._choices):
            return False
        self._picked = index
        self.show_answer()
        return True

    def _show_choice_result(self):
        """Mark the right option, and the one picked when it was wrong; suggest a grade."""
        correct = self._picked is not None and self._choices[self._picked][1]
        for index, (_html, right) in enumerate(self._choices):
            row = self.choices_list.get_row_at_index(index)
            if row is None:
                continue
            row.set_activatable(False)
            if right:
                row.add_css_class('success')
                row.set_subtitle(_('Right answer'))
            elif index == self._picked:
                row.add_css_class('error')
                row.set_subtitle(_('Your answer'))
            else:
                row.add_css_class('dimmed')
        self._set_suggested(answers.choice_rating(correct, self.card))

    def _show_typed_verdict(self, typed):
        verdict = answers.grade_typed(self._expected[1], typed)
        texts = {'exact': _('Correct'), 'close': _('Almost: counts as Hard'),
                 'wrong': _('Not quite')}
        classes = {'exact': 'success', 'close': 'warning', 'wrong': 'error'}
        self.typed_verdict.set_text(texts[verdict.kind] if typed.strip() else _('No answer'))
        for css in classes.values():
            self.typed_verdict.remove_css_class(css)
        self.typed_verdict.add_css_class(classes[verdict.kind])
        self.typed_diff.set_markup(html_to_markup(template.typed_answer_diff(verdict.shown,
                                                                             typed)))
        self.typed_box.set_visible(True)
        self._set_suggested(verdict.rating)

    def _set_suggested(self, rating):
        """The grade Space and Enter give, shown as the coloured answer button."""
        self._suggested = rating
        self._apply_settings()
        for each, button in self._buttons.items():
            if each == rating:
                button.add_css_class('suggested-action')
            else:
                button.remove_css_class('suggested-action')
        if rating in (HARD, EASY) and not self._buttons[rating].get_visible():
            self._buttons[rating].set_visible(True)  # two-button mode still shows it now

    def _on_mode(self, action, parameter):
        mode = parameter.get_string()
        if mode not in answers.MODES or mode == self.mode:
            return
        action.set_state(parameter)
        self.mode = mode
        if self.card is not None and self.side == 'question':
            self._prepare_mode()
            self.show_question()

    def _av_items(self, html):
        """The side's sounds (files that exist) and speech, in order, for the player."""
        items = []
        for kind, value in template.av_tags(html):
            if kind == 'tts':
                items.append(('tts', value))
            else:
                path = self.collection.media.path(value)
                if path.exists():
                    items.append(path)
        return items

    def _read_aloud_items(self, side):
        """Read Aloud's speech for a side: its text in runs, each in its language's voice."""
        html = speech.side_html(self._question, self._answer, side)
        html = template.strip_tts_tags(html)
        context = '\n'.join(self.note.fields) if self.note is not None else ''
        return [('tts', (lang, [], 1.0, text)) for lang, text in speech.runs(html, context)]

    def _play_side(self, side):
        """What a side plays when it is shown: its sounds and {{tts}} speech (with
        auto-play-audio), then the side read aloud (with read-aloud)."""
        items = []
        if self.settings.get_boolean('auto-play-audio'):
            items += self._av_items(self._question if side == 'question' else self._answer)
        if self.settings.get_boolean('read-aloud'):
            items += self._read_aloud_items(side)
        if items:
            self._player.play(items)

    def _on_missing_voice(self, lang):
        """No voice reads `lang` (None: nothing can speak at all): say so above the card,
        once, without stopping the review."""
        if lang is None:
            title = _('Nothing is installed to read cards aloud')
        else:
            base = speech.language_tag(lang).split('-')[0] or lang
            name = LANGUAGE_NAMES.get(base, lang)
            title = _('No voice is installed for {language}').format(language=name)
        self.voice_banner.set_title(title)
        self.voice_banner.set_revealed(True)

    def _on_voice_help(self, _banner):
        dialog = Adw.AlertDialog(
            heading=_('Installing Voices'),
            body=_('Retain reads cards with the voices of your system. Install one of these, '
                   'then open the card again:\n\n'
                   '• Speech Dispatcher with the voice for the language. For Japanese, '
                   'its Open JTalk module reads kanji.\n'
                   '• A Spiel speech provider, such as eSpeak NG or Piper, from '
                   'project-spiel.org.'))
        dialog.add_response('close', _('_Close'))
        dialog.present(self.get_root())
        self.voice_banner.set_revealed(False)

    def toast(self, text, undo=False):
        application = app()
        if hasattr(application, 'toast'):
            application.toast(text, undo=undo)

    # -- the card actions --------------------------------------------------------------------

    def _on_show_answer(self, *_args):
        self.show_answer()

    def _on_rate(self, _action, parameter):
        self.answer(parameter.get_int32())

    def _on_flag(self, _action, parameter):
        self._set_flag(parameter.get_int32())

    def _set_flag(self, flag):
        if self.card is None:
            return
        try:
            with self._quiet():
                self.collection.set_flag([self.card.id], flag)
        except Exception as error:
            app().report(error)
            return
        self._reload()

    def _on_mark(self, *_args):
        if self.note is None:
            return
        try:
            with self._quiet():
                self.collection.set_marked([self.note.id], not self.note.marked)
        except Exception as error:
            app().report(error)
            return
        self._reload()

    def _remove(self, operation, text):
        """Suspend, bury or delete the card shown (an undo step), say so, move on."""
        if self.card is None:
            return
        card = self.card
        try:
            with self._quiet():
                operation(card)
        except Exception as error:
            app().report(error)
            return
        self._last = (card.id, self.collection.undo_label)
        self.toast(text, undo=True)
        self.next()

    def _on_suspend(self, *_args):
        self._remove(lambda card: self.collection.suspend([card.id]), _('Card suspended'))

    def _on_bury(self, *_args):
        self._remove(lambda card: self.collection.bury([card.id]), _('Card buried'))

    def _on_delete(self, *_args):
        self._remove(lambda card: self.collection.remove_notes([card.note_id]),
                     _('Note deleted'))

    def _on_edit(self, *_args):
        if self.note is None:
            return
        from ..dialogs import add_edit

        dialog = add_edit.present_edit(app(), self.get_root(), self.note.id)
        if dialog is not None:
            connect_weak(dialog, 'closed', self._on_dialog_closed)

    def _on_dialog_closed(self, _dialog):
        self._reload()

    def _on_info(self, *_args):
        if self.card is None:
            return
        from ..dialogs import card_info

        card_info.present(app(), self.get_root(), self.card.id)

    def _on_replay(self, *_args):
        if self.card is None:
            return
        items = self._av_items(self._question)
        if self.side == 'answer':
            items += self._av_items(self._answer)
        self._player.play(items)

    def _on_read_aloud(self, *_args):
        if self.card is None or self.side not in ('question', 'answer'):
            return
        items = self._read_aloud_items(self.side)
        if items:
            self._player.play(items)

    def _on_study_more(self, *_args):
        from ..dialogs import custom_study

        dialog = custom_study.present(app(), self.get_root(), self.deck_id)
        if dialog is not None:
            connect_weak(dialog, 'closed', self._on_study_more_closed)

    def _on_study_more_closed(self, _dialog):
        if self.card is None:
            self.next()
