# Retain: user guide

Retain is a flashcard app for GNOME. You write cards, and Retain shows each one again just
before you would forget it, so a few minutes a day keeps thousands of facts in memory. It
opens Anki decks, including the ones other learners share, and writes decks Anki can open.

## The window

The sidebar lists **Today**, **Browse** and **Statistics**, then your decks. Each deck shows
three numbers: new cards (blue), cards in learning (orange) and cards to review (green). A
deck inside another is shown under it; `Spanish::Verbs` is a subdeck of `Spanish`. Right-click
a deck (or press Menu on it) to study, add cards, rename, change its options, export or delete
it. **New Deck** at the bottom makes one; a name with `::` makes a subdeck.

**Today** sums up the day: what is due across every deck, what you have done so far, and your
streak, with a Study button for each deck that has something due.

## Studying

Choose a deck and press **Study Now** (or Ctrl+Enter). A card shows its question; press
**Space** or **Enter** to turn it over, then grade yourself:

| Key | Grade | Meaning |
|---|---|---|
| 1 | Again | You did not remember. The card comes back in a minute or so. |
| 2 | Hard | You remembered, with effort. A shorter wait than Good. |
| 3 | Good | You remembered. The usual wait. |
| 4 | Easy | You remembered at once. A longer wait. |

Each button shows how long the card will wait. **Hard is not a fail**: it still counts as
remembered. If four grades are more than you want, Preferences has *Answer with Again and Good
only*; Hard and Easy stay on the keyboard.

While studying: **E** edits the card, **S** suspends it (it will not come back until you
unsuspend it in Browse), **B** buries it until tomorrow, **M** marks the note with a star,
**F** or Ctrl+1 to Ctrl+4 flags it with a colour, **I** shows the card's information and
review history, **R** replays its sound, **V** reads it aloud, Delete deletes the note, and **Ctrl+Z** undoes your
last grade (or anything else you just did) and shows the card again.

A new card goes through learning steps (one minute, then ten) before it graduates to a review
card, which comes back after a number of days. Review cards you fail go through a relearning
step. The schedule is FSRS, the algorithm Anki uses, which estimates how well you remember
each card and aims for the deck's *desired retention* (90 % by default).

### Study modes

Deck Options → *Study Mode* sets how the deck's cards ask for their answer, and the review
page's More menu changes it for one session:

- **Flip the Card** (the default): think of the answer, show it, grade yourself.
- **Type the Answer**: type it and press Enter. Retain is lenient: case, punctuation,
  a leading *to*, *a* or *the* and notes in brackets don't matter, any one of "tall; high;
  expensive" counts, and for Japanese the kana is as good as the kanji. It suggests a grade:
  **Good** when right, **Hard** for a small typo in an English word, **Again** when wrong.
  Space or Enter accepts it; 1 to 4 pick another, for an answer Retain didn't know was right.
- **Choose from Options**: pick the answer from four (keys 1 to 4). The wrong options come
  from the deck's other cards, chosen to look alike, so the right one isn't given away. A
  right pick suggests **Good** while a card is being learned and **Hard** once it is a
  review card: recognizing an answer is easier than recalling it, so it shouldn't stretch the
  card's interval as much.

Cards a mode can't ask, such as clozes, picture cards and answers longer than a phrase, are
shown as Flip the Card.

### Hearing cards

A card plays its sounds when it is shown. A card whose note type asks for speech, as Anki's
`{{tts ja_JP:Front}}` does, has it read in a voice for that language. **Read Aloud** (V, or the
More menu) reads any card, even without such a template: Japanese in a Japanese voice,
English in an English one, so 猫 / *cat* is read by two voices. Where a card has furigana, the
reading is what is spoken, so 一日 is read ついたち as the card means. Preferences has *Read
Cards Aloud* to do this for every card as it is shown.

