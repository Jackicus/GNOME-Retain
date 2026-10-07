# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The About dialog (app.about): built from the app's metainfo, which the gresource carries
(metainfo.xml), so its name, developer, links, licence and release notes are the metainfo's;
with the debug information a bug report wants (versions, the collection's size, never its
contents).

    about.present(app, parent)
"""

import platform
from gettext import gettext as _

import gi
from gi.repository import Adw, GLib, Gtk

METAINFO = '/io/github/jackicus/Retain/metainfo.xml'
DEBUG_INFO_FILENAME = 'retain-debug-info.txt'
DEVELOPERS = ['Jack Tully']


def release_version(version):
    """The release a build's version belongs to: '0.1.0' for '0.1.0-1a2b3c4'."""
    return version.partition('-')[0]


def debug_info(app):
    gtk = f'{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}'
    adw = f'{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}'
    glib = f'{GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}'
    try:
        gi.require_version('WebKit', '6.0')
        from gi.repository import WebKit

        webkit = (f'{WebKit.get_major_version()}.{WebKit.get_minor_version()}.'
                  f'{WebKit.get_micro_version()}')
    except (ValueError, ImportError):
        webkit = 'not available'
    collection = app.collection
    lines = [
        f'Retain {app.version} ({app.get_application_id()})',
        f'Python {platform.python_version()}, PyGObject {gi.__version__}',
        f'GTK {gtk}, libadwaita {adw}, GLib {glib}, WebKitGTK {webkit}',
        f'Collection: {collection.note_count()} notes, {collection.card_count()} cards'
        + (' (demo)' if app.demo else ''),
    ]
    return '\n'.join(lines) + '\n'


def build(app):
    about = Adw.AboutDialog.new_from_appdata(METAINFO, release_version(app.version))
    about.set_version(app.version)
    about.set_application_icon(app.get_application_id())
    about.set_developers(DEVELOPERS)
    about.set_copyright('© 2026 Jack Tully')
    about.set_comments(_('Flashcards with spaced repetition, for GNOME. Opens and writes '
                         'Anki decks; schedules with FSRS.'))
    # Translators: your names, one per line (with an address if you like), for the About
    # dialog's credits.
    credits = _('translator-credits')
    if credits != 'translator-credits':
        about.set_translator_credits(credits)
    about.set_debug_info(debug_info(app))
    about.set_debug_info_filename(DEBUG_INFO_FILENAME)
    return about


def present(app, parent):
    about = build(app)
    about.present(parent)
    return about
