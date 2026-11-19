"""Deterministic reading match for Japanese answers (agent.md §6a).

Pipeline: NFKC -> numbers/counters to kana -> kanji to kana (fugashi + unidic-lite)
-> katakana to hiragana -> strip punctuation/space. Then compare at mora level.
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from . import romaji
from .jp_numbers import numbers_to_kana

_tagger = None
_tagger_lock = threading.Lock()


def _get_tagger():
    global _tagger
    with _tagger_lock:
        if _tagger is None:
            import fugashi  # imported lazily: loading the dictionary takes ~0.5s

            _tagger = fugashi.Tagger()
        return _tagger


_KANJI_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿々〆ヶ]")


def kata_to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def _strip(s: str) -> str:
    """Drop punctuation, symbols and whitespace (keeps ー, which is category Lm)."""
    return "".join(c for c in s if unicodedata.category(c)[0] not in ("P", "Z", "S", "C"))


def to_kana(text: str) -> str:
    """Kanji -> katakana readings via the analyzer; non-kanji text passes through."""
    if not _KANJI_RE.search(text):
        return text
    out = []
    tagger = _get_tagger()
    with _tagger_lock:  # MeCab taggers are not documented as thread-safe
        words = [(w.surface, getattr(w.feature, "kana", None)) for w in tagger(text)]
    for surface, kana in words:
        if kana and kana != "*" and _KANJI_RE.search(surface):
            out.append(kana)
        else:
            out.append(surface)
    return "".join(out)


_LATIN_RUN = re.compile(r"[A-Za-zĀĪŪĒŌÂÎÛÊÔāīūēōâîûêô']+")
# ヴ is pronounced as b in Japanese; fold it so ヴェ and べ compare equal.
_VU = (("ゔぁ", "ば"), ("ゔぃ", "び"), ("ゔぇ", "べ"), ("ゔぉ", "ぼ"), ("ゔ", "ぶ"))


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = numbers_to_kana(s)
    s = _LATIN_RUN.sub(lambda m: romaji.to_hiragana(m.group()), s)
    s = to_kana(s)
    s = kata_to_hira(s)
    for a, b in _VU:
        s = s.replace(a, b)
    return _strip(s).lower()


def raw_clean(text: str) -> str:
    """For the raw (pre-normalization) comparison: only trim whitespace/punctuation."""
    return _strip(unicodedata.normalize("NFKC", text or "")).lower()


# --- Answer splitting ---------------------------------------------------------

_SPLIT_RE = re.compile(r"[、,，;；/／\n*＊]+")
# ・ separates alternatives (なに・なん) but also joins katakana loanwords
# (コンピューター・ゲーム). Split on it unless BOTH neighbours are katakana.
_DOT_RE = re.compile(r"(?<![ァ-ヺー])・|・(?![ァ-ヺー])")
_PAREN_RE = re.compile(r"[（(]([^()（）]*)[)）]")


def split_answers(field: str | None) -> list[str]:
    """'話す (はなす)、しゃべる' -> ['話す', 'はなす', 'しゃべる'].
    'なに・なん' -> ['なに', 'なん'], but 'コンピューター・ゲーム' stays whole."""
    if not field:
        return []
    field = _DOT_RE.sub("、", unicodedata.normalize("NFKC", field))
    out: list[str] = []
    for part in _SPLIT_RE.split(field):
        inner = _PAREN_RE.findall(part)
        outer = _PAREN_RE.sub(" ", part).strip()
        for piece in [outer, *inner]:
            for sub in _SPLIT_RE.split(piece):
                sub = sub.strip()
                if sub and sub not in out:
                    out.append(sub)
    return out


# --- Mora -----------------------------------------------------------------------

_SMALL = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")


def split_mora(kana: str) -> list[str]:
    mora: list[str] = []
    for c in kana:
        if c in _SMALL and mora:
            mora[-1] += c
        else:
            mora.append(c)
    return mora


def tolerance(mora_count: int) -> int:
    return 1 if mora_count <= 8 else math.ceil(0.15 * mora_count)


_TO_SMALL = str.maketrans("やゆよあいうえおわ", "ゃゅょぁぃぅぇぉゎ")


def _small_pair(first: str, second: str, combined: str) -> bool:
    """True if mora ``first`` + full-size ``second`` is ``combined`` with ``second``
    made small, e.g. き + よ ~ きょ."""
    return len(first) == 1 and len(second) == 1 and first + second.translate(_TO_SMALL) == combined


def _edit_ops(a: list[str], b: list[str]) -> list[tuple[str, str | None, str | None, int]]:
    """Mora-level Levenshtein alignment of a (said) -> b (expected), plus a 1-cost
    op for a small kana said full-size or vice versa (きよ <-> きょ). Returns the
    non-matching ops as (kind, said, expected, index_in_expected)."""
    n, m = len(a), len(b)
    inf = n + m + 1
    d = [[inf] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i >= 2 and _small_pair(a[i - 2], a[i - 1], b[j - 1]):
                best = min(best, d[i - 2][j - 1] + 1)
            if j >= 2 and _small_pair(b[j - 2], b[j - 1], a[i - 1]):
                best = min(best, d[i - 1][j - 2] + 1)
            d[i][j] = best
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and d[i][j] == d[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i >= 2 and j > 0 and _small_pair(a[i - 2], a[i - 1], b[j - 1]) and d[i][j] == d[i - 2][j - 1] + 1:
            ops.append(("small", a[i - 2] + a[i - 1], b[j - 1], j - 1))
            i, j = i - 2, j - 1
        elif j >= 2 and i > 0 and _small_pair(b[j - 2], b[j - 1], a[i - 1]) and d[i][j] == d[i - 1][j - 2] + 1:
            ops.append(("small", a[i - 1], b[j - 2] + b[j - 1], j - 2))
            i, j = i - 1, j - 2
        elif i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + 1:
            ops.append(("sub", a[i - 1], b[j - 1], j - 1))
            i, j = i - 1, j - 1
        elif j > 0 and d[i][j] == d[i][j - 1] + 1:
            ops.append(("missing", None, b[j - 1], j - 1))
            j -= 1
        else:
            ops.append(("extra", a[i - 1], None, j))
            i -= 1
    ops.reverse()
    return ops


def edit_distance(a: list[str], b: list[str]) -> int:
    return len(_edit_ops(a, b))


_VOWEL_OF: dict[str, str] = {}
for _row, _v in (
    ("あかさたなはまやらわがざだばぱぁゃゎ", "あ"),
    ("いきしちにひみりぎじぢびぴぃ", "い"),
    ("うくすつぬふむゆるぐずづぶぷぅゅ", "う"),
    ("えけせてねへめれげぜでべぺぇ", "え"),
    ("おこそとのほもよろをごぞどぼぽぉょ", "お"),
):
    for _c in _row:
        _VOWEL_OF[_c] = _v


def canon_long(kana: str) -> str:
    """Spell every long vowel as ー: いい/いー -> いー, とう/とー -> とー, せんせい -> せんせー,
    こうひい/コーヒー -> こーひー. Used only for comparison (both sides), so Whisper's
    spelling choice (いい vs イー) never counts as a mistake, while a really missing long
    vowel (おばさん vs おばあさん) still does."""
    mora = split_mora(kana)
    out: list[str] = []
    for m in mora:
        prev = out[-1] if out else None
        if prev is not None and prev != "ー" and _is_long_vowel(m, prev):
            out.append("ー")
        else:
            out.append(m)
    return "".join(out)


def _is_long_vowel(mora: str, prev: str | None) -> bool:
    if mora == "ー":
        return True
    if prev is None or mora not in "あいうえお":
        return False
    pv = _VOWEL_OF.get(prev[-1])
    return pv == mora or (pv == "お" and mora == "う") or (pv == "え" and mora == "い")


def _describe(kind: str, said: str | None, exp: str | None, prev: str | None) -> str:
    m = exp if kind == "missing" else said
    if kind in ("missing", "extra"):
        word = "missing" if kind == "missing" else "extra"
        if m == "っ":
            return f"small っ {word}"
        if m == "ん":
            return f"ん {word}"
        if m is not None and _is_long_vowel(m, prev):
            return f"long vowel {word}"
        return f"{m} {word}"
    if kind == "small":
        return f"small kana: {said} instead of {exp}"
    return f"different sound: {said} instead of {exp}"


def describe_diff(said: str, expected: str) -> list[str]:
    a, b = split_mora(said), split_mora(expected)
    notes = []
    for kind, s, e, j in _edit_ops(a, b):
        prev = b[j - 1] if j > 0 else None
        notes.append(_describe(kind, s, e, prev))
    return notes


# --- Matching -------------------------------------------------------------------


@dataclass(frozen=True)
class MatchResult:
    score: int
    feedback: str
    matched: str | None  # the expected answer closest to the transcript


def expected_answers(back: str, reading: str | None, alt_answers: Iterable[str] | None) -> list[str]:
    base = split_answers(reading) if reading and reading.strip() else split_answers(back)
    out = list(base)
    for alt in alt_answers or []:
        for a in split_answers(alt):
            if a not in out:
                out.append(a)
    return out


def match(
    transcript: str,
    front: str,
    back: str,
    reading: str | None = None,
    alt_answers: Iterable[str] | None = None,
) -> MatchResult:
    alts = list(alt_answers or [])
    answers = expected_answers(back, reading, alts)

    # 1. Raw match (guards analyzer misreadings such as 今日 きょう/こんにち).
    raw = raw_clean(transcript)
    raw_targets = {raw_clean(x) for x in [front, back, reading or "", *split_answers(back), *split_answers(reading), *answers]}
    raw_targets.discard("")
    if raw and raw in raw_targets:
        return MatchResult(3, "Correct", transcript.strip())

    said = normalize(transcript)
    said_c = canon_long(said)
    # Whisper sometimes writes short Japanese answers in romaji ("Ben Kyo"), and romaji
    # usually drops vowel length, so length can't be judged from such a transcript.
    from_romaji = romaji.has_latin(transcript)
    best: tuple[int, int, str, str, str] | None = None  # (score, -dist, raw, norm, canon)
    for ans in answers:
        exp = normalize(ans)
        if not exp:
            continue
        exp_c = canon_long(exp)
        if said_c == exp_c:
            return MatchResult(3, "Correct", ans)
        if from_romaji and said_c.replace("ー", "") == exp_c.replace("ー", ""):
            return MatchResult(3, f"Correct (heard as romaji \"{transcript.strip()}\", so vowel length wasn't checked)", ans)
        mora_exp = split_mora(exp_c)
        dist = edit_distance(split_mora(said_c), mora_exp)
        score = 2 if dist <= tolerance(len(mora_exp)) else 1
        cand = (score, -dist, ans, exp, exp_c)
        if best is None or cand[:2] > best[:2]:
            best = cand

    if best is None:
        return MatchResult(1, "No expected answer to compare against.", None)
    score, _, ans, exp, exp_c = best
    notes = describe_diff(said_c, exp_c)
    detail = f" ({'; '.join(notes[:3])})" if notes and len(notes) <= 3 else ""
    return MatchResult(score, f"You said {said or '(nothing)'}, expected {exp}{detail}", ans)