The voices are your system's. Retain never reads a language in a voice of another; when no
voice is installed for a card's language, a banner above the card says so and *How to
Install* explains what to add: Speech Dispatcher (for Japanese, with its Open JTalk module,
which reads kanji) or a Spiel speech provider such as eSpeak NG or Piper.

When the deck is done for the day, **Study More…** offers to learn more new cards, review
more cards that are due, go over cards you recently forgot, review ahead of time, or practise
every card of a tag or of the deck (a practice that changes no schedule).

## Adding cards

**Add Cards** (the + button, or Ctrl+N) opens the editor. Choose a type and a deck, fill in the
fields, and press **Add** (Ctrl+Enter). The dialog stays open for the next card.

- **Basic** makes one card: front, back. *Basic (and reversed card)* makes two, one each way.
  *Basic (type in the answer)* asks you to type the answer and shows what you got wrong.
- **Cloze** makes a card for each gap in a sentence: select a word and press Ctrl+Shift+C to
  turn it into `{{c1::word}}`; a second gap gets `c2`, and so on. Ctrl+Shift+Alt+C reuses the
  same number, so two gaps are hidden together.
- **Image Occlusion** hides parts of a picture: pick an image, draw rectangles or ellipses over
  the labels, and each one becomes a card.

Formatting: Ctrl+B, Ctrl+I, Ctrl+U. Attach a picture or sound with the paperclip button or by
pasting an image. Pasted text comes in as plain text. Fields keep plain, clean HTML, and a field
with formatting the editor cannot show (a table, say) is edited as HTML so nothing is lost.

Tags go in the Tags row, separated by spaces. If a card with the same front already exists,
the editor says so.

## Browsing

**Browse** lists cards (or notes) and lets you search with Anki's syntax:

| Search | Finds |
|---|---|
| `dog` | cards with "dog" anywhere |
| `"to be"` | the phrase |
| `deck:Spanish` | a deck and its subdecks |
| `tag:verbs` | a tag (and `verbs::…` under it) |
| `is:due` `is:new` `is:learn` `is:review` `is:suspended` `is:buried` | by state |
| `flag:1` | a flag (1 red, 2 orange, 3 green, 4 blue, 5 pink, 6 turquoise, 7 purple) |
| `prop:ivl>=30` `prop:due=1` `prop:lapses>3` `prop:s>100` `prop:d>7` | by interval, due day, lapses, stability, difficulty |
| `rated:7` `rated:1:1` | reviewed in the last 7 days; failed today |
| `added:30` `edited:7` `introduced:30` | by when it was added, edited, first seen |
| `note:Cloze` `card:2` `front:dog*` | by note type, card number, field |
| `re:^d.g$` | a regular expression |
| `-tag:verbs` `a or b` `(a or b) c` | not, or, grouping |

Select cards to change their deck, suspend, mark, flag, tag, reset, set a due date or delete
them. Everything can be undone with Ctrl+Z. Enter (or a double click) edits the note.

## Statistics

The Statistics page shows today's summary, a calendar of the last year's reviews, what is due
ahead, the reviews you have done, how your cards divide into new, learning, young and mature,
how well you remember (true retention and an estimate of the cards you hold in memory), the
spread of intervals and difficulties, and when in the day you study. The deck chooser at the
top narrows it to one deck.

## Deck options

Each deck uses a preset, which several decks can share. The options are the daily limits
(new cards and reviews per day), the workload, the desired retention (higher means more
reviews and fewer forgotten cards), and **Optimize Parameters**, which fits the memory model
to your own review history once it has 400 reviews or more.

Under the retention slider, an estimate of what it costs: the reviews a day over the next year
and how many of the preset's cards you would remember at its end. Retain works it out by
playing the preset's cards forward with the memory model, the daily limits and your other
settings, so it changes as you move the slider or change them. It is an estimate: real days
vary with how you answer.

