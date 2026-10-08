#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Render the app's window to a PNG, for checking UI changes without a human.

    scripts/headless.sh scripts/screenshot.py [out.png] [--light] [--size WxH]
                          [--page KEY] [--study [DECK]] [--answer] [--sidebar]
                          [--dialog add|new-deck|options|custom-study|import|export|
                                    preferences|about|shortcuts|edit|notetypes|
                                    notetype|template]
                          [--search QUERY] [--scroll PX] [--setting KEY=VALUE …]

Builds nothing itself: run meson install -C build (or scripts/run.sh) first, and run it
through scripts/headless.sh so the window opens on a private display. The demo collection
(build/demo, generated when missing) is what it shows; settings go to a memory backend and
animations are off. --page is a sidebar key: today, browse, stats, or deck:NAME (a deck's
full name, "Spanish::Verbs"). --study pushes the review page of DECK (the --page's deck, or
the first deck with cards due), --answer with its answer shown. --dialog opens a dialog over
the page and shoots it (notetypes: Manage Note Types; notetype: the page of the type with
the most notes; template: its first card template's editor). --search types a query into the browser. In the narrow layout the
shot shows the sidebar, or the page when --page is given (--sidebar keeps the sidebar).
"""

import argparse
import os
import sys

import harness

parser = argparse.ArgumentParser()
parser.add_argument('out', nargs='?', default=os.path.join(harness.ROOT, 'build',
                                                            'screenshot.png'))
parser.add_argument('--light', action='store_true')
parser.add_argument('--size', default='1000x720')
parser.add_argument('--page', default='today')
parser.add_argument('--study', nargs='?', const='', metavar='DECK')
parser.add_argument('--answer', action='store_true')
parser.add_argument('--sidebar', action='store_true')
parser.add_argument('--dialog', choices=['add', 'new-deck', 'options', 'custom-study', 'import',
                                         'export', 'preferences', 'about', 'shortcuts', 'edit',
                                         'notetypes', 'notetype', 'template'])
parser.add_argument('--search', metavar='QUERY')
parser.add_argument('--scroll', metavar='PX', type=int, default=0)
parser.add_argument('--setting', metavar='KEY=VALUE', action='append', default=[])
args = parser.parse_args()
width, height = (int(n) for n in args.size.split('x'))

app = harness.make_app('Screenshot', light=args.light, size=(width, height))

from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402  (after make_app)

steps = []
failed = False


def resolve_page(window):
    key = args.page
    if key.startswith('deck:') and not key[5:].isdigit():
        deck = app.collection.deck_by_name(key[5:])
        if deck is None:
            sys.exit(f'screenshot: no deck called {key[5:]}')
        key = f'deck:{deck.id}'
    window.show_root(key)


def study(window):
    name = args.study
    if name:
        deck = app.collection.deck_by_name(name)
        deck_id = deck.id if deck else None
    else:
        deck_id = window.current_deck_id()
    if deck_id is None:
        for node in app.scheduler.tree():
            if node.total:
                deck_id = node.deck.id
                break
    if deck_id is None:
        sys.exit('screenshot: no deck to study')
    page = window.study(deck_id)
    if args.answer and hasattr(page, 'show_answer'):
        GLib.timeout_add(600, lambda: (page.show_answer(), GLib.SOURCE_REMOVE)[1])


def open_dialog(window):
    actions = {'add': 'app.add', 'new-deck': 'app.new-deck', 'import': 'app.import',
               'export': 'app.export', 'preferences': 'app.preferences',
               'about': 'app.about', 'shortcuts': 'app.shortcuts',
               'options': 'win.deck-options', 'custom-study': None, 'edit': None,
               'notetypes': 'app.notetypes', 'notetype': None, 'template': None}
    name = actions[args.dialog]
    if args.dialog == 'custom-study':
        from retain.dialogs import custom_study

        custom_study.present(app, window, window.current_deck_id())
    elif args.dialog == 'edit':
        from retain.dialogs import add_edit

        note_id = app.collection.find_notes('deck:Spanish')[0]
        add_edit.present_edit(app, window, note_id)
    elif args.dialog in ('notetype', 'template'):
        from retain.dialogs import notetypes

        busiest = max(app.collection.notetypes(),
                      key=lambda notetype: app.collection.notetype_use(notetype.id))
        dialog = notetypes.present(app, window, busiest.id)
        if args.dialog == 'template':
            dialog.navigation_view.get_visible_page().show_template(0)
    elif args.dialog == 'import' and os.environ.get('RETAIN_IMPORT_FILE'):
        from retain.dialogs import import_export

        import_export.present_import(app, window, os.environ['RETAIN_IMPORT_FILE'])
    elif name.startswith('app.'):
        app.activate_action(name[4:])
    else:
        window.activate_action(name)


def search(window):
    page = window.navigation_view.get_visible_page()
    if hasattr(page, 'search'):
        page.search(args.search)


def scroll(window):
    page = window.navigation_view.get_visible_page()
    for scrolled in harness.descendants(page, Gtk.ScrolledWindow):
        if scrolled.props.vscrollbar_policy != Gtk.PolicyType.NEVER:
            adjustment = scrolled.get_vadjustment()
            adjustment.set_value(min(args.scroll,
                                     adjustment.get_upper() - adjustment.get_page_size()))
            return


def dialog_window(window):
    """The dialog's own window when libadwaita gave it one, else the window."""
    for toplevel in Gtk.Window.list_toplevels():
        if toplevel is not window and toplevel.get_visible() and toplevel.get_mapped():
            if any(True for _ in harness.descendants(toplevel, Adw.Dialog)):
                return toplevel
    return window


def draw_popovers(window, snapshot):
    window_x, window_y = window.get_surface_transform()
    for popover in harness.popovers(window):
        if not popover.get_mapped():
            continue
        surface = popover.get_surface()
        popover_x, popover_y = popover.get_surface_transform()
        point = Graphene.Point()
        point.x = surface.get_position_x() + popover_x - window_x
        point.y = surface.get_position_y() + popover_y - window_y
        snapshot.save()
        snapshot.translate(point)
        Gtk.WidgetPaintable(widget=popover).snapshot(
            snapshot, popover.get_width(), popover.get_height())
        snapshot.restore()


def shoot():
    global failed
    try:
        return _shoot()
    except Exception:
        import traceback

        traceback.print_exc()
        failed = True
        app.quit()
        return GLib.SOURCE_REMOVE


def _shoot():
    window = app.get_active_window()
    if steps:
        step, delay = steps.pop(0)
        step(window)
        GLib.timeout_add(delay, shoot)
        return GLib.SOURCE_REMOVE
    target = dialog_window(window)
    paintable = Gtk.WidgetPaintable(widget=target)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, target.get_width(), target.get_height())
    draw_popovers(target, snapshot)
    texture = target.get_renderer().render_texture(snapshot.to_node(), None)
    texture.save_to_png(args.out)
    print(args.out)
    app.quit()
    return GLib.SOURCE_REMOVE


def on_activate(_app):
    GLib.idle_add(plan)  # after do_activate has made the window


def plan():
    window = app.get_active_window()
    for setting in args.setting:
        key, _sep, value = setting.partition('=')
        app.settings.set_value(key, GLib.Variant.parse(None, value, None, None))
    steps.append((resolve_page, 400))
    if window.split_view.get_collapsed() and args.sidebar:
        steps.append((lambda w: w.split_view.set_show_content(False), 300))
    if args.study is not None:
        steps.append((study, 1500))
    if args.search:
        steps.append((search, 1200))
    if args.scroll:
        steps.append((scroll, 600))
    if args.dialog:
        steps.append((open_dialog, 1200))
    GLib.timeout_add(800, shoot)
    return GLib.SOURCE_REMOVE


app.connect('activate', on_activate)
harness.run_app(app)
if failed:
    sys.exit(1)
