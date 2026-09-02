"""Immutable generation storage contracts and a local filesystem adapter."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Protocol
import uuid


GENERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class GenerationIntegrityError(RuntimeError):
    """Raised when an immutable generation does not match its manifest."""


class LatestConflictError(RuntimeError):
    """Raised when a compare-and-swap latest publication loses a race."""


class GenerationStore(Protocol):
    @property
    def latest_location(self) -> str: ...

    def publish_generation(self, source_dir, generation_id) -> str: ...

    def publish_latest(self, pointer, *, expected_generation_id=None) -> str: ...

    def read_latest(self) -> dict | None: ...

    def restore_generation(
        self, generation_id, destination, *, expected_manifest_hash=None
    ) -> str: ...

    def restore_latest(self, destination) -> str: ...


def payload_hash(payload):
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_generation(path, *, expected_manifest_hash=None):
    """Verify a generation manifest and every declared artifact checksum."""

    path = Path(path)
    manifest_path = path / "generation_manifest.json"
    if not manifest_path.is_file():
        raise GenerationIntegrityError(f"Generation manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GenerationIntegrityError("Generation manifest is not valid JSON.") from error
    if expected_manifest_hash and payload_hash(manifest) != expected_manifest_hash:
        raise GenerationIntegrityError("Generation manifest hash mismatch.")
    checksums = manifest.get("artifact_checksums")
    if not isinstance(checksums, dict):
        raise GenerationIntegrityError("Generation manifest has no artifact checksums.")
    for relative, expected in checksums.items():
        candidate = (path / relative).resolve()
        try:
            candidate.relative_to(path.resolve())
        except ValueError as error:
            raise GenerationIntegrityError(
                f"Artifact path escapes generation: {relative}"
            ) from error
        if not candidate.is_file():
            raise GenerationIntegrityError(f"Generation artifact not found: {relative}")
        actual = file_hash(candidate)
        if actual != expected:
            raise GenerationIntegrityError(
                f"Generation artifact checksum mismatch: {relative}"
            )
    return manifest


class LocalGenerationStore:
    """Filesystem implementation suitable for local and mounted storage."""

    def __init__(self, generation_root, latest_path):
        self.generation_root = Path(generation_root)
        self.latest_path = Path(latest_path)

    @property
    def latest_location(self):
        return str(self.latest_path)

    def _generation_path(self, generation_id):
        generation_id = str(generation_id)
        if not GENERATION_ID_PATTERN.fullmatch(generation_id):
            raise ValueError("Unsafe generation ID.")
        return self.generation_root / generation_id

    def publish_generation(self, source_dir, generation_id):
        source_dir = Path(source_dir)
        destination = self._generation_path(generation_id)
        if destination.exists():
            raise FileExistsError(destination)
        manifest = verify_generation(source_dir)
        if str(manifest.get("generation_id")) != str(generation_id):
            raise GenerationIntegrityError("Generation ID does not match manifest.")
        self.generation_root.mkdir(parents=True, exist_ok=True)
        os.replace(source_dir, destination)
        return str(destination)

    def publish_latest(self, pointer, *, expected_generation_id=None):
        current = self.read_latest()
        current_id = current.get("generation_id") if current else None
        if expected_generation_id is not None and current_id != expected_generation_id:
            raise LatestConflictError(
                f"Latest generation changed: expected={expected_generation_id}, actual={current_id}"
            )
        _atomic_json(self.latest_path, pointer)
        return self.latest_location

    def read_latest(self):
        if not self.latest_path.exists():
            return None
        return json.loads(self.latest_path.read_text(encoding="utf-8"))

    def restore_generation(
        self, generation_id, destination, *, expected_manifest_hash=None
    ):
        source = self._generation_path(generation_id)
        if not source.is_dir():
            raise FileNotFoundError(source)
        verify_generation(source, expected_manifest_hash=expected_manifest_hash)
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".restore.{uuid.uuid4().hex[:8]}"
        try:
            shutil.copytree(source, temporary)
            verify_generation(temporary, expected_manifest_hash=expected_manifest_hash)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return str(destination)

    def restore_latest(self, destination):
        pointer = self.read_latest()
        if pointer is None:
            raise FileNotFoundError(self.latest_path)
        generation_id = pointer.get("generation_id")
        expected_hash = pointer.get("generation_manifest_hash")
        if not generation_id or not expected_hash:
            raise GenerationIntegrityError("Latest pointer is incomplete.")
        return self.restore_generation(
            generation_id,
            destination,
            expected_manifest_hash=expected_hash,
        )
