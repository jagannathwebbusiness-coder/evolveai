"""Independent educational contract/oracle, defined before model generation.

No model response can change this suite, schema, policy or expected results.
"""

import json
import re
from statistics import median

from .models import Case, digest, value_check

CONTRACT = "median-numbers-v1"
CAPABILITY = "median_numbers"
INPUT = {
    "type": "object",
    "properties": {
        "numbers": {
            "type": "array",
            "items": {"type": "integer", "minimum": -1000000, "maximum": 1000000},
            "minItems": 1,
            "maxItems": 20,
        }
    },
    "required": ["numbers"],
    "additionalProperties": False,
}
OUTPUT = {
    "type": "object",
    "properties": {"median": {"type": "number"}},
    "required": ["median"],
    "additionalProperties": False,
}
DESCRIPTION = (
    "Median of 1..20 integers in [-1000000,1000000]; return {median: float}. "
    "DEMO ONLY: executes Python in an unsecured local subprocess."
)


def oracle(arguments):
    value_check(INPUT, arguments)
    return {"median": float(median(arguments["numbers"]))}


def cases():
    accepted = [
        ("unsorted odd", "valid", [9, 1, 4]),
        ("unsorted even", "valid", [9, 1, 4, 7]),
        ("negative values", "valid", [-5, -1, -9]),
        ("duplicates", "valid", [2, 2, 8, 2]),
        ("singleton", "boundary", [0]),
        ("range endpoints", "boundary", [-1000000, 1000000]),
        ("maximum length", "boundary", list(range(20))),
    ]
    rejected = [
        ("empty list", "invalid", {"numbers": []}),
        ("too many", "invalid", {"numbers": list(range(21))}),
        ("out of range", "invalid", {"numbers": [1000001]}),
        ("boolean", "invalid", {"numbers": [True]}),
        ("numeric string", "invalid", {"numbers": ["3"]}),
        ("floating operand", "invalid", {"numbers": [1.5]}),
        ("missing list", "malformed", {}),
        ("not an object", "malformed", None),
        ("injected command", "security", {"numbers": [1], "command": "whoami"}),
        ("injected path", "security", {"numbers": [1], "path": "../../.env"}),
    ]
    return [
        Case(
            label=label,
            category=category,
            input={"numbers": values},
            expected=oracle({"numbers": values}),
        )
        for label, category, values in accepted
    ] + [
        Case(label=label, category=category, input=value, reject=True)
        for label, category, value in rejected
    ]


def goal_arguments(goal):
    """Ground the demo oracle in the explicit user list, never model-extracted operands."""
    if not re.search(r"\bmedian\b", goal, re.I):
        return None

    match = re.search(r"\[[^\[\]]*\]", goal)
    if not match:
        return None
    try:
        value = {"numbers": json.loads(match[0])}
        value_check(INPUT, value)
        return value
    except (ValueError, TypeError):
        return None


def median_only_goal(goal):
    """Only independently specified single-goal templates terminate by oracle.

    Compound/custom goals continue through normal model tool orchestration.
    """
    compact = re.sub(r"\[[^\[\]]*\]", "[]", goal.strip().lower())
    return bool(
        re.fullmatch(
            r"(?:find|calculate|compute|what is|give me)\s+(?:the\s+)?median\s+(?:of\s+)?\[\]\s*[.?!]?"
            r"(?:\s*use the median capability and verify the result[.!]?)?",
            compact,
        )
    )


def check_manifest(manifest):
    if (
        manifest.get("demo_contract") != CONTRACT
        or digest(manifest["input_schema"]) != digest(INPUT)
        or digest(manifest["output_schema"]) != digest(OUTPUT)
        or manifest.get("dependencies") != []
        or manifest.get("permissions") != ["pure_json"]
    ):
        raise ValueError(
            "Demo artifact does not match the independently defined median contract."
        )
