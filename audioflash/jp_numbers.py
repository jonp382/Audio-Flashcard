"""Deterministic readings for numbers and numeral+counter pairs.

unidic-lite reads counters poorly (三本 -> ミモト, 一本 -> イチポン, 三匹 -> サンヒキ), so
numbers are converted to kana here, before the morphological analyzer runs
(agent.md §6a "numbers"). Arabic numerals are always converted; kanji numerals only
when followed by a known counter, so words like 一緒 or 一生 are left to the analyzer.
"""

from __future__ import annotations

import re

_KANJI_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_UNITS = {"十": 10, "百": 100, "千": 1000}
_KANJI_BIG = {"万": 10_000, "億": 100_000_000}

_DIGIT_KANA = ["ぜろ", "いち", "に", "さん", "よん", "ご", "ろく", "なな", "はち", "きゅう"]


def kanji_to_int(s: str) -> int | None:
    """'三百二十一' -> 321, '二〇二六' -> 2026, '一万五千' -> 15000. None if not a numeral."""
    if not s:
        return None
    if all(c in _KANJI_DIGITS for c in s):  # positional, e.g. 二〇二六
        n = 0
        for c in s:
            n = n * 10 + _KANJI_DIGITS[c]
        return n
    total, section, digit = 0, 0, None
    for c in s:
        if c in _KANJI_DIGITS:
            if digit is not None:
                return None
            digit = _KANJI_DIGITS[c]
        elif c in _KANJI_UNITS:
            section += (1 if digit is None else digit) * _KANJI_UNITS[c]
            digit = None
        elif c in _KANJI_BIG:
            section += digit or 0
            total += (section or 1) * _KANJI_BIG[c]
            section, digit = 0, None
        else:
            return None
    return total + section + (digit or 0)


def _read_below_10000(n: int) -> str:
    out = []
    th, n = divmod(n, 1000)
    if th:
        out.append({1: "せん", 3: "さんぜん", 8: "はっせん"}.get(th, _DIGIT_KANA[th] + "せん"))
    h, n = divmod(n, 100)
    if h:
        out.append({1: "ひゃく", 3: "さんびゃく", 6: "ろっぴゃく", 8: "はっぴゃく"}.get(h, _DIGIT_KANA[h] + "ひゃく"))
    t, n = divmod(n, 10)
    if t:
        out.append("じゅう" if t == 1 else _DIGIT_KANA[t] + "じゅう")
    if n:
        out.append(_DIGIT_KANA[n])
    return "".join(out)


def read_number(n: int, four: str = "よん", seven: str = "なな", nine: str = "きゅう") -> str:
    """Kana reading of a non-negative integer. ``four``/``seven``/``nine`` choose the
    reading of a final ones digit (e.g. よ for 四円, しち/く for 七時/九時)."""
    if n == 0:
        return "ぜろ"
    last_digit = n % 10
    parts = []
    rest = n
    for unit, kana in ((100_000_000, "おく"), (10_000, "まん")):
        q, rest = divmod(rest, unit)
        if q:
            parts.append(("いち" if q == 1 else _read_below_10000(q)) + kana)
    if rest:
        parts.append(_read_below_10000(rest))
    s = "".join(parts)
    default = {4: "よん", 7: "なな", 9: "きゅう"}
    chosen = {4: four, 7: seven, 9: nine}
    if last_digit in default and s.endswith(default[last_digit]):
        s = s[: -len(default[last_digit])] + chosen[last_digit]
    return s


_GEMINATE = (("いち", "いっ"), ("ろく", "ろっ"), ("はち", "はっ"), ("じゅう", "じゅっ"), ("ひゃく", "ひゃっ"))
_VOICE = str.maketrans("かきくけこさしすせそたちつてとはひふへほ", "がぎぐげござじずぜぞだぢづでどばびぶべぼ")
_HALF = str.maketrans("はひふへほ", "ぱぴぷぺぽ")


