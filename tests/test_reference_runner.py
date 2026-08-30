from decimal import Decimal

import pandas as pd

from politdata.reference_runner import (
    build_incremental_reference_frames,
)


def test_builds_scoped_reference_frames_with_existing_builders():
    plan = {
        "closure": {
            "affected_organization_ids": ["p1"],
            "affected_report_ids": ["r1"],
        },
        "roots": {
            "deleted_organization_ids": [],
        },
    }
    organizations = pd.DataFrame([{
        "organization_id": "p1",
        "root_party_id": "p1",
        "parent_id": None,
        "entity_type": "party",
        "code": "12345678",
        "name": "ПОЛІТИЧНА ПАРТІЯ ТЕСТ",
        "is_active": True,
    }])
    addresses = pd.DataFrame([{
        "organization_id": "p1",
        "address_type": "register",
        "region": "Київ",
    }])
    analysis_manifest = pd.DataFrame([{
        "report_id": "r1",
        "organization_id": "p1",
        "root_party_id": "p1",
        "year": 2025,
        "quarter": 5,
        "official_selected_report_id": "r1",
        "analysis_selected_report_id": "r1",
        "analysis_selection_method": "official",
        "analysis_override": False,
    }])
    property_moneys = pd.DataFrame([{
        "source_report_id": "r1",
        "organization_id": "p1",
        "root_party_id": "p1",
        "party_account_iban": "UA1",
        "party_account_iban_source": "UA1",
        "party_account_type_source": "current",
        "party_account_type_analytical": "current",
    }])
    state_funding = pd.DataFrame([{
        "source_report_id": "r1",
        "organization_id": "p1",
        "root_party_id": "p1",
        "receiver_account_iban_canonical": "UA1",
        "payment_amount": Decimal("100.00"),
        "payment_operation_date": pd.Timestamp("2025-01-01").date(),
        "payment_type_detail_source": "статутне фінансування",
    }])

    frames = build_incremental_reference_frames(
        plan,
        organizations=organizations,
        addresses=addresses,
        analysis_manifest=analysis_manifest,
        property_moneys=property_moneys,
        state_funding=state_funding,
    )

    assert len(frames["organization_reference"]) == 1
    assert frames["report_context"][
        "source_report_id"
    ].tolist() == ["r1"]
    assert len(frames["report_account_reference"]) == 1
    assert len(frames["state_funding_account_reference"]) == 1


def test_missing_analysis_manifest_report_is_rejected():
    plan = {
        "closure": {
            "affected_organization_ids": ["p1"],
            "affected_report_ids": ["new-report"],
        },
        "roots": {
            "deleted_organization_ids": [],
        },
    }
    organizations = pd.DataFrame([{
        "organization_id": "p1",
        "root_party_id": "p1",
        "entity_type": "party",
        "code": "1",
        "name": "Party",
    }])
    addresses = pd.DataFrame([{
        "organization_id": "p1",
        "address_type": "register",
        "region": "Київ",
    }])
    empty_manifest = pd.DataFrame(columns=[
        "report_id",
        "organization_id",
        "root_party_id",
        "year",
        "quarter",
        "official_selected_report_id",
        "analysis_selected_report_id",
        "analysis_selection_method",
        "analysis_override",
    ])
    property_moneys = pd.DataFrame(columns=[
        "source_report_id",
        "organization_id",
        "root_party_id",
        "party_account_iban",
        "party_account_iban_source",
        "party_account_type_source",
        "party_account_type_analytical",
    ])
    state_funding = pd.DataFrame(columns=[
        "source_report_id",
        "organization_id",
        "root_party_id",
        "receiver_account_iban_canonical",
        "payment_amount",
        "payment_operation_date",
        "payment_type_detail_source",
    ])

    try:
        build_incremental_reference_frames(
            plan,
            organizations=organizations,
            addresses=addresses,
            analysis_manifest=empty_manifest,
            property_moneys=property_moneys,
            state_funding=state_funding,
        )
    except ValueError as exc:
        assert "missing from analysis manifest" in str(exc)
    else:
        raise AssertionError("Expected missing-manifest precondition")
