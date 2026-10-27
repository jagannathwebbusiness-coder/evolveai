import csv
import io
import math

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

from ..config import Settings
from .errors import ToolError


class CSVArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: UUID = Field(
        description="The exact server-provided ID of the CSV attached to this task; never a path."
    )
    sheet: str = Field(
        default="",
        max_length=100,
        description="Excel workbooks only: exact sheet name. Empty means the first non-empty sheet.",
    )
    columns: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Optional exact column names to summarize. Empty means all columns.",
    )


def parse_csv(data: bytes, settings: Settings) -> pd.DataFrame:
    if not data or len(data) > settings.upload_max_bytes:
        raise ToolError(
            "invalid_csv_size", "The CSV is empty or exceeds UPLOAD_MAX_BYTES."
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ToolError(
            "invalid_csv_encoding", "Save the CSV as UTF-8 and upload it again."
        ) from exc
    if "\x00" in text or any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        raise ToolError(
            "invalid_csv_content",
            "Binary data and control characters are not accepted as CSV.",
        )
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader, [])
        header = [name.strip() for name in header]
        if not header or any(not name or len(name) > 100 for name in header):
            raise ToolError(
                "invalid_csv_header",
                "CSV must have nonempty column names of at most 100 characters.",
            )
        if len(header) > settings.csv_max_columns or len(set(header)) != len(header):
            raise ToolError(
                "invalid_csv_header",
                "CSV columns must be unique and within CSV_MAX_COLUMNS.",
            )
        rows = 0
        for row in reader:
            if not row:  # Match pandas' treatment of blank lines.
                continue
            if len(row) != len(header) or any(len(cell) > 4096 for cell in row):
                raise ToolError(
                    "malformed_csv",
                    "Every CSV row must match the header width; cells are limited to 4096 characters.",
                )
            rows += 1
            if rows > settings.csv_max_rows:
                raise ToolError(
                    "csv_row_limit", "CSV exceeds CSV_MAX_ROWS; upload a smaller file."
                )
        if not rows:
            raise ToolError(
                "empty_csv", "CSV must contain a header and at least one data row."
            )
        frame = pd.read_csv(
            io.StringIO(text), header=0, names=header, on_bad_lines="error"
        )
        if len(frame) != rows:
            raise ToolError(
                "malformed_csv", "CSV rows could not be parsed consistently."
            )
        return frame
    except (csv.Error, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ToolError(
            "malformed_csv",
            "CSV could not be parsed. Check quoting and comma-separated row widths.",
        ) from exc


def analyze_frame(frame: pd.DataFrame, columns: list[str]) -> dict:
    if len(set(columns)) != len(columns) or any(
        column not in frame.columns for column in columns
    ):
        raise ToolError(
            "invalid_columns",
            "Select unique column names that exist in the uploaded CSV.",
        )
    selected = frame[columns] if columns else frame
    summaries = {}
    for column in selected.select_dtypes(include="number").columns:
        series = selected[column]
        # Nonfinite data is identified explicitly; it must not leak NaN/Infinity into JSON.
        finite = series.map(
            lambda value: pd.notna(value) and math.isfinite(float(value))
        )
        statistics = series[finite].describe()
        summaries[column] = {
            **{
                str(key): float(value) if math.isfinite(float(value)) else None
                for key, value in statistics.items()
            },
            "nonfinite_count": int(series.notna().sum() - finite.sum()),
            "sum": (
                float(series[finite].sum())
                if math.isfinite(float(series[finite].sum()))
                else None
            ),
        }
    return {
        "row_count": int(len(frame)),
        "columns": list(frame.columns),
        "analyzed_columns": list(selected.columns),
        "missing_values": {
            column: int(count) for column, count in selected.isna().sum().items()
        },
        "numeric_summary": summaries,
        "duplicate_row_count": int(frame.duplicated().sum()),
        "notes": "Descriptive statistics only. Empty/default pandas NA values count as missing; numeric summaries exclude nonfinite values. No causal conclusions.",
    }
