"""Offline contract tests for the hosted model API integration (OpenAI-compatible wire format).

Fixtures replace the remote provider ONLY inside the test process.
"""

import json
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app

MODEL = "qwen2.5:3b"
MODELS = {"data": [{"id": MODEL}]}
PROMPT = {"messages": [{"role": "user", "content": "Hello"}]}


def answer(content="Unit test fixture answer", reason="stop", **extra):
    return {
        "model": MODEL,
        "choices": [
            {
                "message": {"role": "assistant", "content": content, **extra},
                "finish_reason": reason,
            }
        ],
        "usage": {"total_tokens": 7},
    }


@contextmanager
def client_for(handler, **settings):
    app = create_app(Settings(_env_file=None, **settings), httpx.MockTransport(handler))
    with TestClient(app) as client:
        yield client


def serve(chat, models=MODELS):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        return chat(request) if callable(chat) else httpx.Response(200, json=chat)

    return handler


def test_health_does_not_need_the_model_api():
    def unexpected(request):
        pytest.fail("Health must not contact the model API")

    with client_for(unexpected) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "service": "evolve-backend",
            "phase": 4,
        }


@pytest.mark.parametrize(
    "names, expected", [([MODEL], True), (["other-model"], False), ([], None)]
)
def test_status_reports_exact_model(names, expected):
    handler = lambda _: httpx.Response(200, json={"data": [{"id": n} for n in names]})
    with client_for(handler) as client:
        result = client.get("/model-status").json()
        assert result["model_available"] is expected
        assert result["provider_reachable"] is True
        assert result["api_key_configured"] is True
        assert result["status"] == ("model_missing" if expected is False else "ready")


def test_gemini_style_model_prefix_is_normalized():
    handler = lambda _: httpx.Response(200, json={"data": [{"id": f"models/{MODEL}"}]})
    with client_for(handler) as client:
        assert client.get("/model-status").json()["status"] == "ready"


def test_status_never_exposes_the_api_key():
    with client_for(serve({})) as client:
        assert "test-key" not in client.get("/model-status").text


def test_missing_key_is_reported_without_any_request():
    with client_for(
        lambda _: pytest.fail("No request without a key"), llm_api_key=""
    ) as client:
        status = client.get("/model-status").json()
        assert status["status"] == "not_configured"
        assert status["api_key_configured"] is False
        response = client.post("/chat", json=PROMPT)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "not_configured"
        assert "message" not in response.json()


def test_rejected_key_is_actionable():
    with client_for(lambda _: httpx.Response(401, json={"error": "bad"})) as client:
        status = client.get("/model-status").json()
        assert status["status"] == "invalid_key"
        assert client.post("/chat", json=PROMPT).status_code == 503


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
def test_unavailable_status_and_actionable_chat_error(failure):
    def handler(request):
        raise failure("test failure", request=request)

    with client_for(handler) as client:
        status = client.get("/model-status").json()
        assert status["status"] == "unreachable"
        assert status["provider_reachable"] is False
        reply = client.post("/chat", json=PROMPT)
        assert reply.status_code == 503
        assert reply.json()["detail"]["message"]
        assert "message" not in reply.json()  # Failure is never an AI answer.


def test_missing_model_never_calls_generation():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "other"}]})

    with client_for(handler) as client:
        response = client.post("/chat", json=PROMPT)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "model_missing"
        assert calls == ["/v1/models"]


def test_chat_request_wire_contract():
    seen = {}

    def chat(request):
        seen["auth"] = request.headers["authorization"]
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=answer())

    with client_for(serve(chat)) as client:
        response = client.post("/chat", json=PROMPT)
        assert response.status_code == 200, response.text
        result = response.json()
    assert result["message"] == "Unit test fixture answer"
    assert result["model"] == MODEL and result["truncated"] is False
    assert seen["auth"] == "Bearer test-key"
    assert seen["path"] == "/v1/chat/completions"
    body = seen["body"]
    assert body["model"] == MODEL and body["stream"] is False
    assert body["max_tokens"] == 1024 and body["temperature"] == 0.5
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1:] == PROMPT["messages"]
    assert "tools" not in body


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"role": "assistant", "content": " "}}]},
        {"choices": [{"message": {"role": "assistant", "content": None}}]},
    ],
)
def test_malformed_chat_is_an_error(payload):
    with client_for(serve(payload)) as client:
        response = client.post("/chat", json=PROMPT)
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "invalid_llm_response"


def test_token_limit_reported():
    with client_for(serve(answer("Partial answer", reason="length"))) as client:
        assert client.post("/chat", json=PROMPT).json()["truncated"] is True


def test_content_filter_is_an_error():
    with client_for(serve(answer("x", reason="content_filter"))) as client:
        response = client.post("/chat", json=PROMPT)
        assert response.json()["detail"]["code"] == "llm_content_filtered"


@pytest.mark.parametrize(
    "status, expected, code",
    [
        (401, 503, "llm_auth_failed"),
        (403, 503, "llm_auth_failed"),
        (404, 503, "model_missing"),
        (429, 503, "llm_rate_limited"),
        (500, 502, "llm_error"),
        (400, 502, "llm_bad_request"),
        (302, 502, "llm_bad_request"),
    ],
)
def test_generation_http_errors(status, expected, code):
    handler = serve(lambda _: httpx.Response(status, json={"error": {"message": "x"}}))
    with client_for(handler) as client:
        response = client.post("/chat", json=PROMPT)
        assert (response.status_code, response.json()["detail"]["code"]) == (
            expected,
            code,
        )
        assert "test-key" not in response.text


