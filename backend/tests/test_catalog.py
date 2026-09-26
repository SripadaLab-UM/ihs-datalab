import csv
from pathlib import Path

from datalab.data.catalog import Catalog


def test_search_ranks_table_name_matches_first(catalog):
    hits = catalog.search("fitbit steps")
    assert hits[0].table.qualified_name.endswith("VFITBITDAILYDATA")
    assert "TRACKERSTEPS" in hits[0].matching_columns


def test_search_can_be_limited_to_cohorts(catalog):
    hits = catalog.search("fitbit", schemas=["IHS_2024"])
    assert [h.table.qualified_name for h in hits] == ["IHS_2024.VFITBITDAILYDATA"]


def test_search_ignores_empty_queries(catalog):
    assert catalog.search("  ") == []


def test_get_is_case_insensitive_and_tracks_other_cohorts(catalog):
    assert catalog.get("ihs_2025.vw_daily_mood").comment == "Daily mood ratings"
    assert catalog.cohorts_with("VFITBITDAILYDATA") == ["IHS_2024", "IHS_2025"]


def test_save_and_load_round_trip(catalog, tmp_path: Path):
    catalog.save(tmp_path)
    assert (tmp_path / "IHS_2025" / "VFITBITDAILYDATA.yml").exists()
    loaded = Catalog.load(tmp_path)
    assert len(loaded) == len(catalog)
    table = loaded.get("IHS_2025.VFITBITDAILYDATA")
    assert table is not None
    assert [c.name for c in table.columns] == [
        "STUDY_PARTICIPANT_ID",
        "RECORD_DATE",
        "TRACKERSTEPS",
    ]
    assert table.columns[0].nullable is False


def _write_csv(folder: Path, name: str, rows: list[dict[str, str]]) -> None:
    with (folder / f"{name}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _column(column_id: str, name: str, dtype: str, **extra: str) -> dict[str, str]:
    row = {"owner": "IHS_2025", "object_name": "T", "object_type": "TABLE"}
    row |= {"column_id": column_id, "column_name": name, "data_type": dtype}
    row |= {"data_length": "", "data_precision": "", "data_scale": "", "char_length": ""}
    row |= {"nullable": "Y"} | extra
    return row


def test_from_metadata_export(tmp_path: Path):
    # The export script's CSV layout, trimmed to the columns the loader reads.
    _write_csv(
        tmp_path,
        "objects",
        [
            {"owner": "IHS_2025", "object_name": "T", "object_type": "TABLE"},
            {"owner": "IHS_2025", "object_name": "IDX", "object_type": "INDEX"},
        ],
    )
    _write_csv(
        tmp_path,
        "columns",
        [
            _column("2", "NAME", "VARCHAR2", data_length="400", char_length="100"),
            _column("1", "ID", "NUMBER", data_precision="10", data_scale="0", nullable="N"),
        ],
    )
    _write_csv(
        tmp_path,
        "table_comments",
        [{"owner": "IHS_2025", "object_name": "T", "comments": "A table"}],
    )
    _write_csv(
        tmp_path,
        "column_comments",
        [{"owner": "IHS_2025", "object_name": "T", "column_name": "ID", "comments": "Identifier"}],
    )
    _write_csv(
        tmp_path,
        "constraints",
        [
            {
                "owner": "IHS_2025",
                "constraint_name": "T_PK",
                "constraint_type": "P",
                "table_name": "T",
            }
        ],
    )
    _write_csv(
        tmp_path,
        "constraint_columns",
        [{"owner": "IHS_2025", "constraint_name": "T_PK", "column_name": "ID", "position": "1"}],
    )

    catalog = Catalog.from_metadata_export(tmp_path)
    table = catalog.get("IHS_2025.T")
    assert table is not None and len(catalog) == 1
    assert table.comment == "A table"
    assert [(c.name, c.type, c.nullable) for c in table.columns] == [
        ("ID", "NUMBER(10)", False),
        ("NAME", "VARCHAR2(100)", True),
    ]
    assert table.columns[0].comment == "Identifier"
    assert table.primary_key == ["ID"]
