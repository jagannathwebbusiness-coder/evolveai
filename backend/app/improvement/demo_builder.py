"""Explicit demo policy: model source, independent oracle, actual execution, exact activation."""

import json

from pydantic import Field
from starlette.concurrency import run_in_threadpool

from ..storage import Conflict
from ..llm_client import LLMError
from .activation import Activation
from .business_contracts import BY_NAME, check_manifest
from .demo_subprocess import DemoSubprocess
from .models import (
    Artifact,
    Binding,
    ContractInput,
    DecisionInput,
    Manifest,
    Strict,
)
from .persistence import Repository
from .validation import Validator
from .source_formatting import format_candidate_source


class Draft(Strict):
    source: str = Field(min_length=1, max_length=24000)


async def build(settings, client, database, gap_id, record, capability=None):
    if not settings.demo_mode:
        raise Conflict("Local demo execution is disabled.")
    repository = Repository(database)
    capability = capability or BY_NAME["median_numbers"]
    identifier, base_id = repository.proposed(gap_id)
    stem = (
        "median"
        if capability.name == "median_numbers"
        else capability.name.removeprefix("business_")
    )
    name = "candidate_" + stem + "_" + identifier.replace("-", "")[:10]
    adapter = DemoSubprocess(settings)
    validator, activation = Validator(repository, adapter), Activation(
        repository, adapter
    )
    feedback = ""
    for attempt in range(1, settings.agentic_build_attempts + 1):
        record(
            "candidate_generation",
            "The model API is generating candidate source.",
            result={
                "candidate_id": identifier,
                "attempt": attempt,
                "contract": capability.contract,
                "independent_case_count": len(capability.cases()),
                "execution": adapter.status(),
            },
        )
        raw = ""
        try:
            payload = await client.complete(
                [
                    {
                        "role": "system",
                        "content": "Write a small pure Python business/numeric transformation. Return JSON with a single source string containing complete Python code. "
                        "Source must define def run(arguments): with no annotations, defaults, decorators or imports. "
                        "Only ordinary functions at module scope. Do not mutate the input. "
                        "Input is an object, read its named keys with arguments['key']. Return the exact output keys. "
                        "Numeric values must be FLOAT unless the output schema specifies integer. No I/O, introspection, shell, globals, eval or exec. "
                        "Use normal local variable names without leading underscores. Keep source below 40 lines, 120 columns. "
                        "Return only source JSON, no tests or commentary. Contract: "
                        + capability.description
                        + " Formula: "
                        + capability.formula
                        + ' Respond with exactly one JSON object: {"source": "<python code>"}.',
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "input_schema": capability.inputs,
                                "output_schema": capability.outputs,
                                "revision_feedback": feedback,
                            }
                        ),
                    },
                ],
                json_mode=True,
                max_tokens=settings.candidate_max_tokens,
            )
            raw = payload["message"]["content"]
            if (
                payload.get("done") is not True
                or payload.get("done_reason") == "length"
            ):
                raise ValueError("Source generation was incomplete/truncated.")
            draft = Draft.model_validate_json(raw)
            artifact = Artifact(
                source=draft.source,
                manifest=Manifest(
                    name=name,
                    description=capability.description
                    + " DEMO: unsecured host subprocess.",
                    demo_contract=capability.contract,
                    test_provenance="backend_independent_demo",
                    input_schema=capability.inputs,
                    output_schema=capability.outputs,
                    generated_tests=capability.cases()[:2],
                ),
            )
            check_manifest(artifact.manifest.model_dump())
            version = repository.append(identifier, artifact, base_id)
            base_id = version["id"]
            formatted = format_candidate_source(draft.source)
            if formatted != draft.source:
                original_version = version
                artifact = Artifact(source=formatted, manifest=artifact.manifest)
                version = repository.append(identifier, artifact, base_id)
                base_id = version["id"]
                record(
                    "candidate_formatted",
                    "Preserved original model source and a whitespace-only, AST-equivalent formatted version.",
                    tool=name,
                    result={
                        "original_version_id": original_version["id"],
                        "original_source_hash": original_version["source_hash"],
                        "version_id": version["id"],
                        "source_hash": version["source_hash"],
                        "ast_equivalent": True,
                        "logic_repair": False,
                    },
                )
        except LLMError as exc:
            repository.state(base_id, "generation_failed", f"{exc.code}: {exc.message}")
            record(
                "candidate_failed",
                "The model generation failed; no candidate was activated.",
                result={"candidate_id": identifier, "error": exc.code},
            )
            raise
        except (ValueError, TypeError, KeyError, Conflict) as exc:
            feedback = "Generation contract failure: " + str(exc)[:600]
            repository.generation_failure(
                identifier, base_id, raw if isinstance(raw, str) else "", feedback
            )
            base_id = repository.detail(identifier)["versions"][0]["id"]
            record(
                "candidate_failed",
                feedback,
                result={"candidate_id": identifier, "attempt": attempt},
            )
            continue
        binding = Binding(
            **{k: version[k] for k in ("source_hash", "content_hash")},
            version_id=version["id"],
        )
        record(
            "candidate_persisted",
            "Immutable model source and contract persisted.",
            tool=name,
            result={
                "candidate_id": identifier,
                "version_id": version["id"],
                "version": version["version"],
                "source_hash": version["source_hash"],
            },
        )
        validator.contract(
            ContractInput(
                **binding.model_dump(), cases=capability.cases(), confirm=True
            ),
            "demo-independent-oracle-v1",
        )
        run = await run_in_threadpool(validator.validate, binding)
        # These are the actual persisted outcomes, including real subprocess exit and values.
        record(
            "candidate_tests",
            "Candidate validation completed in local DEMO subprocess (not a secure sandbox).",
            tool=name,
            result=run,
        )
        if run["state"] != "passed":
            feedback = json.dumps(
                {
                    "previous_source": version["source"],
                    "static_findings": run["payload"]["static_findings"],
                    "failed_cases": [
                        c for c in run["payload"]["cases"] if not c["passed"]
                    ],
                    "instruction": "Revise using actual independent test failures. Preserve the dictionary interface and exact output keys/types. "
                    + capability.formula,
                }
            )[:6000]
            record(
                "reflection",
                "Candidate failed; revise source using actual validation findings.",
                result={
                    "attempt": attempt,
                    "next_attempt": (
                        attempt + 1
                        if attempt < settings.agentic_build_attempts
                        else None
                    ),
                },
            )
            continue
        for action in ("approve", "activate"):
            activation.decide(
                DecisionInput(
                    **binding.model_dump(),
                    action=action,
                    confirm=True,
                    reason="Explicit local demo task opt-in; independent contract and actual executable tests passed.",
                ),
                "automatic-demo-policy",
            )
        record(
            "candidate_activated",
            "Tested candidate automatically activated under explicit DEMO policy.",
            tool=name,
            result={
                "candidate_id": identifier,
                "version_id": version["id"],
                "source_hash": version["source_hash"],
                "actor": "automatic-demo-policy",
            },
        )
        return name
    raise Conflict(
        "Candidate generation/testing exhausted its bounded attempts. No candidate was activated."
    )
