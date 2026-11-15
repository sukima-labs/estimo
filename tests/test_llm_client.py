from __future__ import annotations

import json

import httpx
import pytest
import respx

from mikiwame.llm.client import (
    BadResponse,
    ImagePart,
    LLMClient,
    LLMError,
    LLMRequest,
    QuotaExceeded,
    extract_json,
)
from mikiwame.llm.registry import NO_THINK, resolve
from tests.conftest import stand_in

ENV = {"OPENCODE_BASE_URL": "https://llm.test/v1", "OPENCODE_API_KEY": "sk-test"}
CHAT_OK = {
    "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 40},
    },
}
MSG_OK = {
    "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": '{"ok": true}'}],
    "usage": {"input_tokens": 90, "output_tokens": 10, "cache_read_input_tokens": 5},
}


RESP_URL = "https://llm.test/v1/responses"
RESP_OK = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]},
    ],
    "usage": {
        "input_tokens": 966,
        "output_tokens": 204,
        "input_tokens_details": {"cached_tokens": 100},
        "output_tokens_details": {"reasoning_tokens": 0},
    },
}
LUNA = "opencode-go:gpt-6-luna"


def client(**kw) -> LLMClient:
    return LLMClient(env=ENV, backoff_s=0, **kw)


@pytest.fixture
def knobs(monkeypatch):
    """Give the stand-in models the knob their protocol family uses."""
    for protocol in ("chat", "messages"):
        monkeypatch.setitem(NO_THINK, f"stand-in-{protocol}", {"thinking": {"type": "disabled"}})


@respx.mock
async def test_responses_body_headers_images_and_usage():
    route = respx.post(RESP_URL).mock(return_value=httpx.Response(200, json=RESP_OK))
    records = []
    ref = resolve(LUNA, need_vision=True)
    res = await client(recorder=records.append).complete(
        ref,
        LLMRequest(system="S", user_text="U", images=[ImagePart(b"\xff\xd8jpeg")], max_tokens=700),
        session_id="mikiwame-abc",
    )
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer sk-test"
    assert req.headers["x-opencode-session"] == "mikiwame-abc"
    assert req.headers["user-agent"].startswith("mikiwame/")
    body = json.loads(req.content)
    assert body["model"] == "gpt-6-luna" and body["store"] is False
    assert body["max_output_tokens"] == 700 and "max_tokens" not in body
    assert body["reasoning"] == {"effort": "none"}
    assert "temperature" not in body  # GPT Luna: 400 "Unsupported parameter"
    system, user = body["input"]
    assert system == {"role": "system", "content": "S"}
    assert user["content"][0]["type"] == "input_image"
    assert user["content"][0]["image_url"].startswith("data:image/jpeg;base64,")
    assert user["content"][-1] == {"type": "input_text", "text": "U"}
    assert res.text == '{"ok": true}'
    assert (res.usage.input_tokens, res.usage.output_tokens, res.usage.cached_tokens) == (
        966,
        204,
        100,
    )
    assert res.cost_usd == pytest.approx(ref.model.cost_usd(966, 204, 100))
    assert records and records[0].ok


@respx.mock
async def test_responses_cut_by_max_tokens_is_empty_output():
    # What GPT Luna sent on 2026-10-06 when reasoning ate the budget of a screenshot reading.
    cut = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "reasoning", "summary": []}, {"type": "reasoning", "summary": []}],
        "usage": {"input_tokens": 966, "output_tokens": 600},
    }
    route = respx.post(RESP_URL).mock(return_value=httpx.Response(200, json=cut))
    records = []
    from mikiwame.llm.client import EmptyOutput

    with pytest.raises(EmptyOutput):
        await client(recorder=records.append).complete(
            resolve(LUNA), LLMRequest(system="S", user_text="U"), session_id="s"
        )
    assert route.call_count == 1 and records[0].cost_usd > 0 and not records[0].ok


@respx.mock
async def test_chat_headers_body_and_usage(knobs):
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=CHAT_OK)
    )
    records = []
    ref = stand_in("chat")
    res = await client(recorder=records.append).complete(
        ref, LLMRequest(system="S", user_text="U"), session_id="mikiwame-abc"
    )
    req = route.calls.last.request
    assert req.headers["x-opencode-session"] == "mikiwame-abc"
    assert req.headers["user-agent"].startswith("mikiwame/")
    assert req.headers["authorization"] == "Bearer sk-test"
    body = httpx.Request("POST", "x", content=req.content).read()
    assert b'"thinking":{"type":"disabled"}' in body.replace(b" ", b"")
    assert res.text == '{"ok": true}'
    assert res.usage.input_tokens == 100 and res.usage.cached_tokens == 40
    assert res.cost_usd == pytest.approx(ref.model.cost_usd(100, 20, 40))
    assert records and records[0].ok


@respx.mock
async def test_messages_protocol_uses_x_api_key_and_images(knobs):
    route = respx.post("https://llm.test/v1/messages").mock(
        return_value=httpx.Response(200, json=MSG_OK)
    )
    ref = stand_in("messages")
    res = await client().complete(
        ref,
        LLMRequest(system="S", user_text="U", images=[ImagePart(b"\xff\xd8jpeg")]),
        session_id="s",
    )
    req = route.calls.last.request
    assert req.headers["x-api-key"] == "sk-test"
    assert "authorization" not in req.headers
    import json

    body = json.loads(req.content)
    assert body["system"] == "S"
    assert body["thinking"] == {"type": "disabled"}
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[-1] == {"type": "text", "text": "U"}
    assert res.text == '{"ok": true}'
    assert res.usage.input_tokens == 95 and res.usage.cached_tokens == 5


