# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A deck's page (pages/deck.py), over test_today's stand-ins."""

import unittest
from unittest import mock

from tests import ROOT  # noqa: F401  (registers src/ as retain)
from tests.gtk import pump, wait_for
from tests.test_today import DeckPagesTestCase, find_all


class DeckPageTest(DeckPagesTestCase):

    def page(self, deck_id):
        from retain.pages.deck import DeckPage

        return self.show(DeckPage(deck_id))

    def test_shows_the_counts_and_the_subdecks(self):
        from retain.pages.today import DeckRow

        page = self.page(self.spanish.id)
        self.assertEqual(page.deck_id, self.spanish.id)
        self.assertEqual(page.get_title(), 'Spanish')
        self.assertFalse(page.path_label.get_visible())
        self.assertEqual(page.new_label.get_text(), '5')
        self.assertEqual(page.learning_label.get_text(), '0')
        self.assertEqual(page.due_label.get_text(), '0')
        self.assertFalse(page.nothing_label.get_visible())
        self.assertEqual(page.study_button.get_label(), '_Study Now')
        self.assertTrue(page.more_button.get_visible())
        self.assertTrue(page.subdecks_group.get_visible())
        rows = find_all(page.subdecks_list, DeckRow)
        self.assertEqual([row.get_title() for row in rows], ['Verbs'])
        self.assertEqual(rows[0].get_subtitle(), '2 new')
        self.assertEqual(page.options_row.get_subtitle(),
                         'Default · 20 new/day · 90% retention')
        self.assertFalse(page.description_card.get_visible())

    def test_study_now_studies_the_deck(self):
        page = self.page(self.spanish.id)
        page.study_button.emit('clicked')
        self.assertEqual(self.window.studied, [(self.spanish.id, None)])

    def test_a_subdeck_row_opens_its_page(self):
        from retain.pages.today import DeckRow

        page = self.page(self.spanish.id)
        row = find_all(page.subdecks_list, DeckRow)[0]
        page.subdecks_list.emit('row-activated', row)
        self.assertEqual(self.window.shown, [f'deck:{self.verbs.id}'])

    def test_nothing_due_offers_study_more(self):
        page = self.page(self.empty.id)
        self.assertTrue(page.nothing_label.get_visible())
        self.assertEqual(page.study_button.get_label(), 'Study _More…')
        self.assertFalse(page.more_button.get_visible())
        self.assertFalse(page.subdecks_group.get_visible())
        with mock.patch('retain.dialogs.custom_study.present') as present:
            page.study_button.emit('clicked')
        present.assert_called_once_with(self.app, self.window, self.empty.id)
        self.assertEqual(self.window.studied, [])

    def test_nested_deck_shows_its_path(self):
        page = self.page(self.verbs.id)
        self.assertEqual(page.get_title(), 'Verbs')
        self.assertTrue(page.path_label.get_visible())
        self.assertEqual(page.path_label.get_text(), 'Spanish › Verbs')

    def test_description_is_shown_as_plain_text(self):
        self.spanish.description = '<p>Everyday <b>words</b>.</p><p>Reviews &amp; new.</p>'
        self.collection.update_deck(self.spanish)
        page = self.page(self.spanish.id)
        self.assertTrue(page.description_card.get_visible())
        self.assertEqual(page.description_label.get_text(),
                         'Everyday words.\nReviews & new.')

    def test_plain_text(self):
        from retain.pages.deck import plain_text

        self.assertEqual(plain_text(''), '')
        self.assertEqual(plain_text('a<br>b<br/>c'), 'a\nb\nc')
        self.assertEqual(plain_text('<div>one</div><div>two</div>'), 'one\ntwo')
        self.assertEqual(plain_text('  spaced   out  '), 'spaced out')
        self.assertEqual(plain_text('x\n\n\n\ny'), 'x\n\ny')

    def test_edit_description_saves_through_the_collection(self):
        page = self.page(self.spanish.id)
        dialog = self.keep(page.present_description_dialog())
        dialog.view.get_buffer().set_text('A new description.')
        dialog.emit('response', 'save')
        self.assertEqual(self.collection.deck(self.spanish.id).description,
                         'A new description.')
        self.assertEqual(self.app.toasts, [('Description saved', True)])
        self.assertTrue(wait_for(
            lambda: page.description_label.get_text() == 'A new description.'))

    def test_unbury_is_offered_only_with_buried_cards(self):
        page = self.page(self.spanish.id)
        self.assertFalse(page.unbury_action.get_enabled())
        cards = self.collection.find_cards('deck:Spanish::Verbs')
        self.collection.bury(cards)
        self.assertTrue(wait_for(page.unbury_action.get_enabled))
        page.unbury_action.activate(None)
        self.assertEqual(self.collection.find_cards('is:buried'), [])
        self.assertEqual(self.app.toasts, [('Cards unburied', True)])

    def test_options_row_opens_deck_options(self):
        page = self.page(self.spanish.id)
        with mock.patch('retain.dialogs.deck_options.present') as present:
            page.options_row.emit('activated')
        present.assert_called_once_with(self.app, self.window, self.spanish.id)

    def test_follows_a_rename(self):
        page = self.page(self.verbs.id)
        self.collection.rename_deck(self.verbs.id, 'Spanish::Verbos')
        self.assertTrue(wait_for(lambda: page.get_title() == 'Verbos'))
        pump()


if __name__ == '__main__':
    unittest.main()
