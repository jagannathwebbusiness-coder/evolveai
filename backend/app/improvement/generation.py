"""The model generation persists failed attempts and never executes their source."""

import json

from pydantic import ValidationError

from ..llm_client import LLMError
from ..storage import Conflict
from .models import Artifact


async def generate(repository, llm, gap_id):
    identifier, proposed_id = repository.proposed(gap_id)
    with repository.database.connect() as db:
        gap = json.loads(
            db.execute(
                "SELECT payload FROM gaps WHERE id=?", (str(gap_id),)
            ).fetchone()[0]
        )
    if not gap["occurrences"]:
        repository.state(
            proposed_id,
            "generation_failed",
            "No retained task evidence remains for this gap.",
        )
        return repository.detail(identifier)
    content = ""
    try:
        status = await llm.model_status()
        if status.status != "ready":
            raise LLMError(503, status.status, status.message)
        response_format = Artifact.model_json_schema()
        # Pydantic's `dict` permits arbitrary schema objects. Constrain the model's
        # output grammar to the required object-contract keys as well as validating afterwards.
        object_contract = {
            "type": "object",
            "properties": {
                "type": {"const": "object"},
                "properties": {"type": "object"},
                "required": {"type": "array", "items": {"type": "string"}},
                "additionalProperties": {"const": False},
            },
            "required": ["type", "properties", "required", "additionalProperties"],
            "additionalProperties": False,
        }
        manifest_format = response_format["$defs"]["Manifest"]["properties"]
        manifest_format["input_schema"] = object_contract
        manifest_format["output_schema"] = object_contract
        manifest_format["name"] = {
            "const": "candidate_tool_" + identifier.replace("-", "")[:10]
        }
        # Only redacted failure category/count are supplied. No messages, file data or preferences.
        payload = await llm.complete(
            [
                {
                    "role": "system",
                    "content": "Generate one small review-only Python pure JSON transformation tool. Return the requested JSON artifact. Source must define only def run(arguments): and permitted standard-library imports. No module-level calls, decorators, annotations, filesystem, environment, network, shell, eval or exec. Tool name starts candidate_. Input/output schemas use type object, properties, required, additionalProperties false. Strings need maxLength<=4000; arrays need items and maxItems<=100. No $ref, anyOf or patterns. Dependencies only math/statistics/decimal/json/re/collections. Permissions [pure_json]. replaces null. At least two generated_tests with label/category/input/expected/reject; these tests are untrusted suggestions. Keep it small (under 80 source lines). Do not claim improvement. Respond with one JSON object matching this JSON Schema: "
                    + json.dumps(response_format),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "capability": gap["capability"],
                            "error_category": gap["error_category"],
                            "occurrences": gap["occurrences"],
                            "instruction": (
                                "Generate a safe ratio tool: input numerator/denominator numbers; output quotient number and defined boolean. If denominator is zero, quotient=0 and defined=false (not a mathematical quotient). Otherwise divide and defined=true. Use additionalProperties=false and required keys in BOTH schemas. At least two suggested tests including zero divisor. No imports needed."
                                if gap["error_category"] == "division_by_zero"
                                else "Propose a small bounded transformation addressing this recorded failure category, not unrelated speculative capability. Human acceptance tests are required before execution."
                            ),
                        }
                    ),
                },
            ],
            json_mode=True,
            max_tokens=llm.settings.candidate_max_tokens,
        )
        content = payload["message"].get("content", "")
        if payload.get("done") is not True or payload.get("done_reason") == "length":
            raise ValueError("Generation was incomplete or truncated.")
        content = payload["message"]["content"]
        if not isinstance(content, str) or len(content) > 40000:
            raise ValueError("Generated artifact is not bounded JSON.")
        artifact = Artifact.model_validate_json(content)
        repository.append(identifier, artifact, proposed_id)
    except LLMError as exc:
        repository.state(proposed_id, "generation_failed", f"{exc.code}: {exc.message}")
    except (ValueError, KeyError, TypeError, ValidationError, Conflict) as exc:
        detail = (
            "; ".join(
                ".".join(str(x) for x in e["loc"])
                + ": "
                + e["type"]
                + " ("
                + e["msg"][:160]
                + ")"
                for e in exc.errors(include_input=False)[:8]
            )
            if isinstance(exc, ValidationError)
            else str(exc)[:300]
        )
        repository.generation_failure(
            identifier,
            proposed_id,
            content if isinstance(content, str) else "",
            "Invalid/incomplete local artifact: "
            + detail
            + ". Raw output retained when available. No execution or tests occurred.",
        )
    return repository.detail(identifier)
