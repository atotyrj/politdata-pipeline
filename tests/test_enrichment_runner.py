import json

import pandas as pd

from politdata.change_set import create_change_set, load_change_set, save_change_set
from politdata.enrichment_runner import (
    load_current_reference_overlay,
    run_incremental_enrichment,
)


def _write(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def test_reference_overlay_replaces_current_rows(tmp_path):
    base = tmp_path / "base"
    delta = tmp_path / "delta"
    delta.mkdir()
    _write(pd.DataFrame([
        {"organization_id": "o1", "value": "old"},
        {"organization_id": "o2", "value": "keep"},
    ]), base / "organization_reference.parquet")
    _write(pd.DataFrame([
        {"organization_id": "o1", "value": "new"},
    ]), delta / "organization_reference.parquet")
    for name, key in (
        ("report_context", "source_report_id"),
        ("report_account_reference", "source_report_id"),
        ("state_funding_account_reference", "organization_id"),
    ):
        _write(pd.DataFrame([{key: "r1" if "report" in name else "o1"}]),
               base / f"{name}.parquet")
        _write(pd.DataFrame([{key: "r1" if "report" in name else "o1"}]),
               delta / f"{name}.parquet")
    (delta / "manifest.json").write_text(json.dumps({}), encoding="utf-8")

    overlay = load_current_reference_overlay(
        ["r1"], reference_root=base, reference_delta_dir=delta
    )
    organizations = overlay["organization_reference"].set_index(
        "organization_id"
    )
    assert organizations.loc["o1", "value"] == "new"
    assert organizations.loc["o2", "value"] == "keep"


def test_incremental_enrichment_writes_only_affected_fragments(
    tmp_path, monkeypatch
):
    change_path = tmp_path / "change.json"
    save_change_set(create_change_set(
        run_id="run-1",
        report_changes=[{
            "report_id": "r1", "organization_id": "o1",
            "change_type": "new", "old_content_hash": None,
            "new_content_hash": "hash",
        }],
    ), change_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({
        "run_id": "run-1",
        "closure": {"affected_report_ids": ["r1"]},
    }), encoding="utf-8")

    fragments = tmp_path / "fragments" / "run-1"
    fragments.mkdir(parents=True)
    (fragments / "manifest.json").write_text("{}", encoding="utf-8")
    normalized = pd.DataFrame([{
        "source_report_id": "r1", "report_year": 2025,
        "report_quarter": 1,
    }])
    _write(normalized, fragments / "payments" / "monetary_contributions" / "r1.parquet")
    _write(normalized, fragments / "report_sections" / "head_info" / "r1.parquet")

    base = tmp_path / "base"
    refs = tmp_path / "refs" / "run-1"
    refs.mkdir(parents=True)
    (refs / "manifest.json").write_text("{}", encoding="utf-8")
    for name, key, value in (
        ("organization_reference", "organization_id", "o1"),
        ("report_context", "source_report_id", "r1"),
        ("report_account_reference", "source_report_id", "r1"),
        ("state_funding_account_reference", "organization_id", "o1"),
    ):
        frame = pd.DataFrame([{key: value}])
        _write(frame, base / f"{name}.parquet")
        _write(frame, refs / f"{name}.parquet")

    monkeypatch.setattr(
        "politdata.enrichment_runner.rebuild_payment_from_normalized_frame",
        lambda frame, **kwargs: frame.assign(enriched=True),
    )
    monkeypatch.setattr(
        "politdata.enrichment_runner.enrich_report_section_frame",
        lambda frame, **kwargs: frame.assign(enriched=True),
    )
    output = tmp_path / "output"
    manifest = run_incremental_enrichment(
        change_set_path=change_path,
        plan_path=plan_path,
        fragment_root=tmp_path / "fragments",
        reference_root=base,
        reference_delta_root=tmp_path / "refs",
        output_root=output,
    )

    assert manifest["affected_report_ids"] == ["r1"]
    assert (output / "run-1" / "payments" / "monetary_contributions" / "r1.parquet").exists()
    assert (output / "run-1" / "report_sections" / "head_info" / "r1.parquet").exists()
    assert load_change_set(change_path)["stages"]["enrichment"]["status"] == "completed"
