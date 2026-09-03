import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from politdata.generation_maintenance import rollback_latest
from politdata.github_releases import (
    BUNDLE_INDEX_NAME,
    GENERATION_MANIFEST_NAME,
    GENERATION_POINTER_NAME,
    PUBLIC_CATALOG_NAME,
    GitHubReleaseGenerationStore,
    ReleaseAssetTooLarge,
    build_generation_bundle,
)
from politdata.storage import (
    GenerationIntegrityError,
    LatestConflictError,
    file_hash,
    payload_hash,
    verify_generation,
)
from politdata.release_rehearsal import (
    build_rehearsal_generation,
    run_release_rehearsal,
)


class MemoryReleaseClient:
    def __init__(self):
        self.releases = {}
        self.payloads = {}
        self.latest_id = None
        self.next_id = 1

    def get_release_by_tag(self, tag):
        return self.releases.get(tag)

    def get_latest_release(self):
        if self.latest_id is None:
            return None
        return next(
            release
            for release in self.releases.values()
            if release["id"] == self.latest_id
        )

    def list_releases(self):
        return list(self.releases.values())

    def create_release(self, *, tag, name, body, target_commitish="main"):
        release = {
            "id": self.next_id,
            "tag_name": tag,
            "name": name,
            "body": body,
            "target_commitish": target_commitish,
            "draft": True,
            "assets": [],
            "upload_url": "memory://upload{?name}",
        }
        self.next_id += 1
        self.releases[tag] = release
        return release

    def update_release(self, release_id, **payload):
        release = next(
            item for item in self.releases.values() if item["id"] == release_id
        )
        release.update(payload)
        if payload.get("make_latest") == "true":
            self.latest_id = release_id
        return release

    def delete_release(self, release_id):
        tag = next(
            tag
            for tag, release in self.releases.items()
            if release["id"] == release_id
        )
        del self.releases[tag]
        if self.latest_id == release_id:
            self.latest_id = None

    def delete_tag(self, tag):
        self.payloads.pop((tag, "tag"), None)

    def upload_asset(self, release, path, *, name=None, content_type=None):
        del content_type
        data = Path(path).read_bytes()
        asset_name = name or Path(path).name
        asset = {
            "id": len(self.payloads) + 1,
            "name": asset_name,
            "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "url": f"memory://{release['id']}/{asset_name}",
            "browser_download_url": f"memory://download/{asset_name}",
        }
        release["assets"].append(asset)
        self.payloads[asset["url"]] = data
        return asset

    def download_asset(self, asset, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payloads[asset["url"]])
        if file_hash(destination) != asset["digest"].split(":", 1)[1]:
            raise GenerationIntegrityError("GitHub asset checksum mismatch")
        return str(destination)


