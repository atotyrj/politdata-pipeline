import pyarrow as pa
from importlib.util import find_spec
import json
import pytest

from politdata.full_rebuild import run_full_replace_stage
from politdata.orchestrator import FullReplacePaths, RunConfig, run_pipeline
from politdata.schema_contracts import load_normalized_schemas
from politdata.storage import LocalGenerationStore, verify_generation


def _source_row(schema):
    row = {}
    for field in schema:
        if not field.name.startswith("source__"):
            continue
        name = field.name.removeprefix("source__")
        if pa.types.is_integer(field.type):
            value = 1
        elif pa.types.is_floating(field.type):
            value = 1.0
        elif pa.types.is_null(field.type):
            value = None
        else:
            value = "2025-01-01" if "date" in name or name == "created_at" else f"fixture-{name}"
        row[name] = value
    return row


def _report_detail():
    schemas = load_normalized_schemas()
    payment = {
        "id": "payment-1",
        "party_id": "party-1",
        "report_status": 1,
        "payer_name": "ІВАНЕНКО ІВАН",
        "payer_code": "1234567890",
        "payer_type": "Фізична особа",
        "payer_account_iban": "UA263223130000026004000045109",
        "payment_amount": "100.00",
        "payment_currency": "UAH",
        "payment_operation_date": "2025-02-01",
        "payment_type": "Фінансування статутної діяльності",
        "receiver_name": "ПОЛІТИЧНА ПАРТІЯ ТЕСТ",
        "receiver_code": "12345678",
        "receiver_type": "Юридична особа",
        "receiver_account_iban": "UA263223130000026004000045109",
    }


    return {
        "code": 0,
        "results": {
            "id": "report-1",
            "year": 2025,
            "quarter": 1,
            "schema_version": 1,
            "report_type": "quarter",
            "signed_date": "2025-04-01",
            "payment_info": {
                "incoming": {
                    "monetary_contributions": [payment],
                    "other_contributions": [],
                    "state_funding": [payment],
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
                    "id": "money-1",
                    "party_id": "party-1",
                    "report_status": 1,
                    "account_type": "Поточний рахунок",
                    "account_number": "UA263223130000026004000045109",
                    "account_holder": "ПОЛІТИЧНА ПАРТІЯ ТЕСТ",
                    "begin_period_balance": "0.00",
                    "end_period_balance": "100.00",
                    "report_period_income": "100.00",
                    "report_period_used_funds": "0.00",
                    "created_at": "2025-04-01",
                }],
                "property_object": [_source_row(schemas["properties/realty"])],
                "property_transport": [_source_row(schemas["properties/transport"])],
                "property_movable": [_source_row(schemas["properties/movable"])],
                "property_intangible_asset": [_source_row(schemas["properties/intangible"])],
                "property_paper": [],
            },
            "obligations": [_source_row(schemas["obligations/obligations"])],
            "head_info": _source_row(schemas["report_state/head_info"]),
            "organizations": [_source_row(schemas["report_state/organizations"])],
            "regional_offices": [_source_row(schemas["report_state/regional_offices"])],
            "employees_by_civil_contract": "1",
            "employees_by_employment_contract": "2",
        },
    }


def _install_network_fixture(monkeypatch):
    parties = [{
        "id": "party-1", "code": "12345678",
        "name": "ПОЛІТИЧНА ПАРТІЯ ТЕСТ", "is_active": True,
        "created_at": "2025-01-01", "updated_at": "2025-01-02",
        "regional_offices": [],
    }]
    organization_payload = {"results": {
        "id": "party-1", "parent": [], "code": "12345678",
        "name": "ПОЛІТИЧНА ПАРТІЯ ТЕСТ", "is_active": True,
        "created_at": "2025-01-01", "updated_at": "2025-01-02",
        "head_info": {
            "head_last_name": "Іваненко", "head_first_name": "Іван",
            "head_middle_name": "Іванович",
        },
        "register_address": {"country": "Україна", "region": "м. Київ"},
    }}
    report_list = [{
        "id": "report-1", "party_id": "party-1",
        "is_party_office": False, "schema_version": 1,
        "report_type": "quarter", "year": 2025, "quarter": 1,
        "signed_date": "2025-04-01", "created_date": "2025-04-01",
    }]

    def fetch_reports(organization_id, **kwargs):
        metadata = {
            "organization_id": organization_id, "declared_count": 1,
            "raw_fetched_rows": 1, "fetched_count": 1,
            "count_mismatch": False, "count_difference": 0,
            "pages_requested": 1, "page_size": 100,
            "duplicate_report_count": 0, "duplicate_report_ids": [],
            "count_changed_during_fetch": False,
        }
        return (report_list, metadata) if kwargs.get("return_metadata") else report_list

    monkeypatch.setattr("politdata.report_discovery.fetch_all_reports", fetch_reports)
    monkeypatch.setattr(
        "politdata.report_details.fetch_report_detail",
        lambda report_id, **_: _report_detail(),
    )
    return parties, organization_payload


