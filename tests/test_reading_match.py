import pytest

from audioflash.jp_numbers import kanji_to_int, numbers_to_kana, read_number
from audioflash.reading_match import (
    describe_diff,
    match,
    normalize,
    split_answers,
    split_mora,
    tolerance,
)


# --- normalization --------------------------------------------------------------


def test_homophone_kanji_normalizes_same():
    assert normalize("離す") == normalize("話す") == "はなす"


def test_katakana_vs_hiragana():
    assert normalize("ハナス") == "はなす"
    assert match("ハナス", "話す", "to speak", "はなす").score == 3


def test_punctuation_and_spaces_stripped():
    assert normalize("はな す。") == "はなす"
    assert normalize("ｈａｎａｓｕ") == "はなす"  # NFKC full-width, then romaji


@pytest.mark.parametrize(
    "text,kana",
    [
        ("3本", "さんぼん"),
        ("1人", "ひとり"),
        ("３本", "さんぼん"),  # full-width digit
        ("1万", "いちまん"),
        ("300", "さんびゃく"),
        ("三つ", "みっつ"),
        ("6本", "ろっぽん"),
        ("8分", "はっぷん"),
        ("3階", "さんがい"),
        ("4時", "よじ"),
        ("20歳", "はたち"),
        ("3日", "みっか"),
        ("何本", "なんぼん"),
        ("十一本", "じゅういっぽん"),
        ("12,000円", "いちまんにせんえん"),
    ],
)
def test_numbers(text, kana):
    assert normalize(text) == kana


def test_kanji_numeral_words_left_to_analyzer():
    assert numbers_to_kana("一緒") == "一緒"
    assert numbers_to_kana("十分") == "十分"  # not forced to じゅっぷん


def test_kanji_to_int():
    assert kanji_to_int("三百二十一") == 321
    assert kanji_to_int("一万五千") == 15000
    assert kanji_to_int("二〇二六") == 2026
    assert kanji_to_int("abc") is None


def test_read_number():
    assert read_number(2026) == "にせんにじゅうろく"
    assert read_number(800) == "はっぴゃく"
    assert read_number(3000) == "さんぜん"
    assert read_number(14) == "じゅうよん"


# --- splitting ------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,answers",
    [
        ("話す、しゃべる", ["話す", "しゃべる"]),
        ("a; b；c", ["a", "b", "c"]),
        ("x/y／z", ["x", "y", "z"]),
        ("a,b，c", ["a", "b", "c"]),
        ("話す (はなす)", ["話す", "はなす"]),
        ("話す（はなす）", ["話す", "はなす"]),
        ("line1\nline2", ["line1", "line2"]),
        ("コンピューター・ゲーム", ["コンピューター・ゲーム"]),
        ("", []),
        (None, []),
    ],
)
def test_split_answers(field, answers):
    assert split_answers(field) == answers


# --- mora & tolerance -------------------------------------------------------------


def test_split_mora():
    assert split_mora("きょう") == ["きょ", "う"]
    assert split_mora("がっこう") == ["が", "っ", "こ", "う"]
    assert split_mora("こーひー") == ["こ", "ー", "ひ", "ー"]
    assert split_mora("ほん") == ["ほ", "ん"]


def test_tolerance_boundary():
    assert tolerance(8) == 1
    assert tolerance(9) == 2  # ceil(1.35)
    assert tolerance(20) == 3


# --- matching ---------------------------------------------------------------------


def test_exact_and_homophone():
    assert match("話す", "話す", "to speak", "はなす").score == 3
    assert match("離す", "話す", "to speak", "はなす").score == 3


def test_raw_match_guard_kyou():
    # The analyzer could read 今日 as こんにち; the raw match accepts it regardless.
    r = match("今日", "今日", "today", "こんにち")
    assert r.score == 3


def test_front_raw_match():
    assert match("猫", "猫", "cat", "ねこ").score == 3


@pytest.mark.parametrize(
    "said,expected,note",
    [
        ("おばさん", "おばあさん", "long vowel missing"),
        ("きて", "きって", "small っ missing"),
        ("ほん", "ほんん", "ん missing"),
        ("きよう", "きょう", "small kana: きよ instead of きょ"),
        ("はなし", "はなす", "different sound: し instead of す"),
    ],
)
def test_one_mora_errors_score_2_with_feedback(said, expected, note):
    r = match(said, "x", "y", expected)
    assert r.score == 2
    assert note in r.feedback
    assert r.feedback.startswith(f"You said {said}, expected {expected}")


