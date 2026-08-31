import json

import pandas as pd

from politdata.change_set import create_change_set, load_change_set, save_change_set
from politdata.incremental_pipeline import run_incremental_downstream
from politdata.normalization.organizations import normalize_organization_card
from politdata.normalization.reference import (
    build_organization_reference,
    build_report_context,
)


def _write(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def test_real_downstream_flow_for_one_changed_report(tmp_path):
    run_id = "e2e-run"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    detail = {
        "id": "r1",
        "year": 2025,
        "quarter": 1,
        "report_type": "quarter",
        "signed_date": "2025-04-01",
        "payment_info": {
            "incoming": {
                "monetary_contributions": [{
                    "id": "payment-1",
                    "payer_name": "ІВАНЕНКО ІВАН",
                    "payer_type": "Фізична особа",
                    "payment_amount": "100.00",
                    "payment_currency": "UAH",
                }],
                "other_contributions": [],
                "state_funding": [],
                "other_incomes": [],
            },
            "outgoing": {
                "budget_expenses": [],
                "outgoing_expenses": [],
                "return_expenses": [],
                "transfer_expenses": [],
            },
        },
        "properties": {
            "moneys": [{
                "account_number": "UA263223130000026004000045109",
                "account_type": "current",
            }],
        },
    }
    raw_path = raw_dir / "r1.json"
    raw_path.write_text(
        json.dumps({"code": 0, "results": detail}), encoding="utf-8"
    )

    organization = {
        "id": "o1",
        "parent": [],
        "code": "12345678",
        "name": "ПОЛІТИЧНА ПАРТІЯ ТЕСТ",
        "is_active": True,
        "head_info": [],
        "register_address": {"region": "Київ"},
        "actual_address": None,
    }
    normalized_org = normalize_organization_card(organization)
    normalized_root = tmp_path / "normalized"
    _write(
        pd.DataFrame(normalized_org["organizations"]),
        normalized_root / "organizations.parquet",
    )
    _write(
        pd.DataFrame(normalized_org["organization_addresses"]),
        normalized_root / "organization_addresses.parquet",
    )
    _write(
        pd.DataFrame([{
            "source_report_id": "baseline", "organization_id": "other",
            "root_party_id": "other", "party_account_iban": "UA0",
            "party_account_iban_source": "UA0",
            "party_account_type_source": "current",
            "party_account_type_analytical": "current",
        }]),
        normalized_root / "properties" / "property_moneys.parquet",
    )
    _write(
        pd.DataFrame([{
            "source_report_id": "baseline", "organization_id": "other",
        }]),
        normalized_root / "payments" / "state_funding.parquet",
    )

    report_state = tmp_path / "report_state.parquet"
    pd.DataFrame([{
        "report_id": "r1", "organization_id": "o1", "root_party_id": "o1",
        "entity_type": "party", "year": 2025, "quarter": 1,
        "selection_method": "official", "status": "success",
        "raw_path": str(raw_path),
    }]).to_parquet(report_state, index=False)
    analysis_manifest = tmp_path / "analysis_manifest.parquet"
    manifest_frame = pd.DataFrame([{
        "report_id": "r1", "organization_id": "o1", "root_party_id": "o1",
        "year": 2025, "quarter": 1,
        "official_selected_report_id": "r1",
        "analysis_selected_report_id": "r1",
        "analysis_selection_method": "official",
        "analysis_override": False,
    }])
    manifest_frame.to_parquet(analysis_manifest, index=False)

    organizations = pd.DataFrame(normalized_org["organizations"])
    addresses = pd.DataFrame(normalized_org["organization_addresses"])
    organization_reference = build_organization_reference(organizations, addresses)
    report_context = build_report_context(manifest_frame, organization_reference)
    reference_root = tmp_path / "reference"
    _write(organization_reference, reference_root / "organization_reference.parquet")
    _write(report_context, reference_root / "report_context.parquet")
    _write(pd.DataFrame({"source_report_id": ["r1"]}), reference_root / "report_account_reference.parquet")
    _write(pd.DataFrame({"organization_id": ["o1"]}), reference_root / "state_funding_account_reference.parquet")

    change_path = tmp_path / "change.json"
    save_change_set(create_change_set(
        run_id=run_id,
        report_changes=[{
            "report_id": "r1", "organization_id": "o1",
            "change_type": "new", "old_content_hash": None,
            "new_content_hash": None,
        }],
    ), change_path)

    fragment_root = tmp_path / "fragments"
    normalized_deltas = tmp_path / "normalized_deltas"
    plan_path = tmp_path / "plan.json"
    reference_deltas = tmp_path / "reference_deltas" / "runs"
    enrichment_deltas = tmp_path / "enrichment_deltas" / "runs"
    result = run_incremental_downstream(
        change_path,
        stage_options={
            "normalization": {
                "report_state_path": report_state,
                "raw_dir": raw_dir,
                "fragment_root": fragment_root,
            },
            "normalized_promotion": {
                "fragment_root": fragment_root,
                "delta_root": normalized_deltas,
            },
            "dependency_plan": {
                "delta_root": normalized_deltas,
                "reference_root": reference_root,
                "output_path": plan_path,
            },
            "references": {
                "plan_path": plan_path,
                "normalized_root": normalized_root,
                "analysis_manifest_path": analysis_manifest,
                "delta_root": normalized_deltas,
                "output_root": reference_deltas,
            },
            "enrichment": {
                "plan_path": plan_path,
                "fragment_root": fragment_root,
                "reference_root": reference_root,
                "reference_delta_root": reference_deltas,
                "output_root": enrichment_deltas,
            },
            "qa_promotion": {"delta_root": enrichment_deltas},
        },
    )

    assert result["status"] == "completed"
    assert load_change_set(change_path)["stages"]["qa"]["status"] == "completed"
    payment = pd.read_parquet(
        enrichment_deltas / run_id / "payments" / "monetary_contributions" / "r1.parquet"
    )
    assert payment.loc[0, "payer_name_normalized"] == "Іваненко Іван"
    assert payment.loc[0, "analysis_selected"]
