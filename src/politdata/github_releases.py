"""GitHub Releases storage for immutable PolitData generations."""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from urllib.parse import quote
import uuid
import zipfile

import requests

from .storage import (
    GENERATION_ID_PATTERN,
    GenerationIntegrityError,
    LatestConflictError,
    atomic_json,
    file_hash,
    payload_hash,
    verify_generation,
)


GITHUB_API_VERSION = "2026-03-10"
DEFAULT_MAX_RELEASE_ASSET_BYTES = 1_900_000_000
RELEASE_ASSET_HARD_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
BUNDLE_INDEX_NAME = "generation_bundle_index.json"
GENERATION_MANIFEST_NAME = "generation_manifest.json"
GENERATION_POINTER_NAME = "generation_pointer.json"
PUBLIC_CATALOG_NAME = "public_artifacts.json"


class GitHubReleaseError(RuntimeError):
    """Raised when GitHub rejects or corrupts a release operation."""


class ReleaseAssetTooLarge(GitHubReleaseError):
    """Raised when one generation file cannot fit in a release asset."""


def _safe_generation_id(value):
    value = str(value)
    if not GENERATION_ID_PATTERN.fullmatch(value):
        raise ValueError("Unsafe generation ID.")
    return value


def _asset_digest(asset):
    value = str(asset.get("digest") or "")
    if not value.startswith("sha256:"):
        raise GenerationIntegrityError(
            f"GitHub release asset has no SHA-256 digest: {asset.get('name')}"
        )
    return value.split(":", 1)[1]


