# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The deck dialogs (dialogs/deck_name.py, deck_options.py, custom_study.py), over
test_today's stand-ins."""

import types
import unittest

from tests import ROOT  # noqa: F401  (registers src/ as retain)
from tests.gtk import pump
from tests.test_today import DeckPagesTestCase


class DeckNameTest(DeckPagesTestCase):

    def test_new_deck_creates_and_shows_it(self):
        from retain.dialogs import deck_name

        dialog = self.keep(deck_name.present_new_deck(self.app, self.window))
        self.assertEqual(dialog.get_title(), 'New Deck')
        self.assertFalse(dialog.apply_button.get_sensitive())
        dialog.entry.set_text('Spanish::Nouns')
        self.assertTrue(dialog.apply_button.get_sensitive())
        dialog.apply_button.emit('clicked')
        deck = self.collection.deck_by_name('Spanish::Nouns')
        self.assertIsNotNone(deck)
        self.assertEqual(self.window.shown, [f'deck:{deck.id}'])
        self.assertEqual(self.app.toasts, [('Deck “Nouns” created', True)])
        self.assertEqual(self.window.dialog_open, [True, False])

    def test_new_deck_under_a_parent_is_prefilled(self):
        from retain.dialogs import deck_name

        dialog = self.keep(deck_name.present_new_deck(self.app, self.window,
                                                      parent_deck_id=self.spanish.id))
        self.assertEqual(dialog.entry.get_text(), 'Spanish::')

    def test_new_deck_refuses_an_existing_name(self):
        from retain.dialogs import deck_name

        dialog = self.keep(deck_name.present_new_deck(self.app, self.window))
        dialog.entry.set_text('spanish')
        dialog.apply_button.emit('clicked')
        self.assertTrue(dialog.error_label.get_visible())
        self.assertIn('already exists', dialog.error_label.get_text())
        self.assertTrue(dialog.entry.has_css_class('error'))
        self.assertEqual(self.window.shown, [])
        dialog.entry.set_text('Spanish 2')
        self.assertFalse(dialog.error_label.get_visible())

    def test_rename_is_prefilled_and_renames(self):
        from retain.dialogs import deck_name

        dialog = self.keep(deck_name.present_rename(self.app, self.window, self.verbs.id))
        self.assertEqual(dialog.get_title(), 'Rename Deck')
        self.assertEqual(dialog.entry.get_text(), 'Spanish::Verbs')
        dialog.entry.set_text('Spanish::Verbos')
        dialog.entry.emit('entry-activated')
        self.assertEqual(self.collection.deck(self.verbs.id).name, 'Spanish::Verbos')
        self.assertEqual(self.app.toasts, [('Deck renamed to “Verbos”', True)])

    def test_rename_shows_the_collection_error(self):
        from retain.dialogs import deck_name

        dialog = self.keep(deck_name.present_rename(self.app, self.window, self.verbs.id))
        dialog.entry.set_text('Empty')
        dialog.apply_button.emit('clicked')
        self.assertIn('already exists', dialog.error_label.get_text())
        self.assertEqual(self.collection.deck(self.verbs.id).name, 'Spanish::Verbs')

    def test_delete_asks_then_deletes_with_undo(self):
        from retain.dialogs import deck_name

        dialog = self.keep(deck_name.confirm_delete(self.app, self.window, self.spanish.id))
        self.assertEqual(dialog.get_heading(), 'Delete “Spanish”?')
        self.assertEqual(dialog.get_body(),
                         'This deletes its 5 cards and 1 subdeck. You can undo this.')
        dialog.emit('response', 'delete')
        self.assertIsNone(self.collection.deck(self.spanish.id))
        self.assertIsNone(self.collection.deck(self.verbs.id))
        self.assertEqual(self.window.forgotten, [self.spanish.id])
        self.assertEqual(self.app.toasts, [('Deck deleted', True)])
        self.assertEqual(self.collection.undo(), 'Delete Deck')
        self.assertIsNotNone(self.collection.deck(self.spanish.id))

    def test_cancelled_delete_keeps_the_deck(self):
        from retain.dialogs import deck_name

        dialog = self.keep(deck_name.confirm_delete(self.app, self.window, self.empty.id))
        self.assertEqual(dialog.get_body(), 'This deletes its 0 cards. You can undo this.')
        dialog.emit('response', 'cancel')
        self.assertIsNotNone(self.collection.deck(self.empty.id))
        self.assertEqual(self.window.forgotten, [])


