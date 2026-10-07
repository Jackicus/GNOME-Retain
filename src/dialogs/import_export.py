# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Import and Export dialogs: app.import, app.export and win.deck-export.

    choose_and_import(app, parent)            # a file chooser, then present_import; None
    present_import(app, parent, path)         # -> ImportDialog, importing `path`
    present_export(app, parent, deck_id=None) # -> ExportDialog, the deck preselected

ImportDialog (import_export.blp) shows a package's options (scheduling, a deck to put it
in) or a text file's (the note type, the deck, which column fills each field, tags, HTML,
updating) with a preview of its first rows, then runs importing.py in a thread on a
Collection of its own (the same path, no backups), reporting through GLib.idle_add; Cancel
asks the import to stop (a package import checks between phases; a stopped import changed
nothing). Once done, the main collection is told what changed (`changed` for decks, notes,
cards and note types) and its undo stack cleared: an import is not an undo step. The result,
or the error, is shown in the dialog, and the sentence toasted too. `dialog.done` becomes
True when the result or the error is shown; `dialog.result` is the ImportResult.

ExportDialog is built in code: what (the collection or a deck) and the format (an Anki
package or a tab-separated text file), with the format's switches, then "Export…" asks for
the target through `_choose_target(suggested_name, done)` (a Gtk.FileDialog; a test
replaces the method) and runs exporting.py in a thread. Done, the dialog says what was
written, offers Show in Files and toasts the sentence. `dialog.done`, `dialog.result` and
`dialog.target` tell a test what happened.

