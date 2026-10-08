# Research notes (October 2026)

What was read before Retain was designed, condensed. Three questions: what people complain
about in Anki desktop, what the alternatives do better or worse, and what GNOME expects.

## Anki desktop: the complaints

- **Dense, non-discoverable, jargon.** "Only after 20–45 minutes do I find where you've hidden
  the feature"; AnkiHub's own research on the Add/Edit window found users confused by "Add",
  by notes versus cards, by cluttered buttons and nested modals. Deleting a note type is Add →
  Type → Manage → Delete.
- **Onboarding needs a 200-page manual** and YouTube; "an app with a 200-page manual has
  disadvantages competing with Duolingo".
- **Many windows.** Users report three to six open; a tab layout was declined upstream.
- **The editor.** Pasting brings foreign HTML, Anki wraps things in `<div>`s, formatting vanishes
  on edit; Markdown was asked for repeatedly.
- **Settings overload.** The deck options page is long; desired retention is misunderstood;
  Hard is misused as a fail.
- **Add-ons for basics, and they break.** The review heatmap is "must-have" and users asked for
  it in core because add-ons break with updates; image occlusion was an add-on until 23.10.
- **Sync** problems: media not syncing, syncing the wrong way and losing cards.
- **Linux.** Wayland off by default, blurry fractional scaling, dark mode not following GNOME,
  an unofficial Flatpak, and a 25.07 launcher that installs Python packages in a terminal for
  twenty minutes.
- Context: Anki's stewardship moved to AnkiHub in 2026 with promises of UI work; a GNOME-native
  app has a window of credibility.

## What must be kept

FSRS (fewer reviews at the same retention), the detailed statistics (true retention, daily
load), note types generating several cards with batch updates, single-file `.apkg` backup and
sharing, the shared-deck ecosystem (most US medical students use Anki), cloze and image
occlusion, keyboard-driven review, custom study and filtered decks, free and open source.

## Alternatives

| App | Better | Worse |
|---|---|---|
| Mochi | clean look, built-in occlusion, retention stats, Markdown | thin stats, no note types, no scheduling-free export, paid sync |
| RemNote | notes and cards in one | slow, apkg import breaks media |
| SuperMemo | incremental reading | a famously slow-moving interface |
| Noji (AnkiPro) | pretty mobile UI | SM-2 behind a subscription, brand confusion |
| Quizlet, Brainscape, Memrise | onboarding, social | paywalls on core modes, ads |
| AnkiDroid | new study screen with gestures, multi-select browser | copies desktop stats layout |
| Flashcards / Memorize (GNOME Circle, archived) | sidebar-of-sets layout, phone-ready | no real spaced repetition, no media, no apkg |
| Memorado (GNOME) | simple | front/back text only |
| Oboete (libcosmic) | FSRS, images, Anki import | not libadwaita; offline only |

No GTK 4 / libadwaita Anki client with FSRS and `.apkg` support existed.

## GNOME design brief (libadwaita 1.9)

- `AdwNavigationSplitView` with `AdwSidebar` (1.9) for the deck list, suffix widgets for the
  counts; `AdwNavigationView` pages; `AdwDialog` (floating, a bottom sheet on phones);
  `AdwShortcutsDialog` (1.8); `AdwToggleGroup`, `AdwWrapBox` (1.7); `AdwSpinner` (1.6);
  `AdwStatusPage` for empty states; toasts with Undo; `GtkColumnView` for the browser.
- HIG: spacing in sixes, typography classes not sizes, header bars with few controls and no
  text buttons, `.pill` for primary actions in open space, destructive actions with undo or a
  dialog, 360 px minimum width, the user's accent colour, never colour alone (pair the three
  counts with position and tooltips).
- The review screen: a clamped card (about 720 px), the answer under the question, four
  homogeneous pill buttons with the interval under each word, counts in the header, one
  suggested button per view.
