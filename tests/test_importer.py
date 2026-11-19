from pathlib import Path

import pytest

from audioflash import importer
from audioflash.errors import AppError
from audioflash.importer import clean_field, decode_bytes, parse_text, split_furigana


def write(tmp_path: Path, name: str, data: bytes | str) -> Path:
    p = tmp_path / name
    p.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return p


def test_bom_utf8():
    text, enc = decode_bytes("﻿front,back\n猫,cat\n".encode("utf-8"))
    assert enc == "utf-8-sig" and text.startswith("front")


def test_cp932_fallback():
    text, enc = decode_bytes("猫,ねこ\n".encode("cp932"))
    assert enc == "cp932" and text == "猫,ねこ\n"


def test_undecodable():
    with pytest.raises(importer.ImportError_):
        decode_bytes(b"\xff\xfe\xfd\x80\x81")


@pytest.mark.parametrize("delim", [",", "\t", ";"])
def test_delimiter_sniffing(delim):
    p = parse_text(f"猫{delim}cat{delim}ねこ\n犬{delim}dog{delim}いぬ\n")
    assert p.delimiter == delim
    assert p.rows[1] == ["犬", "dog", "いぬ"]
    assert p.columns == ["Column 1", "Column 2", "Column 3"]


def test_header_row_detected():
    p = parse_text("front,back,reading\n話す,to speak,はなす\n")
    assert p.has_header and p.columns == ["front", "back", "reading"]
    assert importer.resolve_mapping(p, None) == {
        "front": 0, "back": 1, "reading": 2, "sentence": None, "sentence_meaning": None, "notes": None
    }


def test_no_header_requires_mapping():
    p = parse_text("a,b\n")
    with pytest.raises(importer.ImportError_):
        importer.resolve_mapping(p, None)


def test_anki_export_headers():
    text = "#separator:tab\n#html:true\n#columns:Front\tBack\n話[はな]す\tto <b>speak</b>[sound:x.mp3]\n"
    p = parse_text(text)
    assert p.anki_export and p.delimiter == "\t"
    assert p.columns == ["Front", "Back"]
    assert len(p.rows) == 1


@pytest.mark.parametrize(
    "raw,clean",
    [
        ("to <b>speak</b>", "to speak"),
        ("a<br>b", "a b"),
        ("a&nbsp;b &amp; c", "a b & c"),
        ("猫[sound:neko.mp3]", "猫"),
        ("  x  ", "x"),
        ("<div>one</div><div>two</div>", "one two"),
    ],
)
def test_clean_field(raw, clean):
    assert clean_field(raw) == clean


@pytest.mark.parametrize(
    "field,front,reading",
    [
        ("話[はな]す", "話す", "はなす"),
        ("お茶[ちゃ]", "お茶", "おちゃ"),
        ("今日[きょう]は 天気[てんき]", "今日は天気", "きょうはてんき"),
        ("猫", "猫", None),
    ],
)
def test_split_furigana(field, front, reading):
    assert split_furigana(field) == (front, reading)


@pytest.fixture
def deck(core):
    return core.create_deck("D", "ja")


def pick(core, path: Path):
    core.file_dialog = lambda: str(path)
    return core.pick_import_file()["path"]


def test_full_import_with_mapping(core, deck, tmp_path):
    p = write(tmp_path, "cards.csv", "猫,cat,ねこ\n犬,dog,いぬ\n,empty,x\n猫,cat,ねこ\n")
    path = pick(core, p)
    prev = core.import_preview(path)
    assert prev["total_rows"] == 4 and prev["detected"]["delimiter"] == "comma"
    res = core.import_cards(path, deck["id"], {"front": 0, "back": 1, "reading": 2})
    assert res["imported"] == 2 and res["skipped_duplicates"] == 1
    assert {e["row"] for e in res["errors"]} == {3, 4}
    cards = core.list_cards(deck["id"])
    assert [c["front"] for c in cards] == ["猫", "犬"]  # file order kept
    assert cards[0]["reading"] == "ねこ"


def test_import_skips_existing_fronts(core, deck, tmp_path):
    core.create_card(deck["id"], "猫", "cat")
    path = pick(core, write(tmp_path, "c.csv", "front,back\n猫,cat\n犬,dog\n"))
    res = core.import_cards(path, deck["id"], None)
    assert res["imported"] == 1 and res["skipped_duplicates"] == 1
    assert res["errors"][0]["row"] == 2  # header is row 1


def test_import_anki_furigana_into_reading(core, deck, tmp_path):
    text = "#separator:tab\n#html:true\n話[はな]す\tto <b>speak</b>[sound:a.mp3]\n"
    path = pick(core, write(tmp_path, "anki.txt", text))
    core.import_cards(path, deck["id"], {"front": 0, "back": 1, "reading": None})
    c = core.list_cards(deck["id"])[0]
    assert (c["front"], c["back"], c["reading"]) == ("話す", "to speak", "はなす")


