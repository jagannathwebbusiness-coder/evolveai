import asyncio
import json
from contextlib import contextmanager
from uuid import uuid4

import httpx
from fakeapi import adapt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TAGS = {"models": [{"name": "qwen2.5:3b"}]}
TASK = {
    "messages": [{"role": "user", "content": "Use calculate to add 17 and 25."}],
    "use_tools": True,
    "tool_choice": "calculate",
}
SAMPLE = b"item,revenue,cost\nA,100,60\nB,200,120\nC,,90\n"


def completion(content="", calls=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {
        "model": "qwen2.5:3b",
        "done": True,
        "done_reason": "stop",
        "message": message,
    }


def call(name="calculate", arguments=None, identifier="call-one"):
    return {
        "id": identifier,
        "function": {
            "index": 0,
            "name": name,
            "arguments": (
                arguments
                if arguments is not None
                else {"operation": "add", "a": 17, "b": 25}
            ),
        },
    }


@contextmanager
def client_for(tmp_path, handler, **overrides):
    settings = Settings(
        _env_file=None, evolve_workspace_dir=tmp_path / "workspace", **overrides
    )
    with TestClient(create_app(settings, adapt(handler))) as client:
        yield client


def test_model_tool_model_flow_and_trace(tmp_path):
    bodies = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        body = json.loads(request.content)
        bodies.append(body)
        assert body["model"] == "qwen2.5:3b"
        assert {t["function"]["name"] for t in body["tools"]} == {
            "calculate",
            "analyze_csv",
        }
        if len(bodies) == 1:
            return httpx.Response(200, json=completion(calls=[call()]))
        assert body["messages"][-2]["tool_calls"][0]["id"] == "call-one"
        tool = body["messages"][-1]
        assert tool["role"] == "tool" and tool["tool_name"] == "calculate"
        assert tool["tool_call_id"] == "call-one"
        assert json.loads(tool["content"])["data"]["value"] == 42
        return httpx.Response(200, json=completion("The verified result is 42."))

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 200, response.text
        result = response.json()
        kinds = [e["kind"] for e in result["activity"]]
        assert kinds == [
            "processing_request",
            "model_request",
            "tool_requested",
            "tool_validated",
            "tool_executed",
            "model_request",
            "tool_result_received",
            "final_answer",
        ]
        assert len(bodies) == 2
        assert result["task_id"]


@pytest.mark.parametrize(
    "tool_call,code",
    [
        (call("shell"), "unknown_tool"),
        (
            call(arguments={"operation": "add", "a": "17", "b": 25}),
            "invalid_tool_arguments",
        ),
        (
            call(arguments={"operation": "add", "a": True, "b": 25}),
            "invalid_tool_arguments",
        ),
        (
            call(
                arguments={"operation": "add", "a": 17, "b": 25, "command": "anything"}
            ),
            "invalid_tool_arguments",
        ),
        (call(arguments="{broken}"), "invalid_tool_response"),
    ],
)
def test_unapproved_or_invalid_calls_never_execute(tmp_path, tool_call, code):
    def handler(request):
        return httpx.Response(
            200,
            json=(
                TAGS
                if request.url.path == "/api/tags"
                else completion(calls=[tool_call])
            ),
        )

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert detail["code"] == code
        assert not any(e["kind"] == "tool_executed" for e in detail["activity"])
        assert detail["activity"][-1]["kind"] == "failure"


def test_batch_validated_before_any_execution(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json=(
                TAGS
                if request.url.path == "/api/tags"
                else completion(calls=[call(), call("unknown", identifier="call-two")])
            ),
        )

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 502
        assert not any(
            e["kind"] == "tool_executed" for e in response.json()["detail"]["activity"]
        )


def test_tool_error_returned_to_model_without_success_claim(tmp_path):
    count = 0

    def handler(request):
        nonlocal count
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        count += 1
        if count == 1:
            return httpx.Response(
                200,
                json=completion(
                    calls=[call(arguments={"operation": "divide", "a": 1, "b": 0})]
                ),
            )
        result = json.loads(json.loads(request.content)["messages"][-1]["content"])
        assert result["ok"] is False and result["error"]["code"] == "division_by_zero"
        return httpx.Response(200, json=completion("Division by zero is not defined."))

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 200
        kinds = [e["kind"] for e in response.json()["activity"]]
        assert "tool_failed" in kinds and "tool_executed" not in kinds


def test_missing_required_tool_gets_one_retry_then_error(tmp_path):
    bodies = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion("I think the answer is 42."))

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "required_tool_not_used"
        assert len(bodies) == 2
        assert not any(
            e["kind"] == "tool_executed" for e in response.json()["detail"]["activity"]
        )


def test_auto_chat_does_not_invent_tools(tmp_path):
    def handler(request):
        return httpx.Response(
            200, json=TAGS if request.url.path == "/api/tags" else completion("Hello.")
        )

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json={**TASK, "tool_choice": "auto"})
        assert response.status_code == 200
        assert "No tools were executed" in response.json()["activity"][-1]["message"]


