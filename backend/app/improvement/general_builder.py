"""General (non-contract) tool builder: separate spec/tests/code model calls, backend checks.

There is no backend-defined oracle for an arbitrary new capability, so the evidence is
layered and labeled honestly:

1. A *spec* call defines name, behavior and strict input/output schemas.
2. A *test* call sees only the spec and writes valid/boundary cases (expected outputs).
3. A *code* call sees only the spec (never the tests) and writes ``run(arguments)``.
4. The backend derives invalid/malformed/security cases from the input schema itself,
   checks outputs against the output schema, re-runs for determinism and executes the
   code in the DEMO child process (restricted builtins/imports, timeout, output cap).
5. The result is ``awaiting_approval``: a human reviews source and tests before any use.

Agreement between independently generated code and tests is evidence, not proof.
"""

import ast
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..storage import Conflict, encode, now
from .demo_subprocess import DemoSubprocess
from .models import (
    DEPENDENCIES,
    loose_equal,
    Artifact,
    Binding,
    Case,
    ContractInput,
    Manifest,
    Strict,
    digest,
    schema_check,
    value_check,
)
from .persistence import Repository
from .source_formatting import format_candidate_source
from .validation import Validator, static_checks
from .business_contracts import validate_manifest_input
from ..llm_client import LLMError

PROVENANCE = "model_generated_independent"
ACTOR = "general-builder-model-tests-v1"
SCHEMA_RULES = (
    "JSON Schema subset only: types object/array/string/number/integer/boolean. "
    "Every object needs properties, required and additionalProperties=false (at most 20 "
    "properties). Every string needs maxLength<=4000. Every array needs items and "
    "maxItems<=100. Numbers/integers may use minimum/maximum. No $ref, anyOf, patterns, "
    "formats or defaults. Input and output must both be objects."
)
CODE_RULES = (
    "Python source defining exactly one entry point `def run(arguments):` plus optional "
    "helper functions. No classes, decorators, defaults, annotations, lambdas with "
    "side effects, globals, I/O, files, network, shell, eval, exec, introspection, or names "
    "starting with an underscore. Allowed imports only: "
    + ", ".join(sorted(DEPENDENCIES))
    + " (declare any you import). Read inputs as arguments['key'] and return a dict with "
    "exactly the output keys. Do not mutate the input. Keep it under 80 lines and 110 columns."
)


def normalize_schema(schema, depth=0):
    """Fill the bounds models often omit (string length, array size, additionalProperties)."""
    if not isinstance(schema, dict) or depth > 5:
        return schema
    kind = schema.get("type")
    if kind == "string":
        schema["maxLength"] = min(int(schema.get("maxLength") or 200), 4000) if isinstance(schema.get("maxLength"), (int, float)) else 200
    elif kind == "array":
        cap = schema.get("maxItems")
        schema["maxItems"] = min(int(cap), 100) if isinstance(cap, (int, float)) else 50
        normalize_schema(schema.get("items"), depth + 1)
    elif kind == "object":
        schema["additionalProperties"] = False
        schema.setdefault("required", [])
        for value in (schema.get("properties") or {}).values():
            normalize_schema(value, depth + 1)
    return schema


class GeneralSpec(Strict):
    stem: str = Field(pattern=r"^[a-z][a-z0-9_]{2,23}$")
    description: str = Field(min_length=10, max_length=300)
    behavior: str = Field(min_length=20, max_length=1500)
    input_schema: dict
    output_schema: dict
    dependencies: list[str] = Field(default_factory=list, max_length=8)


class DraftCase(Strict):
    label: str = Field(min_length=1, max_length=80)
    category: Literal["valid", "boundary"]
    input: dict
    expected: dict


class TestDraft(Strict):
    cases: list[DraftCase] = Field(min_length=4, max_length=12)


class CodeDraft(Strict):
    source: str = Field(min_length=1, max_length=24000)


def _wrong_value(schema):
    return {
        "string": 12345,
        "number": "not a number",
        "integer": "not an integer",
        "boolean": "yes",
        "array": "not a list",
        "object": [],
        "null": 0,
    }[schema["type"]]