def test_bad_deck_id(core, tmp_path):
    path = pick(core, write(tmp_path, "c.csv", "a,b\n"))
    with pytest.raises(AppError) as e:
        core.import_cards(path, 999, {"front": 0, "back": 1})
    assert e.value.code == "not_found"


def test_bad_mapping(core, deck, tmp_path):
    path = pick(core, write(tmp_path, "c.csv", "a,b\n"))
    for m in ({"front": 0, "back": 0}, {"front": 5, "back": 1}, {"front": "0", "back": 1}):
        with pytest.raises(AppError) as e:
            core.import_cards(path, deck["id"], m)
        assert e.value.code == "import_error"


def test_only_picked_path_accepted(core, deck, tmp_path):
    picked = write(tmp_path, "a.csv", "a,b\n")
    other = write(tmp_path, "b.csv", "c,d\n")
    with pytest.raises(AppError) as e:
        core.import_preview(str(picked))  # nothing picked yet
    assert e.value.code == "invalid_input"
    pick(core, picked)
    for bad in (str(other), "/etc/passwd", None, 5):
        with pytest.raises(AppError) as e:
            core.import_preview(bad)
        assert e.value.code == "invalid_input"
    assert core.import_preview(str(picked))["total_rows"] == 1


def test_pick_rejects_wrong_extension(core, tmp_path):
    p = write(tmp_path, "x.json", "{}")
    core.file_dialog = lambda: str(p)
    with pytest.raises(AppError) as e:
        core.pick_import_file()
    assert e.value.code == "import_error"


def test_pick_cancelled(core):
    core.file_dialog = lambda: None
    assert core.pick_import_file() is None


def test_cp932_file_import(core, deck, tmp_path):
    path = pick(core, write(tmp_path, "sj.csv", "猫,ねこ\n".encode("cp932")))
    assert core.import_preview(path)["detected"]["encoding"] == "cp932"
    assert core.import_cards(path, deck["id"], {"front": 0, "back": 1})["imported"] == 1


def test_kaishi_style_same_front_two_readings_with_sentences(core, deck, tmp_path):
    # Rows shaped like Kaishi 1.5k: word, reading, meaning, furigana, audio, sentence, translation
    text = (
        "#separator:tab\n#html:true\n"
        "方\tほう\tdirection, side\t方[ほう]\t[sound:a.mp3]\t彼は私の<b>方</b>を見ました。\tHe looked in my direction.\t\n"
        "さん\tさん\tsan\t\t\t\t\t\"Polite honorific.<br>Like \"\"M.\"\" in English.\"\n"
        "方\tかた\tperson (polite form)\t方[かた]\t[sound:b.mp3]\t次の<b>方</b>、どうぞ。\tThe next person, please.\n"
        "方\tかた\tperson (polite form)\t\t\t\t\n"
        "高い\tたかい\texpensive\t\t\tこの時計は<b>高い</b>。\tThis watch is expensive.\n"
        "高い\tたかい\thigh, tall\t\t\tあの山は<b>高い</b>。\tThat mountain is high.\n"
    )
    path = pick(core, write(tmp_path, "kaishi.txt", text))
    res = core.import_cards(
        path, deck["id"], {"front": 0, "back": 2, "reading": 1, "sentence": 5, "sentence_meaning": 6, "notes": 7}
    )
    assert res["imported"] == 5 and res["skipped_duplicates"] == 1
    assert res["errors"][0]["reason"] == "duplicate: 方 (かた)"
    a, san, b, t1, t2 = core.list_cards(deck["id"])
    assert san["notes"] == 'Polite honorific. Like "M." in English.'
    assert a["notes"] is None
    assert (t1["back"], t2["back"]) == ("expensive", "high, tall")
    assert (a["front"], a["reading"], a["back"]) == ("方", "ほう", "direction, side")
    assert (b["front"], b["reading"], b["back"]) == ("方", "かた", "person (polite form)")
    assert b["sentence"] == "次の<b>方</b>、どうぞ。"
    assert b["sentence_meaning"] == "The next person, please."


def test_sentence_columns_optional(core, deck, tmp_path):
    path = pick(core, write(tmp_path, "c.csv", "猫,cat,ねこ\n"))
    core.import_cards(path, deck["id"], {"front": 0, "back": 1, "reading": 2})
    c = core.list_cards(deck["id"])[0]
    assert c["sentence"] is None and c["sentence_meaning"] is None


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("<b>私</b>はアンです。", "<b>私</b>はアンです。"),
        ("<B>猫</B>が<span style='x'>いる</span>[sound:s.mp3]", "<b>猫</b>がいる"),
        ("a &lt;script&gt; b", "a script b"),  # no tags other than <b> survive
        ("<b>私[わたし]</b>は", "<b>私[わたし]</b>は"),
    ],
)
def test_clean_sentence(raw, clean):
    assert importer.clean_sentence(raw) == clean