class GitHubReleaseClient:
    """Small REST client whose token is never included in errors or payloads."""

    def __init__(
        self,
        repository,
        token,
        *,
        session=None,
        api_url="https://api.github.com",
    ):
        try:
            owner, repo = str(repository).split("/", 1)
        except ValueError as error:
            raise ValueError("repository must use OWNER/REPO format.") from error
        if not owner or not repo or "/" in repo:
            raise ValueError("repository must use OWNER/REPO format.")
        if not token:
            raise ValueError("A GitHub token is required.")
        self.owner = owner
        self.repo = repo
        self.repository = f"{owner}/{repo}"
        self.session = session or requests.Session()
        self.api_url = api_url.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "politdata-pipeline",
        }

    @property
    def releases_url(self):
        return f"{self.api_url}/repos/{self.owner}/{self.repo}/releases"

    def _request(self, method, url, *, expected=(200,), **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}) or {})
        timeout = kwargs.pop("timeout", 120)
        response = self.session.request(
            method,
            url,
            headers=headers,
            timeout=timeout,
            **kwargs,
        )
        if response.status_code not in expected:
            detail = (getattr(response, "text", "") or "")[:500]
            raise GitHubReleaseError(
                f"GitHub API {method} {url} returned {response.status_code}: {detail}"
            )
        return response

    def get_release_by_tag(self, tag):
        url = f"{self.releases_url}/tags/{quote(str(tag), safe='')}"
        response = self.session.request(
            "GET",
            url,
            headers=self.headers,
            timeout=120,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            detail = (getattr(response, "text", "") or "")[:500]
            raise GitHubReleaseError(
                f"GitHub API GET release by tag returned {response.status_code}: {detail}"
            )
        return response.json()

    def get_release(self, release_id):
        response = self._request(
            "GET",
            f"{self.releases_url}/{int(release_id)}",
        )
        return response.json()

    def get_latest_release(self):
        response = self.session.request(
            "GET",
            f"{self.releases_url}/latest",
            headers=self.headers,
            timeout=120,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            detail = (getattr(response, "text", "") or "")[:500]
            raise GitHubReleaseError(
                f"GitHub API GET latest release returned {response.status_code}: {detail}"
            )
        return response.json()

    def list_releases(self):
        releases = []
        page = 1
        while True:
            response = self._request(
                "GET",
                self.releases_url,
                params={"per_page": 100, "page": page},
            )
            batch = response.json()
            releases.extend(batch)
            if len(batch) < 100:
                return releases
            page += 1

    def create_release(self, *, tag, name, body, target_commitish="main"):
        response = self._request(
            "POST",
            self.releases_url,
            expected=(201,),
            json={
                "tag_name": tag,
                "target_commitish": target_commitish,
                "name": name,
                "body": body,
                "draft": True,
                "prerelease": False,
                "make_latest": "false",
            },
        )
        return response.json()

    def update_release(self, release_id, **payload):
        response = self._request(
            "PATCH",
            f"{self.releases_url}/{int(release_id)}",
            json=payload,
        )
        return response.json()

    def delete_release(self, release_id):
        self._request(
            "DELETE",
            f"{self.releases_url}/{int(release_id)}",
            expected=(204,),
        )

    def delete_tag(self, tag):
        url = (
            f"{self.api_url}/repos/{self.owner}/{self.repo}/git/refs/tags/"
            f"{quote(str(tag), safe='')}"
        )
        response = self.session.request(
            "DELETE",
            url,
            headers=self.headers,
            timeout=120,
        )
        if response.status_code not in (204, 404):
            detail = (getattr(response, "text", "") or "")[:500]
            raise GitHubReleaseError(
                f"GitHub API DELETE tag returned {response.status_code}: {detail}"
            )

    def upload_asset(self, release, path, *, name=None, content_type=None):
        path = Path(path)
        asset_name = name or path.name
        upload_url = str(release["upload_url"]).split("{", 1)[0]
        content_type = content_type or mimetypes.guess_type(asset_name)[0]
        content_type = content_type or "application/octet-stream"
        with path.open("rb") as stream:
            response = self._request(
                "POST",
                upload_url,
                expected=(201,),
                params={"name": asset_name},
                headers={"Content-Type": content_type},
                data=stream,
                timeout=7200,
            )
        asset = response.json()
        expected = file_hash(path)
        if _asset_digest(asset) != expected:
            raise GenerationIntegrityError(
                f"GitHub asset digest mismatch after upload: {asset_name}"
            )
        return asset

    def download_asset(self, asset, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self._request(
            "GET",
            asset["url"],
            headers={"Accept": "application/octet-stream"},
            stream=True,
            timeout=7200,
        )
        with destination.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    stream.write(chunk)
        if file_hash(destination) != _asset_digest(asset):
            raise GenerationIntegrityError(
                f"GitHub asset checksum mismatch: {asset.get('name')}"
            )
        return str(destination)


def _generation_files(source_dir):
    source_dir = Path(source_dir).resolve()
    files = []
    for directory, directory_names, file_names in os.walk(source_dir):
        directory_names.sort()
        file_names.sort()
        root = Path(directory)
        for name in directory_names:
            candidate = root / name
            if candidate.is_symlink():
                raise GenerationIntegrityError(
                    f"Generation contains a symbolic link: {candidate}"
                )
        for name in file_names:
            candidate = root / name
            if candidate.is_symlink():
                raise GenerationIntegrityError(
                    f"Generation contains a symbolic link: {candidate}"
                )
            relative = candidate.relative_to(source_dir).as_posix()
            files.append((relative, candidate, candidate.stat().st_size))
    return sorted(files, key=lambda item: item[0])


def _bundle_groups(files, max_asset_bytes):
    allowance = max_asset_bytes - 1024 * 1024
    if allowance <= 0:
        raise ValueError("max_asset_bytes is too small.")
    by_root = {}
    for relative, path, size in files:
        if size > allowance:
            raise ReleaseAssetTooLarge(
                f"Generation file exceeds release asset limit: {relative} ({size} bytes)"
            )
        root = PurePosixPath(relative).parts[0] if "/" in relative else "root"
        by_root.setdefault(root, []).append((relative, path, size))

    groups = []
    for root in sorted(by_root):
        part = []
        total = 0
        part_number = 1
        for item in by_root[root]:
            if part and total + item[2] > allowance:
                groups.append((root, part_number, part))
                part_number += 1
                part = []
                total = 0
            part.append(item)
            total += item[2]
        if part:
            groups.append((root, part_number, part))
    return groups


def build_generation_bundle(
    source_dir,
    destination,
    generation_id,
    *,
    max_asset_bytes=DEFAULT_MAX_RELEASE_ASSET_BYTES,
):
    """Create deterministic, restorable release assets for one generation."""

    generation_id = _safe_generation_id(generation_id)
    source_dir = Path(source_dir)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = verify_generation(source_dir)
    if str(manifest.get("generation_id")) != generation_id:
        raise GenerationIntegrityError("Generation ID does not match manifest.")
    files = _generation_files(source_dir)
    groups = _bundle_groups(files, int(max_asset_bytes))
    assets = []
    for root, part_number, members in groups:
        safe_root = "".join(
            character if character.isalnum() or character in "._-" else "-"
            for character in root
        )
        name = f"generation-{safe_root}-{part_number:04d}.zip"
        path = destination / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for relative, member, _size in members:
                archive.write(member, arcname=relative)
        size = path.stat().st_size
        if size >= RELEASE_ASSET_HARD_LIMIT_BYTES or size > int(max_asset_bytes):
            raise ReleaseAssetTooLarge(
                f"Generation bundle exceeds release asset limit: {name} ({size} bytes)"
            )
        assets.append(
            {
                "name": name,
                "size": size,
                "sha256": file_hash(path),
                "files": [relative for relative, _path, _size in members],
            }
        )
    public_assets = []
    used_names = {
        BUNDLE_INDEX_NAME,
        GENERATION_MANIFEST_NAME,
        GENERATION_POINTER_NAME,
        PUBLIC_CATALOG_NAME,
        *(asset["name"] for asset in assets),
    }
    for relative, path, size in files:
        if not relative.startswith("outputs/") or path.suffix.lower() != ".xlsx":
            continue
        name = path.name
        if name in used_names:
            raise GenerationIntegrityError(
                f"Duplicate public release asset name: {name}"
            )
        if size >= RELEASE_ASSET_HARD_LIMIT_BYTES:
            raise ReleaseAssetTooLarge(
                f"Excel workbook exceeds release asset limit: {relative} ({size} bytes)"
            )
        used_names.add(name)
        public_assets.append(
            {
                "source_path": relative,
                "name": name,
                "size": size,
                "sha256": file_hash(path),
            }
        )
    index = {
        "schema_version": 1,
        "generation_id": generation_id,
        "generation_manifest_hash": payload_hash(manifest),
        "bundle_assets": assets,
        "public_assets": public_assets,
    }
    atomic_json(destination / BUNDLE_INDEX_NAME, index)
    shutil.copy2(source_dir / GENERATION_MANIFEST_NAME, destination / GENERATION_MANIFEST_NAME)
    return index


def _build_indexed_bundle_asset(source_dir, destination, item):
    """Recreate one missing ZIP from a previously verified bundle index."""

    source_dir = Path(source_dir).resolve()
    destination = Path(destination)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative in item["files"]:
            pure = PurePosixPath(relative)
            if pure.is_absolute() or ".." in pure.parts:
                raise GenerationIntegrityError(
                    f"Unsafe path in generation bundle index: {relative}"
                )
            source = (source_dir / Path(*pure.parts)).resolve()
            try:
                source.relative_to(source_dir)
            except ValueError as error:
                raise GenerationIntegrityError(
                    f"Bundle-index path escapes generation: {relative}"
                ) from error
            if not source.is_file() or source.is_symlink():
                raise GenerationIntegrityError(
                    f"Bundle-index artifact is unavailable: {relative}"
                )
            archive.write(source, arcname=relative)
    if destination.stat().st_size != int(item["size"]):
        raise GenerationIntegrityError(
            f"Recreated bundle size mismatch: {item['name']}"
        )
    if file_hash(destination) != item["sha256"]:
        raise GenerationIntegrityError(
            f"Recreated bundle checksum mismatch: {item['name']}"
        )
    return destination


def _asset_map(release):
    return {asset["name"]: asset for asset in release.get("assets") or []}


def _safe_extract_zip(path, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            relative = PurePosixPath(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise GenerationIntegrityError(
                    f"Unsafe path in generation bundle: {info.filename}"
                )
            target = (destination / Path(*relative.parts)).resolve()
            try:
                target.relative_to(destination)
            except ValueError as error:
                raise GenerationIntegrityError(
                    f"Generation bundle path escapes destination: {info.filename}"
                ) from error
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


class GitHubReleaseGenerationStore:
    """GenerationStore implementation backed by one GitHub release per run."""

    def __init__(
        self,
        repository,
        token=None,
        *,
        client=None,
        target_commitish="main",
        tag_prefix="politdata-data-",
        max_asset_bytes=DEFAULT_MAX_RELEASE_ASSET_BYTES,
    ):
        self.repository = str(repository)
        self.client = client or GitHubReleaseClient(
            repository,
            token or os.environ.get("GITHUB_TOKEN"),
        )
        self.target_commitish = target_commitish
        self.tag_prefix = tag_prefix
        self.max_asset_bytes = int(max_asset_bytes)
        self._release_ids = {}

    @property
    def latest_location(self):
        return f"https://github.com/{self.repository}/releases/latest"

    def _tag(self, generation_id):
        return self.tag_prefix + _safe_generation_id(generation_id)

    def _generation_id(self, release):
        tag = str(release.get("tag_name") or "")
        if not tag.startswith(self.tag_prefix):
            raise GenerationIntegrityError(
                f"Release is not a PolitData generation: {tag}"
            )
        return _safe_generation_id(tag[len(self.tag_prefix):])

    def _find_release(self, generation_id):
        generation_id = _safe_generation_id(generation_id)
        known_id = self._release_ids.get(generation_id)
        if known_id is not None:
            release = self.client.get_release(known_id)
            if release is not None:
                return release
        tag = self._tag(generation_id)
        release = self.client.get_release_by_tag(tag)
        if release is None:
            release = next(
                (
                    candidate
                    for candidate in self.client.list_releases()
                    if str(candidate.get("tag_name") or "") == tag
                ),
                None,
            )
        if release is not None:
            self._release_ids[generation_id] = release["id"]
        return release

    def _release(self, generation_id):
        release = self._find_release(generation_id)
        if release is None:
            raise FileNotFoundError(self.generation_location(generation_id))
        return release

    def generation_location(self, generation_id):
        tag = self._tag(generation_id)
        return f"https://github.com/{self.repository}/releases/tag/{quote(tag, safe='')}"

    def list_generation_ids(self):
        ids = []
        for release in self.client.list_releases():
            tag = str(release.get("tag_name") or "")
            if tag.startswith(self.tag_prefix):
                ids.append(_safe_generation_id(tag[len(self.tag_prefix):]))
        return sorted(set(ids))

    def _download_json_asset(self, release, name, destination):
        asset = _asset_map(release).get(name)
        if asset is None:
            raise GenerationIntegrityError(f"Release asset not found: {name}")
        self.client.download_asset(asset, destination)
        try:
            return json.loads(Path(destination).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise GenerationIntegrityError(
                f"Release asset is not valid JSON: {name}"
            ) from error

    def read_generation_manifest(
        self, generation_id, *, expected_manifest_hash=None
    ):
        release = self._release(generation_id)
        with tempfile.TemporaryDirectory(prefix="politdata-release-read-") as temporary:
            root = Path(temporary)
            index = self._download_json_asset(
                release, BUNDLE_INDEX_NAME, root / BUNDLE_INDEX_NAME
            )
            manifest = self._download_json_asset(
                release, GENERATION_MANIFEST_NAME, root / GENERATION_MANIFEST_NAME
            )
        actual_hash = payload_hash(manifest)
        if actual_hash != index.get("generation_manifest_hash"):
            raise GenerationIntegrityError("Release generation manifest hash mismatch.")
        if expected_manifest_hash and actual_hash != expected_manifest_hash:
            raise GenerationIntegrityError("Generation manifest hash mismatch.")
        if str(manifest.get("generation_id")) != str(generation_id):
            raise GenerationIntegrityError("Generation ID does not match release.")
        return manifest

    def publish_generation(self, source_dir, generation_id, *, resume_draft=False):
        generation_id = _safe_generation_id(generation_id)
        tag = self._tag(generation_id)
        existing_release = self._find_release(generation_id)
        if existing_release is not None and not (
            resume_draft and existing_release.get("draft") is True
        ):
            raise FileExistsError(self.generation_location(generation_id))
        with tempfile.TemporaryDirectory(prefix="politdata-release-build-") as temporary:
            bundle_root = Path(temporary)
            source_root = Path(source_dir)
            if existing_release is None:
                index = build_generation_bundle(
                    source_root,
                    bundle_root,
                    generation_id,
                    max_asset_bytes=self.max_asset_bytes,
                )
            else:
                manifest = verify_generation(source_root)
                index = self._download_json_asset(
                    existing_release,
                    BUNDLE_INDEX_NAME,
                    bundle_root / BUNDLE_INDEX_NAME,
                )
                if index.get("generation_id") != generation_id:
                    raise GenerationIntegrityError(
                        "Draft release bundle generation ID mismatch."
                    )
                if index.get("generation_manifest_hash") != payload_hash(manifest):
                    raise GenerationIntegrityError(
                        "Draft release manifest differs from local generation."
                    )
                shutil.copy2(
                    source_root / GENERATION_MANIFEST_NAME,
                    bundle_root / GENERATION_MANIFEST_NAME,
                )
            release = existing_release or self.client.create_release(
                tag=tag,
                name=f"PolitData generation {generation_id}",
                body=(
                    "Immutable PolitData generation. Assets are checksum-verified; "
                    "the release remains draft until QA-gated publication."
                ),
                target_commitish=self.target_commitish,
            )
            created_release = existing_release is None
            self._release_ids[generation_id] = release["id"]
            try:
                public_catalog = {
                    "schema_version": 1,
                    "generation_id": generation_id,
                    "release_tag": tag,
                    "workbooks": [],
                }
                for item in index["public_assets"]:
                    public_catalog["workbooks"].append(
                        {
                            **item,
                            "download_url": (
                                f"https://github.com/{self.repository}/releases/download/"
                                f"{quote(tag, safe='')}/{quote(item['name'], safe='')}"
                            ),
                        }
                    )
                atomic_json(bundle_root / PUBLIC_CATALOG_NAME, public_catalog)
                uploaded = _asset_map(release)
                fixed_assets = (
                    (GENERATION_MANIFEST_NAME, bundle_root / GENERATION_MANIFEST_NAME),
                    (BUNDLE_INDEX_NAME, bundle_root / BUNDLE_INDEX_NAME),
                    (PUBLIC_CATALOG_NAME, bundle_root / PUBLIC_CATALOG_NAME),
                )
                for name, path in fixed_assets:
                    if name in uploaded:
                        if _asset_digest(uploaded[name]) != file_hash(path):
                            raise GenerationIntegrityError(
                                f"Existing draft asset differs from local bundle: {name}"
                            )
                        continue
                    asset = self.client.upload_asset(release, path, name=name)
                    uploaded[name] = asset
                for item in index["bundle_assets"]:
                    name = item["name"]
                    if name in uploaded:
                        if _asset_digest(uploaded[name]) != item["sha256"]:
                            raise GenerationIntegrityError(
                                f"Existing draft bundle differs from index: {name}"
                            )
                        continue
                    path = bundle_root / name
                    if not path.exists():
                        _build_indexed_bundle_asset(source_root, path, item)
                    asset = self.client.upload_asset(release, path, name=name)
                    uploaded[name] = asset
                for item in index["public_assets"]:
                    name = item["name"]
                    path = source_root / item["source_path"]
                    if name in uploaded:
                        if _asset_digest(uploaded[name]) != file_hash(path):
                            raise GenerationIntegrityError(
                                f"Existing draft asset differs from local workbook: {name}"
                            )
                        continue
                    asset = self.client.upload_asset(release, path, name=name)
                    uploaded[name] = asset
            except Exception:
                if created_release:
                    self.client.delete_release(release["id"])
                    self._release_ids.pop(generation_id, None)
                raise
        return self.generation_location(generation_id)

    def _static_pointer(self, generation_id):
        manifest = self.read_generation_manifest(generation_id)
        return {
            "schema_version": 1,
            "generation_id": str(generation_id),
            "generation_path": self.generation_location(generation_id),
            "generation_manifest_hash": payload_hash(manifest),
        }

    def publish_latest(self, pointer, *, expected_generation_id=None):
        current = self.read_latest()
        current_id = current.get("generation_id") if current else None
        if expected_generation_id is not None and current_id != expected_generation_id:
            raise LatestConflictError(
                f"Latest generation changed: expected={expected_generation_id}, actual={current_id}"
            )
        generation_id = _safe_generation_id(pointer.get("generation_id"))
        release = self._release(generation_id)
        static_pointer = self._static_pointer(generation_id)
        supplied_hash = pointer.get("generation_manifest_hash")
        if supplied_hash and supplied_hash != static_pointer["generation_manifest_hash"]:
            raise GenerationIntegrityError("Published generation manifest hash mismatch.")
        assets = _asset_map(release)
        if GENERATION_POINTER_NAME not in assets:
            with tempfile.TemporaryDirectory(prefix="politdata-pointer-") as temporary:
                path = Path(temporary) / GENERATION_POINTER_NAME
                atomic_json(path, static_pointer)
                self.client.upload_asset(release, path, name=GENERATION_POINTER_NAME)
        else:
            with tempfile.TemporaryDirectory(prefix="politdata-pointer-read-") as temporary:
                existing = self._download_json_asset(
                    release,
                    GENERATION_POINTER_NAME,
                    Path(temporary) / GENERATION_POINTER_NAME,
                )
            if existing != static_pointer:
                raise GenerationIntegrityError("Release generation pointer mismatch.")
        self.client.update_release(
            release["id"],
            draft=False,
            prerelease=False,
            make_latest="true",
        )
        return self.latest_location

    def read_latest(self):
        release = self.client.get_latest_release()
        if release is None:
            return None
        generation_id = self._generation_id(release)
        with tempfile.TemporaryDirectory(prefix="politdata-latest-") as temporary:
            pointer = self._download_json_asset(
                release,
                GENERATION_POINTER_NAME,
                Path(temporary) / GENERATION_POINTER_NAME,
            )
        if pointer.get("generation_id") != generation_id:
            raise GenerationIntegrityError("Latest release pointer is inconsistent.")
        return pointer

    def restore_generation(
        self, generation_id, destination, *, expected_manifest_hash=None
    ):
        generation_id = _safe_generation_id(generation_id)
        release = self._release(generation_id)
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".restore.{uuid.uuid4().hex[:8]}"
        downloads = temporary.parent / f".downloads.{uuid.uuid4().hex[:8]}"
        temporary.mkdir(parents=True)
        downloads.mkdir(parents=True)
        try:
            index = self._download_json_asset(
                release, BUNDLE_INDEX_NAME, downloads / BUNDLE_INDEX_NAME
            )
            if index.get("generation_id") != generation_id:
                raise GenerationIntegrityError("Release bundle generation ID mismatch.")
            assets = _asset_map(release)
            for item in index.get("bundle_assets") or []:
                asset = assets.get(item["name"])
                if asset is None:
                    raise GenerationIntegrityError(
                        f"Generation bundle asset not found: {item['name']}"
                    )
                if _asset_digest(asset) != item["sha256"]:
                    raise GenerationIntegrityError(
                        f"Generation bundle asset digest mismatch: {item['name']}"
                    )
                path = downloads / item["name"]
                self.client.download_asset(asset, path)
                _safe_extract_zip(path, temporary)
            manifest = verify_generation(
                temporary,
                expected_manifest_hash=(
                    expected_manifest_hash
                    or index.get("generation_manifest_hash")
                ),
            )
            if str(manifest.get("generation_id")) != generation_id:
                raise GenerationIntegrityError("Restored generation ID mismatch.")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
            if downloads.exists():
                shutil.rmtree(downloads)
        return str(destination)

    def restore_latest(self, destination):
        pointer = self.read_latest()
        if pointer is None:
            raise FileNotFoundError(self.latest_location)
        return self.restore_generation(
            pointer["generation_id"],
            destination,
            expected_manifest_hash=pointer["generation_manifest_hash"],
        )

    def delete_generation(
        self, generation_id, *, expected_manifest_hash=None
    ):
        generation_id = _safe_generation_id(generation_id)
        current = self.read_latest()
        current_id = current.get("generation_id") if current else None
        if current_id == generation_id:
            raise LatestConflictError(
                f"Refusing to delete latest generation: {generation_id}"
            )
        self.read_generation_manifest(
            generation_id,
            expected_manifest_hash=expected_manifest_hash,
        )
        release = self._release(generation_id)
        self.client.delete_release(release["id"])
        self.client.delete_tag(self._tag(generation_id))
        self._release_ids.pop(generation_id, None)
        return self.generation_location(generation_id)
