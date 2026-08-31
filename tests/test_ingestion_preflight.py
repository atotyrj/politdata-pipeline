import pandas as pd

from politdata.ingestion_preflight import build_ingestion_preflight


def test_preflight_is_read_only_and_reports_local_readiness(tmp_path):
    manifest = tmp_path / "manifest.parquet"
    refresh = tmp_path / "refresh.parquet"
    discovery = tmp_path / "discovery.parquet"
    details = tmp_path / "details.parquet"
    change_set = tmp_path / "current.json"
    pd.DataFrame({"organization_id": ["o1"]}).to_parquet(manifest)
    pd.DataFrame({"organization_id": ["o1"]}).to_parquet(refresh)
    pd.DataFrame({"organization_id": ["o1"], "status": ["success"]}).to_parquet(discovery)
    pd.DataFrame({"report_id": ["r1"], "status": ["success"]}).to_parquet(details)
    before = {path: path.read_bytes() for path in (manifest, refresh, discovery, details)}

    result = build_ingestion_preflight(
        committed_manifest_path=manifest,
        refresh_state_path=refresh,
        report_discovery_state_path=discovery,
        report_detail_state_path=details,
        change_set_path=change_set,
    )

    assert result["mode"] == "read_only_preflight"
    assert result["network_requests"] == 0
    assert result["writes"] == 0
    assert result["checks"]["ready_for_explicit_ingestion"] is True
    assert {path: path.read_bytes() for path in before} == before
