from datalab.sessions.tracing import numbers_in_answer, trace


def test_finds_the_findings_not_the_furniture():
    answer = (
        "In the 2025 cohort, 1,226 interns had 20,592 participant-days.\n"
        "1. Mean sleep was 7.2 hours (95% CI 7.05 to 7.35).\n"
        "2. See `SELECT 1234 FROM x` and [the report](/work/outputs/r2.html).\n"
        "Two tables were used; data from 2025-04-01 to 06/30/2026.\n"
        "```r\nx <- 99999\n```\n"
    )
    assert numbers_in_answer(answer) == ["1,226", "20,592", "7.2", "7.05", "7.35"]


def test_numbers_trace_to_evidence_allowing_rounding_and_percentages():
    evidence = [
        '{"row_count": 20592, "preview": [["mean", 7.2148]]}',
        "prop_missing 0.1234\nci_low 7.051 ci_high 7.349",
    ]
    claims = {c.text: c.traced for c in trace(
        "20,592 days; mean 7.2 h (7.05 to 7.35); 12.3% missing; 81 participants.", evidence
    )}  # fmt: skip
    assert claims == {
        "20,592": True,
        "7.2": True,
        "7.05": True,
        "7.35": True,
        "12.3%": True,  # from the proportion 0.1234
        "81": False,  # appears nowhere: flagged
    }


def test_a_different_rounding_is_not_a_match():
    assert not trace("mean 7.3", ["mean 7.2148"])[0].traced


def test_an_absurd_number_is_left_out_not_fatal():
    claims = trace("It was 1e2000000 and 43.7% of 1,234 people.", ["43.7", "1234"])
    assert [c.text for c in claims if c.traced] == ["43.7%", "1,234"]


def test_scientific_notation_keeps_its_precision():
    assert not trace("The p-value was 3.2e-4.", ["0.01"])[0].traced
    assert trace("The p-value was 3.2e-4.", ["0.000321"])[0].traced
