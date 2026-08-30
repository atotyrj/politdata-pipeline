import json

import pandas as pd

from politdata.change_set import (
    create_change_set,
    load_change_set,
    save_change_set,
)
from politdata.normalization_runner import (
    normalize_changed_fragments,
)
from politdata.report_details import (
    report_detail_content_hash,
)
from politdata.change_detection import (
    organization_content_hash,
)


def test_normalizes_only_changed_report_to_atomic_fragments(
    tmp_path,
):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    payload = {
        "code": 0,
        "results": {
            "id": "r1",
            "year": 2025,
            "quarter": 2,
            "report_type": "main",
            "signed_date": None,
            "payment_info": {
                "incoming": {
                    "monetary_contributions": [{
                        "id": "payment-1",
                        "payer_name": "Test payer",
                        "payment_amount": "100.25",
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
                    "account_number": "not-an-iban",
                    "account_type": "Поточний рахунок",
                }],
                "property_object": [{
                    "id": "realty-1",
                    "name": "Office",
                }],
            },
            "employees_by_civil_contract": "--",
            "employees_by_employment_contract": "12",
        },
    }
    raw_path = raw_dir / "r1.json"
    raw_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    content_hash = report_detail_content_hash(payload)
    state_path = tmp_path / "state.parquet"
    pd.DataFrame([{
        "report_id": "r1",
        "organization_id": "o1",
        "root_party_id": "p1",
        "entity_type": "party",
        "year": 2025,
        "quarter": 2,
        "selection_method": "official",
        "status": "success",
        "raw_path": str(raw_path),
        "content_hash": content_hash,
    }]).to_parquet(state_path, index=False)
    change_set_path = tmp_path / "change_set.json"
    save_change_set(
        create_change_set(
            run_id="run-1",
            report_changes=[{
                "report_id": "r1",
                "organization_id": "o1",
                "change_type": "new",
                "old_content_hash": None,
                "new_content_hash": content_hash,
            }],
        ),
        change_set_path,
    )

    manifest = normalize_changed_fragments(
        change_set_path=change_set_path,
        report_state_path=state_path,
        raw_dir=raw_dir,
        fragment_root=tmp_path / "fragments",
    )

    output = tmp_path / "fragments" / "run-1"
    assert manifest["report_count"] == 1
    assert (
        output
        / "payments"
        / "monetary_contributions"
        / "r1.parquet"
    ).exists()
    assert (
        output
        / "properties"
        / "property_moneys"
        / "r1.parquet"
    ).exists()
    assert (
        output
        / "report_sections"
        / "realty"
        / "r1.parquet"
    ).exists()
    employee_path = (
        output / "report_sections" / "employee_counts" / "r1.parquet"
    )
    employees = pd.read_parquet(employee_path)
    assert pd.isna(employees.loc[0, "employees_by_civil_contract"])
    assert employees.loc[0, "employees_by_employment_contract"] == 12
    assert load_change_set(change_set_path)["stages"][
        "normalization"
    ]["status"] == "completed"


def test_hash_mismatch_fails_without_committed_output(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_path = raw_dir / "r1.json"
    raw_path.write_text(
        json.dumps({
            "code": 0,
            "results": {
                "id": "r1",
                "year": 2025,
                "quarter": 1,
            },
        }),
        encoding="utf-8",
    )
    state_path = tmp_path / "state.parquet"
    pd.DataFrame([{
        "report_id": "r1",
        "organization_id": "o1",
        "root_party_id": "p1",
        "entity_type": "party",
        "year": 2025,
        "quarter": 1,
        "selection_method": "official",
        "status": "success",
        "raw_path": str(raw_path),
        "content_hash": "expected",
    }]).to_parquet(state_path, index=False)
    change_set_path = tmp_path / "change_set.json"
    save_change_set(
        create_change_set(
            run_id="run-2",
            report_changes=[{
                "report_id": "r1",
                "organization_id": "o1",
                "change_type": "meaningful_change",
                "old_content_hash": "old",
                "new_content_hash": "expected",
            }],
        ),
        change_set_path,
    )

    try:
        normalize_changed_fragments(
            change_set_path=change_set_path,
            report_state_path=state_path,
            raw_dir=raw_dir,
            fragment_root=tmp_path / "fragments",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected semantic hash mismatch")

    assert not (tmp_path / "fragments" / "run-2").exists()
    assert load_change_set(change_set_path)["stages"][
        "normalization"
    ]["status"] == "failed"


def test_normalizes_changed_organization_card(tmp_path):
    organization_raw_dir = tmp_path / "organizations_raw"
    organization_raw_dir.mkdir()
    record = {
        "id": "o1",
        "parent": [],
        "code": "12345678",
        "name": "Test party",
        "is_active": True,
        "created_at": "2025-01-01 10:00:00",
        "updated_at": "2026-01-01 10:00:00",
        "head_info": [],
        "register_address": {
            "country": "Україна",
        },
        "actual_address": None,
    }
    (organization_raw_dir / "o1.json").write_text(
        json.dumps({"code": 0, "results": record}),
        encoding="utf-8",
    )
    change_set_path = tmp_path / "change_set.json"
    save_change_set(
        create_change_set(
            run_id="org-run",
            organization_changes=[{
                "organization_id": "o1",
                "change_type": "meaningful_change",
                "candidate_reasons": ["rolling_refresh"],
                "changed_fields": ["name"],
                "old_content_hash": "old",
                "new_content_hash": organization_content_hash(
                    record
                ),
            }],
        ),
        change_set_path,
    )

    manifest = normalize_changed_fragments(
        change_set_path=change_set_path,
        organization_raw_dir=organization_raw_dir,
        fragment_root=tmp_path / "fragments",
    )

    output = tmp_path / "fragments" / "org-run"
    assert manifest["organization_count"] == 1
    assert manifest["organization_normalization_pending"] is False
    assert (output / "organizations" / "o1.parquet").exists()
    assert (
        output / "organization_heads" / "o1.parquet"
    ).exists()
    assert (
        output / "organization_addresses" / "o1.parquet"
    ).exists()
    assert load_change_set(change_set_path)["stages"][
        "normalization"
    ]["status"] == "completed"
