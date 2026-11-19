"""Romaji -> hiragana, for when Whisper writes a short Japanese answer in Latin letters
("Ben Kyo", "venkyou"). Accepts Hepburn and Kunrei spellings, macron/circumflex long
vowels, doubled consonants (kk -> っk) and n/m before consonants (ん). Japanese has no
/v/, so v-syllables map to b (ヴ is pronounced as b). Letters that do not form a
syllable are left as-is.
"""

from __future__ import annotations

import re
import unicodedata

_TABLE: dict[str, str] = {}


def _add(romaji: str, kana: str) -> None:
    for r in romaji.split("|"):
        _TABLE[r] = kana


for _r, _k in {
    "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
    "ka": "か", "ki": "き", "ku": "く", "ke": "け", "ko": "こ",
    "ga": "が", "gi": "ぎ", "gu": "ぐ", "ge": "げ", "go": "ご",
    "sa": "さ", "shi|si": "し", "su": "す", "se": "せ", "so": "そ",
    "za": "ざ", "ji|zi": "じ", "zu": "ず", "ze": "ぜ", "zo": "ぞ",
    "ta": "た", "chi|ti": "ち", "tsu|tu": "つ", "te": "て", "to": "と",
    "da": "だ", "di": "ぢ", "du|dzu": "づ", "de": "で", "do": "ど",
    "na": "な", "ni": "に", "nu": "ぬ", "ne": "ね", "no": "の",
    "ha": "は", "hi": "ひ", "fu|hu": "ふ", "he": "へ", "ho": "ほ",
    "ba|va": "ば", "bi|vi": "び", "bu|vu": "ぶ", "be|ve": "べ", "bo|vo": "ぼ",
    "pa": "ぱ", "pi": "ぴ", "pu": "ぷ", "pe": "ぺ", "po": "ぽ",
    "ma": "ま", "mi": "み", "mu": "む", "me": "め", "mo": "も",
    "ya": "や", "yu": "ゆ", "yo": "よ",
    "ra|la": "ら", "ri|li": "り", "ru|lu": "る", "re|le": "れ", "ro|lo": "ろ",
    "wa": "わ", "wo": "を", "n'": "ん",
    "fa": "ふぁ", "fi": "ふぃ", "fe": "ふぇ", "fo": "ふぉ",
    "ti'": "てぃ", "di'": "でぃ", "je": "じぇ", "she": "しぇ", "che": "ちぇ",
}.items():
    _add(_r, _k)

for _c, _row in {
    "ky": "き", "gy": "ぎ", "ny": "に", "hy": "ひ", "by": "び", "py": "ぴ", "my": "み", "ry": "り", "ly": "り", "vy": "び",
    "sh": "し", "sy": "し", "ch": "ち", "ty": "ち", "cy": "ち", "j": "じ", "zy": "じ", "jy": "じ", "dy": "ぢ",
}.items():
    _add(_c + "a", _row + "ゃ")
    _add(_c + "u", _row + "ゅ")
    _add(_c + "o", _row + "ょ")

_MAX = max(len(k) for k in _TABLE)
_LONG = {"ā": "aa", "ī": "ii", "ū": "uu", "ē": "ee", "ō": "ou", "â": "aa", "î": "ii", "û": "uu", "ê": "ee", "ô": "ou"}
_LATIN = re.compile(r"[a-z]")
_CONSONANTS = set("bcdfghjklmpqrstvwxyz")


def has_latin(text: str) -> bool:
    return bool(_LATIN.search(unicodedata.normalize("NFKC", text).lower()))


def to_hiragana(text: str) -> str:
    s = unicodedata.normalize("NFC", text.lower())
    s = "".join(_LONG.get(c, c) for c in s)
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        # Doubled consonant -> small tsu (but "nn" is ん).
        if c in _CONSONANTS and c == nxt and c != "n":
            out.append("っ")
            i += 1
            continue
        if c == "t" and s.startswith("tch", i):  # matcha
            out.append("っ")
            i += 1
            continue
        # n / m as moraic nasal: before a consonant (not y), at the end, or "nn".
        if c in "nm":
            if c == "n" and nxt == "n":
                out.append("ん")
                i += 2 if (i + 2 >= len(s) or s[i + 2] not in "aiueoy") else 1
                continue
            if (nxt == "" or nxt in _CONSONANTS - {"y"} or not nxt.isalpha()) and (c == "n" or nxt in "bpm"):
                out.append("ん")
                i += 1
                continue
        for length in range(_MAX, 0, -1):
            chunk = s[i : i + length]
            if chunk in _TABLE:
                out.append(_TABLE[chunk])
                i += length
                break
        else:
            out.append(c)
            i += 1
    return "".join(out)
