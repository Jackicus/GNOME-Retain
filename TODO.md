# Not done yet

What Retain does is in docs/user-guide.md; this is what it does not do yet, roughly in the
order it should be done.

- **Flathub.** The manifests build and run on GNOME 51 and pass the linter, except that the
  app ID needs the repository renamed to `Retain`. The steps are in
  build-aux/flatpak/FLATHUB.md.
- **Sync, what is left.**
  - Merges are per card and per note, not per field.
  - Graves are never pruned.
  - A device that stops syncing stays in the folder (and in Other Devices) until its files are
    deleted by hand: there is no Forget button.
  - A merge runs in one write transaction, during which a write from the main window waits on
    SQLite's busy timeout (an import holds one too, and AnkiConnect's writes fail while it
    runs).
  - Stock note types get a different id on each device, so a grave or rename of one reaches a
    device that joins later only once the ids have met; fixed ids for the stock types would
    end that.
  - Not yet tried: Syncthing or Nextcloud between two real machines, a folder granted through
    the Flatpak's document portal, the close-time sync in the real app, a very large
    collection.
  - AnkiWeb is out of reach (no public API).
- **Accessibility pass.** Orca has not read the review page. The WebKit card face has no
  accessible tree; Read Aloud (V) speaks it, but a text mirror for screen readers is worth
  adding.
- **MathJax.** `\(…\)` in cards stays as written (no network, nothing bundled). Bundling
  MathJax in the gresource would make maths decks render.
- **Audio recording.**
- **Filtered decks** proper (Anki's) are replaced by custom study sessions; an imported
  filtered deck's cards return to their home decks.
- **Exports** are written in place: a failure part way leaves a truncated file where an older
  export may have been. (A temporary file and a rename would not work through the Flatpak's
  file chooser portal, which grants the one file.)
- **Card templates** typed into Manage Note Types are lost, with only a toast on the closed
  dialog, when saving them fails as the dialog closes (a change that would leave notes
  without a card, say).
- **Performance** on very large collections (100k cards) is untested: the browser caps at
  5,000 rows per search, the sidebar counts query each deck, and CSV previews read the whole
  file on the main thread.
- **Translations:** the strings are marked; no catalogue exists yet.
