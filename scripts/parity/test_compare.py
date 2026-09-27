"""Tests for the parity check's comparison and report, on small made-up files.

cd backend && uv run pytest -q ../scripts/parity
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import compare as cmp  # pyright: ignore[reportMissingImports]
from report import write_report  # pyright: ignore[reportMissingImports]

SECRET = "p-7731"  # a stand-in cell value that must never reach a report


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode())
    return path


def run(tmp_path: Path, a: str, b: str, **kw) -> cmp.Comparison:
    return cmp.compare_csv("x.csv", write(tmp_path, "a.csv", a), write(tmp_path, "b.csv", b), **kw)


def test_identical_bytes(tmp_path):
    result = run(tmp_path, "id,n\n1,2\n", "id,n\n1,2\n")
    assert result.verdict == "identical" and result.detail["rows"] == {"prototype": 1, "v1": 1}


def test_only_quoting_differs(tmp_path):
    assert run(tmp_path, 'id,n\n"1","2"\n', "id,n\n1,2\n").verdict == "same_rows"
    assert run(tmp_path, "id,n\r\n1,2\r\n", "id,n\n1,2\n").verdict == "same_rows"


def test_reordering_counts_only_where_order_isnt_defined(tmp_path):
    a, b = "id,n\n1,2\n3,4\n", "id,n\n3,4\n1,2\n"
    assert run(tmp_path, a, b, order_defined=False).verdict == "same_rows_any_order"
    assert run(tmp_path, a, b, order_defined=True).verdict == "differs"


def test_floats_within_tolerance_and_beyond(tmp_path):
    close = run(tmp_path, "id,x\n1,0.1\n2,3\n", "id,x\n1,0.10000000000000002\n2,3\n")
    assert close.verdict == "equal_within_tolerance"
    assert close.detail["columns_differing"]["x"]["cells"] == {"float_tolerance": 1}
    far = run(tmp_path, "id,x\n1,0.1\n2,3\n", "id,x\n1,0.1001\n2,3\n")
    assert far.verdict == "differs"
    assert far.detail["columns_differing"]["x"]["cells"] == {"value": 1}


def test_a_datetime_written_another_way_is_told_apart(tmp_path):
    result = run(
        tmp_path,
        "id,d\n1,2025-05-03T00:00:00\n2,2025-05-04T07:30:00\n",
        "id,d\n1,2025-05-03 00:00:00\n2,2025-05-04 07:30:00\n",
    )
    assert result.verdict == "differs"
    column = result.detail["columns_differing"]["d"]
    assert column["cause"] == "datetime_format_only"
    assert column["prototype"]["shapes"] == {"YYYY-MM-DDTHH:MM:SS": 2}
    assert column["v1"]["shapes"] == {"YYYY-MM-DD HH:MM:SS": 2}
    # A plain date is the same instant as midnight, written differently.
    plain = run(tmp_path, "id,d\n1,2025-05-03\n", "id,d\n1,2025-05-03 00:00:00\n")
    assert plain.detail["columns_differing"]["d"]["cause"] == "datetime_format_only"
    # A different day is a different value.
    day = run(tmp_path, "id,d\n1,2025-05-03\n", "id,d\n1,2025-05-04\n")
    assert "cause" not in day.detail["columns_differing"]["d"]


def test_columns_and_rows_that_dont_match(tmp_path):
    result = run(tmp_path, "id,a,b\n1,2,3\n2,2,3\n", "id,b,c\n1,3,4\n")
    assert result.verdict == "differs"
    assert result.detail["only_in_prototype"] == ["a"] and result.detail["only_in_v1"] == ["c"]
    assert result.detail["rows"] == {"prototype": 2, "v1": 1}


def test_rows_are_aligned_on_the_columns_that_agree(tmp_path):
    # Same keys in another order, one value changed: only that cell differs.
    result = run(tmp_path, "id,x\n1,5\n2,6\n3,7\n", "id,x\n3,7\n1,5\n2,9\n", order_defined=False)
    assert result.detail["alignment"] == {"by": "shared columns", "unique": True, "key_columns": 1}
    assert result.detail["columns_differing"]["x"]["cells"] == {"value": 1}


def test_missing_spellings_count_as_the_same_missing(tmp_path):
    result = run(tmp_path, "id,x\n1,NA\n2,1.5\n", "id,x\n1,\n2,1.5000000000000002\n")
    assert result.verdict == "equal_within_tolerance"


def test_summaries_hold_no_cell_values(tmp_path):
    a = f"id,name,n\n{SECRET},alpha,1\nq-2,beta,2\n"
    b = f"id,name,n\n{SECRET},gamma,1\nq-2,beta,3\n"
    result = run(tmp_path, a, b)
    text = repr(result.to_dict())
    for value in (SECRET, "alpha", "gamma", "beta", "q-2"):
        assert value not in text
    numeric = result.detail["columns_differing"]["n"]["v1"]["numeric"]
    assert numeric == {"count": 2, "sum": 4.0, "mean": 2.0}
    assert set(result.detail["columns_differing"]["n"]["v1"]) == {
        "missing", "distinct", "shapes", "numeric",
    }  # fmt: skip


def test_the_report_holds_no_cell_values(tmp_path):
    result = run(tmp_path, f"id,v\n{SECRET},1\n", f"id,v\n{SECRET},2\n")
    meta = {
        "generated_at": "now",
        "datalab_commit": "a" * 40,
        "pipelines_commit": "b" * 40,
        "prototype_commit": "c" * 40,
        "prototype_image": "img",
    }
    entry = {
        "name": "w",
        "prototype_status": {"status": "succeeded"},
        "v1_status": {"status": "succeeded"},
        "files": [result.to_dict()],
    }
    text = write_report(meta, {"live": [entry], "extracts": [entry]})
    assert SECRET not in text and "differs" in text and "`v`: value 1" in text


def test_shapes():
    assert cmp.shape("") == "missing" and cmp.shape("NA") == "missing"
    assert cmp.shape("12") == "int" and cmp.shape("-1.5e3") == "num"
    assert cmp.shape("2025-05-03") == "YYYY-MM-DD"
    assert cmp.shape("2025-05-03 01:02:03.5") == "YYYY-MM-DD HH:MM:SS.f"
    assert cmp.shape("2025-05-03T01:02Z") == "YYYY-MM-DDTHH:MMZ"
    assert cmp.shape("hello") == "text" and cmp.shape("TRUE") == "bool"


def test_qc_and_other_files(tmp_path):
    same = cmp.compare_json_qc("qc", {"checks": [{"passed": True}]}, {"checks": [{"passed": True}]})
    assert same.verdict == "identical"
    assert cmp.compare_file("f.csv", None, tmp_path).verdict == "missing"
    a, b = write(tmp_path, "a.json", "{}"), write(tmp_path, "b.json", "{ }")
    assert cmp.compare_file("f.json", a, b).verdict == "differs"


def test_rows_align_through_a_date_written_another_way(tmp_path):
    # The key column is written differently on each side; the rows still line
    # up, so only the format shows, not a spurious changed value.
    a = "k,d,x\nz,2025-05-02T00:00:00,1\ny,2025-05-01T00:00:00,2\n"
    b = "k,d,x\nq,2025-05-01 00:00:00,2\nr,2025-05-02 00:00:00,1\n"
    result = run(tmp_path, a, b, order_defined=False)
    columns = result.detail["columns_differing"]
    assert columns["d"]["cause"] == "datetime_format_only"
    assert "x" not in columns and columns["k"]["cells"] == {"value": 2}