def test_two_errors_on_short_word_scores_1():
    assert match("ねこ", "犬", "dog", "いぬ").score == 1


def test_tolerance_8_vs_9_mora():
    eight = "あいうえおかきく"  # 8 mora: tolerance 1
    assert match("あいうえおかきけ", "x", "y", eight).score == 2
    assert match("あいうえおかけけ", "x", "y", eight).score == 1
    nine = "あいうえおかきくけ"  # 9 mora: tolerance 2
    assert match("あいうえおかきここ", "x", "y", nine).score == 2
    assert match("あいうえおかこここ", "x", "y", nine).score == 1


def test_alt_answers_accepted():
    r = match("しゃべる", "話す", "to speak", "はなす", ["しゃべる"])
    assert r.score == 3


def test_multi_answer_reading_field():
    assert match("しゃべる", "話す", "to speak", "はなす、しゃべる").score == 3


def test_back_used_when_no_reading():
    assert match("いぬ", "dog", "犬").score == 3


def test_numbers_in_transcript_match_kana_answer():
    assert match("3本", "三本", "three", "さんぼん").score == 3


def test_feedback_correct():
    assert match("はなす", "話す", "x", "はなす").feedback == "Correct"


def test_describe_diff_extra():
    assert describe_diff("がっこう", "がこう") == ["small っ extra"]


# --- long vowels, romaji, ヴ, ・ splitting (owner feedback 2026-11-17) ------------------


@pytest.mark.parametrize("said", ["いい", "イー", "いー", "イイ"])
def test_ii_spellings_all_correct(said):
    assert match(said, "良い", "good", "いい").score == 3


@pytest.mark.parametrize(
    "a,b",
    [("せんせい", "せんせー"), ("とうきょう", "とーきょー"), ("こうひい", "こーひー"), ("おばあさん", "おばーさん")],
)
def test_canon_long(a, b):
    from audioflash.reading_match import canon_long

    assert canon_long(a) == canon_long(b) == b


def test_long_vowel_still_required():
    r = match("おばさん", "x", "y", "おばあさん")
    assert r.score == 2 and "long vowel missing" in r.feedback
    assert match("べんきょ", "勉強", "study", "べんきょう").score == 2


@pytest.mark.parametrize("said", ["benkyou", "benkyō", "BENKYOU", "ヴェンキョウ", "venkyou"])
def test_romaji_and_vu_transcripts(said):
    assert match(said, "勉強", "study", "べんきょう").score == 3


def test_romaji_without_length_is_lenient_and_says_so():
    r = match("Ben Kyo", "勉強", "study", "べんきょう")
    assert r.score == 3 and "romaji" in r.feedback


def test_b_vs_p_is_still_a_mistake():
    r = match("ペンキョウ", "勉強", "study", "べんきょう")
    assert r.score == 2 and "ぺ instead of べ" in r.feedback


@pytest.mark.parametrize(
    "field,answers",
    [
        ("なに・なん", ["なに", "なん"]),
        ("なに･なん", ["なに", "なん"]),  # half-width dot
        ("なに * なん", ["なに", "なん"]),
        ("テレビ・てれび", ["テレビ", "てれび"]),
        ("コンピューター・ゲーム", ["コンピューター・ゲーム"]),
    ],
)
def test_dot_splitting(field, answers):
    assert split_answers(field) == answers


def test_nani_nan_card():
    assert match("なん", "何", "what", "なに・なん").score == 3
    assert match("なに", "何", "what", "なに・なん").score == 3


@pytest.mark.parametrize(
    "romaji_text,kana",
    [("gakkou", "がっこう"), ("shimbun", "しんぶん"), ("konnichiwa", "こんにちわ"), ("matcha", "まっちゃ"), ("jisho", "じしょ"), ("onna", "おんな")],
)
def test_romaji_to_hiragana(romaji_text, kana):
    from audioflash.romaji import to_hiragana

    assert to_hiragana(romaji_text) == kana
