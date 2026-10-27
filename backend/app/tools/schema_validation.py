"""Small validator for the registered schema subset, with field-level repair guidance.

Pydantic remains the authoritative built-in interface validator. Generated schema
validation retains the existing restricted JSON-schema contract. No new dependency.
"""

import math


def schema_errors(schema, value, root=None, path="$", depth=0):
    if depth > 12:
        return [{"path": path, "message": "Schema nesting limit exceeded."}]
    root = root or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/$defs/"):
            return schema_errors(root["$defs"][ref[8:]], value, root, path, depth + 1)
    if "anyOf" in schema:
        if any(
            not schema_errors(branch, value, root, path, depth + 1)
            for branch in schema["anyOf"]
        ):
            return []
        return [
            {"path": path, "message": "Must match one of the declared alternatives."}
        ]
    kind = schema.get("type")
    valid = {
        "object": type(value) is dict,
        "array": type(value) is list,
        "string": type(value) is str,
        "number": type(value) in (float, int),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "null": value is None,
    }.get(kind, True)
    if not valid or (type(value) is float and not math.isfinite(value)):
        return [
            {
                "path": path,
                "message": f"Expected finite JSON {kind}; booleans/strings are not numbers.",
            }
        ]
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"path": path, "message": "Choose one of " + str(schema["enum"])})
    if kind == "object":
        fields = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(
                    {"path": f"{path}.{key}", "message": "Required field is missing."}
                )
        for key, item in value.items():
            if key in fields:
                errors.extend(
                    schema_errors(fields[key], item, root, f"{path}.{key}", depth + 1)
                )
            elif schema.get("additionalProperties") is False:
                errors.append(
                    {"path": f"{path}.{key}", "message": "Undeclared field; remove it."}
                )
    if kind == "array":
        for index, item in enumerate(value):
            errors.extend(
                schema_errors(
                    schema["items"], item, root, f"{path}[{index}]", depth + 1
                )
            )
    for low, high, actual in (
        ("minimum", "maximum", value if kind in ("number", "integer") else None),
        ("minLength", "maxLength", len(value) if kind == "string" else None),
        ("minItems", "maxItems", len(value) if kind == "array" else None),
    ):
        if actual is not None and (
            (low in schema and actual < schema[low])
            or (high in schema and actual > schema[high])
        ):
            errors.append(
                {
                    "path": path,
                    "message": f"Must satisfy {low}={schema.get(low)} and {high}={schema.get(high)}.",
                }
            )
    return errors[:12]
