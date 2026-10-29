"""Trend and anomaly detection for one numeric column of an attached CSV/Excel sheet."""

import math

import numpy as np
import pandas as pd
from pydantic import Field

from .csv_analysis import CSVArgs
from .errors import ToolError


class TrendArgs(CSVArgs):
    column: str = Field(
        min_length=1,
        max_length=100,
        description="Exact name of the numeric column to analyse for trend and anomalies, in row order.",
    )


def analyze_trend(frame: pd.DataFrame, column: str) -> dict:
    if column not in frame.columns:
        raise ToolError("invalid_columns", "Choose a column that exists in the file.")
    series = pd.to_numeric(frame[column], errors="coerce")
    values = series.dropna()
    values = values[np.isfinite(values)]
    if len(values) < 3:
        raise ToolError(
            "not_enough_data", "Trend analysis needs at least 3 numeric values."
        )
    labels = frame.select_dtypes(exclude="number").columns
    label_column = labels[0] if len(labels) else None
    x = np.arange(len(values), dtype=float)
    slope = float(np.polyfit(x, values.to_numpy(dtype=float), 1)[0])
    first, last = float(values.iloc[0]), float(values.iloc[-1])
    mean = float(values.mean())
    change = ((last - first) / abs(first) * 100) if first else None
    drift = abs(slope) * (len(values) - 1) / abs(mean) if mean else abs(slope)
    direction = (
        "flat" if drift < 0.05 else ("increasing" if slope > 0 else "decreasing")
    )
    std = float(values.std(ddof=0))
    anomalies = []
    if len(values) >= 5 and std > 0:
        for index, value in values.items():
            z = (float(value) - mean) / std
            if abs(z) > 2:
                anomalies.append(
                    {
                        "row": int(index) + 1,
                        "label": (
                            str(frame[label_column].iloc[index])
                            if label_column
                            else None
                        ),
                        "value": float(value),
                        "z_score": round(z, 2),
                    }
                )
    return {
        "column": column,
        "points": int(len(values)),
        "first": first,
        "last": last,
        "percent_change": (
            round(change, 2) if change is not None and math.isfinite(change) else None
        ),
        "slope_per_row": round(slope, 4),
        "direction": direction,
        "mean": round(mean, 4),
        "anomalies": anomalies[:5],
        "method": "linear regression slope; anomaly = |z-score| > 2 (needs 5+ points)",
    }
