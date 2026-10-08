# Not done yet

Retain 0.1.0 was built in one day (2026-10-07). What it does is in docs/user-guide.md; this is
what it does not do yet, roughly in the order it should be done.

- **Sync, what is left.** The folder sync (sync.py) merges per card and per note, not per
  field; graves are never pruned; a device that stops syncing stays in the folder (and in Other
  Devices) until its files are deleted by hand, with no Forget button; a sync's merge runs in one
  transaction that can make the main window wait on a large collection. AnkiWeb is out of reach
  (no public API).
- **Note type editor.** Note types come from the stock set and from imports; there is no
  dialog to add fields or edit templates and CSS (the collection supports it:
  `update_notetype`). Needed: a Manage Note Types dialog with a template editor and preview.
- **Accessibility pass.** No `a11y_check.py` walkthrough yet (Music Sleeve has one); Orca has
  not read the review page. The WebKit card face has no accessible tree: an "Read Card Aloud"
  or a text mirror for screen readers is worth adding.
- **MathJax.** `\(…\)` in cards stays as written (no network, nothing bundled). Bundling
  MathJax in the gresource would make maths decks render.
- **Audio recording** and TTS (`{{tts}}` renders the plain field).
- **Filtered decks** proper (Anki's) are replaced by custom study sessions; an imported
  filtered deck's cards return to their home decks.
- **Flathub.** The manifest in build-aux/flatpak is untested; the app ID and metainfo are
  ready. CI runs the tests on Arch under Xvfb only.
- **Performance** on very large collections (100k cards) is untested: the browser caps at
  5,000 rows per search, the sidebar counts query each deck.
- **Translations:** the strings are marked; no catalogue exists yet.
