import json

import httpx
import pandas as pd
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.tools import knowledge
from app.tools.errors import ToolError
from app.tools.trend import analyze_trend
from fakeapi import adapt

import pytest


def test_knowledge_search_ranks_the_relevant_note(tmp_path):
    settings = Settings(_env_file=None, evolve_workspace_dir=tmp_path)
    knowledge.add_note(settings, "Refund policy", "Refunds are issued within 14 days of purchase.\n\nNo refunds on sale items.")
    knowledge.add_note(settings, "Travel SOP", "Book flights two weeks ahead.\n\nUse the company card.")
    result = knowledge.search(settings, knowledge.SearchArgs(query="refund days"))
    assert result["matches"][0]["source"] == "Refund policy"
    assert "14 days" in result["matches"][0]["passage"]
    assert knowledge.search(settings, knowledge.SearchArgs(query="zebra unicorn"))["matches"] == []


def test_knowledge_rejects_bad_notes_and_ids(tmp_path):
    settings = Settings(_env_file=None, evolve_workspace_dir=tmp_path)
    with pytest.raises(ToolError):
        knowledge.add_note(settings, "", "x")
    with pytest.raises(ToolError):
        knowledge.delete_note(settings, "../../etc/passwd")


def test_knowledge_endpoints_roundtrip():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        created = client.post("/knowledge", json={"title": "Policy", "content": "Expenses need receipts."})
        assert created.status_code == 201
        assert client.get("/knowledge").json()[0]["title"] == "Policy"
        assert client.delete("/knowledge/" + created.json()["id"]).status_code == 200
        assert client.get("/knowledge").json() == []


def test_trend_detects_direction_and_anomaly():
    frame = pd.DataFrame({"month": list("abcdefgh"), "sales": [10, 11, 12, 11, 12, 13, 90, 13]})
    result = analyze_trend(frame, "sales")
    assert result["direction"] == "increasing"
    assert [a["label"] for a in result["anomalies"]] == ["g"]
    flat = analyze_trend(pd.DataFrame({"v": [5, 5, 5, 5, 5]}), "v")
    assert flat["direction"] == "flat" and flat["anomalies"] == []
    with pytest.raises(ToolError):
        analyze_trend(pd.DataFrame({"v": [1, 2]}), "v")


def test_high_risk_request_is_escalated_to_a_human():
    with TestClient(create_app(Settings(_env_file=None), adapt(lambda r: httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})))) as client:
        response = client.post("/chat", json={"messages": [{"role": "user", "content": "Delete all my customer records"}]})
        assert response.status_code == 200
        assert "escalated_to_human" in [e["kind"] for e in response.json()["activity"]]
        assert client.get("/metrics").json()["human_intervention_rate"] == 1.0


def test_reviewer_agent_scores_and_appends_to_the_answer():
    calls = []

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:3b"}]})
        body = json.loads(request.content)
        calls.append(body)
        text = json.dumps(body["messages"])
        if "Reviewer agent" in text:
            content = json.dumps({"score": 4, "critique": "Matches the tool output."})
        elif "Create concrete steps" in text:
            content = json.dumps({"steps": ["Add"], "success_check": "42"})
        elif body.get("tools") and not any(m["role"] == "tool" for m in body["messages"]):
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "calculate", "arguments": json.dumps({"operation": "add", "a": 17, "b": 25})}}]}, "finish_reason": "tool_calls"}]})
        else:
            content = "17 + 25 = 42."
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})

    settings = Settings(_env_file=None, reviewer_enabled=True)
    with TestClient(create_app(settings, httpx.MockTransport(handler))) as client:
        response = client.post("/chat", json={"messages": [{"role": "user", "content": "Use calculate to add 17 and 25."}]})
        assert response.status_code == 200, response.text
        assert "Self-review: 4/5" in response.json()["message"]
        assert "self_review" in [e["kind"] for e in response.json()["activity"]]
        assert client.get("/metrics").json()["average_review_score"] == 4.0
