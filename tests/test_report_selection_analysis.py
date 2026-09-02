import pandas as pd
import pytest

from politdata.report_selection import analysis_detail_manifest


def test_analysis_detail_manifest_resolves_override_not_official_report():
    instances = pd.DataFrame([
        {"organization_id": "o1", "year": 2025, "quarter": 1,
         "report_id": "official", "root_party_id": "o1", "entity_type": "party"},
        {"organization_id": "o1", "year": 2025, "quarter": 1,
         "report_id": "override", "root_party_id": "o1", "entity_type": "party"},
    ])
    analysis = pd.DataFrame([{
        "organization_id": "o1", "year": 2025, "quarter": 1,
        "analysis_selected_report_id": "override",
        "analysis_selection_method": "manual_continuity_override",
    }])

    result = analysis_detail_manifest(instances, analysis)

    assert result["report_id"].tolist() == ["override"]
    assert result["selection_method"].tolist() == ["manual_continuity_override"]


def test_analysis_detail_manifest_rejects_missing_chosen_report():
    instances = pd.DataFrame([{
        "organization_id": "o1", "year": 2025, "quarter": 1,
        "report_id": "r1",
    }])
    analysis = pd.DataFrame([{
        "organization_id": "o1", "year": 2025, "quarter": 1,
        "analysis_selected_report_id": "missing",
    }])

    with pytest.raises(ValueError, match="do not resolve"):
        analysis_detail_manifest(instances, analysis)