def test_iteration_limit(tmp_path):
    count = 0

    def handler(request):
        nonlocal count
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        count += 1
        return httpx.Response(
            200,
            json=completion(
                calls=[call(arguments={"operation": "add", "a": count, "b": 1})]
            ),
        )

    with client_for(tmp_path, handler, agent_max_iterations=2) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "iteration_limit"
        assert response.json()["detail"]["activity"][-1]["kind"] == "iteration_limit"
        assert count == 2


def test_identical_tool_repeat_stops(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json=(
                TAGS if request.url.path == "/api/tags" else completion(calls=[call()])
            ),
        )

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "repeated_tool_call"
        assert (
            sum(
                e["kind"] == "tool_executed"
                for e in response.json()["detail"]["activity"]
            )
            == 1
        )


def test_tool_call_limit_before_execution(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json=(
                TAGS
                if request.url.path == "/api/tags"
                else completion(
                    calls=[
                        call(),
                        call(
                            arguments={"operation": "add", "a": 2, "b": 3},
                            identifier="second",
                        ),
                    ]
                )
            ),
        )

    with client_for(tmp_path, handler, agent_max_tool_calls=1) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "tool_call_limit"
        assert not any(
            e["kind"] == "tool_executed" for e in response.json()["detail"]["activity"]
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        completion(),
        completion(calls=[{"function": {"name": "calculate"}}]),
    ],
)
def test_malformed_model_response(tmp_path, payload):
    def handler(request):
        return httpx.Response(
            200, json=TAGS if request.url.path == "/api/tags" else payload
        )

    with client_for(tmp_path, handler) as client:
        assert (
            client.post("/chat", json=TASK).json()["detail"]["code"]
            in {"invalid_tool_response", "invalid_llm_response"}
        )


def test_truncated_tool_call_not_executed(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json=(
                TAGS
                if request.url.path == "/api/tags"
                else {**completion(calls=[call()]), "done_reason": "length"}
            ),
        )

    with client_for(tmp_path, handler) as client:
        assert (
            client.post("/chat", json=TASK).json()["detail"]["code"]
            == "truncated_tool_call"
        )


def test_model_timeout_with_activity(tmp_path):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        raise httpx.ReadTimeout("test timeout", request=request)

    with client_for(tmp_path, handler) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 504
        assert response.json()["detail"]["code"] == "llm_timeout"
        assert response.json()["detail"]["activity"][-1]["kind"] == "failure"


def test_total_task_deadline(tmp_path):
    async def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        await asyncio.sleep(2)
        return httpx.Response(200, json=completion("Late response"))

    with client_for(tmp_path, handler, agent_timeout_seconds=1) as client:
        response = client.post("/chat", json=TASK)
        assert response.status_code == 504
        assert response.json()["detail"]["code"] == "agent_timeout"


def test_csv_upload_to_model_tool_to_model(tmp_path):
    uploaded = {}
    count = 0

    def handler(request):
        nonlocal count
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        count += 1
        body = json.loads(request.content)
        if count == 1:
            assert uploaded["upload_id"] in body["messages"][0]["content"]
            return httpx.Response(
                200,
                json=completion(
                    calls=[
                        call(
                            "analyze_csv",
                            {
                                "upload_id": uploaded["upload_id"],
                                "columns": ["revenue"],
                            },
                        )
                    ]
                ),
            )
        result = json.loads(body["messages"][-1]["content"])
        assert result["data"]["row_count"] == 3
        assert result["data"]["missing_values"] == {"revenue": 1}
        assert result["data"]["numeric_summary"]["revenue"]["mean"] == 150
        return httpx.Response(
            200,
            json=completion("Three rows, one revenue value missing, mean revenue 150."),
        )

    with client_for(tmp_path, handler) as client:
        response = client.post(
            "/uploads", files={"file": ("sample.csv", SAMPLE, "text/csv")}
        )
        assert response.status_code == 201
        uploaded.update(response.json())
        response = client.post(
            "/chat",
            json={
                **TASK,
                "tool_choice": "auto",
                "task_id": uploaded["task_id"],
                "upload_ids": [uploaded["upload_id"]],
            },
        )
        assert response.status_code == 200, response.text
        assert any(
            e["tool"] == "analyze_csv" and e["kind"] == "tool_executed"
            for e in response.json()["activity"]
        )


def test_csv_invalid_columns_return_actual_tool_error(tmp_path):
    uploaded = {}
    count = 0

    def handler(request):
        nonlocal count
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        count += 1
        if count == 1:
            return httpx.Response(
                200,
                json=completion(
                    calls=[
                        call(
                            "analyze_csv",
                            {
                                "upload_id": uploaded["upload_id"],
                                "columns": ["not-a-column"],
                            },
                        )
                    ]
                ),
            )
        assert (
            json.loads(json.loads(request.content)["messages"][-1]["content"])["error"][
                "code"
            ]
            == "invalid_columns"
        )
        return httpx.Response(
            200, json=completion("The selected column does not exist.")
        )

    with client_for(tmp_path, handler) as client:
        uploaded.update(
            client.post(
                "/uploads", files={"file": ("sample.csv", SAMPLE, "text/csv")}
            ).json()
        )
        response = client.post(
            "/chat",
            json={
                **TASK,
                "tool_choice": "analyze_csv",
                "task_id": uploaded["task_id"],
                "upload_ids": [uploaded["upload_id"]],
            },
        )
        assert response.status_code == 200
        assert any(e["kind"] == "tool_failed" for e in response.json()["activity"])


