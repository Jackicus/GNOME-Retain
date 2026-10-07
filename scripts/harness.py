# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The developer scripts' shared start: the installed app, on its own, off the desktop's
settings, on the demo collection.

screenshot.py runs the installed build (meson install -C build, or scripts/run.sh, first)
in-process. make_app() does what the scripts need:

- the installed modules on sys.path (build/install/share/retain) and the translations bound;
- GSettings on the memory backend with the installed schema, so nothing a script sets reaches
  the desktop's settings;
- the demo collection: build/demo, generated first when it is missing (scripts/demo_collection.py),
  unless RETAIN_DATA_DIR names another; the app runs with --demo;
- gi's versions, the gresource registered, main.Application under an app ID of its own
  (io.github.jackicus.Retain.<suffix>, NON_UNIQUE: beside a running app);
- at startup: the colour scheme forced dark (or light), animations off, stock GNOME's icon
  theme and font (Adwaita, Adwaita Sans 11) instead of the desktop's; the installed icons
  findable; each window made non-resizable, a fixed size a tiling window manager leaves alone.

Call make_app() before importing Gtk: importing it starts GTK, which may read the settings
schemas once and for all.
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = os.path.join(ROOT, 'build', 'install')
PKGDATADIR = os.path.join(PREFIX, 'share', 'retain')
GRESOURCE = os.path.join(PKGDATADIR, 'retain.gresource')
SCHEMA_DIR = os.path.join(PREFIX, 'share', 'glib-2.0', 'schemas')
LOCALEDIR = os.path.join(PREFIX, 'share', 'locale')
ICONS = os.path.join(PREFIX, 'share', 'icons')
DEMO_DIR = os.path.join(ROOT, 'build', 'demo')
BASE_ID = 'io.github.jackicus.Retain'


def ensure_demo_collection():
    """Generate build/demo when it is missing and RETAIN_DATA_DIR names no other."""
    if os.environ.get('RETAIN_DATA_DIR') or os.path.exists(
            os.path.join(DEMO_DIR, 'collection.sqlite')):
        return
    subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'demo_collection.py'),
                    '--data-dir', DEMO_DIR], check=True)


def project_version():
    try:
        with open(os.path.join(ROOT, 'build', 'meson-info', 'intro-projectinfo.json'),
                  encoding='utf-8') as file:
            return json.load(file)['version']
    except (OSError, ValueError, KeyError):
        return '0.0.0'


def installed_icon():
    apps = os.path.join(ICONS, 'hicolor', 'scalable', 'apps')
    names = sorted(os.listdir(apps)) if os.path.isdir(apps) else []
    return next((name.removesuffix('.svg') for name in names
                 if name.startswith(BASE_ID) and name.endswith('.svg')), None)


def require_install():
    if not os.path.exists(GRESOURCE):
        sys.exit(f'{os.path.basename(sys.argv[0])}: no installed build in {PREFIX}: '
                 'run meson install -C build first')


def make_app(suffix, light=False, animations=False, stock_look=True, size=None, name=None):
    """The installed app's main.Application on the demo collection, not yet run."""
    require_install()
    ensure_demo_collection()
    os.environ['GSETTINGS_SCHEMA_DIR'] = SCHEMA_DIR
    os.environ['GSETTINGS_BACKEND'] = 'memory'  # never the desktop's settings
    os.environ.setdefault('RETAIN_DATA_DIR', DEMO_DIR)
    sys.path.insert(1, PKGDATADIR)
    from retain import i18n

    i18n.setup(LOCALEDIR)

    import gi

    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    Gio.Resource.load(GRESOURCE)._register()
    from retain import main

    name = name or suffix.lower()
    GLib.set_prgname(name)
    app = main.Application(project_version(), f'{BASE_ID}.{suffix}', BASE_ID, 'default')
    app.demo = True
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    app.harness_argv = [name, '--demo']

    def on_startup(_app):
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_LIGHT if light else Adw.ColorScheme.FORCE_DARK)
        settings = Gtk.Settings.get_default()
        if not animations:
            settings.set_property('gtk-enable-animations', False)
        if stock_look:
            settings.set_property('gtk-icon-theme-name', 'Adwaita')
            settings.set_property('gtk-font-name', 'Adwaita Sans 11')
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(ICONS)
        if size is not None:
            app.settings.set_int('window-width', size[0])
            app.settings.set_int('window-height', size[1])

    def on_window_added(_app, window):
        window.set_resizable(False)

    app.connect('startup', on_startup)
    app.connect('window-added', on_window_added)
    return app


def run_app(app, *options):
    return app.run(app.harness_argv + list(options))


def descendants(widget, kind=None):
    """Every widget under `widget` (itself included), depth first, of `kind` when given."""
    if kind is None or isinstance(widget, kind):
        yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child, kind)
        child = child.get_next_sibling()


def popovers(widget):
    from gi.repository import Gtk

    return [found for found in descendants(widget, Gtk.Popover) if found.get_visible()]
