"""Read an isolated inference-test DB and export unexecuted audit evidence.

Never opens the user's database for writes, imports candidate code, saves a reviewed
contract, approves a candidate or calls the sandbox. The prepared suite is a DRAFT
for independent human review, not a reviewed/approved application contract.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.improvement.models import Artifact, Case, digest, value_check  # noqa: E402
from app.improvement.validation import static_checks  # noqa: E402


def export(database_path, output):
    with sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT v.*,s.status FROM candidate_versions v JOIN candidate_states s ON s.version_id=v.id WHERE v.manifest IS NOT NULL ORDER BY v.created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "No generated artifact: inspect retained generation failure."
            )
        version = dict(row)
        version["manifest"] = json.loads(version["manifest"])
        artifact = Artifact(manifest=version["manifest"], source=version["source"])
        assert digest(artifact.source) == version["source_hash"]
        assert (
            digest({"manifest": version["manifest"], "source": artifact.source})
            == version["content_hash"]
        )
        gap = json.loads(db.execute("SELECT payload FROM gaps LIMIT 1").fetchone()[0])
    cases = [
        {
            "label": "exact positive ratio",
            "category": "valid",
            "input": {"numerator": 10, "denominator": 2},
            "expected": {"quotient": 5.0, "defined": True},
        },
        {
            "label": "fractional ratio",
            "category": "valid",
            "input": {"numerator": 3, "denominator": 2},
            "expected": {"quotient": 1.5, "defined": True},
        },
        {
            "label": "negative ratio",
            "category": "valid",
            "input": {"numerator": -9, "denominator": 3},
            "expected": {"quotient": -3.0, "defined": True},
        },
        {
            "label": "zero numerator",
            "category": "boundary",
            "input": {"numerator": 0, "denominator": 5},
            "expected": {"quotient": 0.0, "defined": True},
        },
        {
            "label": "undefined nonzero ratio",
            "category": "boundary",
            "input": {"numerator": 7, "denominator": 0},
            "expected": {"quotient": 0, "defined": False},
        },
        {
            "label": "undefined zero ratio",
            "category": "boundary",
            "input": {"numerator": 0, "denominator": 0},
            "expected": {"quotient": 0, "defined": False},
        },
        {
            "label": "boolean is not numeric",
            "category": "invalid",
            "input": {"numerator": True, "denominator": 2},
            "reject": True,
        },
        {
            "label": "numeric string rejected",
            "category": "invalid",
            "input": {"numerator": "10", "denominator": 2},
            "reject": True,
        },
        {
            "label": "missing denominator",
            "category": "invalid",
            "input": {"numerator": 10},
            "reject": True,
        },
        {
            "label": "array is not arguments",
            "category": "malformed",
            "input": [10, 2],
            "reject": True,
        },
        {
            "label": "path and command injection fields",
            "category": "security",
            "input": {
                "numerator": 10,
                "denominator": 2,
                "path": "../../.env",
                "command": "whoami",
            },
            "reject": True,
        },
    ]
    normalized = [Case.model_validate(c).model_dump(mode="json") for c in cases]
    boundary = []
    for case in normalized:
        try:
            value_check(artifact.manifest.input_schema, case["input"])
            accepts = True
        except ValueError:
            accepts = False
        assert accepts != case["reject"], case["label"]
        if not case["reject"]:
            value_check(artifact.manifest.output_schema, case["expected"])
        boundary.append(
            {
                "label": case["label"],
                "host_schema_accepts": accepts,
                "candidate_executed": False,
            }
        )
    evidence = {
        "scope": "REAL MODEL-API GENERATION; NO CANDIDATE EXECUTION",
        "model": "qwen2.5:3b",
        "version": version,
        "gap": gap,
        "source_hash_reproduced": True,
        "artifact_hash_reproduced": True,
        "manifest_hash": digest(version["manifest"]),
        "static_findings": static_checks(artifact),
        "suggested_case_categories": [
            c.category for c in artifact.manifest.generated_tests
        ],
        "draft_suite": {
            "human_reviewed": False,
            "status": "BLOCKED: missing verified OS sandbox and independent human review",
            "problem": "Represent undefined ratios explicitly without claiming zero is their mathematical quotient.",
            "baseline": "Separate tool, no generated baseline or built-in replacement. Existing calculate divide deliberately errors on zero; it remains unchanged.",
            "acceptance": "Every case must pass exact JSON/schema comparison in a verified sandbox. Passing these examples alone does not prove correctness over all finite numbers.",
            "suite_hash": digest(normalized),
            "cases": normalized,
            "host_schema_checks_only": boundary,
            "isolated_results": None,
            "measurable_improvement": None,
        },
    }
    output.write_text(json.dumps(evidence, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                k: evidence[k]
                for k in (
                    "model",
                    "source_hash_reproduced",
                    "artifact_hash_reproduced",
                    "manifest_hash",
                    "static_findings",
                    "suggested_case_categories",
                )
            }
        )
    )
    print("Source (DATA ONLY):\n" + artifact.source)
    print(
        "Source SHA256:",
        version["source_hash"],
        "artifact SHA256:",
        version["content_hash"],
    )
    print(
        "DRAFT suite SHA256:",
        digest(normalized),
        "cases:",
        len(cases),
        "UNREVIEWED, UNEXECUTED",
    )
    print("Evidence saved:", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.database, args.output)