def test_upload_not_owned_by_task_rejected_before_inference(tmp_path):
    with client_for(
        tmp_path,
        lambda _: pytest.fail("No inference allowed for another task's upload"),
    ) as client:
        uploaded = client.post(
            "/uploads", files={"file": ("sample.csv", SAMPLE, "text/csv")}
        ).json()
        response = client.post(
            "/chat",
            json={
                **TASK,
                "task_id": str(uuid4()),
                "upload_ids": [uploaded["upload_id"]],
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "upload_not_found"


def test_model_cannot_select_an_unattached_file(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json=(
                TAGS
                if request.url.path == "/api/tags"
                else completion(
                    calls=[call("analyze_csv", {"upload_id": str(uuid4())})]
                )
            ),
        )

    with client_for(tmp_path, handler) as client:
        assert (
            client.post("/chat", json=TASK).json()["detail"]["code"]
            == "upload_not_attached"
        )


@pytest.mark.parametrize(
    "filename,data,mime",
    [
        ("../outside.csv", SAMPLE, "text/csv"),
        ("..\\outside.csv", SAMPLE, "text/csv"),
        ("CON.csv", SAMPLE, "text/csv"),
        ("data.xlsx", SAMPLE, "text/csv"),
        ("data.csv", SAMPLE, "image/png"),
        ("empty.csv", b"", "text/csv"),
        ("bad.csv", b"a,b\n1,2,3\n", "text/csv"),
        ("binary.csv", b"header\n\x00\x01", "text/csv"),
    ],
)
def test_upload_validation(tmp_path, filename, data, mime):
    with client_for(
        tmp_path, lambda _: pytest.fail("Uploads must not call the model API")
    ) as client:
        assert (
            client.post("/uploads", files={"file": (filename, data, mime)}).status_code
            == 400
        )
        assert not list((tmp_path / "workspace").rglob("*.csv"))


def test_upload_size_and_storage_limits(tmp_path):
    with client_for(
        tmp_path,
        lambda _: pytest.fail("No model"),
        upload_max_bytes=128,
        upload_max_files=1,
    ) as client:
        assert (
            client.post(
                "/uploads",
                files={"file": ("large.csv", b"a\n" + b"x" * 129, "text/csv")},
            ).status_code
            == 413
        )
        assert client.post("/uploads", content=b"x" * 17000).status_code == 413
        first = client.post(
            "/uploads", files={"file": ("same.csv", SAMPLE, "text/csv")}
        )
        assert first.status_code == 201
        assert (
            client.post(
                "/uploads", files={"file": ("same.csv", SAMPLE, "text/csv")}
            ).json()["detail"]["code"]
            == "upload_storage_limit"
        )
        paths = list((tmp_path / "workspace" / "uploads").glob("*.csv"))
        assert len(paths) == 1 and paths[0].name != "same.csv"


def test_unique_storage_names_and_catalog(tmp_path):
    with client_for(tmp_path, lambda _: pytest.fail("No model")) as client:
        first = client.post(
            "/uploads", files={"file": ("same.csv", SAMPLE, "text/csv")}
        ).json()
        second = client.post(
            "/uploads", files={"file": ("same.csv", SAMPLE, "text/csv")}
        ).json()
        assert first["upload_id"] != second["upload_id"]
        catalog = client.get("/tools").json()
        assert {tool["name"] for tool in catalog["tools"]} == {
            "calculate",
            "analyze_csv",
            "list_workspace_files",
            "read_workspace_file",
            "create_report",
        }
        assert all(tool["version"] == "1.0.0" for tool in catalog["tools"])


def test_windows_multipart_path_is_normalized_and_never_used_for_storage(tmp_path):
    # python-multipart normalizes browser-style drive-prefixed filenames to basename.
    with client_for(tmp_path, lambda _: pytest.fail("No model")) as client:
        response = client.post(
            "/uploads", files={"file": ("C:\\outside.csv", SAMPLE, "text/csv")}
        )
        assert response.status_code == 201
        assert response.json()["filename"] == "outside.csv"
        paths = list((tmp_path / "workspace" / "uploads").glob("*.csv"))
        assert len(paths) == 1 and paths[0].name != "outside.csv"


@pytest.mark.parametrize(
    "extra",
    [
        {"upload_ids": ["../outside.csv"], "task_id": str(uuid4())},
        {"upload_ids": [str(uuid4())]},
        {"use_tools": False, "tool_choice": "calculate"},
        {"tool_choice": "analyze_csv"},
        {"tool_choice": "shell"},
    ],
)
def test_new_api_input_validation(tmp_path, extra):
    with client_for(
        tmp_path, lambda _: pytest.fail("Invalid input must not reach the model API")
    ) as client:
        assert client.post("/chat", json={**TASK, **extra}).status_code == 422