@respx.mock
async def test_think_true_sends_no_knob():
    route = respx.post(RESP_URL).mock(return_value=httpx.Response(200, json=RESP_OK))
    await client().complete(
        resolve(LUNA), LLMRequest(system="S", user_text="U", think=True), session_id="s"
    )
    assert b"reasoning" not in route.calls.last.request.content


@respx.mock
async def test_retries_on_5xx_then_succeeds():
    route = respx.post(RESP_URL).mock(
        side_effect=[httpx.Response(502, text="bad gateway"), httpx.Response(200, json=RESP_OK)]
    )
    records = []
    res = await client(recorder=records.append).complete(
        resolve(LUNA), LLMRequest(system="S", user_text="U"), session_id="s"
    )
    assert res.text and route.call_count == 2
    assert [r.ok for r in records] == [False, True]


@respx.mock
async def test_transport_error_exhausts_attempts():
    respx.post(RESP_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(LLMError):
        await client(max_attempts=2).complete(
            resolve(LUNA), LLMRequest(system="S", user_text="U"), session_id="s"
        )


@respx.mock
async def test_429_is_quota_without_retry():
    route = respx.post(RESP_URL).mock(return_value=httpx.Response(429, json={"error": "limit"}))
    with pytest.raises(QuotaExceeded):
        await client().complete(
            resolve(LUNA), LLMRequest(system="S", user_text="U"), session_id="s"
        )
    assert route.call_count == 1


@respx.mock
async def test_400_not_retried():
    route = respx.post(RESP_URL).mock(
        return_value=httpx.Response(400, json={"error": "MissingSessionID"})
    )
    with pytest.raises(LLMError, match="400"):
        await client().complete(
            resolve(LUNA), LLMRequest(system="S", user_text="U"), session_id="s"
        )
    assert route.call_count == 1


def test_missing_env_is_an_error():
    c = LLMClient(env={})
    with pytest.raises(LLMError, match="missing"):
        c._endpoint(resolve(LUNA))


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        'Claro:\n```json\n{"a": 1}\n```',
        'Aquí tienes {"a": 1} espero que sirva',
    ],
)
def test_extract_json(text):
    assert extract_json(text) == {"a": 1}


def test_extract_json_fails():
    with pytest.raises(BadResponse):
        extract_json("no hay json")


@respx.mock
async def test_rejected_knob_is_dropped_and_remembered():
    rejected = httpx.Response(
        400, json={"error": {"param": "reasoning.effort", "message": "Unsupported value: 'none'"}}
    )
    route = respx.post(RESP_URL).mock(
        side_effect=[rejected, httpx.Response(200, json=RESP_OK), httpx.Response(200, json=RESP_OK)]
    )
    c = client(max_attempts=1)
    ref = resolve(LUNA)
    await c.complete(ref, LLMRequest(system="S", user_text="U"), session_id="s")
    await c.complete(ref, LLMRequest(system="S", user_text="U"), session_id="s")
    sent = [b"reasoning" in call.request.content for call in route.calls]
    assert sent == [True, False, False]


@respx.mock
async def test_effort_is_sent_and_dropped_if_rejected():
    """The adversary asks for effort "low" (2026-10-19); an upstream that refuses it still gets
    the request, without the field, and the no-reasoning knob of other roles is untouched."""
    rejected = httpx.Response(
        400, json={"error": {"param": "reasoning.effort", "message": "Unsupported value: 'low'"}}
    )
    route = respx.post(RESP_URL).mock(
        side_effect=[rejected] + [httpx.Response(200, json=RESP_OK)] * 3
    )
    c = client(max_attempts=1)
    ref = resolve(LUNA)
    low = LLMRequest(system="S", user_text="U", think=True, effort="low")
    await c.complete(ref, low, session_id="s")
    await c.complete(ref, low, session_id="s")
    await c.complete(ref, LLMRequest(system="S", user_text="U"), session_id="s")
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert bodies[0]["reasoning"] == {"effort": "low"}
    assert "reasoning" not in bodies[1] and "reasoning" not in bodies[2]
    assert bodies[3]["reasoning"] == {"effort": "none"}


@respx.mock
async def test_empty_output_records_cost_and_is_not_retried():
    empty = {
        "choices": [{"message": {"content": "", "reasoning_content": "x" * 5000}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 1500},
    }
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=empty)
    )
    records = []
    from mikiwame.llm.client import EmptyOutput

    with pytest.raises(EmptyOutput):
        await client(recorder=records.append).complete(
            stand_in("chat"), LLMRequest(system="S", user_text="U"), session_id="s"
        )
    assert route.call_count == 1
    assert records[0].cost_usd > 0 and not records[0].ok


def test_extract_json_is_not_fooled_by_fences_inside_strings():
    # Review 2026-10-01: a bubble containing ```{}``` used to replace the whole transcription.
    answer = json.dumps(
        {
            "images": [
                {
                    "kind": "chat",
                    "messages": [
                        {"from": "them", "text": "paga por Western Union ```{}```"},
                        {"from": "them", "text": "```"},
                        {"from": "me", "text": "vale"},
                        {"from": "them", "text": "```"},
                    ],
                }
            ]
        }
    )
    out = extract_json(answer, require="images")
    assert len(out["images"][0]["messages"]) == 4
    wrapped = f"Aquí tienes:\n```json\n{answer}\n```"
    assert extract_json(wrapped, require="images")["images"]


def test_extract_json_require_rejects_fragments_and_hides_content():
    with pytest.raises(BadResponse) as e:
        extract_json('texto con {"a": 1} y datos +34600000104', require="points")
    assert "+34600000104" not in str(e.value)
