"""Small, bounded JSON contract dialect. No external references or executable validators."""

import hashlib
import json
import math
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

BUILTINS = {
    "calculate",
    "analyze_csv",
    "list_workspace_files",
    "read_workspace_file",
    "create_report",
}
DEPENDENCIES = {
    "math",
    "statistics",
    "decimal",
    "json",
    "re",
    "collections",
    "datetime",
}


def digest(value):
    content = (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def loose_equal(a, b):
    """JSON equality where 212 and 212.0 (or 1e-12 noise) agree; booleans stay distinct."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(loose_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(loose_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def finite_number(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def schema_check(schema, depth=0):
    if depth > 5 or not isinstance(schema, dict):
        raise ValueError("Schema must be a bounded object (depth <= 5).")
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "enum",
        "description",
    }
    kind = schema.get("type")
    specific = {
        "object": {"properties", "required", "additionalProperties"},
        "array": {"items", "minItems", "maxItems"},
        "string": {"minLength", "maxLength"},
        "number": {"minimum", "maximum"},
        "integer": {"minimum", "maximum"},
        "boolean": set(),
        "null": set(),
    }
    if (
        not isinstance(kind, str)
        or kind not in specific
        or schema.keys() - ({"type", "description", "enum"} | specific[kind])
    ):
        raise ValueError("Schema keywords must match their declared type.")
    digest(schema)  # Reject NaN/Infinity anywhere, including enums.
    if schema.keys() - allowed or kind not in {
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "null",
    }:
        raise ValueError(
            "Use the documented JSON schema subset; references and executable patterns are unsupported."
        )
    if "enum" in schema and (
        not isinstance(schema["enum"], list) or not 1 <= len(schema["enum"]) <= 30
    ):
        raise ValueError("Enum must contain 1..30 JSON values.")
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (
            not finite_number(schema[key])
            or (
                key not in {"minimum", "maximum"}
                and (type(schema[key]) is not int or schema[key] < 0)
            )
        ):
            raise ValueError(
                "Schema bounds must be finite and lengths nonnegative integers."
            )
    for lo, hi in (
        ("minimum", "maximum"),
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
    ):
        if lo in schema and hi in schema and schema[lo] > schema[hi]:
            raise ValueError("Schema bounds are inverted.")
    if kind == "object":
        props, required = schema.get("properties"), schema.get("required", [])
        if (
            not isinstance(props, dict)
            or len(props) > 20
            or schema.get("additionalProperties") is not False
        ):
            raise ValueError(
                "Objects need <=20 properties and additionalProperties=false."
            )
        if not isinstance(required, list) or any(
            not isinstance(k, str) or k not in props for k in required
        ):
            raise ValueError("Required keys must be declared properties.")
        for key, value in props.items():
            if not isinstance(key, str) or len(key) > 80:
                raise ValueError("Invalid property name.")
            schema_check(value, depth + 1)
    if kind == "array":
        if not isinstance(schema.get("maxItems"), int) or schema["maxItems"] > 100:
            raise ValueError("Arrays need maxItems <= 100.")
        schema_check(schema.get("items"), depth + 1)
    if kind == "string" and (
        not isinstance(schema.get("maxLength"), int) or schema["maxLength"] > 4000
    ):
        raise ValueError("Strings need maxLength <= 4000.")
    if len(json.dumps(schema)) > 12000:
        raise ValueError("Schema is too large.")


def value_check(schema, value):
    kind = schema["type"]
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    if kind == "number":
        valid = finite_number(value)
    else:
        valid = type(value) is types[kind]
    if not valid or (type(value) is float and not math.isfinite(value)):
        raise ValueError("Value does not match declared JSON type.")
    if "enum" in schema and not any(
        type(v) is type(value) and v == value for v in schema["enum"]
    ):
        raise ValueError("Value is outside enum.")
    if kind == "object":
        if (
            set(value) - schema["properties"].keys()
            or set(schema.get("required", [])) - value.keys()
        ):
            raise ValueError("Missing or undeclared object fields.")
        for key, item in value.items():
            value_check(schema["properties"][key], item)
    if kind == "array":
        for item in value:
            value_check(schema["items"], item)
    for lower, upper, actual in (
        ("minimum", "maximum", value if kind in {"number", "integer"} else None),
        ("minLength", "maxLength", len(value) if kind == "string" else None),
        ("minItems", "maxItems", len(value) if kind == "array" else None),
    ):
        if actual is not None and (
            (lower in schema and actual < schema[lower])
            or (upper in schema and actual > schema[upper])
        ):
            raise ValueError("Value is outside schema bounds.")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Case(Strict):
    label: str = Field(min_length=1, max_length=80)
    category: Literal["valid", "boundary", "invalid", "malformed", "security"]
    input: object
    expected: object = None
    reject: bool = False

    @field_validator("input", "expected")
    @classmethod
    def finite_json(cls, value):
        if len(json.dumps(value, allow_nan=False)) > 16000:
            raise ValueError("Test input/output must be bounded finite JSON.")
        return value


class Manifest(Strict):
    name: str = Field(pattern=r"^candidate_[a-z][a-z0-9_]{0,45}$")
    description: str = Field(min_length=1, max_length=400)
    demo_contract: (
        Literal[
            "median-numbers-v1",
            "business-revenue-v1",
            "business-profit-v1",
            "business-profit-margin-v1",
            "business-break-even-v1",
            "business-roi-v1",
            "business-selling-price-v1",
        ]
        | None
    ) = None
    test_provenance: Literal[
        "model_suggested", "backend_independent_demo", "model_generated_independent"
    ] = "model_suggested"
    behavior: str | None = Field(default=None, max_length=1500)
    input_schema: dict
    output_schema: dict
    dependencies: list[str] = Field(default_factory=list, max_length=8)
    permissions: list[Literal["pure_json"]] = Field(
        default_factory=lambda: ["pure_json"], min_length=1, max_length=1
    )
    replaces: str | None = Field(default=None, max_length=60)
    generated_tests: list[Case] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def contract(self):
        schema_check(self.input_schema)
        schema_check(self.output_schema)
        if (
            self.input_schema["type"] != "object"
            or self.output_schema["type"] != "object"
        ):
            raise ValueError("Tool input and output must be objects.")
        if set(self.dependencies) - DEPENDENCIES:
            raise ValueError(
                "Only the documented standard-library dependencies are allowed."
            )
        if self.replaces in BUILTINS:
            raise ValueError(
                "Built-in replacement is blocked: no approved comparable extension contract exists. Propose a separate tool."
            )
        if self.replaces and self.replaces != self.name:
            raise ValueError(
                "Replacement versions must use the same generated-tool name."
            )
        return self


class Artifact(Strict):
    manifest: Manifest
    source: str = Field(min_length=1, max_length=24000)


class GenerateInput(Strict):
    gap_id: UUID


class DraftToolInput(Strict):
    description: str = Field(min_length=10, max_length=800)
    confirm: Literal[True]


class RevisionInput(Artifact):
    base_version_id: UUID


class Binding(Strict):
    version_id: UUID
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContractInput(Binding):
    cases: list[Case] = Field(min_length=5, max_length=30)
    confirm: Literal[True]


class DecisionInput(Binding):
    action: Literal["approve", "reject", "cancel", "activate", "deactivate", "rollback"]
    confirm: Literal[True]
    reason: str = Field(default="", max_length=500)


class FeedbackInput(Strict):
    rating: Literal["helpful", "unhelpful"]
    # No free-text feedback is copied into general telemetry.


class Snapshot(Strict):
    evaluations: list[dict]
    gaps: list[dict]
    candidates: list[dict]
    active: list[dict]
    audit: list[dict]
    sandbox: dict
    demo: dict = Field(default_factory=dict)
    limitations: list[str]


class CandidateDetail(Strict):
    id: str
    gap_id: str
    created_at: str
    versions: list[dict]


class RunResponse(Strict):
    id: str
    version_id: str
    state: str
    source_hash: str
    content_hash: str
    suite_hash: str
    payload: dict
    created_at: str
