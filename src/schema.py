# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The collection's SQLite schema: one file, collection.sqlite, in the data directory, with
the media files beside it in media/.

Every module that reads the database (collection.py, search.py, stats.py, apkg.py) names
these tables and columns. Times are Unix seconds unless named _ms; days are day numbers
(`days.day_number()`: the proleptic Gregorian ordinal of the local date, the day starting at
the day-start hour). A card's `due` depends on its state: a new card's is its position in the
new queue, a learning or relearning card's the Unix time of its next step, a review card's
the day number it is due. `state` is one of new, learning, review, relearning. `left` is the
number of learning (or relearning) steps still to pass. `buried` is 0 or the day number the
card was buried on (it comes back the next day); `suspended` 0 or 1; `flag` 0 (none) to 7,
Anki's colours. A note's `fields` are joined with \\x1f, its `tags` space-separated with a
space at each end (so ' tag ' matches whole tags). `marked` is the note's star.

The revlog keeps every review: `rating` 1 Again, 2 Hard, 3 Good, 4 Easy (0 for a manual
change: forget, set due); `state` the card's state before the review; `elapsed_days` the days
since the previous review; `scheduled_days` the interval given; `stability` and
`difficulty` the memory state after; `duration_ms` how long the answer took; `kind` review,
manual, or cram (a preview that changed nothing).

A grave says something was deleted, so that a sync (sync.py) deletes it on the other devices
rather than bringing it back: `kind` note, card, deck, notetype or config; `key` the note's
guid, a card's note guid and ord joined with \\x1f, the id of the rest; `name` a deck's
name (decks made apart on two devices match by name); `deleted` the time of the deletion.

`VERSION` is the schema's version, kept in the config table as `schema_version`; upgrade()
brings an older collection's tables up to it, one step per version.
"""

VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL                -- JSON
);

CREATE TABLE IF NOT EXISTS notetypes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'standard',   -- standard, cloze, occlusion
    fields TEXT NOT NULL,              -- JSON: [{"name": …, "description": …}, …]
    templates TEXT NOT NULL,           -- JSON: [{"name": …, "qfmt": …, "afmt": …}, …]
    css TEXT NOT NULL DEFAULT '',
    sort_field INTEGER NOT NULL DEFAULT 0,
    original_id INTEGER,               -- Anki's model id, kept for export
    modified INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_configs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    data TEXT NOT NULL,                -- JSON: deck_config.DeckConfig.to_json()
    modified INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,         -- "Parent::Child"
    config_id INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    collapsed INTEGER NOT NULL DEFAULT 0,
    original_id INTEGER,               -- Anki's deck id, kept for export
    modified INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    guid TEXT NOT NULL,
    notetype_id INTEGER NOT NULL,
    fields TEXT NOT NULL,              -- \\x1f-joined
    sort_field TEXT NOT NULL DEFAULT '',   -- the sort field's text, HTML stripped, folded
    tags TEXT NOT NULL DEFAULT ' ',    -- ' tag1 tag2 '
    marked INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL,
    modified INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_notetype ON notes (notetype_id);
CREATE INDEX IF NOT EXISTS notes_guid ON notes (guid);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL,
    deck_id INTEGER NOT NULL,
    ord INTEGER NOT NULL,              -- the template's index (a cloze number - 1)
    state TEXT NOT NULL DEFAULT 'new',
    due INTEGER NOT NULL DEFAULT 0,
    interval REAL NOT NULL DEFAULT 0,  -- days, the last scheduled interval
    stability REAL NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    left INTEGER NOT NULL DEFAULT 0,
    last_review INTEGER NOT NULL DEFAULT 0,
    flag INTEGER NOT NULL DEFAULT 0,
    suspended INTEGER NOT NULL DEFAULT 0,
    buried INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL,
    modified INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS cards_note ON cards (note_id);
CREATE INDEX IF NOT EXISTS cards_deck_state_due ON cards (deck_id, state, due);

CREATE TABLE IF NOT EXISTS revlog (
    id INTEGER PRIMARY KEY,            -- the review's time in milliseconds
    card_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    state TEXT NOT NULL,
    elapsed_days REAL NOT NULL DEFAULT 0,
    scheduled_days REAL NOT NULL DEFAULT 0,
    stability REAL NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'review'
);
CREATE INDEX IF NOT EXISTS revlog_card ON revlog (card_id);

CREATE TABLE IF NOT EXISTS media (
    name TEXT PRIMARY KEY,             -- the file's name in media/
    sha1 TEXT NOT NULL,
    size INTEGER NOT NULL,
    added INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS graves (
    kind TEXT NOT NULL,                -- note, card, deck, notetype, config
    key TEXT NOT NULL,                 -- a guid, 'guid\\x1ford' for a card, else the id
    name TEXT NOT NULL DEFAULT '',     -- a deck's name
    deleted INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

# What each version adds to the one before: SQL run by upgrade(), in order. (SCHEMA above
# creates whatever is missing as well, so a step only has to do what CREATE IF NOT EXISTS
# cannot: new columns, changed data.)
UPGRADES = {
    2: """
CREATE TABLE IF NOT EXISTS graves (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);
""",
}


def upgrade(db, version):
    """Run the steps from `version` (the collection's schema_version) up to VERSION; the
    version reached. A collection newer than this code is left alone."""
    for step in range(version + 1, VERSION + 1):
        db.executescript(UPGRADES[step])
        version = step
    return version

# Anki's flag colours, by number, as the HIG's named colours.
FLAG_NAMES = {1: 'red', 2: 'orange', 3: 'green', 4: 'blue', 5: 'pink', 6: 'turquoise',
              7: 'purple'}
