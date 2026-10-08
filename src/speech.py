# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What Read Aloud says for a card side: its text, split into runs a voice of each language
can read.

    runs(html, context='') -> [(lang, text)]   lang 'ja', 'zh', 'ko' or '' (the default voice)
    side_html(question, answer, side)  the part of a side worth reading: the answer side
                                       without the question repeated above its <hr id=answer>
    Voice(backend, id, name, languages, handle)   a voice a speech backend offers
    choose_voice(voices, lang, preferred=(), fallback_lang='en') -> Voice or None
    language_tag(lang) -> 'ja-JP'      Anki's ja_JP as BCP 47

A card such as 猫 / "cat" is two languages, and one voice reading both mangles one of them.
The text is split where the script changes: kana go to Japanese, Hangul to Korean, kanji to
Japanese when the side or `context` (the note's other fields) has kana and to Chinese
otherwise (a lone 猫 could be either), everything else to the default voice. Digits, spaces
and punctuation stay with the run they are in. Japanese runs are read from the furigana when
there are any (template.speech_text), so a voice reads 一日 as the card means it.
"""

import collections
import re

from . import template

Voice = collections.namedtuple('Voice', 'backend id name languages handle')

_ANSWER_RULE = re.compile(r'<hr\s+id\s*=\s*["\']?answer["\']?\s*/?>', re.IGNORECASE)


def side_html(question, answer, side):
    if side != 'answer':
        return question
    parts = _ANSWER_RULE.split(answer, maxsplit=1)
    return parts[1] if len(parts) == 2 else answer


def _script(char):
    code = ord(char)
    if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF or 0xFF66 <= code <= 0xFF9F:
        return 'kana'
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF \
            or code in (0x3005, 0x3006, 0x303B):  # 々 〆 〻
        return 'han'
    if 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return 'hangul'
    if char.isalpha():
        return 'latin'
    return None  # digits, spaces, punctuation: whatever run they are in


def _has_kana(text):
    return any(_script(char) == 'kana' for char in text)


def runs(html, context=''):
    """The side's text as [(lang, text)] runs, Japanese ones read from their furigana."""
    # Japanese reading first (ruby and bracket furigana become kana), then the split.
    has_kana = _has_kana(template.strip_html(html)) or _has_kana(context)
    text = template.speech_text(html, 'ja' if has_kana else '')
    han = 'ja' if has_kana else 'zh'
    langs = {'kana': 'ja', 'han': han, 'hangul': 'ko', 'latin': ''}
    found = []
    for char in text:
        script = _script(char)
        lang = langs[script] if script else (found[-1][0] if found else None)
        if found and (lang is None or lang == found[-1][0]):
            found[-1][1] += char
        else:
            found.append([lang if lang is not None else '', char])
    return [(lang, ' '.join(chunk.split())) for lang, chunk in found if chunk.strip()]


def language_tag(lang):
    """Anki's `ja_JP` (or `ja-jp`) as a BCP 47 tag, `ja-JP`; '' stays ''."""
    parts = [part for part in re.split(r'[_-]', lang or '') if part]
    if not parts:
        return ''
    return '-'.join([parts[0].lower()] + [part.upper() if len(part) == 2 else part
                                          for part in parts[1:]])


def choose_voice(voices, lang, preferred=(), fallback_lang='en'):
    """The voice to read `lang` with: one of `preferred` (Anki's voices=, by name or id),
    else one whose languages hold the tag exactly, else one of the same base language
    (ja for ja-JP). Never a voice of another language: a Japanese word read by an English
    voice teaches the wrong sound. `lang` '' (text in no particular language) is read in
    `fallback_lang`, the user's own language."""
    tag = language_tag(lang or fallback_lang).lower()
    if not tag:
        return None
    base = tag.split('-')[0]
    wanted = [name.lower() for name in preferred]
    for name in wanted:
        for voice in voices:
            if name in (str(voice.name).lower(), str(voice.id).lower()):
                return voice
    exact = same_base = None
    for voice in voices:
        languages = [language_tag(language).lower() for language in voice.languages]
        if exact is None and tag in languages:
            exact = voice
        if same_base is None and any(language.split('-')[0] == base for language in languages):
            same_base = voice
    return exact or same_base
