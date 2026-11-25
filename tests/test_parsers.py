import pytest

from app.config import Settings, Thresholds, load_settings
from app.ingest.parsers import (
    ParseError,
    parse_auto,
    parse_csv,
    parse_json,
    parse_text,
    participants,
    to_scored,
)


def test_parse_json_list():
    msgs = parse_json(
        '[{"sender":"a","text":"hi","timestamp":"2024-05-01T10:00:00Z"},{"from":"b","message":"yo"}]'
    )
    assert [m.sender for m in msgs] == ["a", "b"]
    assert msgs[0].timestamp.year == 2024 and msgs[0].timestamp.tzinfo is None
    assert msgs[1].timestamp is None


def test_parse_json_object_and_jsonl():
    assert len(parse_json('{"messages":[{"author":"x","content":"1"}]}')) == 1
    assert len(parse_json('{"sender":"a","text":"1"}\n{"sender":"b","text":"2"}\n')) == 2


def test_parse_json_errors():
    with pytest.raises(ParseError):
        parse_json("")
    with pytest.raises(ParseError):
        parse_json('[{"sender":"a"}]')
    with pytest.raises(ParseError):
        parse_json('{"nope": 1}')


def test_parse_text_lines_and_continuations():
    msgs = parse_text("[2024-05-01 10:00] Kid: hello\nStranger: hey there\nhow are you\n\nKid: good")
    assert [m.sender for m in msgs] == ["Kid", "Stranger", "Kid"]
    assert msgs[1].text == "hey there\nhow are you"
    assert msgs[0].timestamp is not None


def test_parse_text_rejects_garbage():
    with pytest.raises(ParseError):
        parse_text("no colon here at all")


def test_parse_csv_flexible_headers():
    msgs = parse_csv('Author,Message,Time\nkid,hi,1714557600\nother,"hello, friend",1714557660\n')
    assert msgs[1].text == "hello, friend"
    assert msgs[0].timestamp is not None


def test_parse_auto_dispatch():
    assert parse_auto('[{"sender":"a","text":"b"}]')[0].sender == "a"
    assert parse_auto("sender,text\na,b\n")[0].text == "b"
    assert parse_auto("a: b")[0].text == "b"
    assert parse_auto("a: b", filename="log.txt")[0].sender == "a"


def test_to_scored_marks_child_and_hashes():
    msgs = parse_text("Kid: hi\nOther: yo\nkid: bye")
    assert participants(msgs) == ["Kid", "Other", "kid"]
    scored = to_scored(msgs, "Kid")
    assert [m.is_child for m in scored] == [True, False, False]
    assert scored[0].sender_hash != "Kid" and len(scored[0].sender_hash) == 16
    with pytest.raises(ParseError):
        to_scored(msgs, "nobody")


def test_settings_env_and_thresholds():
    s = load_settings({"GUARDIAN_ENGINE": "ensemble", "GUARDIAN_RETENTION_DAYS": "7"})
    assert s.engine == "ensemble" and s.retention_days == 7
    assert not s.llm_available
    with pytest.raises(ValueError):
        Thresholds(medium=50, high=40, critical=90)
    assert Settings().thresholds.high == 70