def _first(reading: str, table: dict) -> str:
    """Apply a sound change to the first kana only (ひき -> びき, not びぎ)."""
    return reading[:1].translate(table) + reading[1:]


def _geminate(num: str, allowed: tuple[str, ...]) -> str | None:
    for tail, rep in _GEMINATE:
        if tail in allowed and num.endswith(tail):
            return num[: -len(tail)] + rep
    return None


# counter -> (reading, class). Classes:
#   h  : 1,6,8,10,100 geminate + p-sound; 3/何/千/万 voice (本 匹 杯)
#   f  : like h but 3/4/何 also give p (分)
#   k  : 1,6,8,10,100 geminate (個 回)
#   kv : k, plus 3/何 voice (階 軒)
#   s  : 1,8,10 geminate (冊 歳)
#   sv : s, plus 3/何 voice (足)
#   t  : 1,8,10 geminate (頭 点)
#   y  : 4 -> よ (円 年)
#   ji : 4 -> よ, 7 -> しち, 9 -> く (時)
#   "" : plain
COUNTERS: dict[str, tuple[str, str]] = {
    "本": ("ほん", "h"), "匹": ("ひき", "h"), "杯": ("はい", "h"), "泊": ("はく", "h"), "歩": ("ほ", "h"),
    "分": ("ふん", "f"),
    "個": ("こ", "k"), "回": ("かい", "k"), "件": ("けん", "k"), "曲": ("きょく", "k"), "課": ("か", "k"),
    "ヶ月": ("かげつ", "k"), "か月": ("かげつ", "k"), "ヵ月": ("かげつ", "k"), "ヶ国": ("かこく", "k"), "か国": ("かこく", "k"),
    "階": ("かい", "kv"), "軒": ("けん", "kv"),
    "冊": ("さつ", "s"), "歳": ("さい", "s"), "才": ("さい", "s"), "週間": ("しゅうかん", "s"), "週": ("しゅう", "s"),
    "足": ("そく", "sv"),
    "頭": ("とう", "t"), "点": ("てん", "t"), "通": ("つう", "t"), "着": ("ちゃく", "t"),
    "円": ("えん", "y"), "年": ("ねん", "y"), "年間": ("ねんかん", "y"),
    "時間": ("じかん", "ji"), "時": ("じ", "ji"),
    "枚": ("まい", ""), "台": ("だい", ""), "番": ("ばん", ""), "度": ("ど", ""), "秒": ("びょう", ""),
    "号": ("ごう", ""), "倍": ("ばい", ""), "羽": ("わ", ""), "名": ("めい", ""), "万": ("まん", ""),
    "人": ("にん", "nin"), "つ": ("つ", "tsu"), "日": ("にち", "nichi"), "日間": ("にちかん", "nichi"),
    "月": ("がつ", "gatsu"),
}
_COUNTER_ALT = "|".join(sorted((re.escape(k) for k in COUNTERS), key=len, reverse=True))

_TSU = {1: "ひとつ", 2: "ふたつ", 3: "みっつ", 4: "よっつ", 5: "いつつ", 6: "むっつ", 7: "ななつ", 8: "やっつ", 9: "ここのつ", 10: "とお"}
_NICHI = {1: "いちにち", 2: "ふつか", 3: "みっか", 4: "よっか", 5: "いつか", 6: "むいか", 7: "なのか", 8: "ようか", 9: "ここのか", 10: "とおか", 14: "じゅうよっか", 20: "はつか", 24: "にじゅうよっか"}
_GATSU = {4: "しがつ", 7: "しちがつ", 9: "くがつ"}


