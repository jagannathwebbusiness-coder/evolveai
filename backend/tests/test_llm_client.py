"""LLMClient wire-format unit tests (no network)."""

import json

import httpx
import pytest

from app.config import Settings
from app.llm_client import LLMClient, LLMError


def client_with(handler, **settings):
    settings = Settings(_env_file=None, **settings)
    http = httpx.AsyncClient(
        base_url=settings.llm_base_url, transport=httpx.MockTransport(handler)
    )
    return LLMClient(settings, http)


def completion(message, reason="stop"):
    return {
        "model": "m",
        "choices": [{"message": message, "finish_reason": reason}],
        "usage": {"total_tokens": 3},
    }


def test_wire_messages_convert_tool_history():
    wire = LLMClient.wire_messages(
        [
            {"role": "system", "content": "s"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "calculate", "arguments": {"a": 1}}}
                ],
            },
            {
                "role": "tool",
                "tool_name": "calculate",
                "tool_call_id": "c1",
                "content": "{}",
            },
        ]
    )
    assert wire[1]["content"] is None
    call = wire[1]["tool_calls"][0]
    assert call["type"] == "function" and call["id"] == "c1"
    assert json.loads(call["function"]["arguments"]) == {"a": 1}
    assert wire[2] == {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "calculate",
        "content": "{}",
    }


@pytest.mark.anyio
async def test_complete_parses_tool_calls_and_sends_tools():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "abc",
                            "type": "function",
                            "function": {
                                "name": "calculate",
                                "arguments": '{"operation": "add", "a": 1, "b": 2}',
                            },
                        }
                    ],
                },
                reason="tool_calls",
            ),
        )

    client = client_with(handler, llm_api_key="k")
    tools = [{"type": "function", "function": {"name": "calculate"}}]
    result = await client.complete(
        [{"role": "user", "content": "hi"}], tools=tools, json_mode=True, max_tokens=99
    )
    assert seen["tools"] == tools and seen["tool_choice"] == "auto"
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["max_tokens"] == 99
    call = result["message"]["tool_calls"][0]
    assert call == {
        "id": "abc",
        "function": {
            "name": "calculate",
            "arguments": {"operation": "add", "a": 1, "b": 2},
        },
    }
    assert result["done"] is True and result["done_reason"] == "stop"


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["not json", "[1, 2]", "42"])
async def test_undecodable_arguments_stay_raw_for_explicit_recovery(raw):
    handler = lambda _: httpx.Response(
        200,
        json=completion(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "calculate", "arguments": raw}}],
            }
        ),
    )
    result = await client_with(handler).complete([{"role": "user", "content": "x"}])
    assert result["message"]["tool_calls"][0]["function"]["arguments"] == raw
    assert result["message"]["tool_calls"][0]["id"] == "call-0"


@pytest.mark.anyio
async def test_length_finish_reason_marks_truncation():
    handler = lambda _: httpx.Response(
        200, json=completion({"role": "assistant", "content": "cut"}, "length")
    )
    result = await client_with(handler).complete([{"role": "user", "content": "x"}])
    assert result["done_reason"] == "length"


@pytest.mark.anyio
async def test_error_text_never_contains_the_key():
    handler = lambda _: httpx.Response(400, json={"error": {"message": "bad param"}})
    client = client_with(handler, llm_api_key="sk-very-secret")
    with pytest.raises(LLMError) as caught:
        await client.complete([{"role": "user", "content": "x"}])
    assert "sk-very-secret" not in caught.value.message
    assert "bad param" in caught.value.message


@pytest.mark.anyio
async def test_status_is_cached_for_stable_states_only():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    client = client_with(handler, llm_api_key="k", llm_model="m", llm_provider="groq")
    assert (await client.model_status()).status == "ready"
    assert (await client.model_status()).status == "ready"
    assert len(calls) == 1
    assert (await client.model_status(force=True)).status == "ready"
    assert len(calls) == 2


@pytest.fixture
def anyio_backend():
    return "asyncio"
