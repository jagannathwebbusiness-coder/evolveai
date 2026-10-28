"""General tool builder: scripted model replies, REAL child-process execution, real SQLite."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.improvement.general_builder import build_general, reject_cases
from app.improvement.models import loose_equal
from app.llm_client import LLMError
from app.main import create_app
from app.storage import Conflict, Database

SPEC = {
    "stem": "c_to_f",
    "description": "Convert a temperature from Celsius to Fahrenheit.",
    "behavior": "Return fahrenheit = celsius * 9 / 5 + 32 as a number, without rounding.",
    "input_schema": {
        "type": "object",
        "properties": {"celsius": {"type": "number", "minimum": -273.15, "maximum": 1000}},
        "required": ["celsius"],
        "additionalProperties": False,
    },
    "output_schema": {
        "type": "object",
        "properties": {"fahrenheit": {"type": "number"}},
        "required": ["fahrenheit"],
        "additionalProperties": False,
    },
    "dependencies": [],
}


def case(label, category, celsius, fahrenheit):
    return {
        "label": label,
        "category": category,
        "input": {"celsius": celsius},
        "expected": {"fahrenheit": fahrenheit},
    }


TESTS = {
    "cases": [
        case("freezing", "valid", 0, 32),
        case("boiling", "valid", 100, 212),
        case("equal point", "valid", -40, -40),
        case("body", "boundary", 37.5, 99.5),
        case("max", "boundary", 1000, 1832),
    ]
}
CODE = 'def run(arguments):\n    return {"fahrenheit": arguments["celsius"] * 9 / 5 + 32}\n'
WRONG = 'def run(arguments):\n    return {"fahrenheit": arguments["celsius"] + 32}\n'


class Scripted:
    """Stands in for LLMClient.complete only; the rest of the pipeline is real."""

    def __init__(self, *replies, reason="stop"):
        self.replies, self.prompts, self.reason = list(replies), [], reason

    async def complete(self, messages, **kwargs):
        self.prompts.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str) and "def run" in reply:
            reply = {"source": reply}  # Source text is delivered in the code reply's JSON.
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {
            "model": "scripted",
            "message": {"role": "assistant", "content": content, "tool_calls": []},
            "done": True,
            "done_reason": self.reason,
        }


@pytest.fixture
def env():
    settings = Settings(_env_file=None, demo_mode=True)
    database = Database(settings)
    database.initialize()
    events = []
    return (
        settings,
        database,
        lambda kind, message, **extra: events.append(
            {"kind": kind, "message": message, **extra}
        ),
        events,
    )


def build(env, client, goal="Convert Celsius to Fahrenheit"):
    settings, database, record, _ = env
    return asyncio.run(
        build_general(settings, client, database, record, goal, "c_to_f", {}, "task-1")
    )


def versions(database):
    with database.connect() as db:
        return db.execute(
            "SELECT v.version, s.status FROM candidate_versions v JOIN candidate_states s ON s.version_id=v.id ORDER BY v.version"
        ).fetchall()


def test_happy_path_drafts_but_never_activates(env):
    client = Scripted(SPEC, TESTS, CODE)
    outcome = build(env, client)
    _, database, _, events = env
    assert outcome["name"].startswith("candidate_c_to_f_")
    assert outcome["model_cases"] == 5 and outcome["passed"] >= 14
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM active_tools").fetchone()[0] == 0
        status = db.execute(
            "SELECT status FROM candidate_states WHERE version_id=?",
            (outcome["version_id"],),
        ).fetchone()[0]
        contract = db.execute("SELECT cases, actor FROM contracts").fetchone()
        manifest = json.loads(
            db.execute("SELECT manifest FROM candidate_versions WHERE id=?", (outcome["version_id"],)).fetchone()[0]
        )
    assert status == "awaiting_approval"
    assert manifest["test_provenance"] == "model_generated_independent"
    assert manifest["behavior"].startswith("Return fahrenheit")
    categories = {c["category"] for c in json.loads(contract["cases"])}
    assert categories == {"valid", "boundary", "invalid", "malformed", "security"}
    kinds = [e["kind"] for e in events]
    assert kinds.index("tool_spec_drafted") < kinds.index("candidate_determinism")
    assert kinds.index("candidate_determinism") < kinds.index("candidate_tests")
    tested = next(e for e in events if e["kind"] == "candidate_tests")
    assert tested["result"]["state"] == "passed"
    # Spec, tests and code are three separate calls; the code call never sees the tests.
    assert len(client.prompts) == 3
    code_prompt = json.dumps(client.prompts[2])
    assert "freezing" not in code_prompt and "expected" not in code_prompt


def test_failing_code_is_revised_from_actual_failures_then_passes(env):
    client = Scripted(SPEC, TESTS, WRONG, CODE)
    outcome = build(env, client)
    feedback = json.dumps(client.prompts[3])
    assert "failing_cases" in feedback and "follow the spec exactly" in feedback
    assert outcome["passed"] >= 14
    assert [s for _, s in versions(env[1])].count("awaiting_approval") == 1


def test_code_that_never_passes_is_not_offered_for_approval(env):
    with pytest.raises(Conflict) as caught:
        build(env, Scripted(SPEC, TESTS, WRONG, WRONG))
    assert "Nothing was activated" in str(caught.value)
    assert "awaiting_approval" not in [s for _, s in versions(env[1])]


def test_nondeterministic_code_is_rejected_before_testing(env):
    flaky = (
        "import datetime\n\ndef run(arguments):\n"
        '    return {"fahrenheit": float(datetime.datetime.now().microsecond)}\n'
    )
    spec = {**SPEC, "dependencies": ["datetime"]}
    with pytest.raises(Conflict):
        build(env, Scripted(spec, TESTS, flaky, flaky))
    determinism = [e for e in env[3] if e["kind"] == "candidate_determinism"]
    assert determinism and not determinism[0]["result"]["deterministic"]
    assert not any(e["kind"] == "candidate_tests" for e in env[3])


@pytest.mark.parametrize(
    "source",
    [
        'def run(arguments):\n    return {"fahrenheit": float(open("secret.txt").read())}\n',
        'import os\n\ndef run(arguments):\n    return {"fahrenheit": 1.0}\n',
        'def run(arguments):\n    return {"fahrenheit": float(eval("1"))}\n',
        'def run(arguments):\n    return {"fahrenheit": arguments.__class__}\n',
    ],
)
def test_forbidden_code_never_executes(env, source):
    with pytest.raises(Conflict):
        build(env, Scripted(SPEC, TESTS, source, source))
    assert not any(e["kind"] == "candidate_tests" for e in env[3])


def test_runtime_import_and_builtin_restrictions_hold(env):
    # Passes static checks (declared dependency) but the child's guard allows only the list.
    spec = {**SPEC, "dependencies": ["math"]}
    sneaky = (
        "import math\n\ndef run(arguments):\n"
        '    return {"fahrenheit": math.floor(arguments["celsius"] * 9 / 5 + 32)}\n'
    )
    tests = {
        "cases": [
            case("a", "valid", 0, 32),
            case("b", "valid", 100, 212),
            case("d", "valid", -40, -40),
            case("c", "boundary", 1000, 1832),
        ]
    }
    assert build(env, Scripted(spec, tests, sneaky))["passed"] >= 12


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: {**s, "output_schema": {"type": "array", "items": {"type": "number"}, "maxItems": 3}},
        lambda s: {**s, "dependencies": ["os"]},
        lambda s: {**s, "stem": "Bad Name"},
    ],
)
def test_invalid_spec_is_refused(env, mutate):
    bad = mutate(SPEC)
    with pytest.raises(Conflict):
        build(env, Scripted(bad, bad))


def test_tests_invalid_under_the_spec_are_dropped(env):
    cases = TESTS["cases"] + [case("impossible", "valid", 5000, 9032)]
    outcome = build(env, Scripted(SPEC, {"cases": cases}, CODE))
    assert outcome["model_cases"] == 5


def test_too_few_usable_tests_fail_closed(env):
    junk = {"cases": [case(f"x{i}", "valid", 99999, 0) for i in range(4)]}
    with pytest.raises(Conflict):
        build(env, Scripted(SPEC, junk))


def test_truncated_or_invalid_model_json_is_retried_then_fails(env):
    with pytest.raises(Conflict):
        build(env, Scripted("not json", "{}"))
    with pytest.raises(Conflict):
        build(env, Scripted({"x": 1}, {"x": 2}))


def test_provider_failure_propagates_without_a_candidate(env):
    with pytest.raises(LLMError):
        build(env, Scripted(SPEC, TESTS, LLMError(503, "llm_rate_limited", "quota")))
    assert not any(s == "awaiting_approval" for _, s in versions(env[1]))


def test_demo_mode_is_required():
    settings = Settings(_env_file=None, demo_mode=False)
    database = Database(settings)
    database.initialize()
    with pytest.raises(Conflict):
        asyncio.run(
            build_general(settings, Scripted(), database, lambda *a, **k: None, "g", "t", {}, "x")
        )


def test_reject_cases_are_derived_from_the_schema_and_really_rejected():
    manifest = {"input_schema": SPEC["input_schema"], "output_schema": SPEC["output_schema"]}
    cases = reject_cases(manifest, {"celsius": 1})
    labels = {c.label for c in cases}
    assert {"wrong type for celsius", "celsius above maximum", "celsius below minimum"} <= labels
    assert {"missing celsius", "not an object", "injected command field"} <= labels
    assert {c.category for c in cases} == {"invalid", "malformed", "security"}
    assert all(c.reject for c in cases)


@pytest.mark.parametrize(
    "a, b, equal",
    [
        (212, 212.0, True),
        ({"x": [1, 2.0]}, {"x": [1.0, 2]}, True),
        (0.1 + 0.2, 0.3, True),
        (True, 1, False),
        ("1", 1, False),
        ({"a": 1}, {"a": 1, "b": 2}, False),
        ([1, 2], [1], False),
    ],
)
def test_loose_equal(a, b, equal):
    assert loose_equal(a, b) is equal


# ---- end to end through the agent and the human approval gate --------------------------
def completion(message, reason="stop"):
    return httpx.Response(
        200,
        json={"model": "m", "choices": [{"message": message, "finish_reason": reason}], "usage": {}},
    )


def tool_call(name, arguments):
    return completion(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
            ],
        },
        "tool_calls",
    )


def text(content):
    return completion({"role": "assistant", "content": content})


GOAL = "Convert 100 degrees Celsius to Fahrenheit."
PLAN = {"steps": ["Convert the temperature"], "success_check": "A Fahrenheit value exists"}


def agent_transport(script):
    queue = list(script)

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:3b"}]})
        body = json.loads(request.content)
        assert queue, "unexpected model request"
        step = queue.pop(0)
        return step(body) if callable(step) else text(json.dumps(step))

    return httpx.MockTransport(handler)


def first_tool(prefix):
    def step(body):
        names = [t["function"]["name"] for t in body.get("tools", [])]
        match = next(n for n in names if n.startswith(prefix))
        return tool_call(match, {"celsius": 100})

    return step


def review_headers(client):
    headers = {"Origin": "http://127.0.0.1:5173", "X-Evolve-Review": "human-review-v1"}
    token = client.post("/improvement/review-session", headers=headers).json()["token"]
    return headers | {"X-Evolve-Review-Token": token}


def test_unknown_tool_drafts_for_approval_then_runs_only_after_human_approval():
    settings = Settings(_env_file=None, demo_mode=True)
    task = {
        "messages": [{"role": "user", "content": GOAL}],
        "use_tools": True,
        "agentic": True,
        "demo_tools": True,
    }
    script = [
        PLAN,
        lambda body: tool_call("celsius_to_fahrenheit", {"celsius": 100}),
        SPEC,
        TESTS,
        {"source": CODE},
    ]
    with TestClient(create_app(settings, agent_transport(script))) as client:
        drafted = client.post("/chat", json=task)
        assert drafted.status_code == 200, drafted.text
        result = drafted.json()
        kinds = [e["kind"] for e in result["activity"]]
        assert "approval_required" in kinds and "candidate_activated" not in kinds
        assert "awaiting your approval" in result["message"]
        snapshot = client.get("/improvement").json()
        assert snapshot["active"] == []
        candidate = snapshot["candidates"][0]
        assert candidate["status"] == "awaiting_approval"

        # Without a human decision the tool cannot be used, even by an opted-in task.
        client.app.state.llm  # app is live; a new task must not see the draft
        tools = {t["name"] for t in client.get("/tools").json()["tools"]}
        assert not any(n.startswith("candidate_c_to_f") for n in tools)

        headers = review_headers(client)
        binding = {
            "version_id": candidate["version_id"],
            "source_hash": candidate["source_hash"],
            "content_hash": candidate["content_hash"],
            "confirm": True,
        }
        for action in ("approve", "activate"):
            reply = client.post("/improvement/decision", headers=headers, json=binding | {"action": action})
            assert reply.status_code == 200, reply.text
        assert len(client.get("/improvement").json()["active"]) == 1

        script[:] = []  # (list identity unused; a fresh transport is not needed)

    # A fresh app on the same database offers the approved tool and labels its result.
    settings2 = Settings(_env_file=None, demo_mode=True)
    second = [
        PLAN,
        first_tool("candidate_c_to_f"),
        lambda body: text("The result is 212 degrees Fahrenheit."),
    ]
    with TestClient(create_app(settings2, agent_transport(second))) as client:
        reply = client.post("/chat", json=task)
        assert reply.status_code == 200, reply.text
        result = reply.json()
        executed = [e for e in result["activity"] if e["kind"] == "tool_executed"]
        assert executed and executed[0]["result"]["data"] == {"fahrenheit": 212.0}
        assert "approved" in result["message"] and "not " in result["message"]
        assert any(e["kind"] == "result_unverified_tool" for e in result["activity"])
        # Without the per-task demo opt-in the approved tool is not offered at all.
        listing = client.get("/tools").json()["tools"]
        assert any(t["origin"] == "general_approved" for t in listing)


def test_no_draft_without_demo_opt_in_or_for_business_tasks():
    settings = Settings(_env_file=None, demo_mode=True)
    plain = {
        "messages": [{"role": "user", "content": GOAL}],
        "use_tools": True,
        "agentic": True,
        "demo_tools": False,
    }
    script = [PLAN, lambda body: tool_call("celsius_to_fahrenheit", {"celsius": 100})]
    script += [lambda body: text("I cannot do that.")] * 4
    with TestClient(create_app(settings, agent_transport(script))) as client:
        reply = client.post("/chat", json=plain)
        kinds = [e["kind"] for e in (reply.json().get("activity") or reply.json()["detail"]["activity"])]
        assert "approval_required" not in kinds and "candidate_generation" not in kinds


def test_draft_endpoint_requires_human_review_and_demo_mode():
    with TestClient(create_app(Settings(_env_file=None, demo_mode=True), agent_transport([SPEC, TESTS, {"source": CODE}]))) as client:
        body = {"description": "Convert Celsius to Fahrenheit.", "confirm": True}
        assert client.post("/improvement/draft-tool", json=body).status_code == 403
        reply = client.post("/improvement/draft-tool", headers=review_headers(client), json=body)
        assert reply.status_code == 201, reply.text
        assert reply.json()["outcome"]["name"].startswith("candidate_c_to_f_")
        assert any(e["kind"] == "approval_required" or e["kind"] == "candidate_tests" for e in reply.json()["events"])
        assert client.get("/improvement").json()["active"] == []
    with TestClient(create_app(Settings(_env_file=None, demo_mode=False), agent_transport([]))) as client:
        reply = client.post(
            "/improvement/draft-tool",
            headers=review_headers(client),
            json={"description": "Convert Celsius to Fahrenheit.", "confirm": True},
        )
        assert reply.status_code == 403 and reply.json()["detail"]["code"] == "demo_disabled"


def test_child_builtins_and_imports_are_restricted():
    from app.improvement.demo_runner import restricted_builtins

    safe = restricted_builtins()
    for name in ("open", "eval", "exec", "compile", "getattr", "globals", "type", "input"):
        assert name not in safe
    assert safe["len"]([1, 2]) == 2
    assert safe["__import__"]("math").floor(1.5) == 1
    for module in ("os", "sys", "subprocess", "socket", "pathlib", "importlib"):
        with pytest.raises(ImportError):
            safe["__import__"](module)
    with pytest.raises(ImportError):
        safe["__import__"]("math", None, None, ("x",), 1)  # Relative imports are refused.


@pytest.mark.parametrize(
    "name, ok",
    [
        ("celsius_to_fahrenheit", True),
        ("compound_interest", True),
        ("truncate_text", True),
        ("send_email", False),
        ("read_file", False),
        ("download_report", False),
        ("run_shell", False),
        ("Bad-Name", False),
        ("ab", False),
    ],
)
def test_only_pure_computation_names_trigger_a_draft(name, ok):
    from app.agent.core import pure_tool_name

    assert pure_tool_name(name) is ok


def test_dependencies_are_derived_from_actual_imports(env):
    code = (
        "import math\n\ndef run(arguments):\n"
        '    return {"fahrenheit": math.floor(arguments["celsius"] * 9 / 5 + 32)}\n'
    )
    tests = {
        "cases": [
            case("a", "valid", 0, 32),
            case("b", "valid", 100, 212),
            case("d", "valid", -40, -40),
            case("c", "boundary", 1000, 1832),
        ]
    }
    outcome = build(env, Scripted(SPEC, tests, code))  # Spec declared no dependencies.
    with env[1].connect() as db:
        manifest = json.loads(
            db.execute(
                "SELECT manifest FROM candidate_versions WHERE id=?",
                (outcome["version_id"],),
            ).fetchone()[0]
        )
    assert manifest["dependencies"] == ["math"]


def test_imports_outside_the_allowlist_are_refused_before_persisting(env):
    code = "import os\n\ndef run(arguments):\n    return {}\n"
    with pytest.raises(Conflict):
        build(env, Scripted(SPEC, TESTS, code, code))
    assert not any(e["kind"] == "candidate_persisted" for e in env[3])


# ---- cross-checking disputed model-written expectations ----------------------------------
def with_wrong(*labels):
    cases = []
    for item in TESTS["cases"]:
        item = json.loads(json.dumps(item))
        if item["label"] in labels:
            item["expected"]["fahrenheit"] += 1000  # A mistake in the test writer's arithmetic.
        cases.append(item)
    return {"cases": cases}


# A second, differently written implementation of the same spec.
OTHER = (
    "def run(arguments):\n"
    '    value = arguments["celsius"] * 1.8\n'
    '    return {"fahrenheit": value + 32}\n'
)
SHIFTED = 'def run(arguments):\n    return {"fahrenheit": arguments["celsius"] * 9 / 5 + 1032}\n'
DIFFERENT = 'def run(arguments):\n    return {"fahrenheit": 7.0}\n'


def test_second_implementation_agreeing_with_the_code_corrects_the_test_and_discloses(env):
    client = Scripted(SPEC, with_wrong("body"), CODE, OTHER)
    outcome = build(env, client)
    assert outcome["disputed"] == 1
    assert len(client.prompts) == 4  # spec, tests, code, cross-check implementation
    assert "different correct formulation" in json.dumps(client.prompts[3])
    assert "freezing" not in json.dumps(client.prompts[3])  # It never sees the tests.
    adjudicated = next(e for e in env[3] if e["kind"] == "tests_adjudicated")
    assert adjudicated["result"]["method"] == "independent_second_implementation"
    assert adjudicated["result"]["resolved_for_code"] == ["body"]
    with env[1].connect() as db:
        suite = json.loads(
            db.execute(
                "SELECT c.cases FROM contracts c JOIN candidate_states s ON s.version_id=c.version_id WHERE s.status='awaiting_approval'"
            ).fetchone()[0]
        )
        assert db.execute("SELECT COUNT(*) FROM active_tools").fetchone()[0] == 0
    labels = [c["label"] for c in suite]
    assert "[disputed] body" in labels and "body" not in labels


def test_second_implementation_agreeing_with_the_test_forces_a_code_revision(env):
    # B sides with the (shifted) expectation, so the first code is the suspect one.
    client = Scripted(SPEC, with_wrong("body"), CODE, SHIFTED, CODE)
    with pytest.raises(Conflict):
        build(env, client)
    assert not any(s == "awaiting_approval" for _, s in versions(env[1]))
    adjudicated = next(e for e in env[3] if e["kind"] == "tests_adjudicated")
    assert adjudicated["result"]["verdicts"] == {"body": "test"}
    assert "failing_cases" in json.dumps(client.prompts[4])


def test_a_second_implementation_that_agrees_with_nobody_changes_nothing(env):
    with pytest.raises(Conflict):
        build(env, Scripted(SPEC, with_wrong("body"), CODE, DIFFERENT, CODE))
    adjudicated = next(e for e in env[3] if e["kind"] == "tests_adjudicated")
    assert adjudicated["result"]["verdicts"] == {"body": "unclear"}
    assert not any(s == "awaiting_approval" for _, s in versions(env[1]))


def test_an_unusable_cross_check_implementation_changes_nothing(env):
    bad = "import os\n\ndef run(arguments):\n    return {}\n"
    with pytest.raises(Conflict):
        build(env, Scripted(SPEC, with_wrong("body"), CODE, bad, bad, CODE))
    assert not any(s == "awaiting_approval" for _, s in versions(env[1]))


def test_many_disputes_are_not_cross_checked(env):
    client = Scripted(SPEC, with_wrong("freezing", "boiling", "equal point", "body"), CODE, CODE)
    with pytest.raises(Conflict):
        build(env, client)
    assert not any(e["kind"] == "tests_adjudicated" for e in env[3])


def test_build_a_tool_request_builds_stores_and_trials_without_a_tool_name():
    settings = Settings(_env_file=None, demo_mode=True)
    task = {
        "messages": [
            {"role": "user", "content": "Create a tool that converts celsius to fahrenheit"}
        ],
    }
    with TestClient(
        create_app(settings, agent_transport([SPEC, TESTS, {"source": CODE}]))
    ) as client:
        response = client.post("/chat", json=task)
        assert response.status_code == 200, response.text
        result = response.json()
        kinds = [e["kind"] for e in result["activity"]]
        assert {"tool_spec_drafted", "candidate_tests", "trial_run"} <= set(kinds)
        assert "Trial run" in result["message"]
        assert client.get("/improvement").json()["active"] == []
        assert client.get("/improvement").json()["candidates"][0]["status"] == "awaiting_approval"
        assert "reflexion_saved" in kinds


def test_reflexion_lessons_are_stored_and_recalled_for_similar_goals():
    settings = Settings(_env_file=None, demo_mode=True)
    build = {
        "messages": [
            {"role": "user", "content": "Create a tool that converts celsius to fahrenheit"}
        ],
    }
    ask = {"messages": [{"role": "user", "content": "How do celsius and fahrenheit relate"}]}
    script = [SPEC, TESTS, {"source": CODE}, PLAN, lambda body: text("Fahrenheit is celsius times nine fifths plus thirty two.")]
    with TestClient(create_app(settings, agent_transport(script))) as client:
        assert client.post("/chat", json=build).status_code == 200
        reply = client.post("/chat", json=ask)
        assert reply.status_code == 200, reply.text
        recalled = [e for e in reply.json()["activity"] if e["kind"] == "reflexion_recalled"]
        assert recalled and "celsius" in recalled[0]["result"]["lessons"][0]["lesson"].lower()


def test_spec_schema_bounds_are_filled_in_when_the_model_omits_them():
    from app.improvement.general_builder import normalize_schema
    from app.improvement.models import schema_check

    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}, "rows": {"type": "array", "items": {"type": "string"}}},
    }
    schema_check(normalize_schema(schema))