class DeckOptionsTest(DeckPagesTestCase):

    def dialog(self, deck_id=None):
        from retain.dialogs import deck_options

        return self.keep(deck_options.present(self.app, self.window,
                                              deck_id or self.spanish.id))

    def test_shows_the_preset(self):
        dialog = self.dialog()
        self.assertEqual(dialog.get_title(), 'Deck Options')
        self.assertEqual(dialog.preset_row.get_selected_item().get_string(), 'Default')
        self.assertEqual(dialog.preset_row.get_subtitle(), 'Used by 4 decks')
        self.assertEqual(dialog.new_per_day_row.get_value(), 20)
        self.assertEqual(dialog.reviews_per_day_row.get_value(), 200)
        self.assertEqual(dialog.learning_steps_row.get_text(), '1m 10m')
        self.assertEqual(dialog.relearning_steps_row.get_text(), '10m')
        self.assertEqual(dialog.parameters_row.get_text(), '')
        self.assertFalse(dialog.parameters_reset_button.get_visible())
        self.assertEqual(dialog.retention_value.get_text(), '90%')

    def test_a_changed_spin_row_saves_to_the_preset(self):
        dialog = self.dialog()
        dialog.new_per_day_row.set_value(35)
        dialog.bury_row.set_active(False)
        self.assertTrue(dialog.save())
        config = self.collection.config_for_deck(self.spanish.id)
        self.assertEqual(config.new_per_day, 35)
        self.assertFalse(config.bury_siblings)
        self.assertEqual(self.app.toasts, [('Deck options saved', True)])
        self.assertFalse(dialog.save())  # nothing more to save
        self.assertEqual(self.collection.undo(), 'Change Deck Options')
        self.assertEqual(self.collection.config_for_deck(self.spanish.id).new_per_day, 20)

    def test_closing_saves(self):
        dialog = self.dialog()
        dialog.reviews_per_day_row.set_value(150)
        dialog.force_close()
        pump()
        self.assertEqual(self.collection.config_for_deck(self.spanish.id).reviews_per_day, 150)

    def test_invalid_steps_show_an_error_and_are_not_saved(self):
        dialog = self.dialog()
        dialog.learning_steps_row.set_text('1m soon')
        self.assertTrue(dialog.learning_steps_row.has_css_class('error'))
        dialog.relearning_steps_row.set_text('5m 1h 1d')
        self.assertFalse(dialog.relearning_steps_row.has_css_class('error'))
        dialog.save()
        config = self.collection.config_for_deck(self.spanish.id)
        self.assertEqual(config.learning_steps, [1, 10])
        self.assertEqual(config.relearning_steps, [5, 60, 1440])
        dialog.learning_steps_row.set_text('2m 15m')
        self.assertFalse(dialog.learning_steps_row.has_css_class('error'))

    def test_steps_round_trip(self):
        from retain.dialogs.deck_options import format_steps, parse_steps

        self.assertEqual(parse_steps('1m 10m'), [1, 10])
        self.assertEqual(parse_steps('1, 10, 1h, 2d'), [1, 10, 60, 2880])
        self.assertEqual(parse_steps('0.5m'), [0.5])
        self.assertEqual(parse_steps(''), [])
        self.assertEqual(format_steps([1, 10, 60, 2880, 0.5]), '1m 10m 1h 2d 0.5m')
        for bad in ('x', '0m', '5s', '1m,,2x'):
            with self.assertRaises(ValueError):
                parse_steps(bad)

    def test_retention_sentence_and_estimate(self):
        dialog = self.dialog()
        dialog.retention_adjustment.set_value(0.85)
        self.assertEqual(dialog.retention_value.get_text(), '85%')
        self.assertEqual(dialog.retention_subtitle.get_text(),
                         'Aim to remember 85% of cards when they come up. '
                         'Higher means more reviews.')
        self.assertIn('fewer reviews than at 90%', dialog.retention_estimate.get_text())
        dialog.retention_adjustment.set_value(0.95)
        self.assertIn('more reviews than at 90%', dialog.retention_estimate.get_text())
        dialog.retention_adjustment.set_value(0.90)
        self.assertEqual(dialog.retention_estimate.get_text(),
                         'About as many reviews as at 90%')
        dialog.save()
        self.assertEqual(self.collection.config_for_deck(self.spanish.id).desired_retention,
                         0.9)

    def test_optimize_is_disabled_under_the_minimum(self):
        from retain import optimizer

        dialog = self.dialog()
        self.assertFalse(dialog.optimize_row.get_sensitive())
        self.assertEqual(dialog.optimize_row.get_subtitle(),
                         f'Needs at least {optimizer.MINIMUM_REVIEWS} reviews (0 so far)')

    def test_parameters_entry_validates_and_resets(self):
        from retain import fsrs

        dialog = self.dialog()
        dialog.parameters_row.set_text('1, 2, 3')
        self.assertTrue(dialog.parameters_row.has_css_class('error'))
        dialog.save()
        self.assertIsNone(self.collection.config_for_deck(self.spanish.id).fsrs_parameters)
        text = ', '.join(str(value) for value in fsrs.DEFAULT_PARAMETERS)
        dialog.parameters_row.set_text(text)
        self.assertFalse(dialog.parameters_row.has_css_class('error'))
        self.assertTrue(dialog.parameters_reset_button.get_visible())
        dialog.save()
        self.assertEqual(self.collection.config_for_deck(self.spanish.id).fsrs_parameters,
                         list(fsrs.DEFAULT_PARAMETERS))
        dialog.parameters_reset_button.emit('clicked')
        self.assertEqual(dialog.parameters_row.get_text(), '')
        dialog.save()
        self.assertIsNone(self.collection.config_for_deck(self.spanish.id).fsrs_parameters)

    def test_choosing_a_preset_assigns_it(self):
        from retain.deck_config import DeckConfig

        heavy = self.collection.add_deck_config(DeckConfig(name='Heavy', new_per_day=40))
        dialog = self.dialog()
        names = [dialog.preset_row.get_model().get_string(i)
                 for i in range(dialog.preset_row.get_model().get_n_items())]
        self.assertEqual(names, ['Default', 'Heavy'])
        dialog.preset_row.set_selected(1)
        self.assertEqual(self.collection.deck(self.spanish.id).config_id, heavy.id)
        self.assertEqual(dialog.new_per_day_row.get_value(), 40)
        self.assertEqual(dialog.preset_row.get_subtitle(), 'Used by 1 deck')

    def test_preset_menu_adds_renames_and_deletes(self):
        dialog = self.dialog()
        self.assertFalse(dialog._preset_actions['delete'].get_enabled())
        dialog.new_per_day_row.set_value(30)
        dialog._preset_actions['add'].activate(None)
        pump()
        name_dialog = self.keep(_newest_dialog(dialog))
        self.assertEqual(name_dialog.get_title(), 'New Preset')
        name_dialog.entry.set_text('Heavy')
        name_dialog.apply_button.emit('clicked')
        heavy = next(c for c in self.collection.deck_configs() if c.name == 'Heavy')
        self.assertEqual(heavy.new_per_day, 30)
        self.assertEqual(self.collection.deck(self.spanish.id).config_id, heavy.id)
        self.assertEqual(dialog.config.id, heavy.id)
        self.assertTrue(dialog._preset_actions['delete'].get_enabled())
        dialog._preset_actions['rename'].activate(None)
        pump()
        name_dialog = self.keep(_newest_dialog(dialog))
        name_dialog.entry.set_text('Heavier')
        name_dialog.apply_button.emit('clicked')
        self.assertEqual(self.collection.deck_config(heavy.id).name, 'Heavier')
        dialog._preset_actions['delete'].activate(None)
        self.assertIsNone(self.collection.deck_config(heavy.id))
        self.assertEqual(self.collection.deck(self.spanish.id).config_id, 1)
        self.assertEqual(dialog.config.name, 'Default')


