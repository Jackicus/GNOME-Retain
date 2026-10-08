# Retain

A flashcard app with spaced repetition for GNOME, meant to replace Anki desktop for GNOME
users: Python and PyGObject, GTK 4 and libadwaita, Blueprint for the UI, SQLite for the
collection, Meson, gettext. GPL-2.0-or-later. App ID `io.github.jackicus.Retain`
(`io.github.jackicus.Retain.Devel` with `-Dprofile=development`), resource base path
`/io/github/jackicus/Retain`, gettext domain and binary `retain`. It should feel like a GNOME
core app (one window, pages, the HIG followed), carry Anki's substance (FSRS, note types, cloze,
image occlusion, .apkg in and out, search syntax, statistics) and drop its clutter.

Developed on GTK 4.22, libadwaita 1.9, Python 3.14 and PyGObject 3.56. The minimums are Python
3.12 (`pyproject.toml`) and, in `meson.build`, GTK 4.20, GLib 2.84, libadwaita 1.9, PyGObject
3.50, Meson 1.2: use no newer API without raising them there and in README.md. This file holds
rules and pointers: a module's API is in its docstring, product decisions and their reasons in
`docs/decisions.md`, the research behind them in `docs/research.md`.

## Architecture

One thread runs GTK; SQLite work is quick enough to run on it. Long work (an import, the FSRS
optimizer) runs in a thread with a connection of its own and reports through `GLib.idle_add`.

```
Application (main.py)    app.settings, app.collection; app.* actions; app.toast(), app.undo()
├─ Window (window.py)    AdwToastOverlay > AdwNavigationSplitView; sidebar.py fills the
│  │                     AdwSidebar (Today, Browse, Statistics; then the deck tree)
│  ├─ content            AdwNavigationView: a sidebar item replaces the stack with its root page
│  │                     (pages/: today, deck, browse, stats); Study pushes pages/review.py
│  └─ dialogs/           add_edit (the note editor), deck_options, custom_study, import/export,
│                        preferences, about, shortcuts
├─ Collection (collection.py)   the SQLite collection (schema.py): decks, note types, notes,
│                        cards, revlog, media, config; the undo stack; signals on change
├─ Scheduler (scheduler.py)     what to show next and what an answer does (fsrs.py is the
│                        memory model; deck_config.py the per-deck settings; days.py the day)
├─ template.py           Anki's card templates: {{Field}}, cloze, hints, type-in, conditionals
├─ search.py             Anki's search syntax to SQL over schema.py's tables
├─ apkg.py               .apkg/.colpkg in and out (legacy and zstd packages), CSV/TSV in
├─ ankiconnect.py        AnkiConnect's API on 127.0.0.1:8765 for Yomitan and co. (a thread
│                        serving HTTP; the actions run on the GTK thread through idle_add)
├─ media.py              the media folder: adding files, naming, references in fields
├─ stats.py              the queries behind Statistics
└─ optimizer.py          fits FSRS parameters to the revlog (a thread)
```

Data lives in `$XDG_DATA_HOME/retain/` (`collection.sqlite`, `media/`, `backups/`); the .Devel
build in `retain-devel/`; `RETAIN_DATA_DIR` overrides both (the tests set it to a temporary
directory, `--demo` to build/demo). GSettings: one schema for both builds.

## Rules

- **Model code has no GTK**: collection.py, scheduler.py, fsrs.py, template.py, search.py,
  apkg.py, ankiconnect.py, media.py, stats.py, days.py, deck_config.py and optimizer.py import
  GLib/GObject at most, and are tested without a display. Pages and widgets call them; they never reach into
  widgets.
- **Everything undoable**: a change to the collection goes through `Collection.undoable(label)`
  so `app.undo()` (Ctrl+Z) can put it back; a destructive action shows a toast with Undo, not a
  confirmation, except deleting a deck or a note type (an `AdwAlertDialog`).
- **Blueprint 0.22** (`template $RetainName: Parent { … }`); a `.blp` compiles to a `.ui` of the
  same bare name, flat in build/src, listed in src/meson.build and src/retain.gresource.xml
  (tests/test_build_lists.py checks the lists, and po/POTFILES.in).
- **libadwaita first**: its widgets and style classes (`card`, `boxed-list`, `pill`, `title-1`
  to `title-4`, `heading`, `caption`, `dimmed`, `numeric`, `accent`, `success`, `warning`,
  `error`) before CSS; CSS only in `src/style.css`; no hard-coded colours (the accent is the
  user's). Every page fits a 360 px wide window.
- **Strings**: `from gettext import gettext as _` in Python, `_("…")` in Blueprint; header
  capitalization for labels and menu items, sentence case for descriptions; a menu item that
  opens a dialog ends in `…`. The app's name in a string is "Retain"; "Anki" names the other
  app's format ("Anki deck (.apkg)").
- **Actions and shortcuts**: `app.*` in main.py, `win.*` in window.py; every shortcut goes in
  `src/shortcuts.py`, which feeds the accelerators and the Keyboard Shortcuts dialog
  (tests/test_shortcuts.py). Review keys (Space, 1–4, E, S, B, F, Ctrl+Z…) are the review
  page's own key controller, never application accelerators.
- **Style**: `ruff check .` clean (`pyproject.toml`: 100 columns, single quotes). 4-space
  indents, no type annotations, a docstring where a module or function is not obvious, comments
  that describe the code as it is. Every source file starts with the two SPDX lines
  (tests/test_spdx.py). `log = logging.getLogger(__name__)`; no `print` in `src/`.
- **Tests**: stdlib `unittest` in `tests/test_<module>.py`, each starting with
  `from tests import ROOT` (it registers `src/` as `retain` and isolates settings and data).
  Logic is tested with a `Collection` on a temporary path (`tests/support.py`'s
  `temporary_collection()`); widget tests use `tests/gtk.py`'s `@requires_gtk` and import
  template modules inside the test. Fixtures are invented: no real decks, names or IDs.
- **Privacy**: the repository is public; no real collection data in code, tests, fixtures,
  docs or screenshots. `scripts/run.sh --demo` and the scripts use build/demo, never the real
  collection.

## More

- `.claude/rules/ui.md` (the window's seams, pages, dialogs, style) and `gtk-notes.md` (toolkit
  behaviour found the hard way), loaded when the Read tool reads a file of that area; working
  through a shell, read them first.
- `docs/`: `user-guide.md` (for users), `decisions.md` (settled choices and why),
  `research.md` (what was read before designing). `TODO.md`: what is not done.
- `scripts/`: `check.sh`, `run.sh [--demo]`, `headless.sh`, `screenshot.py`, `harness.py`,
  `demo_collection.py` (the invented collection in build/demo, for screenshots and --demo).

## Verifying a change

1. `scripts/check.sh` passes (byte-compile, ruff, meson build, unit tests, data validation).
   CI runs it in an Arch Linux container under Xvfb.
2. Anything visible: `scripts/headless.sh scripts/screenshot.py build/x.png --page PAGE`
   (also `--light`, `--size 360x640`), each looked at with the Read tool.
3. `docs/user-guide.md` describes what the app does; a change that makes a line there or here
   wrong fixes it in the same commit.
