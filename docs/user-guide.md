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
review history, **R** replays its sound, Delete deletes the note, and **Ctrl+Z** undoes your
last grade (or anything else you just did) and shows the card again.

A new card goes through learning steps (one minute, then ten) before it graduates to a review
card, which comes back after a number of days. Review cards you fail go through a relearning
step. The schedule is FSRS, the algorithm Anki uses, which estimates how well you remember
each card and aims for the deck's *desired retention* (90 % by default).

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
(new cards and reviews per day), the desired retention (higher means more reviews and fewer
forgotten cards), and **Optimize Parameters**, which fits the memory model to your own review
history once it has 400 reviews or more. Under *Advanced*: learning and relearning steps, the
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
the location and can make a backup on the spot. There is no cloud sync: copy the folder, or
export a deck, to move it to another machine.

## Keyboard shortcuts

Ctrl+? shows them all.
