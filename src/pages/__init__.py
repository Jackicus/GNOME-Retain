# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The pages: one Adw.NavigationPage per destination of the sidebar (today, browse, stats,
deck:ID), made by make_root() on the first visit, and the pushed ones (review, card
information), made where they are pushed.

    page = make_root('deck:123')       # None for an unknown key
    app()                              # the Application, for a page that needs the collection

A root page is an Adw.NavigationPage with its own Adw.ToolbarView and Adw.HeaderBar
(window.py replaces the navigation stack with it). Its module is imported by its factory,
never at the top of window.py (startup time). A page listens to `app().collection`'s
`changed` signal while it is mapped and refreshes itself from the collection: it holds no
state of its own that the collection does not.
"""

from gi.repository import Gio


def app():
    return Gio.Application.get_default()


def make_root(key):
    if key == 'today':
        from .today import TodayPage

        return TodayPage()
    if key == 'browse':
        from .browse import BrowsePage

        return BrowsePage()
    if key == 'stats':
        from .stats import StatsPage

        return StatsPage()
    if key.startswith('deck:'):
        from .deck import DeckPage

        return DeckPage(int(key[5:]))
    return None