def _fixture_stage(monkeypatch, *, generate_excel=False, fail_after_stage=False):
    parties, organization_payload = _install_network_fixture(monkeypatch)

    def stage(*, config, paths):
        result = run_full_replace_stage(
            config=config,
            paths=paths,
            generate_excel=generate_excel,
            fetch_parties_fn=lambda: parties,
            fetch_organization_fn=lambda _: organization_payload,
        )
        if fail_after_stage:
            raise RuntimeError("intentional failure after full stage")
        return result

    return stage


def test_full_replace_real_transforms_from_fixture_to_enrichment(tmp_path, monkeypatch):
    excel_available = find_spec("xlsxwriter") is not None
    config = RunConfig(
        mode="full-replace",
        run_id="fixture-full",
        confirm_full_replace=True,
        control_root=tmp_path / "control",
        run_root=tmp_path / "runs",
        generation_root=tmp_path / "generations",
    )
    staging = tmp_path / "staging"
    paths = FullReplacePaths(
        run_dir=tmp_path,
        staging_dir=staging,
        raw_dir=staging / "raw",
        interim_dir=staging / "interim",
        processed_dir=staging / "processed",
        outputs_dir=staging / "outputs",
    )
    for path in (paths.raw_dir, paths.interim_dir, paths.processed_dir, paths.outputs_dir):
        path.mkdir(parents=True)

    parties, organization_payload = _install_network_fixture(monkeypatch)

    result = run_full_replace_stage(
        config=config,
        paths=paths,
        generate_excel=excel_available,
        fetch_parties_fn=lambda: parties,
        fetch_organization_fn=lambda _: organization_payload,
    )

    assert result["status"] == "completed"
    assert result["qa"]["passed"] is True
    assert result["row_counts"]["analysis_selected_reports"] == 1
    assert (paths.processed_dir / "payments" / "monetary_contributions.parquet").exists()
    assert (paths.processed_dir / "properties" / "realty.parquet").exists()
    assert (paths.interim_dir / "normalized" / "properties" / "paper.parquet").exists()
    if excel_available:
        assert len(result["artifact_locations"]["excel_workbooks"]) == 18
        assert len(list(paths.outputs_dir.glob("*.xlsx"))) == 18


def test_full_fixture_publishes_generation_manifest_and_latest(tmp_path, monkeypatch):
    config = RunConfig(
        mode="full-replace", run_id="fixture-published",
        confirm_full_replace=True, publish=True,
        control_root=tmp_path / "control", run_root=tmp_path / "runs",
        generation_root=tmp_path / "generations",
    )

    result = run_pipeline(
        config,
        full_replace_stage_runner=_fixture_stage(monkeypatch),
    )

    generation = tmp_path / "generations" / "fixture-published"
    manifest = json.loads(
        (generation / "generation_manifest.json").read_text(encoding="utf-8")
    )
    latest = json.loads(
        (tmp_path / "control" / "latest.json").read_text(encoding="utf-8")
    )
    assert result["result"]["status"] == "published"
    assert manifest["status"] == "validated"
    assert manifest["qa"]["passed"] is True
    assert manifest["row_counts"]["analysis_selected_reports"] == 1
    assert manifest["artifact_checksums"]
    assert latest["generation_id"] == "fixture-published"
    assert latest["generation_path"] == str(generation)
    assert latest["generation_manifest_hash"]
    assert (generation / "processed" / "payments" / "monetary_contributions.parquet").exists()
    assert not (tmp_path / "runs" / "fixture-published" / "staging").exists()
    restored = tmp_path / "clean-runner" / "fixture-published"
    LocalGenerationStore(
        tmp_path / "generations", tmp_path / "control" / "latest.json"
    ).restore_latest(restored)
    restored_manifest = verify_generation(restored)
    assert restored_manifest["generation_id"] == "fixture-published"
    assert (
        restored / "processed" / "payments" / "monetary_contributions.parquet"
    ).exists()


def test_full_fixture_failure_preserves_active_generation(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir(parents=True)
    previous_latest = {
        "schema_version": 1,
        "generation_id": "previous",
        "generation_path": "immutable/previous",
    }
    (control / "latest.json").write_text(
        json.dumps(previous_latest), encoding="utf-8"
    )
    config = RunConfig(
        mode="full-replace", run_id="fixture-intentional-failure",
        confirm_full_replace=True, publish=True,
        control_root=control, run_root=tmp_path / "runs",
        generation_root=tmp_path / "generations",
    )

    with pytest.raises(RuntimeError, match="intentional failure"):
        run_pipeline(
            config,
            full_replace_stage_runner=_fixture_stage(
                monkeypatch, fail_after_stage=True,
            ),
        )

    assert json.loads((control / "latest.json").read_text(encoding="utf-8")) == previous_latest
    assert not (tmp_path / "generations" / "fixture-intentional-failure").exists()
    failed_staging = tmp_path / "runs" / "fixture-intentional-failure" / "staging"
    assert (failed_staging / "processed" / "reference" / "report_context.parquet").exists()
    assert not (control / "writer.lock").exists()
    journal = json.loads(
        (control / "runs" / "fixture-intentional-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert journal["status"] == "failed"
    assert journal["error_type"] == "RuntimeError"
