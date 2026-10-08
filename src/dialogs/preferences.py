# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Preferences dialog: app.preferences (Ctrl+,).

    present(app, parent) -> PreferencesDialog

One page, General (preferences.blp). Reviewing: the review page's switches (show-remaining,
show-intervals, auto-play-audio, read-aloud, two-button-mode) and the card's text size
(card-text-scale, shown as a percentage). Day: the hour a new day starts (day-start-hour)
and how many minutes a learning card may be shown early (learn-ahead-minutes). Other Apps:
whether card-mining apps may add notes through the AnkiConnect API (ankiconnect-enabled),
and the key they must send (ankiconnect-key). Collection: where the collection is, with Open
Folder; the backups, with Back Up Now (Collection.backup(force=True)) and the folder; and
Check Media, which counts the files no note refers to and the files notes refer to that are
missing, in an alert that offers to delete the unused ones. Sync (sync.py): the folder
(sync-folder, chosen through the file chooser portal; choosing one syncs at once), this
computer's name for the others (sync-device-name, the host name until changed), Sync Now
(app.sync_now(), with a spinner and the sync's progress in place of the last sync's time)
and Sync Automatically (sync-automatically); Other Devices lists the devices that synced
through the folder, from their devices/ID.json, with when each last did.

The switches are bound to their settings with Gio.Settings.bind; the spin rows by hand,
since the keys are integers (or a factor) and the rows show doubles. The bindings are let go
when the dialog closes.
"""

import logging
import os
import time
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from .. import sync

log = logging.getLogger(__name__)

# The switch rows bound one to one to their settings: (template child, key).
SWITCHES = (
    ('remaining_row', 'show-remaining'),
    ('intervals_row', 'show-intervals'),
    ('sound_row', 'auto-play-audio'),
    ('read_aloud_row', 'read-aloud'),
    ('two_button_row', 'two-button-mode'),
    ('ankiconnect_row', 'ankiconnect-enabled'),
    ('auto_row', 'sync-automatically'),
)
# The spin rows: (template child, key, the row's value for the setting's 1).
SPINS = (
    ('text_size_row', 'card-text-scale', 100.0),
    ('day_start_row', 'day-start-hour', 1.0),
    ('learn_ahead_row', 'learn-ahead-minutes', 1.0),
)
LISTED_NAMES = 8   # media file names listed in the Check Media alert, per kind


def present(app, parent):
    dialog = PreferencesDialog(app, parent)
    dialog.present(parent)
    return dialog


@Gtk.Template(resource_path='/io/github/jackicus/Retain/preferences.ui')
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = 'RetainPreferencesDialog'

    remaining_row = Gtk.Template.Child()
    intervals_row = Gtk.Template.Child()
    sound_row = Gtk.Template.Child()
    read_aloud_row = Gtk.Template.Child()
    two_button_row = Gtk.Template.Child()
    text_size_row = Gtk.Template.Child()
    day_start_row = Gtk.Template.Child()
    learn_ahead_row = Gtk.Template.Child()
    ankiconnect_row = Gtk.Template.Child()
    ankiconnect_key_row = Gtk.Template.Child()
    location_row = Gtk.Template.Child()
    open_button = Gtk.Template.Child()
    backups_row = Gtk.Template.Child()
    backups_folder_button = Gtk.Template.Child()
    backup_button = Gtk.Template.Child()
    media_row = Gtk.Template.Child()
    media_button = Gtk.Template.Child()
    folder_row = Gtk.Template.Child()
    forget_button = Gtk.Template.Child()
    choose_button = Gtk.Template.Child()
    device_name_row = Gtk.Template.Child()
    sync_row = Gtk.Template.Child()
    sync_spinner = Gtk.Template.Child()
    sync_button = Gtk.Template.Child()
    auto_row = Gtk.Template.Child()
    devices_group = Gtk.Template.Child()

    def __init__(self, app, parent=None):
        super().__init__()
        self._app = app
        self._settings = settings = app.settings
        self._quiet = False     # a spin row is being set from its setting
        self._handlers = []     # (settings, handler id)
        if hasattr(parent, 'set_dialog_open'):
            self.connect('map', lambda *_args: parent.set_dialog_open(True))
            self.connect('closed', lambda *_args: parent.set_dialog_open(False))
        self.connect('closed', self._on_closed)

        for child, key in SWITCHES:
            settings.bind(key, getattr(self, child), 'active', Gio.SettingsBindFlags.DEFAULT)
        for child, key, scale in SPINS:
            row = getattr(self, child)
            self._set_spin(row, key, scale)
            row.connect('notify::value', self._on_spin_changed, key, scale)
            self._handlers.append((settings, settings.connect(
                'changed::' + key, lambda *_args, row=row, key=key, scale=scale:
                self._set_spin(row, key, scale))))

        settings.bind('ankiconnect-key', self.ankiconnect_key_row, 'text',
                      Gio.SettingsBindFlags.DEFAULT)

        self.open_button.connect('clicked', lambda *_args: self.open_folder(self.data_dir))
        self.backups_folder_button.connect(
            'clicked', lambda *_args: self.open_folder(self.backups_dir))
        self.backup_button.connect('clicked', lambda *_args: self.back_up())
        self.media_button.connect('clicked', lambda *_args: self.check_media())
        self.location_row.set_subtitle(self._pretty(self.data_dir))
        self._update_backups()

        settings.bind('sync-device-name', self.device_name_row, 'text',
                      Gio.SettingsBindFlags.DEFAULT)
        if not settings.get_string('sync-device-name').strip():
            self.device_name_row.set_text(sync.default_device_name())
        self.choose_button.connect('clicked', lambda *_args: self.choose_folder())
        self.forget_button.connect('clicked', lambda *_args: self.forget_folder())
        self.sync_button.connect('clicked', lambda *_args: self._app.sync_now())
        self._device_rows = []
        self._sync_message = None  # the running sync's progress
        self._handlers.append((settings, settings.connect(
            'changed::sync-folder', lambda *_args: self._update_sync())))
        runner = app.sync
        for signal, callback in (('started', self._on_sync_started),
                                 ('progress', self._on_sync_progress),
                                 ('finished', self._on_sync_finished)):
            self._handlers.append((runner, runner.connect(signal, callback)))
        self._update_sync()

    # -- settings ----------------------------------------------------------------------------

    def _read(self, key):
        value = self._settings.get_value(key)
        return value.get_double() if value.get_type_string() == 'd' else value.get_int32()

    def _set_spin(self, row, key, scale):
        self._quiet = True
        try:
            row.set_value(round(self._read(key) * scale))
        finally:
            self._quiet = False

    def _on_spin_changed(self, row, _pspec, key, scale):
        if self._quiet:
            return
        value = row.get_value() / scale
        if self._settings.get_value(key).get_type_string() == 'd':
            if abs(value - self._settings.get_double(key)) > 1e-9:
                self._settings.set_double(key, value)
        elif int(round(value)) != self._settings.get_int(key):
            self._settings.set_int(key, int(round(value)))

    def _on_closed(self, _dialog):
        for child, _key in SWITCHES:
            Gio.Settings.unbind(getattr(self, child), 'active')
        Gio.Settings.unbind(self.ankiconnect_key_row, 'text')
        Gio.Settings.unbind(self.device_name_row, 'text')
        for source, handler in self._handlers:
            source.disconnect(handler)
        self._handlers = []

    # -- the collection ----------------------------------------------------------------------

    @property
    def data_dir(self):
        return self._app.collection.path.parent

    @property
    def backups_dir(self):
        return self.data_dir / 'backups'

    @staticmethod
    def _pretty(path):
        """The path with the home folder as ~."""
        path, home = str(path), os.path.expanduser('~')
        if home and path.startswith(home + os.sep):
            return '~' + path[len(home):]
        return path

    def _backups(self):
        return sorted(self.backups_dir.glob('collection-*.sqlite')) if (
            self.backups_dir.is_dir()) else []

    def _update_backups(self):
        backups = self._backups()
        if not backups:
            self.backups_row.set_subtitle(_('No backups yet'))
            return
        newest = time.localtime(backups[-1].stat().st_mtime)
        count = ngettext('{} backup', '{} backups', len(backups)).format(len(backups))
        # Translators: {count} is "3 backups", {when} a date and time.
        self.backups_row.set_subtitle(_('{count}, the newest from {when}').format(
            count=count, when=time.strftime('%x %H:%M', newest)))

    def open_folder(self, path):
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(str(path)))
        root = self.get_root()

        def on_launched(_launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error as error:
                self._app.report(error)

        launcher.launch(root if isinstance(root, Gtk.Window) else None, None, on_launched)

    def back_up(self):
        """A backup now, whatever the last one's age; the toast names the file."""
        try:
            path = self._app.collection.backup(force=True)
        except OSError as error:
            self._app.report(error)
            return None
        self._update_backups()
        self._app.toast(_('Backed up to {name}').format(name=path.name))
        return path

    # -- sync --------------------------------------------------------------------------------

    def choose_folder(self):
        """Ask for the sync folder (the portal's folder chooser), then sync with it."""
        dialog = Gtk.FileDialog(title=_('Choose a Sync Folder'), modal=True)
        folder = self._settings.get_string('sync-folder')
        if folder:
            dialog.set_initial_folder(Gio.File.new_for_path(folder))
        root = self.get_root()

        def on_chosen(_dialog, result):
            try:
                chosen = dialog.select_folder_finish(result)
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self._app.report(error)
                return
            if chosen is not None and chosen.get_path():
                self.set_folder(chosen.get_path())

        dialog.select_folder(root if isinstance(root, Gtk.Window) else None, None, on_chosen)
        return dialog

    def set_folder(self, path):
        """Sync through `path` from now on, starting now."""
        self._settings.set_string('sync-folder', str(path))
        self._app.sync_now()

    def forget_folder(self):
        self._settings.set_string('sync-folder', '')

    def _on_sync_started(self, _runner):
        self._sync_message = _('Syncing…')
        self._update_sync()

    def _on_sync_progress(self, _runner, _fraction, message):
        self._sync_message = message
        self._update_sync()

    def _on_sync_finished(self, _runner, _result, _error):
        self._sync_message = None
        self._update_sync()

    def _update_sync(self):
        folder = self._settings.get_string('sync-folder')
        running = self._app.sync.running
        self.folder_row.set_subtitle(self._pretty(folder) if folder else _('None chosen'))
        self.forget_button.set_visible(bool(folder))
        for row in (self.device_name_row, self.sync_row, self.auto_row):
            row.set_sensitive(bool(folder))
        self.sync_button.set_sensitive(bool(folder) and not running)
        self.sync_spinner.set_visible(running)
        if running:
            self.sync_row.set_subtitle(self._sync_message or _('Syncing…'))
        else:
            last = self._app.collection.get(sync.LAST_KEY)
            self.sync_row.set_subtitle(_when(last) if last else _('Never'))
        self._update_devices(folder)

    def _update_devices(self, folder):
        for row in self._device_rows:
            self.devices_group.remove(row)
        self._device_rows = []
        self.devices_group.set_visible(bool(folder))
        if not folder:
            return
        own = (self._app.collection.get(sync.DEVICE_KEY) or {}).get('id')
        found = sync.devices(folder, own)
        self.devices_group.set_description(
            None if found else _('None yet: choose the same folder in Retain on your other '
                                 'computers.'))
        for device in found:
            # Translators: {when} is a date and time.
            row = Adw.ActionRow(use_markup=False,
                                title=device['name'] or _('Unnamed Device'),
                                subtitle=_('Last synced {when}').format(
                                    when=_when(device['synced'])))
            row.set_title_lines(1)
            self.devices_group.add(row)
            self._device_rows.append(row)

    def check_media(self):
        """Count the unused and the missing media files and say so in an alert, with
        Delete Unused when there are unused ones."""
        collection = self._app.collection
        referenced = collection.referenced_media()
        unused = [name for name in collection.media.names() if name not in referenced]
        missing = collection.media.missing(referenced)
        body = self._media_report(unused, missing)
        dialog = Adw.AlertDialog(heading=_('Media Checked'), body=body)
        dialog.add_response('close', _('_Close'))
        if unused:
            dialog.add_response('delete', _('_Delete Unused'))
            dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('close')
        dialog.set_close_response('close')
        dialog.connect('response', self._on_media_response, referenced)
        dialog.present(self)
        self.media_alert = dialog
        return dialog

    @staticmethod
    def _media_report(unused, missing):
        parts = []
        if not unused and not missing:
            return _('Every media file is used, and every file the notes refer to is there.')
        if unused:
            count = len(unused)
            parts.append(ngettext('{} file is used by no note', '{} files are used by no note',
                                  count).format(count) + ': ' + _list_names(unused))
        if missing:
            count = len(missing)
            parts.append(ngettext('{} file the notes refer to is missing',
                                  '{} files the notes refer to are missing',
                                  count).format(count) + ': ' + _list_names(missing))
        return '\n\n'.join(part + '.' for part in parts)

    def _on_media_response(self, _dialog, response, referenced):
        if response != 'delete':
            return
        try:
            removed = self._app.collection.media.remove_unused(referenced)
        except OSError as error:
            self._app.report(error)
            return
        count = len(removed)
        self._app.toast(ngettext('Removed {} unused file', 'Removed {} unused files',
                                 count).format(count))


def _when(timestamp):
    return time.strftime('%x %H:%M', time.localtime(timestamp))


def _list_names(names):
    shown = ', '.join(names[:LISTED_NAMES])
    if len(names) > LISTED_NAMES:
        more = len(names) - LISTED_NAMES
        shown += ' ' + ngettext('and {} more', 'and {} more', more).format(more)
    return shown