class CustomStudyTest(DeckPagesTestCase):

    def setUp(self):
        super().setUp()
        self.sessions = []  # (deck_id, kind, amount)
        self.empty_session = False

        def custom_session(deck_id, kind, amount=None, now=None):
            self.sessions.append((deck_id, kind, amount))
            counts = (0, 0, 0) if self.empty_session else (0, 0, 3)
            return types.SimpleNamespace(counts=lambda: counts, kind=kind)

        self.app.scheduler.custom_session = custom_session

    def dialog(self, deck_id=None):
        from retain.dialogs import custom_study

        return self.keep(custom_study.present(self.app, self.window,
                                              deck_id or self.spanish.id))

    def test_each_choice_makes_its_session(self):
        for kind, amount in (('new', 10), ('review', 20), ('forgotten', 7), ('ahead', 1),
                             ('tag', 'verbs'), ('all', None)):
            with self.subTest(kind=kind):
                dialog = self.dialog()
                dialog.choose(kind)
                self.assertEqual(dialog.chosen, kind)
                session = dialog.start()
                self.assertEqual(self.sessions[-1], (self.spanish.id, kind, amount))
                self.assertEqual(self.window.studied[-1], (self.spanish.id, session))
                self.assertEqual(session.kind, kind)

    def test_amounts_follow_the_rows(self):
        dialog = self.dialog()
        dialog.new_row.set_value(25)
        dialog.choose('new')
        dialog.start()
        self.assertEqual(self.sessions, [(self.spanish.id, 'new', 25)])

    def test_tags_are_the_decks_own(self):
        dialog = self.dialog()
        self.assertEqual(dialog.tags, ['verbs'])
        self.assertTrue(dialog.tag_row.get_sensitive())
        other = self.dialog(self.empty.id)
        self.assertEqual(other.tags, [])
        self.assertFalse(other.tag_row.get_sensitive())
        other.choose('tag')
        self.assertFalse(other.start_button.get_sensitive())
        self.assertIsNone(other.start())
        self.assertEqual(self.sessions, [])

    def test_no_matching_cards_toasts(self):
        self.empty_session = True
        dialog = self.dialog()
        dialog.choose('forgotten')
        self.assertIsNone(dialog.start())
        self.assertEqual(self.app.toasts, [('No cards match', False)])
        self.assertEqual(self.window.studied, [])
        self.assertEqual(self.sessions, [(self.spanish.id, 'forgotten', 7)])


def _newest_dialog(over):
    """The dialog presented last over a widget (a name dialog over the options dialog)."""
    from gi.repository import Adw, Gtk

    from tests.test_today import find_all

    found = []
    for toplevel in Gtk.Window.list_toplevels():
        found.extend(dialog for dialog in find_all(toplevel, Adw.Dialog) if dialog is not over)
    return found[-1]


if __name__ == '__main__':
    unittest.main()
