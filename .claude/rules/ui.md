---
paths:
  - "src/**/*.blp"
  - "src/window.py"
  - "src/sidebar.py"
  - "src/pages/**"
  - "src/widgets/**"
  - "src/dialogs/**"
  - "src/style.css"
---

# Window, pages, widgets and dialogs

Platform behaviour worth knowing is in `gtk-notes.md`.

- The window's seams (window.py): `show_root(key)`, `browse(query)`, `push(page)`, `pop()`,
  `study(deck_id, session=None)`, `current_deck_id()`, `forget_deck(id)`, `add_toast(toast)`,
  `set_dialog_open(bool)`, `set_menu_deck(id)`, `undone(label)`; the app's (main.py):
  `app.collection`, `app.scheduler`, `app.settings`, `app.toast(text, undo=False)`,
  `app.report(error)`, `app.undo()`, `app.import_file(gio_file)`. A page finds the app through
  `pages.app()`. Dialog modules expose `present*(app, parent, …)` functions returning the
  dialog; a dialog calls `parent.set_dialog_open()` on map and close.
- A root page (today, browse, stats, deck:ID) is an `Adw.NavigationPage` with its own
  `Adw.ToolbarView` and `Adw.HeaderBar`, made by `pages.make_root()` on the first visit and
  kept by the window; it refreshes from `app.collection`'s `changed` signal while mapped
  (connected weakly on map, disconnected on unmap, debounced) and holds no state the
  collection does not. The review page is pushed (`window.study`) and freed when popped.
- Every change to the collection goes through its undoable methods and shows a toast with
  Undo (`app.toast(text, undo=True)`); deleting a deck or a note type asks first.
- libadwaita widgets and style classes before CSS (`card`, `boxed-list`, `pill`, `title-1` to
  `title-4`, `heading`, `caption`, `dimmed`, `numeric`, `accent`, `success`, `warning`,
  `error`); CSS only in `src/style.css`, one commented block per widget; no hard-coded colours
  except the seven flag colours (a flag is a colour). The three counts use the `count-new`,
  `count-learning` and `count-due` classes.
- Every page fits a 360 px wide window (a breakpoint in the page's `Adw.BreakpointBin`).
  Icon-only buttons have `tooltip-text`; decorative images are `accessible-role: presentation`;
  charts are role IMG with a description of their data.
- Review keys, the browser's and the editor's are the pages' own key controllers
  (`shortcuts.REVIEW`, `BROWSE`, `EDITOR`), never application accelerators.
- Build, install and screenshot: `meson compile -C build && meson install -C build --quiet &&
  scripts/headless.sh scripts/screenshot.py build/NAME.png --light --page KEY` (also
  `--study DECK [--answer]`, `--dialog NAME`, `--search QUERY`, `--scroll PX`,
  `--size 360x640`), then look at the PNG with the Read tool. Sessions building side by side
  wrap that in `flock build/.lock sh -c '…'`.
