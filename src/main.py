# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The application: what the app is made of, its app.* actions and its lifecycle.

    app = Application(version, app_id, base_id, profile)   # the launcher's main()
    app.run(sys.argv)

do_handle_local_options reads --demo first; do_startup opens the collection (collection.py,
in the data directory default_data_dir() names, or RETAIN_DATA_DIR; `--demo` uses build/demo,
an invented collection, never the real one) and makes the Scheduler over it, and starts the
AnkiConnect API (ankiconnect.py) when card-mining apps are allowed (the ankiconnect-enabled
setting; a port already taken, by Anki itself, is toasted when the switch is turned on);
do_activate builds the Window (imported only then) and, with a sync folder set and
sync-automatically on, syncs (sync.py, `app.sync` the SyncRunner); closing the window syncs
again first (sync_before_closing(), the window hidden meanwhile). `sync_now()` is Sync Now:
it toasts what the sync brought, or reports why it failed.
`.apkg` and `.colpkg` files given on the command line (or opened from Files) open the import
dialog. app.* actions: add, new-deck, import, export, undo, preferences, shortcuts, about,
quit. Every message for the user goes through toast(); every error through report(), which
toasts a sentence and logs the rest. Quitting closes the collection, which backs it up.
"""

import logging
import os
import pathlib
import sys
from gettext import gettext as _
from gettext import ngettext

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .collection import Collection, CollectionError, default_data_dir  # noqa: E402
from .scheduler import Scheduler  # noqa: E402
from .shortcuts import ACCELS  # noqa: E402
from .sync import SyncRunner, describe_result  # noqa: E402

log = logging.getLogger(__name__)

CLOSING_SYNC_TIMEOUT = 60  # seconds a closing window waits for its sync at most
RESOURCE_PATH = '/io/github/jackicus/Retain'
PACKAGE_SUFFIXES = ('.apkg', '.colpkg')


class Application(Adw.Application):
    """The app. `demo` is true under --demo: the collection is build/demo's."""

    def __init__(self, version, app_id, base_id, profile):
        super().__init__(
            application_id=app_id,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
            resource_base_path=RESOURCE_PATH,
        )
        self.version = version
        self.base_id = base_id
        self.profile = profile
        self.demo = False
        self.data_dir = None
        self.settings = None  # in do_startup: GSettings
        self.collection = None  # in do_startup
        self.scheduler = None  # in do_startup
        self.ankiconnect = None  # ankiconnect.Server while card-mining apps are allowed
        self.sync = None  # in do_startup: sync.SyncRunner
        self._sync_quiet = False  # the running sync toasts only news (an automatic one)
        self._closing = None  # the window waiting for its closing sync
        self._pending_files = []
        self.add_main_option('demo', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _('Show an invented collection (build/demo), not yours'), None)
        self.add_main_option('debug', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _('Log what the app does'), None)
        self.add_main_option('version', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _('Print the version and exit'), None)

    # -- lifecycle ---------------------------------------------------------------------------

    def do_handle_local_options(self, options):
        """The options, before startup (so --demo decides which collection opens)."""
        if options.contains('version'):
            sys.stdout.write(f'retain {self.version}\n')
            return 0
        if options.contains('debug'):
            logging.getLogger('retain').setLevel(logging.DEBUG)
        if options.contains('demo'):
            self.demo = True
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.settings = Gio.Settings.new(self.base_id)
        self.data_dir = self._data_dir()
        self.collection = Collection(self.data_dir / 'collection.sqlite',
                                     day_start_hour=self.settings.get_int('day-start-hour'))
        self.scheduler = Scheduler(self.collection,
                                   self.settings.get_int('learn-ahead-minutes'))
        self.settings.connect('changed::day-start-hour', self._on_day_start_changed)
        self.settings.connect('changed::learn-ahead-minutes', self._on_learn_ahead_changed)
        self.settings.connect('changed::ankiconnect-enabled', self._on_ankiconnect_changed)
        self.settings.connect('changed::ankiconnect-key', self._on_ankiconnect_key_changed)
        self.sync = SyncRunner(self.settings, self.collection)
        self.sync.connect('finished', self._on_sync_finished)
        self._add_actions()
        for name, accels in ACCELS.items():
            self.set_accels_for_action(name, accels)
        self._load_css()
        if self.settings.get_boolean('ankiconnect-enabled'):
            self.start_ankiconnect(quiet=True)

    def _data_dir(self):
        if self.demo:
            override = os.environ.get('RETAIN_DATA_DIR')
            if override:
                return pathlib.Path(override)
            demo = _demo_dir()
            if demo is None or not (demo / 'collection.sqlite').exists():
                sys.exit(_('--demo needs a demo collection: run scripts/demo_collection.py '
                           'or set RETAIN_DATA_DIR'))
            return demo
        return default_data_dir(self.profile)

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            from .window import Window

            window = Window(application=self)
            if self.settings.get_boolean('sync-automatically') and not self.demo:
                self.sync_now(quiet=True)
        window.present()

    def do_open(self, files, _hint):
        self.do_activate()
        packages = [file for file in files
                    if (file.get_basename() or '').lower().endswith(PACKAGE_SUFFIXES)]
        for file in files:
            if file not in packages:
                self.toast(_('Retain opens Anki decks (.apkg) and collections (.colpkg).'))
        for file in packages:
            self.import_file(file)

    def do_shutdown(self):
        self.stop_ankiconnect()
        if self.collection is not None:
            try:
                self.collection.close()
            except Exception:
                log.exception('closing the collection')
            self.collection = None
        Adw.Application.do_shutdown(self)

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_resource(RESOURCE_PATH + '/style.css')
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _on_day_start_changed(self, settings, key):
        self.collection.day_start_hour = settings.get_int(key)
        self.collection.unbury_past()
        self.collection.emit('changed', 'cards')

    def _on_learn_ahead_changed(self, settings, key):
        self.scheduler.learn_ahead_minutes = settings.get_int(key)

    # -- actions ---------------------------------------------------------------------------

    # -- sync (sync.py) ----------------------------------------------------------------------

    def sync_now(self, quiet=False):
        """Sync with the sync folder in a thread; False when none is set. A quiet sync (the
        automatic one) toasts only when something came in."""
        if not self.sync.running:
            self._sync_quiet = quiet
        elif not quiet:
            self._sync_quiet = False
        return self.sync.start()

    def _on_sync_finished(self, _runner, result, error):
        quiet, self._sync_quiet = self._sync_quiet, False
        if self._closing is not None:
            if error:
                log.warning('sync before closing failed: %s', error)
            return
        if error:
            # Translators: before the reason a sync failed.
            self.report(CollectionError(error), context=_('Not synced'))
        elif result is not None and (not quiet or result.changed() or result.problems):
            self.toast(describe_result(result))

    def sync_before_closing(self, window):
        """Sync before the window closes, when automatic sync is on: the window hides and is
        destroyed once the sync is done (or after CLOSING_SYNC_TIMEOUT). True when the close
        must wait."""
        if self._closing is not None:
            return True
        if (self.demo or not self.settings.get_boolean('sync-automatically')
                or not self.sync.folder):
            return False
        self._closing = window
        window.set_visible(False)
        handlers = []

        def finish(*_args):
            if self.sync.running or self.sync.again:
                return GLib.SOURCE_REMOVE  # the closing sync is the one after this
            for handler in handlers:
                self.sync.disconnect(handler)
            handlers.clear()
            if self._closing is window:
                self._closing = None
                window.destroy()
            return GLib.SOURCE_REMOVE

        def finish_anyway():
            if self._closing is window:
                log.warning('the closing sync took too long; closing without it')
                self.sync.again = False
                for handler in handlers:
                    self.sync.disconnect(handler)
                handlers.clear()
                self._closing = None
                window.destroy()
            return GLib.SOURCE_REMOVE

        handlers.append(self.sync.connect('finished', finish))
        self.sync_now(quiet=True)
        GLib.timeout_add_seconds(CLOSING_SYNC_TIMEOUT, finish_anyway)
        return True

    # -- card-mining apps (ankiconnect.py) ---------------------------------------------------

    def start_ankiconnect(self, quiet=False):
        """Answer AnkiConnect requests; when the port is taken (Anki is open), say so."""
        from . import ankiconnect

        if self.ankiconnect is not None:
            return
        api = ankiconnect.Api(self.collection, browse=self._browse_for_api,
                              edit=self._edit_for_api, added=self._added_by_api,
                              key=self.settings.get_string('ankiconnect-key'))
        server = ankiconnect.Server(api)
        try:
            server.start()
        except OSError as error:
            log.warning('AnkiConnect API not started: %s', error)
            if not quiet:
                self.toast(_('Port {port} is in use: is Anki open?').format(
                    port=ankiconnect.PORT))
            return
        self.ankiconnect = server

    def stop_ankiconnect(self):
        if self.ankiconnect is not None:
            self.ankiconnect.stop()
            self.ankiconnect = None

    def _on_ankiconnect_changed(self, settings, key):
        if settings.get_boolean(key):
            self.start_ankiconnect()
        else:
            self.stop_ankiconnect()

    def _on_ankiconnect_key_changed(self, settings, key):
        if self.ankiconnect is not None:
            self.ankiconnect.api.key = settings.get_string(key)

    def _browse_for_api(self, query):
        self.do_activate()
        self.get_active_window().browse(query)

    def _edit_for_api(self, note_id):
        from .dialogs import add_edit

        self.do_activate()
        add_edit.present_edit(self, self.get_active_window(), note_id)

    def _added_by_api(self, note_ids):
        text = ngettext('Note added by another app', '{n} notes added by another app',
                        len(note_ids)).format(n=len(note_ids))
        self.toast(text, undo=True)

    def _add_actions(self):
        for name, callback in (
                ('add', self.on_add), ('new-deck', self.on_new_deck),
                ('import', self.on_import), ('export', self.on_export),
                ('undo', self.on_undo), ('preferences', self.on_preferences),
                ('shortcuts', self.on_shortcuts), ('about', self.on_about),
                ('quit', self.on_quit)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
        self.lookup_action('undo').set_enabled(False)
        self.collection.connect('changed', lambda *_args: self._update_undo())

    def _update_undo(self):
        self.lookup_action('undo').set_enabled(self.collection.can_undo())

    def on_add(self, *_args):
        from .dialogs import add_edit

        window = self.get_active_window()
        add_edit.present_add(self, window, deck_id=window.current_deck_id() if window else None)

    def on_new_deck(self, *_args):
        from .dialogs import deck_name

        deck_name.present_new_deck(self, self.get_active_window())

    def on_import(self, *_args):
        from .dialogs import import_export

        import_export.choose_and_import(self, self.get_active_window())

    def import_file(self, file):
        """Open the import dialog for a package file (a Gio.File)."""
        from .dialogs import import_export

        import_export.present_import(self, self.get_active_window(), file.get_path())

    def on_export(self, *_args):
        from .dialogs import import_export

        window = self.get_active_window()
        import_export.present_export(self, window,
                                     deck_id=window.current_deck_id() if window else None)

    def on_undo(self, *_args):
        self.undo()

    def undo(self):
        """Put the newest change back and say what it was; False when there was none."""
        label = self.collection.undo()
        if label is None:
            return False
        window = self.get_active_window()
        if window is not None:
            window.undone(label)
        # Translators: a toast after Ctrl+Z; {action} is what was undone ("Delete Deck").
        self.toast(_('Undone: {action}').format(action=label))
        return True

    def on_preferences(self, *_args):
        from .dialogs import preferences

        preferences.present(self, self.get_active_window())

    def on_shortcuts(self, *_args):
        from .dialogs import shortcuts

        shortcuts.present(self.get_active_window())

    def on_about(self, *_args):
        from .dialogs import about

        about.present(self, self.get_active_window())

    def on_quit(self, *_args):
        window = self.get_active_window()
        if window is not None:
            window.close()
        else:
            self.quit()

    # -- messages ----------------------------------------------------------------------------

    def toast(self, text, undo=False, timeout=0):
        """Show a toast on the window; with `undo`, with an Undo button (app.undo)."""
        window = self.get_active_window()
        if window is None:
            log.info('toast without a window: %s', text)
            return None
        toast = Adw.Toast(title=text, timeout=timeout)
        if undo:
            toast.set_button_label(_('Undo'))
            toast.set_action_name('app.undo')
        window.add_toast(toast)
        return toast

    def report(self, error, context=None):
        """Tell the user an error in a sentence and log the rest."""
        if isinstance(error, CollectionError):
            message = str(error)
        else:
            message = _('Something went wrong: {error}').format(error=error)
        if context:
            message = f'{context}: {message}'
        log.warning('reported: %s', message, exc_info=not isinstance(error, CollectionError))
        self.toast(message)


def _demo_dir():
    """build/demo of the source tree the running module sits in, when it is one."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / 'meson.build').exists() and (parent / 'scripts').is_dir():
            return parent / 'build' / 'demo'
    return None


def main(version, app_id, base_id, profile):
    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s', stream=sys.stderr)
    logging.getLogger('retain').setLevel(logging.INFO)
    app = Application(version, app_id, base_id, profile)
    return app.run(sys.argv)