def reject_cases(manifest, base):
    """Deterministic invalid/malformed/security cases derived from the input schema."""
    schema = manifest["input_schema"]
    props = schema["properties"]
    candidates = []
    for key, sub in props.items():
        candidates.append(
            ("invalid", f"wrong type for {key}", {**base, key: _wrong_value(sub)})
        )
        if sub["type"] == "string":
            candidates.append(
                ("invalid", f"oversized {key}", {**base, key: "x" * (sub["maxLength"] + 1)})
            )
        if sub["type"] in {"number", "integer"} and "maximum" in sub:
            candidates.append(
                ("invalid", f"{key} above maximum", {**base, key: sub["maximum"] + 1})
            )
        if sub["type"] in {"number", "integer"} and "minimum" in sub:
            candidates.append(
                ("invalid", f"{key} below minimum", {**base, key: sub["minimum"] - 1})
            )
        if sub["type"] == "array":
            candidates.append(
                ("invalid", f"too many {key}", {**base, key: [0] * (sub["maxItems"] + 1)})
            )
    for key in schema.get("required", []):
        candidates.append(
            ("malformed", f"missing {key}", {k: v for k, v in base.items() if k != key})
        )
    candidates += [
        ("malformed", "not an object", []),
        ("malformed", "null input", None),
        ("security", "injected command field", {**base, "command": "rm -rf /"}),
        ("security", "injected import field", {**base, "__import__": "os"}),
        ("security", "injected path field", {**base, "path": "../../secrets"}),
    ]
    cases, seen = [], set()
    for category, label, value in candidates:
        try:
            validate_manifest_input(manifest, value)
            continue  # Accepted by the schema, so it is not a boundary rejection test.
        except (ValueError, TypeError, KeyError):
            pass
        if label in seen:
            continue
        seen.add(label)
        cases.append(
            Case(label=label, category=category, input=value, expected=None, reject=True)
        )
    return cases[:14]


