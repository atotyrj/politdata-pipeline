import json

from politdata.production_baseline import assemble_existing_baseline
from politdata.storage import verify_generation


def test_assemble_existing_baseline_uses_only_canonical_components(tmp_path):
    raw = tmp_path / "data" / "raw"
    for name in (
        "organization_details",
        "parties",
        "party_account_versions",
        "party_accounts",
        "report_details",
        "report_lists",
    ):
        (raw / name).mkdir(parents=True)
        (raw / name / "record.json").write_text("{}", encoding="utf-8")
    (raw / "report_detail_probes").mkdir()
    (raw / "report_detail_probes" / "probe.json").write_text("{}")

    interim = tmp_path / "data" / "interim"
    (interim / "reports").mkdir(parents=True)
    (interim / "reports" / "selected.txt").write_text("selected")
    (interim / "backups").mkdir()
    (interim / "backups" / "old.txt").write_text("old")
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    (processed / "dataset.parquet").write_bytes(b"parquet")
    excel = tmp_path / "outputs"
    excel.mkdir()
    (excel / "01_party_information.xlsx").write_bytes(b"xlsx")

    destination = tmp_path / "generation"
    manifest = assemble_existing_baseline(
        destination,
        "baseline-1",
        raw_root=raw,
        interim_root=interim,
        processed_root=processed,
        excel_root=excel,
        code_revision="abc123",
    )

    assert verify_generation(destination) == manifest
    assert not (destination / "raw" / "report_detail_probes").exists()
    assert not (destination / "interim" / "backups").exists()
    assert (destination / "outputs" / "01_party_information.xlsx").is_file()
    payload = json.loads((destination / "generation_manifest.json").read_text())
    assert payload["code_revision"] == "abc123"