- Charts: GNOME apps draw their own with `GtkSnapshot`/cairo (Health, Colophon's heatmap); no
  libadwaita chart widget exists.
- Card rendering: WebKitGTK 6.0 is accepted in GNOME Circle (Apostrophe); a transparent view
  with an injected user style sheet follows the theme; `load_html` with a `file://` base URI
  resolves media; handle `[sound:…]` natively.

## FSRS-6 and the apkg format

The formulas, default parameters and bounds in fsrs.py come from the open-spaced-repetition
reference implementations (py-fsrs, fsrs-rs; MIT), and the apkg layout (legacy schema 11,
the zstd `.anki21b` packages with protobuf media index, card type/queue/due encodings, the
revlog, the stock templates, the image occlusion field syntax) from Anki's source and the
AnkiDroid wiki. Python 3.14's `compression.zstd` reads the current packages without a
third-party module.

## Text to speech on Linux (October 2026)

libspiel 1.0.4 (April 2025): `Spiel.Speaker` (async `new`, `voices` as a list model,
`speak`, `cancel`, the `utterance-finished`, `-canceled` and `-error` signals) and
`Spiel.Utterance` (text, voice, BCP 47 language, rate 0.1–10). It is in neither the GNOME 50
nor the 51 runtime (apps bundle it); its providers (espeak-ng, Piper) are D-Bus services
from the project's own Flatpak repository, not Flathub, and the sandbox cannot allow a
`*.Speech.Provider` suffix (WebKit bug 280684). With no voice for a language it uses the
first voice; with none at all it warns and emits nothing. Speech Dispatcher's Python
`speechd` module (`SSIPClient`, `speak` with END and CANCEL callbacks, `set_rate` −100…100,
voices per output module); in a Flatpak it needs `--filesystem=xdg-run/speech-dispatcher:ro`.
Japanese: espeak-ng reads kana only; Piper's one Japanese voice (hi_fi_captain, CC BY-NC-SA)
needs Piper 1.7 and is not in Spiel's Piper provider; Open JTalk through Speech Dispatcher
(`sd_openjtalk`) reads kanji with pitch accent. Anki: `{{tts ja_JP voices=A,B speed=0.8:F}}`,
voices a preference list, nothing on Linux without an add-on.

## Study modes (October 2026)

Short answer with feedback beats multiple choice for retention three days on (Kang,
McDermott & Roediger 2007), though both beat restudy (Smith & Karpicke 2014; a recall-then-
choose hybrid added little). Competitive distractors make multiple choice teach more (Little
& Bjork 2014); lures read can be learned as facts unless feedback follows (Roediger & Marsh
2005; Butler & Roediger 2008). Covert retrieval helps as much as overt (Smith, Roediger &
Karpicke 2013); self-graders are overconfident (Dunlosky & Rawson 2012), the case for typed,
checked answers. Duolingo's half-life regression pools exercise types unweighted (Settles &
Meeder 2016); SuperMemo auto-grades multiple choice with an override; no app documents
weighting recognition below recall. Item writing: plausible distractors alike in length and
form, one right answer (Haladyna, Downing & Rodriguez 2002); three options are as reliable as
four (Rodriguez 2005). Anki's typed answers (rslib typeanswer.rs) diff after NFC, with `ci`
and `nc` options, and never grade.

## Sources

Anki forums (threads 17042, 66053, 43692, 929, 30172, 3651, 20906, 6000, 41667, 23621,
69849, 40879, 10231, 31509, 54046, 19196, 33746, 10409, 28652, 16226, 17286, 66584, 39408,
66080, 68610, 25415, 53705, 67394, 37129, 46152, 63865), r/Anki "20 reasons why Anki isn't
popular", Hacker News 46861313 and 39163094, borretti.me on Mochi, the RemNote forum, the
Anki knock-offs FAQ, Capterra reviews, GitHub for Memorize and Memorado, Flathub for Oboete,
the libadwaita 1.9 class index and style classes page, the GNOME HIG, the WebKitGTK 6.0
reference, open-spaced-repetition's py-fsrs and fsrs-rs, ankitects/anki's rslib and the
AnkiDroid database wiki, and PEP 784. For speech: github.com/project-spiel/libspiel
(spiel-speaker.c, spiel-registry.c), project-spiel.org, spiel-it's manifest,
speech-provider-piper, brailcom/speechd's client.py, rhasspy/piper-voices, OHF-Voice/piper1-gpl
1.7.0, espeak-ng issue 366, Anki's qt/aqt/tts.py and the templates manual, Mozilla bug
1857367.