class Builder:
    def __init__(self, settings, client, database, record):
        self.settings, self.client, self.database, self.record = (
            settings,
            client,
            database,
            record,
        )

    async def ask(self, model, system, user, label, tries=2, temperature=0):
        feedback = ""
        for attempt in range(1, tries + 1):
            self.record(
                "model_request",
                f"Drafting the tool {label}.",
                result={"step": label, "attempt": attempt},
            )
            payload = await self.client.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user + feedback},
                ],
                json_mode=True,
                max_tokens=self.settings.candidate_max_tokens,
                temperature=temperature,
            )
            if payload["done_reason"] == "length":
                feedback = "\nYour previous reply was cut off. Be shorter."
                continue
            try:
                return model.model_validate_json(payload["message"]["content"])
            except (ValidationError, ValueError) as exc:
                detail = (
                    "; ".join(
                        ".".join(map(str, e["loc"])) + ": " + e["msg"][:120]
                        for e in exc.errors(include_input=False)[:6]
                    )
                    if isinstance(exc, ValidationError)
                    else str(exc)[:200]
                )
                feedback = "\nYour previous reply was invalid: " + detail
        raise Conflict(f"The model could not produce a valid {label} after {tries} tries.")

    # ---- steps -------------------------------------------------------------------
    async def spec(self, goal, requested, arguments):
        spec = await self.ask(
            GeneralSpec,
            "Design ONE small pure-computation tool that a user's task needs. Reply with a "
            'JSON object with keys: "stem" (snake_case name, 3-24 chars), "description", '
            '"behavior" (a precise, complete, unambiguous specification of the algorithm, '
            'rounding, units and edge cases), "input_schema", "output_schema", '
            '"dependencies" (subset of ' + ", ".join(sorted(DEPENDENCIES)) + "). "
            + SCHEMA_RULES,
            json.dumps(
                {
                    "user_task_untrusted": goal[:800],
                    "tool_name_the_agent_tried": requested,
                    "example_arguments_the_agent_tried": arguments,
                }
            )[:2500],
            "spec",
        )
        try:
            normalize_schema(spec.input_schema)
            normalize_schema(spec.output_schema)
            schema_check(spec.input_schema)
            schema_check(spec.output_schema)
        except (ValueError, KeyError, TypeError) as exc:
            raise Conflict(f"The drafted schemas are invalid: {exc}"[:300]) from exc
        if (
            spec.input_schema["type"] != "object"
            or spec.output_schema["type"] != "object"
            or set(spec.dependencies) - DEPENDENCIES
        ):
            raise Conflict("The drafted spec violates the tool contract rules.")
        return spec

    async def tests(self, spec):
        draft = await self.ask(
            TestDraft,
            "Write acceptance test cases for the tool specified below, from the spec ONLY. "
            'Reply with JSON {"cases":[{"label","category","input","expected"}]} with at '
            "least 3 category 'valid' and 2 category 'boundary' cases. Every input must be "
            "valid under input_schema; every expected must be the exact output (valid under "
            "output_schema) computed by hand with great care. Prefer values whose results "
            "are exact (integers, halves) and avoid floating-point ambiguity.",
            json.dumps(spec.model_dump()),
            "tests",
        )
        cases, seen = [], set()
        for item in draft.cases:
            try:
                value_check(spec.input_schema, item.input)
                value_check(spec.output_schema, item.expected)
            except ValueError:
                continue
            if item.label in seen:
                continue
            seen.add(item.label)
            cases.append(
                Case(
                    label=item.label,
                    category=item.category,
                    input=item.input,
                    expected=item.expected,
                )
            )
        kinds = [c.category for c in cases]
        if kinds.count("valid") < 2 or kinds.count("boundary") < 1:
            raise Conflict(
                "Too few drafted test cases were valid under the spec's own schemas."
            )
        return cases

    async def code(self, spec, feedback):
        return await self.ask(
            CodeDraft,
            "Implement the tool specified below. " + CODE_RULES + ' Reply with JSON {"source": "<python code>"}.',
            json.dumps(
                {
                    "spec": spec.model_dump(),
                    "revision_feedback": feedback,
                }
            )[:9000],
            "code",
        )

    async def second_implementation(self, spec):
        """An independently written second implementation used to cross-check disputes."""
        return await self.ask(
            CodeDraft,
            "Implement the tool specified below. Write it independently and, where a "
            "different correct formulation exists (for example iterative instead of "
            "closed-form, or integer/decimal arithmetic instead of floats), prefer that one. "
            + CODE_RULES
            + ' Reply with JSON {"source": "<python code>"}.',
            json.dumps({"spec": spec.model_dump()}),
            "cross-check implementation",
            temperature=0.7,
        )

    def gap(self, name, goal, task_id):
        fingerprint = f"general:{name}:{digest(goal[:200])[:12]}"
        identifier = str(uuid4())
        value = {
            "id": identifier,
            "capability": name,
            "error_category": "unknown_tool",
            "occurrences": 1,
            "minimum_evidence": 1,
            "status": "observed_general_gap",
            "evidence": [{"task_id": task_id, "category": "unknown_tool"}],
            "proposed_improvement": "Draft a model-tested tool for review; requires human approval before any use.",
            "expected_benefit": "Serve this capability in later tasks after approval.",
            "risk": "high_untrusted_demo_host_code",
        }
        with self.database.connect() as db:
            prior = db.execute(
                "SELECT id FROM gaps WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if prior:
                return prior[0]
            db.execute(
                "INSERT INTO gaps VALUES(?,?,?,?)",
                (identifier, fingerprint, encode(value), now()),
            )
        return identifier


def imported_modules(source):
    """Top-level module names the source imports; unparseable source imports nothing here."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def _results(execution, count):
    values = execution.get("results") if isinstance(execution, dict) else None
    if (
        not isinstance(values, list)
        or len(values) != count
        or execution.get("timeout")
        or execution.get("output_limit")
        or execution.get("exit_code") != 0
    ):
        return None
    return values


async def build_general(settings, client, database, record, goal, requested, arguments, task_id):
    """Draft, test and persist a tool awaiting human approval, then trial it once.

    The trial runs the new code in the DEMO subprocess on the user's own arguments (or the
    first model-written valid case) so the answer shows the tool working. It never activates
    the tool: permanent use still needs the human approval step.
    """
    outcome = await _build_general(
        settings, client, database, record, goal, requested, arguments, task_id
    )
    version = Repository(database).version(outcome["version_id"])
    outcome["trial"] = None
    source = version["source"]
    adapter = DemoSubprocess(settings)
    tests = version["manifest"].get("generated_tests") or []
    pool = [arguments] if isinstance(arguments, dict) and arguments else []
    pool += [t["input"] for t in tests if not t.get("reject")][:1]
    for given in pool:
        try:
            execution = await run_in_threadpool(adapter.run, source, [given])
        except Exception:
            continue
        values = _results(execution, 1)
        if values and values[0].get("ok") is True:
            outcome["trial"] = {
                "input": given,
                "output": values[0].get("value"),
                "user_supplied": given is arguments,
            }
            break
    record(
        "trial_run",
        "Ran the new tool once in the DEMO subprocess to show it working. Not independently verified."
        if outcome["trial"]
        else "No valid input was available for a trial run.",
        tool=outcome["name"],
        result=outcome["trial"],
    )
    return outcome


async def _build_general(settings, client, database, record, goal, requested, arguments, task_id):
    if not settings.demo_mode:
        raise Conflict("Local demo execution is disabled.")
    builder = Builder(settings, client, database, record)
    repository = Repository(database)
    adapter = DemoSubprocess(settings)
    validator = Validator(repository, adapter)

    try:
        spec = await builder.spec(goal, requested, arguments)
        cases = await builder.tests(spec)
    except LLMError:
        raise
    record(
        "tool_spec_drafted",
        "The model drafted a spec and, separately, acceptance tests (the code writer will not see them).",
        result={
            "name_stem": spec.stem,
            "description": spec.description,
            "behavior": spec.behavior,
            "input_schema": spec.input_schema,
            "output_schema": spec.output_schema,
            "model_test_cases": len(cases),
        },
    )
    gap_id = builder.gap(requested, goal, task_id)
    identifier, base_id = repository.proposed(gap_id)
    name = f"candidate_{spec.stem}_{identifier.replace('-', '')[:10]}"
    manifest_fields = dict(
        name=name,
        description=spec.description + " MODEL-TESTED: not independently verified.",
        behavior=spec.behavior,
        test_provenance=PROVENANCE,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        dependencies=spec.dependencies,
        generated_tests=cases[:20],
    )
    valid_inputs = [c.input for c in cases]
    feedback = ""
    adjudicated = False
    for attempt in range(1, settings.agentic_build_attempts + 1):
        record(
            "candidate_generation",
            "The model API is writing the tool source from the spec only.",
            result={"candidate_id": identifier, "attempt": attempt, "provenance": PROVENANCE},
        )
        raw = ""
        try:
            draft = await builder.code(spec, feedback)
            raw = draft.source
            imported = imported_modules(draft.source)
            if imported - DEPENDENCIES:
                raise Conflict(
                    "Imports outside the allowed list: "
                    + ", ".join(sorted(imported - DEPENDENCIES))
                    + ". Allowed: "
                    + ", ".join(sorted(DEPENDENCIES))
                )
            # The declared list always equals what the code really imports (plus the spec).
            manifest_fields["dependencies"] = sorted(
                set(spec.dependencies) | imported
            )
            manifest = Manifest(**manifest_fields)
            artifact = Artifact(source=draft.source, manifest=manifest)
            version = repository.append(identifier, artifact, base_id)
            base_id = version["id"]
            formatted = format_candidate_source(draft.source)
            if formatted != draft.source:
                artifact = Artifact(source=formatted, manifest=manifest)
                version = repository.append(identifier, artifact, base_id)
                base_id = version["id"]
        except LLMError as exc:
            repository.state(base_id, "generation_failed", f"{exc.code}: {exc.message}")
            record(
                "candidate_failed",
                "Model generation failed; nothing was drafted.",
                result={"candidate_id": identifier, "error": exc.code},
            )
            raise
        except (ValueError, ValidationError, Conflict) as exc:
            feedback = "Previous attempt rejected: " + str(exc)[:500]
            repository.generation_failure(identifier, base_id, raw, feedback)
            base_id = repository.detail(identifier)["versions"][0]["id"]
            record("candidate_failed", feedback, result={"attempt": attempt})
            continue
        binding = Binding(
            version_id=version["id"],
            source_hash=version["source_hash"],
            content_hash=version["content_hash"],
        )
        record(
            "candidate_persisted",
            "Immutable source, spec and model tests persisted.",
            tool=name,
            result={
                "candidate_id": identifier,
                "version_id": version["id"],
                "source_hash": version["source_hash"],
            },
        )
        findings = static_checks(Artifact(source=version["source"], manifest=manifest))
        if findings:
            feedback = "Static findings: " + "; ".join(findings)[:600]
            repository.state(version["id"], "validation_failed", feedback)
            record("candidate_failed", feedback, tool=name, result={"attempt": attempt})
            continue
        # Determinism: identical inputs must give identical outputs across two runs.
        first = await run_in_threadpool(adapter.run, version["source"], valid_inputs)
        second = await run_in_threadpool(adapter.run, version["source"], valid_inputs)
        a, b = _results(first, len(valid_inputs)), _results(second, len(valid_inputs))
        deterministic = a is not None and b is not None and digest(a) == digest(b)
        record(
            "candidate_determinism",
            "Re-ran the same inputs twice and compared outputs."
            + ("" if deterministic else " Outputs differed or execution failed."),
            tool=name,
            result={"deterministic": deterministic},
        )
        if not deterministic:
            feedback = "Execution failed or was not deterministic. Use only pure logic."
            repository.state(version["id"], "validation_failed", feedback)
            continue
        suite = cases + reject_cases(version["manifest"], valid_inputs[0])
        try:
            validator.contract(
                ContractInput(**binding.model_dump(), cases=suite, confirm=True), ACTOR
            )
        except Conflict as exc:
            feedback = "Test suite rejected: " + str(exc)[:300]
            repository.state(version["id"], "validation_failed", feedback)
            record("candidate_failed", feedback, tool=name, result={"attempt": attempt})
            continue
        run = await run_in_threadpool(validator.validate, binding)
        record(
            "candidate_tests",
            "Candidate ran its model-written tests in the DEMO subprocess (not a secure sandbox).",
            tool=name,
            result={
                "state": run["state"],
                "passed": sum(1 for c in run["payload"]["cases"] if c["passed"]),
                "total": len(run["payload"]["cases"]),
                "cases": run["payload"]["cases"],
            },
        )
        if run["state"] == "passed":
            return {
                "name": name,
                "candidate_id": identifier,
                "version_id": version["id"],
                "source_hash": version["source_hash"],
                "passed": len(run["payload"]["cases"]),
                "model_cases": len(cases),
                "disputed": 0,
            }
        failed = [c for c in run["payload"]["cases"] if not c["passed"]]
        by_label = {c.label: c for c in cases}
        model_failed = [c for c in failed if c["label"] in by_label]
        agreeing = len(cases) - len(model_failed)
        eligible = (
            model_failed
            and len(model_failed) == len(failed)  # Boundary/reject checks all held.
            and not run["payload"]["static_findings"]
            and len(model_failed) <= 3
            and agreeing >= 3
            and not adjudicated
        )
        if eligible:
            adjudicated = True
            actual = {
                c["label"]: (c.get("actual") or {}).get("value") for c in model_failed
            }
            disputes = [
                {
                    "label": label,
                    "input": by_label[label].input,
                    "test_expected": by_label[label].expected,
                    "implementation_actual": actual[label],
                }
                for label in actual
                if actual[label] is not None
            ]
            verdicts = {}
            second = None
            if disputes:
                try:
                    second = (await builder.second_implementation(spec)).source
                    extra = imported_modules(second)
                    if extra - DEPENDENCIES or static_checks(
                        Artifact(
                            source=second,
                            manifest=Manifest(
                                **{
                                    **manifest_fields,
                                    "dependencies": sorted(DEPENDENCIES),
                                }
                            ),
                        )
                    ):
                        second = None
                except (Conflict, ValueError, ValidationError):
                    second = None
            if second:
                other = _results(
                    await run_in_threadpool(
                        adapter.run, second, [d["input"] for d in disputes]
                    ),
                    len(disputes),
                )
                for position, dispute in enumerate(disputes):
                    value = (
                        other[position].get("value")
                        if other and other[position].get("ok") is True
                        else None
                    )
                    if value is None:
                        verdicts[dispute["label"]] = "unclear"
                    elif loose_equal(value, dispute["implementation_actual"]):
                        verdicts[dispute["label"]] = "code"
                    elif loose_equal(value, dispute["test_expected"]):
                        verdicts[dispute["label"]] = "test"
                    else:
                        verdicts[dispute["label"]] = "unclear"
            code_right = {
                label for label, verdict in verdicts.items() if verdict == "code"
            } & set(actual)
            record(
                "tests_adjudicated",
                "Some model-written expectations disagreed with the code; an independently "
                "written second implementation cross-checked them.",
                tool=name,
                result={
                    "method": "independent_second_implementation",
                    "disputed": [d["label"] for d in disputes],
                    "verdicts": verdicts,
                    "resolved_for_code": sorted(code_right),
                },
            )
            if code_right and code_right == set(actual):
                adjusted = []
                for item in cases:
                    if item.label in code_right:
                        item = Case(
                            label=("[disputed] " + item.label)[:80],
                            category=item.category,
                            input=item.input,
                            expected=actual[item.label],
                        )
                    adjusted.append(item)
                try:
                    for item in adjusted:
                        value_check(spec.output_schema, item.expected)
                    manifest = Manifest(**{**manifest_fields, "generated_tests": adjusted[:20]})
                    version = repository.append(
                        identifier, Artifact(source=version["source"], manifest=manifest), base_id
                    )
                    base_id = version["id"]
                    binding = Binding(
                        version_id=version["id"],
                        source_hash=version["source_hash"],
                        content_hash=version["content_hash"],
                    )
                    validator.contract(
                        ContractInput(
                            **binding.model_dump(),
                            cases=adjusted + reject_cases(version["manifest"], valid_inputs[0]),
                            confirm=True,
                        ),
                        ACTOR,
                    )
                except (ValueError, Conflict) as exc:
                    record("candidate_failed", "Adjusted suite rejected: " + str(exc)[:200], tool=name)
                else:
                    run = await run_in_threadpool(validator.validate, binding)
                    record(
                        "candidate_tests",
                        "Re-ran the suite with disputed expectations resolved per the third opinion.",
                        tool=name,
                        result={
                            "state": run["state"],
                            "passed": sum(1 for c in run["payload"]["cases"] if c["passed"]),
                            "total": len(run["payload"]["cases"]),
                            "cases": run["payload"]["cases"],
                        },
                    )
                    if run["state"] == "passed":
                        return {
                            "name": name,
                            "candidate_id": identifier,
                            "version_id": version["id"],
                            "source_hash": version["source_hash"],
                            "passed": len(run["payload"]["cases"]),
                            "model_cases": len(cases),
                            "disputed": len(code_right),
                        }
                    failed = [c for c in run["payload"]["cases"] if not c["passed"]]
        feedback = json.dumps(
            {
                "previous_source": version["source"],
                "static_findings": run["payload"]["static_findings"],
                "failing_cases": [
                    {
                        "label": c["label"],
                        "category": c["category"],
                        "actual": c.get("actual"),
                    }
                    for c in failed
                ][:6],
                "note": "The tests were written separately and could also be wrong; "
                "follow the spec exactly.",
            }
        )[:5000]
        record(
            "reflection",
            "Tests disagree with the code; revising once from the actual failures.",
            result={"attempt": attempt, "failed": [c["label"] for c in failed][:6]},
        )
    raise Conflict(
        "The drafted tool did not pass its tests within the bounded attempts. Nothing was activated; the attempt is kept for review in Self-Development."
    )