*Load Balancing* (on by default) evens out the days. When a card is due back in about a month,
any day within a few days of that is as good; Retain picks one that has fewer reviews due on
the preset's decks, so you don't get a heavy day followed by a light one. With *Bury Siblings*
on, it also keeps a note's cards off the same day. *Easy Days* make some weekdays lighter:
set a day to *Reduced* for fewer reviews then, or *Minimum* for almost none. They work through
load balancing, so they need it on, and they shape when cards are scheduled from now on, not
cards already scheduled. Under *Advanced*: learning and relearning steps, the
order of new cards, where new cards go among reviews, whether a card's siblings are buried for
the day when you see one, the leech threshold and action, the maximum interval, and the raw
FSRS parameters.

## Importing and exporting

**Import…** (Ctrl+O) opens an Anki deck or collection (`.apkg`, `.colpkg`), with its pictures,
sounds, note types and, if you keep *Include scheduling* on, every card's progress and review
history. It also reads text files, one note per line, with fields separated by tabs, commas or
semicolons. Opening an `.apkg` from Files does the same.

**Export…** writes a deck or the whole collection as an Anki deck, with or without scheduling,
or as a tab-separated text file.

## Adding cards from other apps

Yomitan, asbplayer and other tools that make cards in Anki through the AnkiConnect add-on can
make them in Retain instead. Turn on *Allow Card-Mining Apps* in Preferences, then point the
tool at its usual address, `http://127.0.0.1:8765`. In Yomitan that is Settings → Anki → Enable
Anki integration; choose a Retain deck and note type there, and map their fields. Each note
added shows a toast with Undo.

Retain answers only while it is open, and only to programs on this computer: browser
extensions and local scripts. Web pages are always refused. To be stricter, set a key in
Preferences and give the tool the same key. If Anki itself is open, it holds the address, and
Retain says the port is in use.

## Where your data is

The collection lives in `~/.local/share/retain/` (`collection.sqlite` and `media/`); backups
are made in `backups/` there, up to ten, no more often than every six hours. Preferences shows
the location and can make a backup on the spot. To move the collection to another machine,
copy the folder, export it, or sync it (below).

## Syncing between computers

Retain syncs through a folder that another tool already keeps the same on your computers:
Syncthing, Nextcloud, Dropbox, or a USB stick you carry between them. There is no server and
no account.

1. Make an empty folder that the tool syncs. With **Syncthing**, add a folder (for example
   `~/Sync/Retain`) and share it with your other computers; with **Nextcloud**, make the folder
   inside the folder the Nextcloud desktop client syncs.
2. In Retain on the first computer, open Preferences → Sync → *Choose…* and pick the folder.
   Retain syncs at once.
3. Wait for the tool to copy the folder to the second computer, install Retain there, and
   choose the same folder. Its collection merges with the first one's.

From then on Retain syncs when it opens and before it closes (*Sync Automatically*), and
whenever you press *Sync Now*. A toast says what came in ("Synced: 12 cards, 3 notes from
Laptop"). *Other Devices* lists the computers that sync through the folder and when each last
did. Each computer writes only its own file in the folder, so the tool never makes conflict
copies.

What to know:

- **Last change wins, per card and per note.** If you edit the same note on two computers
  before they sync, the later edit is kept and the other is lost. Reviews are never lost: every
  review from every computer is kept, and the card's schedule is the one from its latest
  review. Deleting a note or deck on one computer deletes it on the others, unless it was edited
  on another computer after the deletion.
- **Sync often.** Using Retain on two computers while one is offline for days works, but the
  merge is per card and per note, not per field: expect the later of two edits to win, not a
  blend of both.
- **Keep the clocks right.** "Later" is judged by each computer's clock; one that is far off
  wins or loses every tie.
- **Before each merge Retain makes a backup** (Preferences → Backups), so a merge you do not
  like can be undone by restoring it. A sync clears Undo, as an import does.
- **Let the tool finish.** If the folder is still being copied, Retain skips a file it cannot
  read and picks it up at the next sync. Media files go both ways; two different files with the
  same name are both kept, one renamed.

## Keyboard shortcuts

Ctrl+? shows them all.