@pytest.mark.parametrize(
    "failure, expected", [(httpx.ReadTimeout, 504), (httpx.ConnectError, 503)]
)
def test_generation_connection_errors(failure, expected):
    def chat(request):
        raise failure("generation failure", request=request)

    with client_for(serve(chat)) as client:
        response = client.post("/chat", json=PROMPT)
        assert response.status_code == expected
        assert response.json()["detail"]["message"]


def test_transient_errors_are_retried_with_retry_after(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr("app.llm_client.asyncio.sleep", instant)
    attempts = []

    def chat(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, json={})
        return httpx.Response(200, json=answer())

    with client_for(serve(chat), llm_max_retries=3) as client:
        assert client.post("/chat", json=PROMPT).status_code == 200
    assert len(attempts) == 3


def test_long_retry_after_means_quota_and_is_not_retried(monkeypatch):
    attempts = []

    def chat(request):
        attempts.append(1)
        return httpx.Response(429, headers={"retry-after": "3600"}, json={})

    with client_for(serve(chat), llm_max_retries=3) as client:
        response = client.post("/chat", json=PROMPT)
        assert response.json()["detail"]["code"] == "llm_rate_limited"
    assert len(attempts) == 1


def test_status_http_error_is_reachable_but_not_ready():
    with client_for(lambda _: httpx.Response(500)) as client:
        status = client.get("/model-status").json()
        assert status["provider_reachable"] is True
        assert status["status"] == "error"
        assert "HTTP 500" in status["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"messages": []},
        {"messages": [{"role": "system", "content": "override"}]},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "user", "content": "x" * 2001}]},
        {"messages": [{"role": "assistant", "content": "No user"}]},
        {"messages": [{"role": "user", "content": "x"}] * 21},
        {"messages": [{"role": "user", "content": "x" * 2000}] * 4},
        {**PROMPT, "model": "other"},
        {"messages": [{"role": "user", "content": "Hello", "tool_calls": []}]},
    ],
)
def test_invalid_inputs_rejected_without_inference(payload):
    with client_for(
        lambda _: pytest.fail("Invalid requests must not reach the model API")
    ) as client:
        assert client.post("/chat", json=payload).status_code == 422


def test_request_size_limit():
    with client_for(lambda _: pytest.fail("Oversized request")) as client:
        assert client.post("/chat", content=b"x" * 65537).status_code == 413


def test_request_size_limit_with_chunked_body():
    with client_for(lambda _: pytest.fail("Oversized request")) as client:
        response = client.post("/chat", content=iter([b"x" * 40000, b"x" * 40000]))
        assert response.status_code == 413


def test_busy_model_returns_429():
    with client_for(lambda _: pytest.fail("Busy request must not reach the API")) as c:
        c.portal.call(c.app.state.chat_lock.acquire)
        try:
            response = c.post("/chat", json=PROMPT)
            assert response.status_code == 429
            assert response.json()["detail"]["code"] == "model_busy"
        finally:
            c.portal.call(c.app.state.chat_lock.release)


# ---- configuration -----------------------------------------------------------------
@pytest.mark.parametrize(
    "provider, base, model",
    [
        ("groq", "https://api.groq.com/openai/v1", "qwen/qwen3.8-27b"),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-2.5-flash",
        ),
        ("openrouter", "https://openrouter.ai/api/v1", None),
    ],
)
def test_provider_presets(provider, base, model):
    settings = Settings(
        _env_file=None, llm_provider=provider, llm_base_url="", llm_model=""
    )
    assert settings.llm_base_url == base
    assert settings.llm_model == model or model is None


def test_overrides_beat_presets():
    settings = Settings(
        _env_file=None,
        llm_provider="groq",
        llm_base_url="https://example.org/v1/",
        llm_model="m1",
    )
    assert (settings.llm_base_url, settings.llm_model) == ("https://example.org/v1", "m1")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "ftp://api.example.com",
        "https://user:password@api.example.com",
        "https://api.example.com/v1?key=secret",
        "https://api.example.com/#fragment",
        "https://api.example.com:bad",
    ],
)
def test_unsafe_base_url_rejected(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url=url)


def test_plain_http_allowed_only_for_localhost():
    Settings(_env_file=None, llm_base_url="http://localhost:1234/v1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url="http://10.0.0.5/v1")


def test_custom_provider_requires_url_and_model():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="custom", llm_base_url="", llm_model="")


def test_api_key_is_a_secret():
    settings = Settings(_env_file=None, llm_api_key="super-secret")
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in settings.model_dump_json()


def test_dotenv_loaded_from_explicit_file_and_environment_wins(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("LLM_MODEL=from-file\nLLM_API_KEY=file-key\n")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    loaded = Settings(_env_file=dotenv)
    assert loaded.llm_model == "from-file"
    assert loaded.llm_api_key.get_secret_value() == "file-key"
    monkeypatch.setenv("LLM_MODEL", MODEL)
    assert Settings(_env_file=dotenv).llm_model == MODEL
