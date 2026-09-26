from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.data.helpers import find_concept, join_keys


def table(schema, name, *columns, comment=""):
    return TableInfo(schema, name, "VIEW", comment, [Column(c, t) for c, t in columns])


CATALOG = Catalog(
    [
        table(
            "IHS_2025", "FITBITSLEEP",
            ("PARTICIPANTIDENTIFIER", "VARCHAR2(64)"), ("STARTDATE", "DATE"),
            ("MINUTESASLEEP", "NUMBER"), comment="Fitbit sleep logs",
        ),
        table(
            "IHS_2025", "FITBITDAILYDATA",
            ("PARTICIPANTIDENTIFIER", "VARCHAR2(64)"), ("RECORD_DATE", "DATE"),
            ("STEPS", "NUMBER"),
        ),
        table(
            "IHS_2025", "VW_SEP_SURVEY",
            ("STUDY_PARTICIPANT_ID", "NUMBER"), ("interest1", "NUMBER"),
        ),
        table(
            "IHS_2025", "PARTICIPANTS",
            ("PARTICIPANTIDENTIFIER", "VARCHAR2(64)"), ("STUDY_PARTICIPANT_ID", "NUMBER"),
        ),
        table("IHS_2024", "FITBITSLEEP", ("PARTICIPANTIDENTIFIER", "VARCHAR2(64)")),
        table("IHS_2025", "DAILYMOOD", ("PARTICIPANTIDENTIFIER", "NUMBER"), ("MOOD", "NUMBER")),
    ]
)  # fmt: skip


def test_join_keys_puts_the_participant_first_and_warns_about_date_grain():
    result = join_keys(CATALOG, "IHS_2025.FITBITSLEEP", "IHS_2025.FITBITDAILYDATA")
    assert [k["column"] for k in result["shared_columns"]] == ["PARTICIPANTIDENTIFIER"]
    assert any("no shared date" in n for n in result["notes"])


def test_record_keeping_timestamps_arent_join_dates():
    catalog = Catalog(
        [
            table("S", "A", ("PARTICIPANTID", "N"), ("MODIFIEDDATE", "D"), ("STARTDATE", "D")),
            table("S", "B", ("PARTICIPANTID", "N"), ("MODIFIEDDATE", "D"), ("DATE", "D")),
        ]
    )  # fmt: skip
    result = join_keys(catalog, "S.A", "S.B")
    assert [k["role"] for k in result["shared_columns"]] == ["participant", "audit"]
    assert "don't join on it" in result["shared_columns"][1]["note"]
    assert any("STARTDATE" in n and "in B: DATE" in n for n in result["notes"])


def test_different_identifiers_point_to_a_linking_table():
    result = join_keys(CATALOG, "IHS_2025.FITBITSLEEP", "IHS_2025.VW_SEP_SURVEY")
    assert result["shared_columns"] == []
    assert any("IHS_2025.PARTICIPANTS" in n for n in result["notes"])


def test_cross_cohort_joins_and_type_mismatches_are_flagged():
    across = join_keys(CATALOG, "IHS_2025.FITBITSLEEP", "IHS_2024.FITBITSLEEP")
    assert any("different cohorts" in n for n in across["notes"])
    mismatch = join_keys(CATALOG, "IHS_2025.FITBITSLEEP", "IHS_2025.DAILYMOOD")
    assert "types differ" in mismatch["shared_columns"][0]["note"]
    assert "error" in join_keys(CATALOG, "IHS_2025.NOPE", "IHS_2025.FITBITSLEEP")


def test_concepts_find_tables_by_the_words_the_catalog_uses():
    result = find_concept(CATALOG, "sleep", cohorts=["IHS_2025"])
    tables = [c["table"] for c in result["candidates"]]
    assert tables[0] == "IHS_2025.FITBITSLEEP"
    assert "IHS_2024.FITBITSLEEP" not in tables
    depression = find_concept(CATALOG, "depression")
    assert "IHS_2025.VW_SEP_SURVEY" in [c["table"] for c in depression["candidates"]]


def test_words_inside_names_dont_make_a_date():
    catalog = Catalog(
        [
            table("S", "A", ("PARTICIPANTID", "N"), ("GENDER", "VARCHAR2(8)"), ("TIMEINBED", "N")),
            table("S", "B", ("PARTICIPANTID", "N"), ("GENDER", "VARCHAR2(8)"), ("TIMEINBED", "N")),
        ]
    )  # fmt: skip
    result = join_keys(catalog, "S.A", "S.B")
    assert {k["column"]: k["role"] for k in result["shared_columns"]}["GENDER"] == "other"
    assert any("no shared date" in n for n in result["notes"])