Both tell the parent window when they are open (`parent.set_dialog_open`) so its keyed
actions step aside.
"""

import logging
import os
import threading
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from .. import apkg, exporting, importing
from ..collection import Collection, CollectionError

log = logging.getLogger(__name__)

PACKAGE_SUFFIXES = ('.apkg', '.colpkg')
TEXT_SUFFIXES = ('.txt', '.csv', '.tsv')
PREVIEW_ROWS = 5
PREVIEW_CELL = 40      # characters of a cell shown in the preview
PROBLEM_ROWS = 50      # problems listed in the expander before '… and N more'


def _track(dialog, parent):
    """Tell the parent window while the dialog is open, when it can be told."""
    if not hasattr(parent, 'set_dialog_open'):
        return
    dialog.connect('map', lambda *_args: parent.set_dialog_open(True))
    dialog.connect('closed', lambda *_args: parent.set_dialog_open(False))


def _string_list(strings):
    model = Gtk.StringList()
    for text in strings:
        model.append(text)
    return model


def _root_window(widget):
    root = widget.get_root() if widget is not None else None
    return root if isinstance(root, Gtk.Window) else None


def _file_filters():
    packages = Gtk.FileFilter(name=_('Anki decks and collections'))
    for suffix in PACKAGE_SUFFIXES:
        packages.add_pattern('*' + suffix)
    texts = Gtk.FileFilter(name=_('Text files'))
    for suffix in TEXT_SUFFIXES:
        texts.add_pattern('*' + suffix)
    everything = Gtk.FileFilter(name=_('All files'))
    everything.add_pattern('*')
    filters = Gio.ListStore.new(Gtk.FileFilter)
    for item in (packages, texts, everything):
        filters.append(item)
    return filters


def choose_and_import(app, parent):
    """Ask for a file to import, then open the Import dialog on it. Returns None: the
    dialog comes once a file is chosen."""
    chooser = Gtk.FileDialog(title=_('Import'), modal=True)
    filters = _file_filters()
    chooser.set_filters(filters)
    chooser.set_default_filter(filters.get_item(0))

    def on_chosen(_chooser, result):
        try:
            file = chooser.open_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                app.report(error)
            return
        path = file.get_path() if file is not None else None
        if path:
            present_import(app, parent, path)

    chooser.open(_root_window(parent), None, on_chosen)
    return None


def present_import(app, parent, path):
    dialog = ImportDialog(app, parent, path)
    dialog.present(parent)
    return dialog


def present_export(app, parent, deck_id=None):
    dialog = ExportDialog(app, parent, deck_id=deck_id)
    dialog.present(parent)
    return dialog


def _shorten(text, limit=PREVIEW_CELL):
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _kind_of(path):
    """'package' for an Anki deck or collection, 'text' for anything else."""
    return 'package' if path.lower().endswith(PACKAGE_SUFFIXES) else 'text'


@Gtk.Template(resource_path='/io/github/jackicus/Retain/import_export.ui')
class ImportDialog(Adw.Dialog):
    __gtype_name__ = 'RetainImportDialog'

    stack = Gtk.Template.Child()
    file_row = Gtk.Template.Child()
    package_group = Gtk.Template.Child()
    scheduling_row = Gtk.Template.Child()
    deck_row = Gtk.Template.Child()
    text_group = Gtk.Template.Child()
    notetype_row = Gtk.Template.Child()
    text_deck_row = Gtk.Template.Child()
    tags_row = Gtk.Template.Child()
    html_row = Gtk.Template.Child()
    update_row = Gtk.Template.Child()
    columns_group = Gtk.Template.Child()
    preview_group = Gtk.Template.Child()
    preview_label = Gtk.Template.Child()
    error_label = Gtk.Template.Child()
    progress_label = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()
    done_icon = Gtk.Template.Child()
    done_title = Gtk.Template.Child()
    done_body = Gtk.Template.Child()
    problems_group = Gtk.Template.Child()
    problems_row = Gtk.Template.Child()
    import_button = Gtk.Template.Child()
    cancel_button = Gtk.Template.Child()
    close_button = Gtk.Template.Child()

    def __init__(self, app, parent, path):
        super().__init__()
        self._app = app
        self.path = str(path)
        self.kind = _kind_of(self.path)
        self.done = False       # the result or the error is shown
        self.result = None      # the ImportResult, once done
        self.error = None       # the error's sentence, when it failed
        self._stop = threading.Event()
        self._working = False
        self._close_when_done = False
        self._deck_ids = []        # the deck combo rows' choices, by index
        self._deck_names = []
        self._notetypes = []       # the note type row's choices, by index
        self._column_rows = []     # (field index, Adw.ComboRow)
        self._columns = 0
        self._notetype_handler = None
        _track(self, parent)
        self.connect('close-attempt', self._on_close_attempt)
        self.import_button.connect('clicked', lambda *_args: self.start())
        self.cancel_button.connect('clicked', lambda *_args: self.cancel())
        self.close_button.connect('clicked', lambda *_args: self.close())
        self.file_row.set_subtitle(os.path.basename(self.path))
        self._fill_decks()
        if self.kind == 'package':
            self.file_row.set_title(_('Anki collection')
                                    if self.path.lower().endswith('.colpkg')
                                    else _('Anki deck'))
            self.text_group.set_visible(False)
            self.columns_group.set_visible(False)
            self.preview_group.set_visible(False)
        else:
            self.file_row.set_title(_('Text file'))
            self.package_group.set_visible(False)
            self._fill_notetypes()
            self._preview()
            self._notetype_handler = self.notetype_row.connect(
                'notify::selected', lambda *_args: self._preview())

    # -- the setup page ----------------------------------------------------------------------

    def _fill_decks(self):
        decks = self._app.collection.decks()
        names = [deck.name for deck in decks]
        self._deck_ids = [deck.id for deck in decks]
        self._deck_names = names
        self.deck_row.set_model(_string_list([_('As in the file')] + names))
        self.text_deck_row.set_model(_string_list(names))

    def _fill_notetypes(self):
        self._notetypes = self._app.collection.notetypes()
        self.notetype_row.set_model(_string_list([nt.name for nt in self._notetypes]))

    def _selected_notetype(self):
        index = self.notetype_row.get_selected()
        if 0 <= index < len(self._notetypes):
            return self._notetypes[index]
        return None

    def _preview(self):
        """Read the text file's first rows: the column rows for the note type chosen, the
        preview, or the error when it cannot be read."""
        notetype = self._selected_notetype()
        fields = notetype.fields if notetype else []
        try:
            preview = importing.preview_text(self.path, fields, limit=PREVIEW_ROWS)
        except (apkg.PackageError, OSError) as error:
            self._show_setup_error(str(error))
            return
        self.error_label.set_visible(False)
        self.import_button.set_sensitive(True)
        rows = preview['rows']
        self._columns = preview['columns']
        self.html_row.set_active(bool(preview['info'].get('html')))
        first = rows[0] if rows else []
        choices = [_('Skip')]
        for column in range(self._columns):
            sample = _shorten(first[column]) if column < len(first) else ''
            if sample:
                # Translators: a column of a text file, with its first value.
                choices.append(_('Column {number}: {value}').format(number=column + 1,
                                                                    value=sample))
            else:
                choices.append(_('Column {number}').format(number=column + 1))
        for _index, row in self._column_rows:
            self.columns_group.remove(row)
        self._column_rows = []
        for index, field in enumerate(fields):
            row = Adw.ComboRow(title=field['name'])
            row.set_model(_string_list(choices))
            suggested = preview['suggested_map'][index]
            row.set_selected(0 if suggested is None else suggested + 1)
            self.columns_group.add(row)
            self._column_rows.append((index, row))
        lines = [' · '.join(_shorten(cell) for cell in row) for row in rows]
        self.preview_label.set_text('\n'.join(lines) if lines else _('The file is empty.'))
        self.import_button.set_sensitive(bool(rows) and bool(fields))

    def _show_setup_error(self, message):
        self.error_label.set_text(message)
        self.error_label.set_visible(True)
        self.import_button.set_sensitive(False)

    def field_map(self):
        """The column chosen for each field of the note type (None: skipped)."""
        field_map = []
        for _index, row in self._column_rows:
            selected = row.get_selected()
            field_map.append(None if selected <= 0 else selected - 1)
        return field_map

    # -- running -----------------------------------------------------------------------------

    def start(self):
        """Run the import in a thread, on a collection of its own."""
        if self._working or self.done:
            return
        if self.kind == 'package':
            index = self.deck_row.get_selected()  # into_deck is a deck's name
            into_deck = self._deck_names[index - 1] if index > 0 else None
            options = {'with_scheduling': self.scheduling_row.get_active(),
                       'into_deck': into_deck}
        else:
            notetype = self._selected_notetype()
            deck_index = self.text_deck_row.get_selected()
            if notetype is None or not (0 <= deck_index < len(self._deck_ids)):
                return
            options = {'notetype_id': notetype.id, 'deck_id': self._deck_ids[deck_index],
                       'field_map': self.field_map(),
                       'tags': self.tags_row.get_text().split(),
                       'allow_html': self.html_row.get_active(),
                       'update_existing': self.update_row.get_active()}
        self._working = True
        self._stop.clear()
        self.set_can_close(False)
        self.import_button.set_visible(False)
        self.cancel_button.set_visible(self.kind == 'package')
        self.progress_bar.set_fraction(0.0)
        self.progress_label.set_text(_('Importing…'))
        self.stack.set_visible_child_name('working')
        thread = threading.Thread(target=self._work, args=(options,), name='retain-import',
                                  daemon=True)
        thread.start()

    def cancel(self):
        """Ask the import to stop: it rolls back and reports itself stopped."""
        if self._working:
            self._stop.set()
            self.cancel_button.set_sensitive(False)
            self.progress_label.set_text(_('Stopping…'))

    def _work(self, options):
        main = self._app.collection
        collection = None
        try:
            collection = Collection(main.path, day_start_hour=main.day_start_hour,
                                    backups=False)
            if self.kind == 'package':
                result = importing.import_package(collection, self.path,
                                                  progress=self._progress,
                                                  should_stop=self._stop.is_set, **options)
            else:
                result = importing.import_text(collection, self.path,
                                               progress=self._progress, **options)
        except (apkg.PackageError, CollectionError, OSError) as error:
            GLib.idle_add(self._failed, str(error))
        except Exception as error:  # the dialog must come back whatever went wrong
            log.exception('importing %s', self.path)
            GLib.idle_add(self._failed, _('Something went wrong: {error}').format(error=error))
        else:
            GLib.idle_add(self._finished, result)
        finally:
            if collection is not None:
                collection.close()

    def _progress(self, fraction, message):
        GLib.idle_add(self._show_progress, fraction, message)

    def _show_progress(self, fraction, message):
        if self._working and not self._stop.is_set():
            self.progress_bar.set_fraction(fraction)
            self.progress_label.set_text(message)
        return GLib.SOURCE_REMOVE

    def _finished(self, result):
        self._working = False
        self.result = result
        collection = self._app.collection
        if not result.stopped:
            collection.clear_undo()
            for kind in ('notetypes', 'decks', 'notes', 'cards'):
                collection.emit('changed', kind)
        sentence = importing.describe_result(result)
        if result.stopped:
            self._show_done('process-stop-symbolic', 'warning', _('Import Stopped'), sentence)
        elif result.problems:
            self._show_done('dialog-warning-symbolic', 'warning', _('Imported'), sentence)
            self._list_problems(result.problems)
        else:
            self._show_done('emblem-ok-symbolic', 'success', _('Imported'), sentence)
        self._app.toast(sentence)
        return GLib.SOURCE_REMOVE

    def _failed(self, message):
        self._working = False
        self.error = message
        self._show_done('dialog-error-symbolic', 'error', _('Import Failed'), message)
        return GLib.SOURCE_REMOVE

    def _show_done(self, icon_name, style, title, body):
        for old in ('success', 'warning', 'error'):
            self.done_icon.remove_css_class(old)
        self.done_icon.add_css_class(style)
        self.done_icon.set_from_icon_name(icon_name)
        self.done_title.set_text(title)
        self.done_body.set_text(body)
        self.cancel_button.set_visible(False)
        self.close_button.set_visible(True)
        self.stack.set_visible_child_name('done')
        self.set_can_close(True)
        self.done = True
        if self._close_when_done:
            self.force_close()

    def _list_problems(self, problems):
        count = len(problems)
        self.problems_row.set_title(ngettext('{} Problem', '{} Problems', count).format(count))
        for problem in problems[:PROBLEM_ROWS]:
            row = Adw.ActionRow(title=problem, title_lines=3)
            row.add_css_class('property')
            self.problems_row.add_row(row)
        if count > PROBLEM_ROWS:
            more = count - PROBLEM_ROWS
            self.problems_row.add_row(Adw.ActionRow(
                title=ngettext('… and {} more', '… and {} more', more).format(more)))
        self.problems_group.set_visible(True)

    def _on_close_attempt(self, *_args):
        """Closing while the import runs (can-close is off then) asks it to stop; the dialog
        closes once it has."""
        self._close_when_done = True
        if self.kind == 'package':
            self.cancel()


# -- Export --------------------------------------------------------------------------------

FORMAT_PACKAGE, FORMAT_TEXT = 0, 1


class ExportDialog(Adw.Dialog):
    """See the module docstring. Built in code: a Gtk.Stack of setup, working and done."""

    __gtype_name__ = 'RetainExportDialog'

    def __init__(self, app, parent, deck_id=None):
        super().__init__(title=_('Export'), content_width=420)
        self._app = app
        self.done = False
        self.result = None     # export_package()'s dict, or {'notes': n} for a text file
        self.target = None     # the path written
        self.error = None
        self._working = False
        self._deck_ids = []
        _track(self, parent)
        self._build()
        self._fill_decks(deck_id)
        self._update_format()

    def _build(self):
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.stack = Gtk.Stack(vhomogeneous=False,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        view.set_content(self.stack)
        self.set_child(view)

        # Setup: what, the format and its switches.
        group = Adw.PreferencesGroup()
        self.what_row = Adw.ComboRow(title=_('What'))
        group.add(self.what_row)
        self.format_row = Adw.ComboRow(title=_('Format'))
        self.format_row.set_model(_string_list([_('Anki deck (.apkg)'),
                                                _('Text (.txt, tab-separated)')]))
        self.format_row.connect('notify::selected', lambda *_args: self._update_format())
        group.add(self.format_row)
        self.scheduling_row = Adw.SwitchRow(
            title=_('Include Scheduling'),
            subtitle=_('Each card’s progress and review history'), active=True)
        group.add(self.scheduling_row)
        self.media_row = Adw.SwitchRow(
            title=_('Include Media'),
            subtitle=_('The images and sounds the notes refer to'), active=True)
        group.add(self.media_row)
        self.tags_row = Adw.SwitchRow(title=_('Include Tags'),
                                      subtitle=_('A last column with each note’s tags'),
                                      active=True)
        group.add(self.tags_row)
        setup = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=12,
                        margin_bottom=24, margin_start=12, margin_end=12)
        setup.append(group)
        clamp = Adw.Clamp(maximum_size=600, tightening_threshold=400, child=setup)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True, child=clamp)
        self.stack.add_named(scrolled, 'setup')

        # Working: a spinner and what is happening.
        working = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          valign=Gtk.Align.CENTER, margin_top=36, margin_bottom=36,
                          margin_start=24, margin_end=24)
        working.append(Adw.Spinner(width_request=48, height_request=48,
                                   halign=Gtk.Align.CENTER))
        self.progress_label = Gtk.Label(label=_('Exporting…'), wrap=True,
                                        justify=Gtk.Justification.CENTER)
        self.progress_label.add_css_class('title-4')
        working.append(self.progress_label)
        self.stack.add_named(working, 'working')

        # Done: the sentence and Show in Files.
        done = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=36,
                       margin_bottom=24, margin_start=24, margin_end=24)
        self.done_icon = Gtk.Image(icon_name='emblem-ok-symbolic', pixel_size=48,
                                   accessible_role=Gtk.AccessibleRole.PRESENTATION)
        done.append(self.done_icon)
        self.done_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.done_title.add_css_class('title-2')
        done.append(self.done_title)
        self.done_body = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                   selectable=True)
        done.append(self.done_body)
        self.show_button = Gtk.Button(label=_('_Show in Files'), use_underline=True,
                                      halign=Gtk.Align.CENTER, margin_top=12)
        self.show_button.add_css_class('pill')
        self.show_button.connect('clicked', lambda *_args: self.show_in_files())
        done.append(self.show_button)
        self.stack.add_named(done, 'done')

        # The bottom bar: one button per state.
        bar = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=24,
                      margin_start=24, margin_end=24)
        self.export_button = Gtk.Button(label=_('_Export…'), use_underline=True)
        self.export_button.add_css_class('pill')
        self.export_button.add_css_class('suggested-action')
        self.export_button.connect('clicked', lambda *_args: self.start())
        bar.append(self.export_button)
        self.close_button = Gtk.Button(label=_('_Close'), use_underline=True, visible=False)
        self.close_button.add_css_class('pill')
        self.close_button.connect('clicked', lambda *_args: self.close())
        bar.append(self.close_button)
        view.add_bottom_bar(bar)

    def _fill_decks(self, deck_id):
        decks = self._app.collection.decks()
        self._deck_ids = [None] + [deck.id for deck in decks]
        self.what_row.set_model(_string_list([_('Whole collection')]
                                             + [deck.name for deck in decks]))
        if deck_id in self._deck_ids:
            self.what_row.set_selected(self._deck_ids.index(deck_id))

    def _update_format(self):
        package = self.format_row.get_selected() == FORMAT_PACKAGE
        self.scheduling_row.set_visible(package)
        self.media_row.set_visible(package)
        self.tags_row.set_visible(not package)

    @property
    def deck_id(self):
        index = self.what_row.get_selected()
        return self._deck_ids[index] if 0 <= index < len(self._deck_ids) else None

    @property
    def is_package(self):
        return self.format_row.get_selected() == FORMAT_PACKAGE

    def suggested_name(self):
        """'Spanish.apkg', 'Retain collection.txt': the deck's last name part, or the app's."""
        deck = self._app.collection.deck(self.deck_id) if self.deck_id is not None else None
        stem = deck.basename if deck else _('Retain collection')
        stem = stem.replace('/', '-').strip() or _('Retain collection')
        return stem + ('.apkg' if self.is_package else '.txt')

    def _choose_target(self, suggested_name, done):
        """Ask where to write, then done(path) (None when dismissed). A test replaces this
        with one that answers at once."""
        chooser = Gtk.FileDialog(title=_('Export'), modal=True, initial_name=suggested_name)
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads and os.path.isdir(downloads):
            chooser.set_initial_folder(Gio.File.new_for_path(downloads))
        if self.is_package:
            file_filter = Gtk.FileFilter(name=_('Anki deck'))
            file_filter.add_pattern('*.apkg')
        else:
            file_filter = Gtk.FileFilter(name=_('Text files'))
            for suffix in TEXT_SUFFIXES:
                file_filter.add_pattern('*' + suffix)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        chooser.set_filters(filters)
        chooser.set_default_filter(file_filter)

        def on_chosen(_chooser, result):
            try:
                file = chooser.save_finish(result)
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self._app.report(error)
                done(None)
                return
            done(file.get_path() if file is not None else None)

        chooser.save(_root_window(self), None, on_chosen)

    def start(self):
        """Ask for the target, then export in a thread."""
        if self._working or self.done:
            return
        self._choose_target(self.suggested_name(), self._on_target)

    def _on_target(self, path):
        if not path or self._working or self.done:
            return
        self.target = str(path)
        options = {'deck_id': self.deck_id}
        if self.is_package:
            options.update(with_scheduling=self.scheduling_row.get_active(),
                           include_media=self.media_row.get_active())
        else:
            options.update(include_tags=self.tags_row.get_active())
        self._working = True
        self.set_can_close(False)
        self.export_button.set_visible(False)
        self.stack.set_visible_child_name('working')
        thread = threading.Thread(target=self._work, args=(self.is_package, options),
                                  name='retain-export', daemon=True)
        thread.start()

    def _work(self, package, options):
        main = self._app.collection
        collection = None
        try:
            collection = Collection(main.path, day_start_hour=main.day_start_hour,
                                    backups=False)
            if package:
                result = exporting.export_package(collection, self.target,
                                                  progress=self._progress, **options)
            else:
                count = exporting.export_text(collection, self.target, **options)
                result = {'notes': count}
        except (apkg.PackageError, CollectionError, OSError) as error:
            GLib.idle_add(self._failed, str(error))
        except Exception as error:
            log.exception('exporting to %s', self.target)
            GLib.idle_add(self._failed, _('Something went wrong: {error}').format(error=error))
        else:
            GLib.idle_add(self._finished, result)
        finally:
            if collection is not None:
                collection.close()

    def _progress(self, _fraction, message):
        GLib.idle_add(self._show_progress, message)

    def _show_progress(self, message):
        if self._working:
            self.progress_label.set_text(message)
        return GLib.SOURCE_REMOVE

    def _finished(self, result):
        self._working = False
        self.result = result
        count = result.get('notes', 0)
        name = os.path.basename(self.target)
        sentence = ngettext('Exported {count} note to {name}', 'Exported {count} notes to {name}',
                            count).format(count=count, name=name)
        missing = result.get('missing') or []
        body = sentence
        if missing:
            body += '\n' + ngettext('{} referenced media file was not found',
                                    '{} referenced media files were not found',
                                    len(missing)).format(len(missing))
        self._show_done('emblem-ok-symbolic', 'success', _('Exported'), body)
        self.show_button.set_visible(True)
        self._app.toast(sentence)
        return GLib.SOURCE_REMOVE

    def _failed(self, message):
        self._working = False
        self.error = message
        self._show_done('dialog-error-symbolic', 'error', _('Export Failed'), message)
        self.show_button.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _show_done(self, icon_name, style, title, body):
        for old in ('success', 'warning', 'error'):
            self.done_icon.remove_css_class(old)
        self.done_icon.add_css_class(style)
        self.done_icon.set_from_icon_name(icon_name)
        self.done_title.set_text(title)
        self.done_body.set_text(body)
        self.close_button.set_visible(True)
        self.stack.set_visible_child_name('done')
        self.set_can_close(True)
        self.done = True

    def show_in_files(self):
        """Open the folder the file was written to, the file selected."""
        if not self.target:
            return
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(self.target))

        def on_opened(_launcher, result):
            try:
                launcher.open_containing_folder_finish(result)
            except GLib.Error as error:
                self._app.report(error)

        launcher.open_containing_folder(_root_window(self), None, on_opened)
