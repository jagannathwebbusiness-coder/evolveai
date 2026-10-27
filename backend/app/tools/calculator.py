import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import ToolError

Number = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1e12, le=1e12)]


class CalculatorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal[
        "add",
        "subtract",
        "multiply",
        "divide",
        "profit",
        "profit_margin",
        "percentage_change",
    ]
    a: Number = Field(
        description="First operand; revenue for profit/margin, old value for percentage_change."
    )
    b: Number = Field(
        description="Second operand; cost for profit/margin, new value for percentage_change."
    )


def calculate(args: CalculatorArgs) -> dict:
    a, b = args.a, args.b
    if args.operation in {"divide", "profit_margin", "percentage_change"}:
        denominator = b if args.operation == "divide" else a
        if denominator == 0:
            raise ToolError(
                "division_by_zero", "The divisor/revenue/old value must not be zero."
            )
    if args.operation in {"profit", "profit_margin"} and (a < 0 or b < 0):
        raise ToolError(
            "invalid_business_input", "Revenue and cost must be nonnegative."
        )
    match args.operation:
        case "add":
            result = a + b
        case "subtract" | "profit":
            result = a - b
        case "multiply":
            result = a * b
        case "divide":
            result = a / b
        case "profit_margin":
            result = (a - b) / a * 100
        case "percentage_change":
            result = (b - a) / abs(a) * 100
    if not math.isfinite(result):
        raise ToolError(
            "numeric_overflow",
            "The calculation exceeds finite numeric limits. Use smaller values.",
        )
    return {
        "operation": args.operation,
        "a": a,
        "b": b,
        "value": result,
        "unit": (
            "percent"
            if args.operation in {"profit_margin", "percentage_change"}
            else "number"
        ),
    }
