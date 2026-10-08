# Decisions

Settled choices and why. The research they rest on is in `research.md`.

## One window, pages, no jargon

Anki opens a window for the deck list, another for Browse, another for Add, another for the
preview, and its maintainer has said tabs will not happen. Users report three to six windows
open at once. Retain has one window: a sidebar of destinations and decks, pages in a navigation
view, dialogs for the editor and the settings. The research's most repeated root cause was
that Anki exposes its data model (note → note type → templates → cards) before the user has a
reason to care; the Add dialog therefore says "Add Cards", starts on Basic with Front and Back,
and shows the type only as a row to change.

## FSRS by default, one slider

The scheduler is FSRS-6, the same algorithm Anki offers, with Anki's learning steps around it.
Users of Anki praise FSRS ("enable, set retention, optimize monthly") and complain about the
deck options page it sits in. The deck options show the daily limits, a retention slider with a
sentence saying what it does, and an Optimize button; the rest is under Advanced. The optimizer
is a pure-Python coordinate descent (optimizer.py) so no torch download is needed.

## Hard is not a fail

A sourced complaint: users press Hard when they mean Again. The grades keep Anki's four (muscle
memory, and the apkg ecosystem) but the buttons name their waits, and a preference reduces them
to Again and Good.

## Anki's formats, faithfully

The whole point of leaving Anki desktop is to keep Anki's decks: `.apkg` and `.colpkg` in and
out, including media, note types, scheduling and the review log; Anki's template syntax
rendered as Anki renders it (template.py); Anki's search syntax (search.py); cloze and image
occlusion built in. Image occlusion is Anki's own note type, so cards travel both ways.

## WebKitGTK renders the card, GTK renders everything else

Shared decks are HTML and CSS. A native renderer would break most of them. The card face is
one WebKitGTK view, transparent, with libadwaita's colours injected, and never takes the
keyboard; everything around it is libadwaita. Without WebKit (an unusual install) a plain
renderer shows the text.

## Undo instead of confirmations

Every change to the collection is an undo step (collection.py's undoable), so deleting,
suspending, burying, grading and editing show a toast with Undo. The two exceptions are
deleting a deck or a note type, which ask first, as the HIG allows for large losses.

## The built-in features Anki needs add-ons for

The review heatmap, the streak, true retention, card information with history, a leech tag
and toast, type-in-the-answer diffs and image occlusion are all built in; there is no add-on
system. Add-ons breaking on every Anki update was a top complaint.

## AnkiConnect's API, not a new one

Card-mining tools (Yomitan, asbplayer) already speak AnkiConnect, an Anki add-on's HTTP API on
127.0.0.1:8765, so Retain answers that API (the subset those tools use) rather than inventing
one nobody calls. It is off by default. Because any web page could send a POST to localhost,
requests whose Origin is a web page are refused, as is a Host other than the loopback address
(DNS rebinding); browser extensions and clients that send no Origin are let in, and a key can
be required. Paths and URLs in storeMediaFile are refused: only the data a tool sends is
stored. Error messages stay AnkiConnect's English, since clients match on them.

## Study modes, graded by what they measure

Flip stays the default; Type the Answer and Choose from Options are a deck option and a
session switch. Typing is graded leniently (any alternative, kana for kanji, a typo in an
English word as Hard, none for Japanese, where one kana is another word) and only *suggests*
the grade, which the user can override: no answer list is complete. The gain from typing is
honest grading more than production itself (covert and overt retrieval help memory alike;
self-graders are overconfident). Multiple choice measures recognition, which is easier than
recall and less durable, so a right pick suggests Good only in learning, where grades move
short steps, and Hard on a review card, so FSRS does not stretch a recalled card's interval
on a recognized one. Distractors come from the deck, alike in part of speech, tags and
length, never sharing an alternative with the answer; four options, three when the deck
has too few.

## Load balancing as Anki does it, per preset

Anki 24.11's load balancer, and its easy days, with the same weights: a day in the fuzz range
is drawn with weight (1/reviews)^2.15 × (1/days)^3, times a sibling factor and an easy-day
factor, by the card's fuzz seed (so the buttons' preview and the answer agree); intervals over
90 days are fuzzed as before. Three differences. The switch is on the preset, not
collection-wide as Anki's `loadBalancerEnabled` (exported in the preset as well, and read
back; an Anki package has no such field and imports as on). A day with nothing due counts as
half a review instead of taking full weight whatever its factors, which in Anki lets an
empty Minimum day or a sibling's day take cards. Suspended cards do not count as load. The
estimate under the retention slider is a simulation (workload.simulate: fsrs-rs's simulator in
outline, with its default rating proportions, the preset's limits and the balancer) rather
than a ratio of intervals, because what someone choosing a retention needs is reviews a day
and cards remembered for their own cards; a year of a sampled 2,500 cards runs in a thread in
well under a second. Anki's offer to reschedule existing cards when easy days change is left
out: the balancer shapes cards as they are answered.

## Speech: the system's voices, never the wrong language

Cards are read by the system's voices through Spiel (GNOME's speech framework) and, second,
Speech Dispatcher, which most distributions run and whose Open JTalk module is today the only
Linux voice that reads Japanese kanji well (espeak-ng reads kana only; Spiel has no Japanese
voice yet). Retain chooses the voice itself, by language: Spiel's own choice falls back to
its first voice whatever the language, and a Japanese word read in an English voice teaches
the wrong sound, so a missing voice is said in a banner, with what to install, rather than
read badly. `{{tts}}` renders Anki's `[anki:tts]` tag, spoken and not shown, as in Anki.
Read Aloud splits a side by script so a two-language card gets two voices, and reads
Japanese from its furigana, since a voice guessing a kanji's reading is often wrong.