def read_with_counter(n: int | None, counter: str) -> str:
    """n=None means 何 (なん)."""
    reading, cls = COUNTERS[counter]
    if cls == "nin":
        if n == 1:
            return "ひとり"
        if n == 2:
            return "ふたり"
        return ("なん" if n is None else read_number(n, four="よ")) + "にん"
    if cls == "tsu":
        if n is None:
            return "いくつ"
        if n in _TSU:
            return _TSU[n]
        return read_number(n) + "つ"
    if cls == "nichi":
        if n is None:
            return "なんにち" + reading[2:]
        if n in _NICHI and counter == "日":
            return _NICHI[n]
        if n in _NICHI and n != 1:
            return _NICHI[n] + "かん"
        return read_number(n) + reading
    if cls == "gatsu":
        if n is None:
            return "なんがつ"
        return _GATSU.get(n, read_number(n) + "がつ")
    if n == 20 and counter in ("歳", "才"):
        return "はたち"

    if cls == "y":
        num = "なん" if n is None else read_number(n, four="よ")
    elif cls == "ji":
        num = "なん" if n is None else read_number(n, four="よ", seven="しち", nine="く")
    else:
        num = "なん" if n is None else read_number(n)

    if cls in ("h", "f"):
        g = _geminate(num, ("いち", "ろく", "はち", "じゅう", "ひゃく"))
        if g:
            return g + _first(reading, _HALF)
        if cls == "f" and (num.endswith("さん") or num.endswith("よん") or num == "なん"):
            return num + _first(reading, _HALF)
        if num.endswith(("さん", "せん", "まん")) or num == "なん":
            return num + _first(reading, _VOICE)
        return num + reading
    if cls in ("k", "kv"):
        g = _geminate(num, ("いち", "ろく", "はち", "じゅう", "ひゃく"))
        if g:
            return g + reading
        if cls == "kv" and (num.endswith("さん") or num == "なん"):
            return num + _first(reading, _VOICE)
        return num + reading
    if cls in ("s", "sv", "t"):
        g = _geminate(num, ("いち", "はち", "じゅう"))
        if g:
            return g + reading
        if cls == "sv" and (num.endswith("さん") or num == "なん"):
            return num + _first(reading, _VOICE)
        return num + reading
    return num + reading


# Kanji words that start with a numeral+counter but are lexical items with their own
# reading. Left for the analyzer.
_KANJI_EXCEPTIONS = ("十分", "三日月", "一月", "一日中", "一人一人", "一人で", "一つ一つ", "一番")

_ARABIC = r"\d+(?:,\d{3})*(?:\.\d+)?"
_KANJI_NUM = r"[〇零一二三四五六七八九十百千万億]+"
_TOKEN_RE = re.compile(rf"(?P<ar>{_ARABIC})(?P<arc>{_COUNTER_ALT})?|(?P<kj>{_KANJI_NUM}|何)(?P<kjc>{_COUNTER_ALT})")


def _arabic_to_int(s: str) -> int | None:
    s = s.replace(",", "")
    if "." in s:
        return None
    return int(s)


def _read_decimal(s: str) -> str:
    whole, frac = s.replace(",", "").split(".", 1)
    return read_number(int(whole)) + "てん" + "".join(_DIGIT_KANA[int(d)] for d in frac)


def numbers_to_kana(text: str) -> str:
    """Replace numbers (and numeral+counter pairs) with kana. Input should be NFKC'd."""

    def repl(m: re.Match) -> str:
        start = m.start()
        for exc in _KANJI_EXCEPTIONS:
            if text.startswith(exc, start) and m.group("kj"):
                return m.group(0)
        if m.group("ar") is not None:
            n = _arabic_to_int(m.group("ar"))
            counter = m.group("arc")
            if n is None:
                return _read_decimal(m.group("ar")) + (counter or "")
            if counter:
                return read_with_counter(n, counter)
            return read_number(n)
        kj = m.group("kj")
        n = None if kj == "何" else kanji_to_int(kj)
        if kj != "何" and n is None:
            return m.group(0)
        if kj == "何" and m.group("kjc") in ("人", "日", "日間", "時", "時間", "分", "本", "匹", "杯", "回", "個", "階", "歳", "枚", "台", "番", "度", "月", "年", "つ", "円", "冊"):
            return read_with_counter(None, m.group("kjc"))
        if kj == "何":
            return m.group(0)
        return read_with_counter(n, m.group("kjc"))

    return _TOKEN_RE.sub(repl, text)
