import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.tools.calculator import CalculatorArgs, calculate
from app.tools.csv_analysis import analyze_frame, parse_csv
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry
from app.uploads import UploadedCSV, UploadStore

SAMPLE = b"item,revenue,cost\nA,100,60\nB,200,120\nC,,90\n"


@pytest.mark.parametrize(
    "operation,a,b,expected",
    [
        ("add", 17, 25, 42),
        ("subtract", 10, 4, 6),
        ("multiply", 7, -3, -21),
        ("divide", 9, 4, 2.25),
        ("profit", 12500, 8750, 3750),
        ("profit_margin", 12500, 8750, 30),
        ("percentage_change", 100, 125, 25),
        ("percentage_change", -100, -75, 25),
        ("profit", 100, 150, -50),
    ],
)
def test_calculator_correctness(operation, a, b, expected):
    result = calculate(CalculatorArgs(operation=operation, a=a, b=b))
    assert result["value"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "operation,a,b,code",
    [
        ("divide", 10, 0, "division_by_zero"),
        ("profit_margin", 0, 3, "division_by_zero"),
        ("percentage_change", 0, 3, "division_by_zero"),
        ("profit", -1, 3, "invalid_business_input"),
        ("divide", 1e12, 1e-310, "numeric_overflow"),
    ],
)
def test_calculator_errors(operation, a, b, code):
    with pytest.raises(ToolError) as error:
        calculate(CalculatorArgs(operation=operation, a=a, b=b))
    assert error.value.code == code


@pytest.mark.parametrize("value", ["3", True, None, float("nan"), float("inf"), 1e13])
def test_calculator_invalid_numbers(value):
    with pytest.raises(ValidationError):
        CalculatorArgs(operation="add", a=value, b=1)


def test_no_expression_or_arbitrary_operation():
    with pytest.raises(ValidationError):
        CalculatorArgs(operation="eval", a=1, b=2, expression="__import__('os')")


def test_known_csv_statistics_and_missing_values():
    result = analyze_frame(parse_csv(SAMPLE, Settings(_env_file=None)), [])
    assert result["row_count"] == 3
    assert result["columns"] == ["item", "revenue", "cost"]
    assert result["missing_values"] == {"item": 0, "revenue": 1, "cost": 0}
    assert result["numeric_summary"]["revenue"]["mean"] == 150
    assert result["numeric_summary"]["revenue"]["sum"] == 300
    assert result["numeric_summary"]["cost"]["mean"] == 90
    assert result["duplicate_row_count"] == 0
    json.dumps(result, allow_nan=False)


def test_csv_column_selection_and_single_value_std():
    frame = parse_csv(b"name,value\nA,1\n", Settings(_env_file=None))
    result = analyze_frame(frame, ["value"])
    assert result["analyzed_columns"] == ["value"]
    assert result["numeric_summary"]["value"]["std"] is None
    with pytest.raises(ToolError, match="column names"):
        analyze_frame(frame, ["missing"])
    with pytest.raises(ToolError):
        analyze_frame(frame, ["value", "value"])


def test_csv_nonfinite_values_are_explicit_and_json_safe():
    result = analyze_frame(
        parse_csv(b"value\n1\ninf\n-inf\n", Settings(_env_file=None)), []
    )
    assert result["numeric_summary"]["value"]["nonfinite_count"] == 2
    assert result["numeric_summary"]["value"]["mean"] == 1
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"a,b\n",
        b"\n",
        b"a,a\n1,2\n",
        b"a,\n1,2\n",
        b"a,b\n1,2,3\n",
        b"a,b\n1\n",
        b'a,b\n"unterminated,2\n',
        b"a,b\n1,\x00\n",
        b"a\n\xff\n",
        b"a\n\x01\n",
        b"a\n" + b"x" * 4097,
    ],
)
def test_invalid_csv_content(data):
    with pytest.raises(ToolError):
        parse_csv(data, Settings(_env_file=None))


def test_csv_limits():
    for settings in [
        Settings(_env_file=None, csv_max_rows=1),
        Settings(_env_file=None, csv_max_columns=1),
        Settings(_env_file=None, upload_max_bytes=128),
    ]:
        with pytest.raises(ToolError):
            parse_csv(
                SAMPLE if settings.upload_max_bytes != 128 else SAMPLE + b" " * 128,
                settings,
            )


def test_csv_bom_quoted_fields_and_blank_lines():
    frame = parse_csv(
        '\ufeffname,value\n"A, B",3\n\nC,5\n'.encode("utf-8"), Settings(_env_file=None)
    )
    assert len(frame) == 2
    assert frame.iloc[0]["name"] == "A, B"


def test_registry_allowlist_and_task_scope(tmp_path):
    settings = Settings(_env_file=None, evolve_workspace_dir=tmp_path)
    store = UploadStore(settings)
    task = uuid4()
    uploaded = store.save(SAMPLE, "sample.csv", task)
    registry = ToolRegistry(settings, store, task, [uploaded.upload_id])
    assert {item["function"]["name"] for item in registry.definitions()} == {
        "calculate",
        "analyze_csv",
        "analyze_trend",
        "search_documents",
    }
    with pytest.raises(ToolError) as error:
        registry.validate("os.system", {"command": "whoami"})
    assert error.value.code == "unknown_tool"
    with pytest.raises(ToolError):
        registry.validate("calculate", {"operation": "add", "a": "1", "b": 2})
    with pytest.raises(ToolError):
        ToolRegistry(settings, store, uuid4(), [uploaded.upload_id]).validate(
            "analyze_csv", {"upload_id": str(uploaded.upload_id)}
        )
    with pytest.raises(ToolError):
        ToolRegistry(settings, store, task, []).validate(
            "analyze_csv", {"upload_id": str(uploaded.upload_id)}
        )
    result = registry.execute(
        "analyze_csv",
        registry.validate("analyze_csv", {"upload_id": str(uploaded.upload_id)}),
    )
    assert result["row_count"] == 3


def test_record_path_tampering_prevented(tmp_path):
    settings = Settings(_env_file=None, evolve_workspace_dir=tmp_path / "workspace")
    store = UploadStore(settings)
    task = uuid4()
    upload = store.save(SAMPLE, "sample.csv", task)
    record = store.records[upload.upload_id]
    outside = tmp_path / "private.csv"
    outside.write_bytes(SAMPLE)
    store.records[upload.upload_id] = UploadedCSV(
        task, upload.upload_id, record.filename, outside, len(SAMPLE)
    )
    with pytest.raises(ToolError) as error:
        store.read(upload.upload_id, task)
    assert error.value.code == "unsafe_upload_path"


@pytest.mark.parametrize(
    "filename", ["../outside.csv", "..\\outside.csv", "C:\\outside.csv"]
)
def test_store_rejects_unparsed_paths(tmp_path, filename):
    with pytest.raises(ToolError) as error:
        UploadStore(Settings(_env_file=None, evolve_workspace_dir=tmp_path)).save(
            SAMPLE, filename, uuid4()
        )
    assert error.value.code == "invalid_filename"


def test_resolved_upload_directory_escape_prevented(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, evolve_workspace_dir=tmp_path / "workspace")
    store = UploadStore(settings)
    original = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda self, *args, **kwargs: (
            tmp_path / "outside"
            if self == store.root / "uploads"
            else original(self, *args, **kwargs)
        ),
    )
    with pytest.raises(ToolError) as error:
        store.save(SAMPLE, "sample.csv", uuid4())
    assert error.value.code == "unsafe_upload_path"
