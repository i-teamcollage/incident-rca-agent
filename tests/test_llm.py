import json

import pytest

from incident_rca_agent.llm import LLM, InvalidOutput, ReplayMiss, ToolCall, fingerprint, run_dir_name

MESSAGES = [{"role": "user", "content": "Extract the IBAN."}]


class FakeOllama:
    """Stands in for the Ollama server: answers from a script and records what it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, str):
            answer = {"content": answer}
        return {"content": "", "tool_calls": [], "prompt_tokens": 120, "output_tokens": 30, **answer}


def make(tmp_path, mode, transport=None, **kwargs):
    return LLM(model="ministral-3:8b", mode=mode, runs_root=tmp_path, num_ctx=8192, transport=transport, **kwargs)


def test_run_folder_is_named_after_the_model_tag():
    assert run_dir_name("ministral-3:8b") == "ministral-3-8b"


def test_fingerprint_ignores_key_order():
    a = {"model": "m", "messages": MESSAGES, "options": {"seed": 7, "temperature": 0}}
    b = {"options": {"temperature": 0, "seed": 7}, "messages": MESSAGES, "model": "m"}
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_changes_when_the_question_changes():
    a = {"model": "m", "messages": MESSAGES}
    b = {"model": "m", "messages": [{"role": "user", "content": "Extract the BIC."}]}
    assert fingerprint(a) != fingerprint(b)


def test_unknown_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="mode"):
        make(tmp_path, "sometimes")


def test_every_call_is_deterministic(tmp_path):
    fake = FakeOllama("DE89370400440532013000")
    make(tmp_path, "live", fake, seed=11).chat(MESSAGES)
    assert fake.requests[0]["options"] == {"temperature": 0, "seed": 11, "num_ctx": 8192}


def test_live_mode_returns_the_model_answer(tmp_path):
    reply = make(tmp_path, "live", FakeOllama("DE89370400440532013000")).chat(MESSAGES)
    assert reply.text == "DE89370400440532013000"
    assert (reply.prompt_tokens, reply.output_tokens) == (120, 30)


def test_live_mode_does_not_write_a_run(tmp_path):
    make(tmp_path, "live", FakeOllama("x")).chat(MESSAGES)
    assert not (tmp_path / "ministral-3-8b" / "calls.jsonl").exists()


def test_recorded_answers_replay_without_the_model(tmp_path):
    recorder = make(tmp_path, "record", FakeOllama("DE89370400440532013000"))
    recorder.start_recording()
    recorder.chat(MESSAGES, label="doc-001")

    replay = make(tmp_path, "replay", transport=None)
    reply = replay.chat(MESSAGES, label="doc-001")
    assert reply.text == "DE89370400440532013000"


def test_replay_keeps_the_live_timing(tmp_path):
    recorder = make(tmp_path, "record", FakeOllama("x"))
    recorder.start_recording()
    live_reply = recorder.chat(MESSAGES)
    assert make(tmp_path, "replay").chat(MESSAGES).seconds == live_reply.seconds


def test_replay_miss_says_what_to_do(tmp_path):
    recorder = make(tmp_path, "record", FakeOllama("x"))
    recorder.start_recording()
    recorder.chat(MESSAGES)
    replay = make(tmp_path, "replay")
    with pytest.raises(ReplayMiss, match="doc-002.*make record"):
        replay.chat([{"role": "user", "content": "Something new"}], label="doc-002")


def test_replay_without_a_run_explains_the_missing_file(tmp_path):
    with pytest.raises(ReplayMiss, match="calls.jsonl"):
        make(tmp_path, "replay").chat(MESSAGES, label="doc-001")


def test_start_recording_discards_the_previous_run(tmp_path):
    first = make(tmp_path, "record", FakeOllama("old"))
    first.start_recording()
    first.chat([{"role": "user", "content": "old question"}])
    second = make(tmp_path, "record", FakeOllama("new"))
    second.start_recording()
    second.chat(MESSAGES)
    lines = (tmp_path / "ministral-3-8b" / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["response"]["content"] == "new"


def test_tool_calls_are_returned_as_objects(tmp_path):
    fake = FakeOllama({"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "NH-1042"}}]})
    reply = make(tmp_path, "live", fake).chat(MESSAGES, tools=[{"type": "function"}])
    assert reply.tool_calls == [ToolCall(name="lookup_order", arguments={"order_id": "NH-1042"})]
    assert fake.requests[0]["tools"] == [{"type": "function"}]


SCHEMA = {"type": "object", "properties": {"iban": {"type": "string"}}, "required": ["iban"]}


def check_iban(data):
    if not isinstance(data, dict) or "iban" not in data:
        raise ValueError("iban is missing")
    return data["iban"]


def test_structured_answer_is_parsed_and_validated(tmp_path):
    fake = FakeOllama('{"iban": "DE89370400440532013000"}')
    reply = make(tmp_path, "live", fake).structured(MESSAGES, schema=SCHEMA, validate=check_iban)
    assert reply.data == "DE89370400440532013000"
    assert fake.requests[0]["format"] == SCHEMA


def test_invalid_answer_gets_one_retry_with_the_error(tmp_path):
    fake = FakeOllama('{"bic": "COBADEFFXXX"}', '{"iban": "DE89370400440532013000"}')
    reply = make(tmp_path, "live", fake).structured(MESSAGES, schema=SCHEMA, validate=check_iban)
    assert reply.data == "DE89370400440532013000"
    retry_prompt = fake.requests[1]["messages"][-1]["content"]
    assert "iban is missing" in retry_prompt


def test_two_invalid_answers_raise_invalid_output(tmp_path):
    fake = FakeOllama("not json", '{"bic": "x"}')
    with pytest.raises(InvalidOutput, match="doc-007"):
        make(tmp_path, "live", fake).structured(MESSAGES, schema=SCHEMA, validate=check_iban, label="doc-007")


def test_retry_is_recorded_and_replayed_too(tmp_path):
    recorder = make(tmp_path, "record", FakeOllama("not json", '{"iban": "DE89370400440532013000"}'))
    recorder.start_recording()
    recorder.structured(MESSAGES, schema=SCHEMA, validate=check_iban)
    reply = make(tmp_path, "replay").structured(MESSAGES, schema=SCHEMA, validate=check_iban)
    assert reply.data == "DE89370400440532013000"
