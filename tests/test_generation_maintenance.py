import json

import pytest

from politdata.generation_maintenance import (
    apply_retention_plan,
    build_public_artifact_catalog,
    build_retention_plan,
    rollback_latest,
    write_public_artifact_catalog,
)
from politdata.storage import (
    GenerationIntegrityError,
    LatestConflictError,
    LocalGenerationStore,
    file_hash,
    payload_hash,
)


def _store(tmp_path):
    return LocalGenerationStore(
        tmp_path / "generations",
        tmp_path / "control" / "latest.json",
    )


def _publish_generation(store, tmp_path, generation_id, ordinal):
    source = tmp_path / f"staging-{generation_id}"
    files = {
        "raw/source.json": b'{"private": "raw"}',
        "interim/state.json": b'{"internal": true}',
        "processed/payments.parquet": f"payments-{ordinal}".encode(),
        "outputs/payments.xlsx": f"workbook-{ordinal}".encode(),
    }
    checksums = {}
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        checksums[relative] = file_hash(path)
    manifest = {
        "schema_version": 1,
        "generation_id": generation_id,
        "status": "validated",
        "completed_at_utc": f"2026-09-{ordinal:02d}T00:00:00+00:00",
        "code_revision": f"revision-{ordinal}",
        "row_counts": {"payments": ordinal},
        "qa": {"passed": True},
        "artifact_checksums": checksums,
    }
    (source / "generation_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    store.publish_generation(source, generation_id)
    return manifest


def test_retention_preview_and_apply_keep_latest_recent_and_explicit(tmp_path):
    store = _store(tmp_path)
    for ordinal in range(1, 5):
        _publish_generation(store, tmp_path, f"g{ordinal}", ordinal)
    store.publish_latest({"generation_id": "g4"})

    plan = build_retention_plan(
        store,
        keep_latest=2,
        protected_generation_ids=("g1",),
    )

    assert plan["status"] == "planned"
    assert plan["keep_generation_ids"] == ["g4", "g3", "g1"]
    assert [item["generation_id"] for item in plan["delete"]] == ["g2"]
    assert set(store.list_generation_ids()) == {"g1", "g2", "g3", "g4"}

    result = apply_retention_plan(
        store,
        plan,
        expected_current_generation_id="g4",
    )

    assert result["deleted_generation_ids"] == ["g2"]
    assert set(store.list_generation_ids()) == {"g1", "g3", "g4"}
    assert store.read_latest() == {"generation_id": "g4"}


def test_retention_refuses_stale_latest_without_deleting(tmp_path):
    store = _store(tmp_path)
    for ordinal in range(1, 4):
        _publish_generation(store, tmp_path, f"g{ordinal}", ordinal)
    store.publish_latest({"generation_id": "g3"})
    plan = build_retention_plan(store, keep_latest=1)
    store.publish_latest(
        {"generation_id": "g2"},
        expected_generation_id="g3",
    )

    with pytest.raises(LatestConflictError, match="actual=g2"):
        apply_retention_plan(
            store,
            plan,
            expected_current_generation_id="g3",
        )

    assert set(store.list_generation_ids()) == {"g1", "g2", "g3"}


def test_retention_plan_is_bound_to_generation_manifest_hash(tmp_path):
    store = _store(tmp_path)
    _publish_generation(store, tmp_path, "g1", 1)
    _publish_generation(store, tmp_path, "g2", 2)
    store.publish_latest({"generation_id": "g2"})
    plan = build_retention_plan(store, keep_latest=1)
    manifest_path = tmp_path / "generations" / "g1" / "generation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["row_counts"]["payments"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(GenerationIntegrityError, match="manifest hash"):
        apply_retention_plan(
            store,
            plan,
            expected_current_generation_id="g2",
        )

    assert (tmp_path / "generations" / "g1").is_dir()


def test_rollback_switches_latest_only_to_verified_generation(tmp_path):
    store = _store(tmp_path)
    old_manifest = _publish_generation(store, tmp_path, "g1", 1)
    _publish_generation(store, tmp_path, "g2", 2)
    store.publish_latest({"generation_id": "g2"})

    result = rollback_latest(
        store,
        "g1",
        expected_current_generation_id="g2",
    )
    latest = store.read_latest()

    assert result["status"] == "rolled_back"
    assert latest["generation_id"] == "g1"
    assert latest["rollback_from_generation_id"] == "g2"
    assert latest["generation_manifest_hash"] == payload_hash(old_manifest)
    assert store.read_generation_manifest(
        "g1", expected_manifest_hash=latest["generation_manifest_hash"]
    ) == old_manifest


def test_rollback_preserves_latest_when_target_is_corrupted(tmp_path):
    store = _store(tmp_path)
    _publish_generation(store, tmp_path, "g1", 1)
    _publish_generation(store, tmp_path, "g2", 2)
    current = {"generation_id": "g2"}
    store.publish_latest(current)
    (tmp_path / "generations" / "g1" / "outputs" / "payments.xlsx").write_bytes(
        b"corrupted"
    )

    with pytest.raises(GenerationIntegrityError, match="checksum mismatch"):
        rollback_latest(
            store,
            "g1",
            expected_current_generation_id="g2",
        )

    assert store.read_latest() == current


def test_public_catalog_contains_only_verified_analytical_artifacts(tmp_path):
    store = _store(tmp_path)
    _publish_generation(store, tmp_path, "g1", 1)
    _publish_generation(store, tmp_path, "g2", 2)
    store.publish_latest({"generation_id": "g2"})

    catalog = build_public_artifact_catalog(store)

    assert catalog["latest_generation_id"] == "g2"
    assert [item["generation_id"] for item in catalog["generations"]] == [
        "g2",
        "g1",
    ]
    latest = catalog["generations"][0]
    assert latest["is_latest"] is True
    paths = [item["path"] for item in latest["artifacts"]]
    assert paths == ["outputs/payments.xlsx", "processed/payments.parquet"]
    assert all("raw/" not in item["relative_download_path"] for item in latest["artifacts"])
    assert all("interim/" not in item["relative_download_path"] for item in latest["artifacts"])
    assert all("generations\\" not in item["relative_download_path"] for item in latest["artifacts"])
    assert "generation_path" not in json.dumps(catalog)


def test_public_catalog_write_is_valid_json(tmp_path):
    store = _store(tmp_path)
    _publish_generation(store, tmp_path, "g1", 1)
    store.publish_latest({"generation_id": "g1"})
    output = tmp_path / "public" / "catalog.json"

    catalog = write_public_artifact_catalog(store, output)

    assert json.loads(output.read_text(encoding="utf-8")) == catalog


def test_public_catalog_refuses_raw_prefix_even_when_requested(tmp_path):
    store = _store(tmp_path)
    _publish_generation(store, tmp_path, "g1", 1)

    with pytest.raises(ValueError, match="processed/ or outputs/"):
        build_public_artifact_catalog(store, include_prefixes=("raw/",))
