---
paths:
  - "src/**/*.blp"
  - "src/window.py"
  - "src/sidebar.py"
  - "src/pages/**"
  - "src/widgets/**"
  - "src/dialogs/**"
---

# GTK 4.22, libadwaita 1.9 and PyGObject 3.56: behaviour this code depends on

Each of these was checked against the toolkit, or found the hard way while building the app.

## Widgets and lifetimes

- A widget that can be dropped (a pushed page, a dialog, a row) connects a child's or an
  owned object's signal through `widgets.util.connect_weak(obj, signal, self._method)`, never
  `obj.connect(signal, self._method)`: the cycle through C keeps the widget alive for ever.
  Follow a widget's lifetime with a GObject weak reference (`obj.weak_ref()`), not `weakref`:
  PyGObject drops a widget's Python wrapper while the widget lives and makes a new one.
- A Python subclass of `Gtk.TextChildAnchor` crashes on insert (GTK builds the anchor's
  segment in `gtk_text_child_anchor_new`, which `g_object_new` skips): use a plain anchor and
  hang a Python attribute on it (widgets/html_text.py's `Media`); PyGObject keeps it alive
  through a toggle reference.
- The value `Gsk.RoundedRect().init_from_rect(...)` returns wraps a dead temporary (garbage
  bounds, nothing drawn, pixman warnings): keep the instance and call `init_from_rect` on it.
- A Python `do_measure` on a widget with a layout manager is never called: set the layout
  manager to None and implement both `do_measure` and `do_size_allocate` (widgets/charts.py,
  widgets/occlusion_editor.py).
- Reparenting a widget during allocation (a breakpoint's `apply` handler moving the toggle
  group into a bottom bar) leaves a stale frame: do it from an idle (pages/stats.py).
- `Adw.NavigationView` refuses a second page with the same tag: the review page has none,
  since Study More… pushes a custom session's page over a deck's.
- `Adw.Dialog` opens as a bottom sheet only in a window of at most 450×360 px; at 360×640 it
  is a window of its own (which a screenshot of the main window misses: scripts/screenshot.py
  shoots the dialog's window). Keyed window actions are disabled while a dialog is open
  (`window.set_dialog_open`), so a dialog's entry gets its keys.
- `Adw.Sidebar` (1.9) cannot nest or indent: a subdeck's title carries em spaces. Its
  `setup-menu` signal says which item a section's `menu-model` opens on.
- A `Gtk.Builder` cannot load a `.ui` that declares a template, so a menu a page shares is
  built in code (sidebar.py's deck menu), not pulled out of window.ui.
- WebKitGTK's view must be non-focusable (`can-focus` and `focusable` off), or Space and the
  number keys never reach the review page's key controller; the controller runs in the
  capture phase on the page. The view's background is transparent and its colours come from
  a user style sheet rebuilt on `notify::dark` (widgets/card_view.py). Without WebKit, or with
  `RETAIN_NO_WEBKIT=1` (CI: bubblewrap cannot start in the container), a Pango label renders
  the card.
- In headless screenshots the accent colour is green (no Settings portal), so the accent and
  the success colour look alike there; on a desktop they differ.

## Lists

- `Gtk.ColumnView` sorts by re-querying the collection with an SQL `ORDER BY`, not through a
  `Gtk.Sorter` over the model: the browser may hold tens of thousands of rows.
- A `Gtk.MultiSelection` loses its selection when the model is refilled: the browser keeps
  the selected card ids and selects them again after a refresh.
