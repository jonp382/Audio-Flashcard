import pytest

from audioflash.evaluator import GradingFailed, LLMUnavailable, build_messages, parse_grade
from audioflash.grading import grade_answer


def test_check_detects_model_and_thinking(ollama_fake):
    c = ollama_fake.client()
    st = c.check()
    assert st.reachable and st.model_present and st.supports_thinking
    assert c.setup_steps() == []


def test_think_sent_only_when_supported(ollama_fake):
    c = ollama_fake.client()
    c.check()
    c.grade("話す", "to speak", None, "en", "to talk")
    chat = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert chat["think"] is False
    assert chat["keep_alive"] == "30m"
    assert chat["options"] == {"temperature": 0.1, "num_predict": 256}
    assert chat["format"]["required"] == ["score", "feedback"]

    ollama_fake.capabilities = ["completion"]
    c.check()
    c.grade("話す", "to speak", None, "en", "to talk")
    chat = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert "think" not in chat


def test_missing_model_setup_steps(ollama_fake):
    ollama_fake.models = ["llama3:8b"]
    c = ollama_fake.client()
    c.check()
    assert not c.ready
    assert c.setup_steps() == ["ollama pull qwen3:8b"]
    with pytest.raises(LLMUnavailable):
        c.grade("a", "b", None, "en", "c")


def test_unreachable(ollama_fake):
    ollama_fake.down = True
    c = ollama_fake.client()
    c.check()
    assert not c.status.reachable
    assert "Install ollama" in c.setup_steps()[0]
    with pytest.raises(LLMUnavailable):
        c.grade("a", "b", None, "en", "c")


def test_down_after_check_is_unavailable(ollama_fake):
    c = ollama_fake.client()
    c.check()
    ollama_fake.down = True
    with pytest.raises(LLMUnavailable):
        c.grade("a", "b", None, "en", "c")


def test_unparseable_output(ollama_fake):
    ollama_fake.reply = "not json"
    c = ollama_fake.client()
    with pytest.raises(GradingFailed):
        c.grade("a", "b", None, "en", "c")
    ollama_fake.reply = {"feedback": "no score"}
    with pytest.raises(GradingFailed):
        c.grade("a", "b", None, "en", "c")


def test_score_clamped():
    assert parse_grade('{"score": 7, "feedback": "x"}').score == 3
    assert parse_grade('{"score": -2, "feedback": "x"}').score == 0


def test_warm_up_request(ollama_fake):
    c = ollama_fake.client()
    c.warm_up()
    path, body = ollama_fake.requests[-1]
    assert path == "/api/generate" and body == {"model": "qwen3:8b", "keep_alive": "30m"}


def test_prompt_contains_card_fields():
    msgs = build_messages("話す", "to speak", "はなす", "en", "to talk")
    user = msgs[1]["content"]
    for s in ("話す", "to speak", "はなす", "English", "to talk"):
        assert s in user
    assert "0 =" in msgs[0]["content"] and "3 =" in msgs[0]["content"]


def test_model_tag_is_a_setting(ollama_fake):
    ollama_fake.models = ["gemma3:4b"]
    c = ollama_fake.client()
    c.configure("localhost", 11434, "gemma3:4b")
    c.check()
    c.grade("a", "b", None, "en", "c")
    assert [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]["model"] == "gemma3:4b"


# --- dispatch -----------------------------------------------------------------------


def test_ja_never_calls_ollama(ollama_fake):
    c = ollama_fake.client()
    g = grade_answer("ja", "はなす", "話す", "to speak", "はなす", [], c)
    assert g.score == 3 and g.is_correct
    assert ollama_fake.requests == []


def test_ja_works_with_ollama_down(ollama_fake):
    ollama_fake.down = True
    g = grade_answer("ja", "おばさん", "x", "y", "おばあさん", [], ollama_fake.client())
    assert g.score == 2 and "long vowel missing" in g.feedback


def test_en_uses_llm(ollama_fake):
    ollama_fake.reply = {"score": 2, "feedback": "Close."}
    g = grade_answer("en", "to talk", "話す", "to speak", None, ["to chat"], ollama_fake.client())
    assert (g.score, g.feedback, g.is_correct) == (2, "Close.", True)
    chat = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert "to chat" in chat["messages"][1]["content"]
