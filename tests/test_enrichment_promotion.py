import json

import pandas as pd
import pytest

from politdata.change_set import create_change_set, load_change_set, save_change_set
from politdata.enrichment_promotion import (
    load_enrichment_state,
    promote_enrichment_delta,
    validate_enrichment_delta,
)


def _run(tmp_path, *, wrong_id=False):
    change_path = tmp_path / "change.json"
    change = create_change_set(
        run_id="run-1",
        report_changes=[{
            "report_id": "r1", "organization_id": "o1",
            "change_type": "new", "old_content_hash": None,
            "new_content_hash": "hash",
        }],
    )
    change["stages"]["enrichment"]["status"] = "completed"
    save_change_set(change, change_path)
    root = tmp_path / "enriched" / "runs"
    run = root / "run-1"
    target = run / "payments" / "other_incomes"
    target.mkdir(parents=True)
    pd.DataFrame([{
        "source_report_id": "other" if wrong_id else "r1",
        "payment_amount": 10,
    }]).to_parquet(target / "r1.parquet", index=False)
    manifest = {
        "schema_version": 1, "run_id": "run-1",
        "affected_report_ids": ["r1"],
        "rows": {"payments": {"other_incomes": 1}, "report_sections": {}},
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return change_path, root, run, manifest


def test_qa_and_promotion_advance_report_index(tmp_path):
    change_path, root, _, _ = _run(tmp_path)
    entry = promote_enrichment_delta(change_path, root)
    assert entry["fragment_rows"] == 1
    assert load_enrichment_state(root)["reports"]["r1"]["run_id"] == "run-1"
    assert load_change_set(change_path)["stages"]["qa"]["status"] == "completed"
    assert promote_enrichment_delta(change_path, root) == entry


def test_qa_rejects_cross_report_fragment(tmp_path):
    _, _, run, manifest = _run(tmp_path, wrong_id=True)
    with pytest.raises(ValueError, match="another report ID"):
        validate_enrichment_delta(run, manifest)
