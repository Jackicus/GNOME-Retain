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

## No sync

AnkiWeb has no public API and the sync protocol is Anki's own; a sync of our own would need a
server. The collection is one folder; Preferences shows where it is. Sync is a possible later
feature, not a promise.

## Python, like Music Sleeve

The owner's other GNOME apps are Python (Music Sleeve) and Rust (3D Viewer). A flashcard app is
not performance-bound, SQLite does the work, and Python keeps the FSRS and format code readable
and testable without a build step. The model code has no GTK and is tested without a display.
