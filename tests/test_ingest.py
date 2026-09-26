"""Row mapping and header matching.

`read_sheets` is a thin openpyxl wrapper; the logic worth testing is the pure
mapping underneath it, which needs no spreadsheet.
"""
import sqlite3

import pytest

from app.db import initialise, query
from app.ingest import load, map_row, normalise, unmapped

HEADERS = [
    "Individual/SHOP", "Exchange_Type", "State", "Issuer_Name", "Issuer_ID",
    "Is_Issuer_New_to_Exchange?(Yes_or_No)", "SADP_Only", "Plan_ID", "Plan_Type",
    "QHP or SADP?", "Metal_Level", "URL_Claims_Payment_Policies",
    "Issuer_Claims_Received_Out_of_Network", "Issuer_Claims_Received_In_Network",
    "Issuer_Claims_Denied_Out_of_Network", "Issuer_Claims_Denied_In_Network",
    "Issuer_Claims_Resubmitted_Out_of_Network", "Issuer_Claims_Resubmitted_In_Network",
    "Issuer_Internal_Appeals_Filed", "Issuer_Number_Internal_Appeals_Overturned",
    "Issuer_Percent_Internal_Appeals_Overturned", "Issuer_External_Appeals_Filed",
    "Issuer_Number_External_Appeals_Overturned",
    "Issuer_Percent_External_Appeals_Overturned",
    "Plan_Number_Claims_Received_Out_of_Network",
    "Plan_Number_Claims_Received_In_Network",
    "Plan_Number_Claims_Denied_Out_of_Network",
    "Plan_Number_Claims_Denied_In_Network",
    "Plan_Number_Claims_Resubmitted_Out_of_Network",
    "Plan_Number_Claims_Resubmitted_In_Network",
    "Plan_Number_Claims_Denied_Referral_Required",
    "Plan_Number_Claims_Denied_Due_To_Out_Of_Network",
    "Plan_Number_Claims_Denied_Services_Excluded",
    "Plan_Number_Claims_Denied_Not_Medically_Necessary_Excluding_Behavioral_Health",
    "Plan_Number_Claims_Denied_Not_Medically_Necessary_Behavioral_Health_Only",
    "Plan_Number_Claims_Denied_Due_To_Enrolle_Benefit_Limit_Reached",
    "Plan_Number_Claims_Denied_Due_To_Member_Not_Covered",
    "Plan_Number_Claims_Denied_Due_To_Investigational_Experimental_Cosmetic_Proceduce",
    "Plan_Number_Claims_Denied_Due_To_Administrative_Reason",
    "Plan_Number_Claims_Denied_Other", "Rate_Review", "Financial_Information",
    "Average Monthly Enrollment", "Average Monthly Disenrollment",
]

# The real first data row of the PY2025 Individual QHP sheet.
ROW = [
    "Individual", "FFE", "OR", "PacificSource Health Plans", "10091", "No", "No",
    "10091OR0750002", "PPO", "QHP", "Silver", "https://example.org/policy",
    "3910", "452760", "1268", "9756", "", "", "4055", "946", "23.33", "11", "2",
    "18.18", "402", "51286", "129", "1426", "", "", "154", "129", "88", "51",
    "12", "0", "77", "9", "1715", "204", "https://example.org/rr",
    "https://example.org/fin", "3457", "121",
]


def test_headers_map_to_the_expected_columns():
    record = map_row(HEADERS, ROW)
    assert record["state"] == "OR"
    assert record["issuer_id"] == "10091"
    assert record["issuer_name"] == "PacificSource Health Plans"
    assert record["plan_id"] == "10091OR0750002"
    assert record["issuer_claims_received_in_network"] == 452760
    assert record["issuer_claims_denied_in_network"] == 9756
    assert record["plan_claims_denied_in_network"] == 1426
    assert record["denied_administrative"] == 1715
    assert record["internal_appeals_filed"] == 4055
    assert record["internal_appeals_overturned"] == 946
    assert record["average_monthly_enrollment"] == 3457


def test_blank_numeric_cells_become_none_not_zero():
    record = map_row(HEADERS, ROW)
    assert record["denied_benefit_limit_reached"] == 0, "a real reported zero"
    # Resubmitted columns are blank in this row and are not mapped at all,
    # but an unreported mapped column must be None.
    row = list(ROW)
    row[HEADERS.index("Issuer_Claims_Denied_In_Network")] = ""
    assert map_row(HEADERS, row)["issuer_claims_denied_in_network"] is None


def test_cms_typos_in_their_own_headers_are_accepted():
    """CMS ships 'Enrolle' and 'Proceduce'. Both spellings must map."""
    corrected = [
        h.replace("Enrolle_Benefit", "Enrollee_Benefit").replace("Proceduce", "Procedure")
        for h in HEADERS
    ]
    typo = map_row(HEADERS, ROW)
    fixed = map_row(corrected, ROW)
    assert typo["denied_benefit_limit_reached"] == fixed["denied_benefit_limit_reached"]
    assert typo["denied_experimental"] == fixed["denied_experimental"]


def test_column_reordering_does_not_corrupt_the_load():
    """Matching by name, not position. Positional indexing would misfile silently."""
    pairs = list(zip(HEADERS, ROW, strict=True))
    reordered = list(reversed(pairs))
    headers = [h for h, _ in reordered]
    values = [v for _, v in reordered]

    assert map_row(headers, values) == map_row(HEADERS, ROW)


def test_normalise_ignores_case_spacing_and_punctuation():
    assert normalise("Average Monthly Enrollment") == normalise(
        "average_monthly_enrollment"
    )
    assert normalise("Issuer_ID") == normalise("issuer id")


def test_row_without_an_issuer_is_dropped():
    row = list(ROW)
    row[HEADERS.index("Issuer_ID")] = ""
    assert map_row(HEADERS, row) is None


def test_row_without_a_state_is_dropped():
    row = list(ROW)
    row[HEADERS.index("State")] = "N/A"
    assert map_row(HEADERS, row) is None


def test_unmapped_headers_are_reported_not_hidden():
    """A new upstream column should be visible, not silently ignored."""
    reported = unmapped(HEADERS + ["Brand_New_CMS_Column"])
    assert "Brand_New_CMS_Column" in reported
    assert "State" not in reported


def test_load_writes_rows_that_the_metric_views_can_read(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialise(connection)

    monkeypatch.setattr(
        "app.ingest.read_sheets",
        lambda path: iter([("Transparency 2025 - Ind QHP", HEADERS, ROW)]),
    )
    report = load("ignored.xlsx", connection)

    assert report.rows_loaded == 1
    assert report.rows_skipped == 0
    rows = query(connection, "SELECT * FROM issuer_denial_rates")
    assert rows[0]["denial_rate"] == pytest.approx(9756 / 452760, rel=1e-6)
    assert rows[0]["overturn_rate"] == pytest.approx(946 / 4055, rel=1e-6)
