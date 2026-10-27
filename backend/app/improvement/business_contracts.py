"""Finite, independently specified business contracts. No arbitrary contract invention.

Input grounding is deterministic: explicit JSON/named operands or the documented
units/price/variable/fixed-cost scenario. Model extraction cannot grant data access.
"""

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from . import demo_contracts as median
from .models import Case, digest, value_check, finite_number


def object_schema(fields):
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }


def number(minimum=0, maximum=1e9):
    return {"type": "number", "minimum": minimum, "maximum": maximum}


@dataclass(frozen=True)
class Capability:
    name: str
    contract: str
    description: str
    inputs: dict
    outputs: dict
    formula: str
    samples: tuple
    builtin: str | None = None

    def validate(self, arguments):
        value_check(self.inputs, arguments)
        if (
            self.name == "business_break_even"
            and arguments["unit_price"] <= arguments["variable_cost"]
        ):
            raise ValueError(
                "unit_price must exceed variable_cost; otherwise no finite break-even exists."
            )
        if self.name == "business_break_even" and any(
            Decimal(str(v)) * 100 != (Decimal(str(v)) * 100).to_integral_value()
            for v in arguments.values()
        ):
            raise ValueError(
                "Break-even monetary inputs must have at most two decimal places (currency cents)."
            )

    def oracle(self, arguments):
        self.validate(arguments)
        if self.name == "median_numbers":
            return median.oracle(arguments)
        a = {k: Decimal(str(v)) for k, v in arguments.items()}
        if self.name == "business_revenue":
            result = {"revenue": a["units"] * a["unit_price"]}
        elif self.name == "business_profit":
            result = {"profit": a["revenue"] - a["total_cost"]}
        elif self.name == "business_profit_margin":
            result = {
                "profit_margin_percent": (a["revenue"] - a["total_cost"])
                / a["revenue"]
                * 100
            }
        elif self.name == "business_roi":
            result = {"roi_percent": a["net_profit"] / a["investment"] * 100}
        elif self.name == "business_selling_price":
            result = {
                "selling_price": a["unit_cost"] / (1 - a["target_margin_percent"] / 100)
            }
        else:
            contribution = a["unit_price"] - a["variable_cost"]
            units = int(
                (a["fixed_cost"] / contribution).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            return {
                "break_even_units": units,
                "break_even_revenue": float(units * a["unit_price"]),
                "contribution_per_unit": float(contribution),
            }
        return {key: float(value) for key, value in result.items()}

    def matches(self, actual, expected):
        if self.name == "median_numbers":
            return digest(actual) == digest(expected)
        try:
            value_check(self.outputs, actual)
            return all(
                type(actual[k]) is not bool
                and math.isclose(actual[k], v, rel_tol=1e-10, abs_tol=1e-8)
                for k, v in expected.items()
            )
        except (ValueError, TypeError, KeyError):
            return False

    def arguments_match(self, actual, expected):
        try:
            self.validate(actual)
            self.validate(expected)
            # JSON 600 and 600.0 are equivalent. Integer-only fields still
            # reject floats because validation precedes comparison.
            return actual == expected
        except (ValueError, TypeError, KeyError):
            return False

    def cases(self):
        if self.name == "median_numbers":
            return median.cases()
        accepted = [
            Case(
                label=f"independent {index}",
                category="valid" if index < 3 else "boundary",
                input=a,
                expected=self.oracle(a),
            )
            for index, a in enumerate(self.samples)
        ]
        base = dict(self.samples[0])
        key = next(iter(base))
        invalid = [
            ("missing operand", "malformed", {}),
            ("non object", "malformed", None),
            ("numeric string", "invalid", base | {key: "10"}),
            ("boolean", "invalid", base | {key: True}),
            ("out of range", "invalid", base | {key: 1e15}),
            ("injected command", "security", base | {"command": "whoami"}),
            ("injected path", "security", base | {"path": "../../.env"}),
        ]
        if self.name == "business_break_even":
            invalid += [
                (
                    "no contribution",
                    "invalid",
                    base | {"unit_price": base["variable_cost"]},
                ),
                ("negative contribution", "invalid", base | {"unit_price": 0}),
                ("fractional currency cent", "invalid", base | {"unit_price": 600.001}),
            ]
        elif self.name == "business_roi":
            invalid.append(("zero investment", "invalid", base | {"investment": 0}))
        elif self.name == "business_profit_margin":
            invalid.append(("zero revenue", "invalid", base | {"revenue": 0}))
        elif self.name == "business_selling_price":
            invalid.append(
                ("100 percent margin", "invalid", base | {"target_margin_percent": 100})
            )
        else:
            invalid.append(("negative operand", "invalid", base | {key: -1}))
        return accepted + [
            Case(label=label, category=category, input=a, reject=True)
            for label, category, a in invalid
        ]

    def builtin_arguments(self, arguments):
        if self.builtin == "multiply":
            return {
                "operation": "multiply",
                "a": arguments["units"],
                "b": arguments["unit_price"],
            }
        if self.builtin:
            return {
                "operation": self.builtin,
                "a": arguments["revenue"],
                "b": arguments["total_cost"],
            }
        return None

    def from_builtin(self, result):
        return {next(iter(self.outputs["properties"])): result["value"]}


CAPABILITIES = [
    Capability(
        "median_numbers",
        median.CONTRACT,
        median.DESCRIPTION,
        median.INPUT,
        median.OUTPUT,
        "Sort arguments['numbers']; odd: float(middle); even: average two middle values. Return median as FLOAT.",
        (),
    ),
    Capability(
        "business_revenue",
        "business-revenue-v1",
        "Revenue = units sold times unit_price.",
        object_schema(
            {
                "units": {"type": "integer", "minimum": 0, "maximum": 1000000},
                "unit_price": number(),
            }
        ),
        object_schema({"revenue": number(0, 1e15)}),
        "Return {'revenue': float(arguments['units'] * arguments['unit_price'])}. Do not round.",
        (
            {"units": 200, "unit_price": 600},
            {"units": 3, "unit_price": 19.5},
            {"units": 7, "unit_price": 2},
            {"units": 0, "unit_price": 600},
            {"units": 1000000, "unit_price": 1e9},
        ),
        "multiply",
    ),
    Capability(
        "business_profit",
        "business-profit-v1",
        "Profit = revenue minus total_cost, including losses.",
        object_schema({"revenue": number(), "total_cost": number()}),
        object_schema({"profit": number(-1e9)}),
        "Return profit = float(revenue - total_cost). Read both keys from arguments. Do not round.",
        (
            {"revenue": 120000, "total_cost": 105000},
            {"revenue": 100, "total_cost": 130},
            {"revenue": 19.5, "total_cost": 4.25},
            {"revenue": 0, "total_cost": 0},
            {"revenue": 1e9, "total_cost": 0},
        ),
        "profit",
    ),
    Capability(
        "business_profit_margin",
        "business-profit-margin-v1",
        "Net profit margin in percent, not markup.",
        object_schema({"revenue": number(0.01), "total_cost": number()}),
        object_schema({"profit_margin_percent": number(-1e13, 100)}),
        "profit_margin_percent = float((revenue - total_cost) / revenue * 100). Do not round.",
        (
            {"revenue": 120000, "total_cost": 105000},
            {"revenue": 100, "total_cost": 130},
            {"revenue": 3, "total_cost": 1},
            {"revenue": 0.01, "total_cost": 0},
            {"revenue": 1e9, "total_cost": 1e9},
        ),
        "profit_margin",
    ),
    Capability(
        "business_break_even",
        "business-break-even-v1",
        "Whole-unit break-even and revenue; requires positive contribution.",
        object_schema(
            {"fixed_cost": number(), "unit_price": number(), "variable_cost": number()}
        ),
        object_schema(
            {
                "break_even_units": {"type": "integer", "minimum": 0},
                "break_even_revenue": number(0, 1e20),
                "contribution_per_unit": number(0, 1e9),
            }
        ),
        "Currency inputs have at most two decimals. Compute integer cents first: p = int(round(arguments['unit_price'] * 100)); "
        "v = int(round(arguments['variable_cost'] * 100)); f = int(round(arguments['fixed_cost'] * 100)); c = p - v. "
        "units = (f + c - 1) // c. Return {'break_even_units': units, 'break_even_revenue': float(units * p / 100), "
        "'contribution_per_unit': float(c / 100)}. No imports. Integer cents prevent floating-point ceil mistakes.",
        (
            {"fixed_cost": 45000, "unit_price": 600, "variable_cost": 300},
            {"fixed_cost": 1000, "unit_price": 90, "variable_cost": 30},
            {"fixed_cost": 20, "unit_price": 3.5, "variable_cost": 1.5},
            {"fixed_cost": 0, "unit_price": 100, "variable_cost": 10},
            {"fixed_cost": 1e9, "unit_price": 1000000, "variable_cost": 999999},
            {"fixed_cost": 1, "unit_price": 0.3, "variable_cost": 0.2},
        ),
    ),
    Capability(
        "business_roi",
        "business-roi-v1",
        "ROI percent from NET profit and positive investment; not gross return.",
        object_schema({"net_profit": number(-1e9), "investment": number(0.01)}),
        object_schema({"roi_percent": number(-1e13, 1e13)}),
        "Return {'roi_percent': float(net_profit / investment * 100)} using arguments keys. Do not round.",
        (
            {"net_profit": 300, "investment": 1000},
            {"net_profit": -200, "investment": 1000},
            {"net_profit": 1, "investment": 3},
            {"net_profit": 0, "investment": 0.01},
            {"net_profit": 1e9, "investment": 0.01},
        ),
    ),
    Capability(
        "business_selling_price",
        "business-selling-price-v1",
        "Selling price from unit_cost and target profit margin percent; excludes tax.",
        object_schema(
            {"unit_cost": number(), "target_margin_percent": number(0, 99.99)}
        ),
        object_schema({"selling_price": number(0, 1e14)}),
        "Return {'selling_price': float(unit_cost / (1 - target_margin_percent / 100))}. "
        "Margin is a percent of selling price, NOT markup on cost. Do not round.",
        (
            {"unit_cost": 80, "target_margin_percent": 20},
            {"unit_cost": 100, "target_margin_percent": 30},
            {"unit_cost": 19.5, "target_margin_percent": 25},
            {"unit_cost": 0, "target_margin_percent": 0},
            {"unit_cost": 100, "target_margin_percent": 99.99},
        ),
    ),
]
BY_NAME = {c.name: c for c in CAPABILITIES}
BY_CONTRACT = {c.contract: c for c in CAPABILITIES}


def check_manifest(manifest):
    capability = BY_CONTRACT.get(manifest.get("demo_contract"))
    if (
        not capability
        or digest(manifest["input_schema"]) != digest(capability.inputs)
        or digest(manifest["output_schema"]) != digest(capability.outputs)
        or manifest.get("dependencies") != []
        or manifest.get("permissions") != ["pure_json"]
    ):
        raise ValueError(
            "Demo artifact does not match an independently defined capability contract."
        )
    return capability


def validate_manifest_input(manifest, arguments):
    value_check(manifest["input_schema"], arguments)
    if manifest.get("demo_contract"):
        check_manifest(manifest).validate(arguments)


PATTERNS = {
    "business_revenue": r"\brevenue\b",
    "business_profit": r"\bprofit\b(?!\s+margin)",
    "business_profit_margin": r"\b(?:profit\s+)?margin\b",
    "business_break_even": r"\bbreak[ -]?even\b",
    "business_roi": r"\b(?:roi|return on investment)\b",
    "business_selling_price": r"\bselling[ _-]?price\b",
}

ALIASES = {
    "fixed_cost": r"fixed[ _-]?costs?",
    "unit_price": r"(?:unit[ _-]?price|selling price)",
    "variable_cost": r"variable[ _-]?cost(?:\s+per\s+unit)?",
    "units": r"units",
    "revenue": r"revenue",
    "total_cost": r"(?:total[ _-]?costs?|(?<!variable )(?<!unit )(?<!fixed )costs?)",
    "net_profit": r"net[ _-]?profit",
    "investment": r"investment",
    "unit_cost": r"unit[ _-]?cost",
    "target_margin_percent": r"target[ _-]?margin(?:[ _-]?percent)?",
}
NUMERIC = r"([-+]?\d[\d,]*(?:\.\d+)?)"


def named_value_pattern(label):
    return (
        r"\b" + label + r"\s*(?:(?:is|are|of|=|:)\s*)?(?:₹|Rs\.?|INR|\$)?\s*" + NUMERIC
    )


def task_intent(goal):
    intent = re.sub(r"\{[^{}]*\}|\[[^\[\]]*\]", "", goal.lower())
    for label in ALIASES.values():
        intent = re.sub(named_value_pattern(label), " ", intent, flags=re.I)
    # Both are outputs of break-even, not separate gross-revenue requests.
    return re.sub(r"\bbreak[ -]?even\s+(?:units?|revenue)\b", "break-even", intent)


def goal_requirements(goal):
    """Return grounded requests. Never guess missing numbers or consume model operands."""
    grounded = median.goal_arguments(goal)
    if grounded:
        return [
            {
                "capability": BY_NAME[median.CAPABILITY],
                "arguments": grounded,
                "label": "median",
            }
        ]
    operands = {}
    for match in re.finditer(r"\{[^{}]*\}", goal):
        try:
            data = json.loads(match[0])
            for key, value in data.items():
                if key in operands and operands[key] != value:
                    return []  # Ambiguous input is never silently overwritten.
                operands[key] = value
        except (ValueError, TypeError):
            continue
    aliases, numeric = ALIASES, NUMERIC
    if set(operands) - aliases.keys():
        return []
    for key, label in aliases.items():
        matches = re.findall(
            named_value_pattern(label),
            goal,
            re.I,
        )
        values = {float(s.replace(",", "")) for s in matches}
        if len(values) > 1 or (
            values and key in operands and operands[key] not in values
        ):
            return []
        if values:
            value = values.pop()
            operands[key] = (
                int(value) if key == "units" and value.is_integer() else value
            )
    # Documented plain-language unit-economics scenario.
    sold = re.search(r"\bsells?\s+" + numeric + r"\s+units\b", goal, re.I)
    price = re.search(
        r"\bat\s*(?:₹|Rs\.?|INR|\$)?\s*" + numeric + r"\s+(?:each|per unit)\b",
        goal,
        re.I,
    )
    if sold and "units" not in operands:
        value = float(sold[1].replace(",", ""))
        operands["units"] = int(value) if value.is_integer() else value
    if price and "unit_price" not in operands:
        operands["unit_price"] = float(price[1].replace(",", ""))
    requested = [
        name for name, pattern in PATTERNS.items() if re.search(pattern, goal, re.I)
    ]
    # A key in the input object is not a request to calculate that key.
    intent = task_intent(goal)
    requested = [name for name in requested if re.search(PATTERNS[name], intent, re.I)]
    if "business_selling_price" in requested:
        requested = [n for n in requested if n != "business_profit_margin"]
    if "business_roi" in requested:
        requested = [n for n in requested if n != "business_profit"]
    scenario = all(
        k in operands for k in ("units", "unit_price", "variable_cost", "fixed_cost")
    )
    if scenario and all(
        finite_number(operands[k])
        for k in ("units", "unit_price", "variable_cost", "fixed_cost")
    ):
        operands["revenue"] = operands["units"] * operands["unit_price"]
        operands["total_cost"] = (
            operands["units"] * operands["variable_cost"] + operands["fixed_cost"]
        )
    requirements = []
    for name in requested:
        capability = BY_NAME[name]
        keys = capability.inputs["required"]
        if all(key in operands for key in keys):
            requirements.append(
                {
                    "capability": capability,
                    "arguments": {k: operands[k] for k in keys},
                    "label": name,
                }
            )
    if scenario and all(
        finite_number(operands[k])
        for k in ("units", "unit_price", "variable_cost", "fixed_cost")
    ):
        increase = re.search(r"sales increase by\s+" + numeric + r"\s*%", goal, re.I)
        if increase and "business_profit" in requested:
            units = operands["units"] * (1 + float(increase[1].replace(",", "")) / 100)
            requirements.append(
                {
                    "capability": BY_NAME["business_profit"],
                    "label": "projected_profit",
                    "arguments": {
                        "revenue": units * operands["unit_price"],
                        "total_cost": units * operands["variable_cost"]
                        + operands["fixed_cost"],
                    },
                }
            )
    return requirements


def render_results(results):
    if len(results) == 1 and results[0]["capability"].name == "median_numbers":
        return f"The median is {results[0]['result']['median']:g}."
    lines = []
    for item in results:
        for key, value in item["result"].items():
            label = "projected_profit" if item["label"] == "projected_profit" else key
            lines.append(f"{label}: {value:.10g}")
        if item["capability"].name == "business_profit":
            lines.append(
                f"{'projected_total_cost' if item['label'] == 'projected_profit' else 'total_cost'}: {item['arguments']['total_cost']:.10g}"
            )
    return "Verified tool results:\n" + "\n".join(lines)
