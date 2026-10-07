# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The review page's remaining counts: new, learning and due, as Anki's "12 + 3 + 45" under
the deck name, each number in its colour (style.css's count-new, count-learning and
count-due) and the kind of the card shown underlined.

    counts = RetainCounts()
    counts.set_counts(new, learning, due, current='new')   # current: new, learning, due, None

The page hides the widget when the show-remaining setting is off (it binds the setting to
`visible`).
"""

from gettext import gettext as _

from gi.repository import GLib, Gtk

KINDS = ('new', 'learning', 'due')


class RetainCounts(Gtk.Box):
    __gtype_name__ = 'RetainCounts'

    def __init__(self):
        super().__init__(spacing=4, halign=Gtk.Align.CENTER)
        self.add_css_class('review-counts')
        self.add_css_class('caption')
        self._labels = {}
        for index, kind in enumerate(KINDS):
            if index:
                plus = Gtk.Label(label='+')
                plus.add_css_class('dimmed')
                self.append(plus)
            label = Gtk.Label(label='0', use_markup=True)
            label.add_css_class('numeric')
            label.add_css_class(f'count-{kind}')
            self.append(label)
            self._labels[kind] = label
        self.update_property([Gtk.AccessibleProperty.LABEL], [_('Remaining cards')])
        self.set_counts(0, 0, 0)

    def set_counts(self, new, learning, due, current=None):
        """Show the three counts, `current` (a kind, or None) underlined."""
        for kind, value in zip(KINDS, (new, learning, due), strict=True):
            text = GLib.markup_escape_text(str(value))
            self._labels[kind].set_markup(f'<u>{text}</u>' if kind == current else text)
        self.set_tooltip_text(
            _('{new} new, {learning} learning, {due} to review').format(
                new=new, learning=learning, due=due))


def kind_of(card):
    """The count a card belongs to: new, learning (learning and relearning) or due."""
    if card is None:
        return None
    if card.state == 'new':
        return 'new'
    if card.state in ('learning', 'relearning'):
        return 'learning'
    return 'due'
