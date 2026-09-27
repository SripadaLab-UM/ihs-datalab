import time

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


def test_a_reference_list_has_no_findings():
    # From a real answer: 14 of 14 of these numbers were flagged, all volumes and pages.
    answer = (
        "- Bent B, et al. *npj Digital Medicine*. 2020;3:18. DOI: `10.1038/s41746-020-0226-6`.\n"
        "- Böttcher S, et al. *Scientific Reports*. 2022;12:21412.\n"
        "- Collins T, et al. *Stud Health Technol Inform*. 2021;281:1077-1078.\n"
        "- Manta C, et al. *Digital Biomarkers*. 2021;5(2):127\u2013147.\n"
        "- Hogan JW, et al. *Statistics in Medicine*. 2004;23:1455-1497.\n"
        "- Smith A. *J Sleep Res*. 2022;31(4):e13520. doi:10.1111/jsr.13520\n"
        "- Jones B. https://doi.org/10.1038/s41598-022-25949-x\n"
        "- Lee C. In: Methods, pp. 1077-1078. PMID: 34042743. arXiv:2101.01234\n"
        "- See also 10.1001/jama.2020.1234.\n"
    )
    assert numbers_in_answer(answer) == []


def test_findings_beside_a_citation_are_still_checked():
    answer = (
        "Among 55 participants, 58.7% had sleep data (Bent 2020;3:55), and enrolment "
        "was 1,226 (2025) versus 1,105 (2024), pp. 18-19 of the report aside."
    )
    assert numbers_in_answer(answer) == ["55", "58.7%", "1,226", "1,105"]


def test_findings_shaped_like_citations_are_kept():
    # From review: each of these once lost a finding to a citation pattern.
    cases = {
        "Median 45(12), 30 and n=12(5), 40 controls.": ["45", "12", "30", "40"],
        "| Age, mean (SD) | 45(12), 30(8) |": ["45", "12", "30"],
        "Mean age 7.2(1), 45 men.": ["7.2", "45"],
        "Enrolment was 312, 280 (2023), 250 (2024).": ["312", "280", "250"],
        "wear-time 12.5, 14 (2022)": ["12.5", "14"],
        "(p 0.012, n=312); p. 0.03; p. 45 of them": ["0.012", "312", "0.03", "45"],
        "In 2024; 31:14 ratio, and 2021; 55:45 split": ["31", "14", "55", "45"],
        "incidence 10.2345/100000 and 10.3456/1,000 person-days": [
            "10.2345", "100000", "10.3456", "1,000",
        ],
        "see 10.1001/jama.2020.1234,45.6% had": ["45.6%"],
        "vol. 250 mL was given; issue. 45 people": ["250", "45"],
        "**Age, mean (SD)**, 45(12), 30": ["45", "12", "30"],
        "- **Total**, 312, 280": ["312", "280"],
        "**Enrolled** 312, 280 (2023)": ["312", "280"],
        "* Counts 312, 280 (2023), 250 (2024)": ["312", "280", "250"],
        "*n*, 312, 280 and _cohort_, 45, 30": ["312", "280", "45", "30"],
        "rate 10.2345/person-years, 10.1234/1000PY": ["10.2345", "10.1234"],
        "rate 10.1234/1000PY.": ["10.1234"],
        "incidence 10.2345/1000-person-years; IR 10.3456/100k-py": [
            "10.2345", "1000", "10.3456",
        ],
    }  # fmt: skip
    for answer, expected in cases.items():
        assert numbers_in_answer(answer) == expected, answer


def test_a_finding_at_the_start_of_a_line_or_bullet_is_checked():
    cases = {
        "- 312 participants were enrolled": ["312"],
        "* 1,226 interns": ["1,226"],
        "+ 45 withdrew": ["45"],
        "  - 45 withdrew": ["45"],
        "- 12. was the median": ["12"],
        "312 participants were enrolled.": ["312"],
        "Enrolled:\n312 participants": ["312"],
        "-312 change in steps": ["-312"],
        "1.5 hours more sleep\n12.5 hours in bed": ["1.5", "12.5"],
        "| 312 | 7.2 |\n|---|---|": ["312", "7.2"],
    }
    for answer, expected in cases.items():
        assert numbers_in_answer(answer) == expected, answer


def test_ordered_list_numbers_are_left_out():
    cases = {
        "11. Sleep\n12. Steps\n13. Mood": [],
        "  15) nested item": [],
        "Steps:\n\n99.\n100.": [],
        "Steps:\n1. Sleep\n12. Steps": [],  # a list that starts at 1 may follow text
        "- Sleep\n  - Naps\n1. Steps\n12. Mood": [],
        "Intro\n> 12. item\n> 13. item": [],
        "> 12. item\n>\n> 13. item": [],
        "42. Mean sleep was 7.2 hours": ["7.2"],
        "## 3. Results": [],
        "2025 cohort": [],
        "7 sites": [],
    }
    for answer, expected in cases.items():
        assert numbers_in_answer(answer) == expected, answer


def test_numbered_headings_are_left_out():
    cases = {
        "## 12. Results": [],
        "### 12) Results": [],
        "Some text\n## 14. Discussion": [],
        "> ## 15. Quoted": [],
        "## 312 participants": ["312"],
    }
    for answer, expected in cases.items():
        assert numbers_in_answer(answer) == expected, answer


def test_a_hard_wrapped_count_is_checked():
    # A list can't break into a paragraph unless it starts at 1, so these
    # are sentences, not list items.
    cases = {
        "The cohort enrolled\n312. Of these, 45 withdrew.": ["312", "45"],
        "Total enrolled:\n  312.": ["312"],
        "Steps:\n99.\n100.": ["99", "100"],  # 99. is text, so 100. is too
        "> The cohort enrolled\n> 312. Of these": ["312"],
        "- The cohort enrolled\n312. Of these, 45 withdrew.": ["312", "45"],
        "- Sleep\n  - Naps\n12. Steps": ["12"],
    }
    for answer, expected in cases.items():
        assert numbers_in_answer(answer) == expected, answer


def test_removed_text_does_not_make_a_line_start():
    cases = {
        "`n` 312. rows": ["312"],
        "2024-03-01 312. rows": ["312"],
        "PMID: 34042743 312. rows": ["312"],
        "doi:10.1038/abc`x`312 people": ["312"],
        "10.1038/s41598-022`x`312 people": ["312"],
        "```\nx\n```\n\n12. item": [],
    }
    for answer, expected in cases.items():
        assert numbers_in_answer(answer) == expected, answer


def test_long_answers_stay_fast():
    started = time.monotonic()
    numbers_in_answer("10.1234/" * 32000 + " 2021;" * 20000 + " pp. " * 20000)
    assert time.monotonic() - started < 2
