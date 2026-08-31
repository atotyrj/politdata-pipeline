import pandas as pd

from politdata.report_selection import selected_report_manifest, select_official_reports


def test_selection_accepts_exactly_one_signed_instance_per_period():
    reports = pd.DataFrame([
        {"report_id": "unsigned", "organization_id": "o", "year": 2025, "quarter": 1, "signed_date": None},
        {"report_id": "signed", "organization_id": "o", "year": 2025, "quarter": 1, "signed_date": "2025-04-01"},
        {"report_id": "none", "organization_id": "o", "year": 2025, "quarter": 2, "signed_date": None},
        {"report_id": "a", "organization_id": "o", "year": 2025, "quarter": 3, "signed_date": "2025-10-01"},
        {"report_id": "b", "organization_id": "o", "year": 2025, "quarter": 3, "signed_date": "2025-10-02"},
    ])

    instances, profile = select_official_reports(reports)
    selected, _ = selected_report_manifest(reports)

    assert selected["report_id"].tolist() == ["signed"]
    assert profile.set_index("quarter")["selection_status"].to_dict() == {
        1: "unique_signed", 2: "no_signed", 3: "multiple_signed"
    }
    assert instances.set_index("report_id").loc["a", "instance_selection_role"] == "ambiguous_signed_candidate"