def _generation(path, generation_id="g1", ordinal=1):
    files = {
        "raw/source.json": b'{"private": true}',
        "interim/state.json": b'{"internal": true}',
        "processed/payments.parquet": f"payments-{ordinal}".encode(),
        "outputs/payments.xlsx": f"workbook-{ordinal}".encode(),
        "outputs/organizations.xlsx": f"organizations-{ordinal}".encode(),
    }
    checksums = {}
    for relative, content in files.items():
        artifact = path / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(content)
        checksums[relative] = file_hash(artifact)
    manifest = {
        "schema_version": 1,
        "generation_id": generation_id,
        "status": "validated",
        "completed_at_utc": f"2026-09-{ordinal:02d}T00:00:00+00:00",
        "artifact_checksums": checksums,
    }
    (path / GENERATION_MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


def _store(client):
    return GitHubReleaseGenerationStore(
        "atotyrj/politdata-pipeline", client=client
    )


def test_bundle_contains_restorable_parts_and_lists_excel_public_assets(tmp_path):
    source = tmp_path / "source"
    _generation(source)

    index = build_generation_bundle(source, tmp_path / "bundle", "g1")

    assert index["generation_id"] == "g1"
    assert {item["name"] for item in index["public_assets"]} == {
        "payments.xlsx",
        "organizations.xlsx",
    }
    bundled = {
        relative
        for asset in index["bundle_assets"]
        for relative in asset["files"]
    }
    assert "raw/source.json" in bundled
    assert "outputs/payments.xlsx" in bundled
    assert (tmp_path / "bundle" / BUNDLE_INDEX_NAME).is_file()


def test_release_store_publishes_draft_then_latest_and_restores(tmp_path):
    client = MemoryReleaseClient()
    store = _store(client)
    source = tmp_path / "source"
    manifest = _generation(source)

    store.publish_generation(source, "g1")
    release = client.get_release_by_tag("politdata-data-g1")
    assert release["draft"] is True
    assert GENERATION_POINTER_NAME not in {
        asset["name"] for asset in release["assets"]
    }
    assert {"payments.xlsx", "organizations.xlsx", PUBLIC_CATALOG_NAME} <= {
        asset["name"] for asset in release["assets"]
    }

    pointer = {
        "generation_id": "g1",
        "generation_manifest_hash": payload_hash(manifest),
    }
    store.publish_latest(pointer)
    assert store.read_latest()["generation_id"] == "g1"
    assert release["draft"] is False

    restored = tmp_path / "restored"
    store.restore_latest(restored)
    assert verify_generation(restored) == manifest
    assert (restored / "raw" / "source.json").read_bytes() == b'{"private": true}'


def test_release_public_catalog_has_direct_workbook_links_only(tmp_path):
    client = MemoryReleaseClient()
    store = _store(client)
    source = tmp_path / "source"
    _generation(source)
    store.publish_generation(source, "g1")
    release = client.get_release_by_tag("politdata-data-g1")
    catalog_asset = next(
        asset for asset in release["assets"] if asset["name"] == PUBLIC_CATALOG_NAME
    )
    catalog = json.loads(client.payloads[catalog_asset["url"]])

    assert [item["name"] for item in catalog["workbooks"]] == [
        "organizations.xlsx",
        "payments.xlsx",
    ]
    assert all("/releases/download/" in item["download_url"] for item in catalog["workbooks"])
    assert "raw" not in json.dumps(catalog)


def test_release_restore_rejects_tampered_bundle_without_target(tmp_path):
    client = MemoryReleaseClient()
    store = _store(client)
    source = tmp_path / "source"
    _generation(source)
    store.publish_generation(source, "g1")
    release = client.get_release_by_tag("politdata-data-g1")
    bundle = next(
        asset for asset in release["assets"] if asset["name"].endswith(".zip")
    )
    client.payloads[bundle["url"]] += b"tampered"
    destination = tmp_path / "restore"

    with pytest.raises(GenerationIntegrityError, match="checksum mismatch"):
        store.restore_generation("g1", destination)

    assert not destination.exists()


def test_release_store_rollback_and_delete_guard(tmp_path):
    client = MemoryReleaseClient()
    store = _store(client)
    manifests = {}
    for ordinal in (1, 2):
        source = tmp_path / f"source-g{ordinal}"
        manifests[ordinal] = _generation(source, f"g{ordinal}", ordinal)
        store.publish_generation(source, f"g{ordinal}")
    store.publish_latest(
        {
            "generation_id": "g2",
            "generation_manifest_hash": payload_hash(manifests[2]),
        }
    )

    with pytest.raises(LatestConflictError, match="latest"):
        store.delete_generation("g2")
    rollback_latest(store, "g1", expected_current_generation_id="g2")
    assert store.read_latest()["generation_id"] == "g1"
    store.delete_generation("g2")
    assert store.list_generation_ids() == ["g1"]


def test_bundle_rejects_single_file_over_configured_limit(tmp_path):
    source = tmp_path / "source"
    _generation(source)
    oversized = source / "raw" / "oversized.bin"
    oversized.write_bytes(b"x" * 2048)
    manifest_path = source / GENERATION_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_checksums"]["raw/oversized.bin"] = file_hash(oversized)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseAssetTooLarge, match="oversized.bin"):
        build_generation_bundle(
            source,
            tmp_path / "bundle",
            "g1",
            max_asset_bytes=1024 * 1024 + 1024,
        )


def test_rehearsal_generation_is_small_valid_and_synthetic(tmp_path):
    destination = tmp_path / "rehearsal"

    manifest = build_rehearsal_generation(destination, "rehearsal-1")

    assert verify_generation(destination) == manifest
    assert manifest["qa"] == {"passed": True, "synthetic_fixture": True}
    assert set(manifest["artifact_checksums"]) == {
        "raw/rehearsal_source.json",
        "interim/rehearsal_checkpoint.json",
        "processed/rehearsal_result.json",
        "outputs/rehearsal.xlsx",
    }
    assert zipfile.is_zipfile(destination / "outputs" / "rehearsal.xlsx")


@pytest.mark.parametrize("delete_after", [False, True])
def test_release_rehearsal_uploads_restores_and_optionally_deletes(delete_after):
    client = MemoryReleaseClient()

    result = run_release_rehearsal(
        "atotyrj/politdata-pipeline",
        "rehearsal-2",
        client=client,
        delete_after_verification=delete_after,
    )

    assert result["status"] == "verified"
    assert result["source"] == "synthetic_fixture_only"
    assert result["latest_changed"] is False
    assert result["draft_deleted"] is delete_after
    release = client.get_release_by_tag("politdata-data-rehearsal-2")
    assert (release is None) is delete_after
    if release is not None:
        assert release["draft"] is True
        assert "rehearsal.xlsx" in {asset["name"] for asset in release["assets"]}