## Sync through a folder, one snapshot per device

AnkiWeb has no public API and its protocol is Anki's own; a sync of our own with a server would
need one run for every user. Most people who want two computers in step already run something
that syncs folders (Syncthing, Nextcloud, Dropbox), so Retain syncs through such a folder
(sync.py) and needs no server or account.

- **Each device writes only its own files** (`devices/ID.sqlite`, a snapshot of its collection,
  and `devices/ID.json`, its name and last sync), written to a temporary file, flushed and
  renamed. File-sync tools make conflict copies when two machines change one file; with one
  writer per file they never do, and a half-copied snapshot is skipped (read-only, checked with
  `quick_check`) rather than merged. Syncing the live `collection.sqlite` itself would corrupt
  it the first time two machines wrote between syncs.
- **Last writer wins per object**, by `modified`: presets, note types, decks, notes (by guid)
  and cards (by note guid and ord, which survive an independent import of one .apkg on two
  machines; ids need not). A tie is broken by comparing the contents, so both sides choose
  alike. Per-field merging would need a history of changes the collection does not keep.
  Every change to a row sets its `modified`, scheduling included; a new collection's stock note
  types, Default deck and default preset are modified at 0, so a fresh second computer never
  overrides the first's customised ones.
- **Reviews are the union by id**: a review is a fact, never overwritten.
- **Deletions leave graves** (schema version 2: kind, guid or id, time). A grave as new as an
  object or newer deletes it; an object edited after its grave lives, and comes back on the
  device that deleted it. A review alone is not an edit of the note, so it does not save a
  deleted note. Graves travel with the snapshots and are copied, so a deletion reaches a third
  device even through a second. Undoing a deletion takes its grave away; a sync clears the undo
  stack, since what it would undo may already be on the other devices.
- **Objects made apart are matched by what they are** (a deck by name, a note type by Anki id
  or by name, kind and field names, a preset by name) and then take the smaller of the two ids,
  so later syncs match them by id.
- **Media**: files go both ways; a name both sides use for different contents keeps the shared
  file under the name and renames the local one (`name-2.ext`), rewriting the local notes'
  references, which makes those notes newer so the rename travels.
- **The device id** is random and kept in the collection's config with a fingerprint of the
  machine and path; a collection copied to another computer gets a new id, or two devices would
  write the same snapshot.
- **A backup before a merge**, only when another device's snapshot changed: syncing on every
  open and close would otherwise rotate the ten backups out within days.
- **Clock skew is not corrected**: the times are compared as they are, and the user guide says
  to keep clocks right.

## Python, like Music Sleeve

The owner's other GNOME apps are Python (Music Sleeve) and Rust (3D Viewer). A flashcard app is
not performance-bound, SQLite does the work, and Python keeps the FSRS and format code readable
and testable without a build step. The model code has no GTK and is tested without a display.
