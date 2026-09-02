import json

import pytest

from politdata.storage import (
    GenerationIntegrityError,
    LatestConflictError,
    LocalGenerationStore,
    file_hash,
    payload_hash,
    verify_generation,
)


def _generation(path, generation_id="g1"):
    artifact = path / "processed" / "result.parquet"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"validated-generation")
    manifest = {
        "schema_version": 1,
        "generation_id": generation_id,
        "status": "validated",
        "artifact_checksums": {
            "processed/result.parquet": file_hash(artifact),
        },
    }
    (path / "generation_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


def test_local_store_publishes_and_restores_latest_with_integrity_check(tmp_path):
    source = tmp_path / "staging"
    manifest = _generation(source)
    store = LocalGenerationStore(
        tmp_path / "store", tmp_path / "control" / "latest.json"
    )

    location = store.publish_generation(source, "g1")
    pointer = {
        "generation_id": "g1",
        "generation_path": location,
        "generation_manifest_hash": payload_hash(manifest),
    }
    store.publish_latest(pointer)
    restored = tmp_path / "restored" / "g1"
    store.restore_latest(restored)

    assert not source.exists()
    assert store.read_latest() == pointer
    assert verify_generation(restored) == manifest
    assert (restored / "processed" / "result.parquet").read_bytes() == b"validated-generation"


def test_restore_rejects_corrupted_generation_without_creating_target(tmp_path):
    source = tmp_path / "staging"
    manifest = _generation(source)
    store = LocalGenerationStore(
        tmp_path / "store", tmp_path / "control" / "latest.json"
    )
    store.publish_generation(source, "g1")
    stored_artifact = tmp_path / "store" / "g1" / "processed" / "result.parquet"
    stored_artifact.write_bytes(b"corrupted")
    destination = tmp_path / "restore-target"

    with pytest.raises(GenerationIntegrityError, match="checksum mismatch"):
        store.restore_generation(
            "g1", destination, expected_manifest_hash=payload_hash(manifest)
        )

    assert not destination.exists()


def test_latest_compare_and_swap_preserves_newer_pointer(tmp_path):
    store = LocalGenerationStore(
        tmp_path / "store", tmp_path / "control" / "latest.json"
    )
    newer = {"generation_id": "newer"}
    store.publish_latest(newer)

    with pytest.raises(LatestConflictError, match="expected=older"):
        store.publish_latest(
            {"generation_id": "candidate"}, expected_generation_id="older"
        )

    assert store.read_latest() == newer


def test_generation_paths_cannot_escape_store_root(tmp_path):
    store = LocalGenerationStore(
        tmp_path / "store", tmp_path / "control" / "latest.json"
    )

    with pytest.raises(ValueError, match="Unsafe"):
        store.restore_generation("../outside", tmp_path / "target")
