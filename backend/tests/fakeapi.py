"""Offline model-API fixtures for tests ONLY.

Most fixtures describe a model turn in a compact legacy dialect: GET /api/tags lists models and
POST /api/chat returns {model, message{content, tool_calls[{id, function{name, arguments}}]},
done, done_reason}. ``adapt`` wraps such a handler in a transport that speaks the OpenAI-compatible
wire protocol the application really uses (GET /models, POST /chat/completions), translating both
directions. The production code never imports this module and never sees the legacy dialect.
"""

import inspect
import json

import httpx

LEGACY_TAGS = {"models": [{"name": "qwen2.5:3b"}]}


def to_legacy_request(request: httpx.Request) -> httpx.Request:
    if request.url.path.endswith("/models"):
        return httpx.Request("GET", "http://fake/api/tags")
    body = json.loads(request.content)
    messages = []
    for message in body["messages"]:
        message = dict(message)
        if message.get("tool_calls"):
            message["tool_calls"] = [
                {
                    "id": call["id"],
                    "function": {
                        "name": call["function"]["name"],
                        "arguments": json.loads(call["function"]["arguments"]),
                    },
                }
                for call in message["tool_calls"]
            ]
            message["content"] = message["content"] or ""
        if message["role"] == "tool":
            message["tool_name"] = message.pop("name")
        messages.append(message)
    legacy = {
        "model": body["model"],
        "messages": messages,
        "stream": False,
        "options": {"temperature": body["temperature"], "num_predict": body["max_tokens"]},
    }
    if "tools" in body:
        legacy["tools"] = body["tools"]
    if body.get("response_format"):
        legacy["format"] = "json"
    return httpx.Request("POST", "http://fake/api/chat", json=legacy)


def to_wire_response(response: httpx.Response, was_models: bool) -> httpx.Response:
    if response.status_code != 200:
        return response
    data = json.loads(response.content)
    if was_models:
        return httpx.Response(
            200, json={"data": [{"id": m["name"]} for m in data["models"]]}
        )
    message = dict(data["message"])
    calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call["function"]
        arguments = function["arguments"]
        calls.append(
            {
                "id": call.get("id") or f"call-{index}",
                "type": "function",
                "function": {
                    "name": function["name"],
                    "arguments": arguments
                    if isinstance(arguments, str)
                    else json.dumps(arguments),
                },
            }
        )
    wire = {"role": "assistant", "content": message.get("content") or ""}
    if calls:
        wire["tool_calls"] = calls
    return httpx.Response(
        200,
        json={
            "model": data.get("model", "qwen2.5:3b"),
            "choices": [
                {
                    "message": wire,
                    "finish_reason": "length"
                    if data.get("done_reason") == "length"
                    else "stop",
                }
            ],
            "usage": {},
        },
    )


def adapt(handler) -> httpx.MockTransport:
    async def wire_handler(request: httpx.Request) -> httpx.Response:
        legacy = to_legacy_request(request)
        response = handler(legacy)
        if inspect.isawaitable(response):
            response = await response
        try:
            return to_wire_response(response, legacy.url.path == "/api/tags")
        except (KeyError, TypeError):
            # A deliberately malformed fixture stays malformed on the wire too.
            return httpx.Response(200, json={"malformed": True})

    return httpx.MockTransport(wire_handler)
