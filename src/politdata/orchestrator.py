"""Unified, fail-safe orchestration for PolitData pipeline runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import json
import os
import re
import uuid

from .change_set import DEFAULT_CURRENT_CHANGE_SET_PATH
from .ingestion_runner import run_limited_organization_ingestion
from .report_discovery import DEFAULT_REFRESH_INTERVAL_DAYS
from .storage import LocalGenerationStore, payload_hash


RUN_MANIFEST_SCHEMA_VERSION = 1
DEFAULT_CONTROL_ROOT = Path("data/control")
DEFAULT_RUN_ROOT = Path("data/pipeline_runs")
DEFAULT_GENERATION_ROOT = Path("data/generations")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_AUTO_FULL_REPLACE_STAGE = object()


class RunMode(str, Enum):
    FULL_REPLACE = "full-replace"
    INCREMENTAL = "incremental"


class WriterLockError(RuntimeError):
    """Raised when another writer already owns the pipeline lock."""


class FullReplaceNotConfigured(RuntimeError):
    """Raised when no complete RAW-to-output stage runner is available."""


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _new_run_id():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp." + uuid.uuid4().hex)
    try:
        with temp.open("w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@dataclass(frozen=True)
class RunConfig:
    mode: RunMode | str
    organization_limit: int | None = None
    report_discovery_limit: int | None = None
    report_detail_limit: int | None = None
    report_refresh_interval_days: float = DEFAULT_REFRESH_INTERVAL_DAYS
    change_set_path: Path | str = DEFAULT_CURRENT_CHANGE_SET_PATH
    run_downstream: bool = True
    confirm_full_replace: bool = False
    publish: bool = False
    dry_run: bool = False
    run_id: str | None = None
    code_revision: str | None = None
    control_root: Path | str = DEFAULT_CONTROL_ROOT
    run_root: Path | str = DEFAULT_RUN_ROOT
    generation_root: Path | str = DEFAULT_GENERATION_ROOT

    def __post_init__(self):
        object.__setattr__(self, "mode", RunMode(self.mode))
        object.__setattr__(self, "change_set_path", Path(self.change_set_path))
        object.__setattr__(self, "control_root", Path(self.control_root))
        object.__setattr__(self, "run_root", Path(self.run_root))
        object.__setattr__(self, "generation_root", Path(self.generation_root))
        object.__setattr__(self, "run_id", self.run_id or _new_run_id())

        if not RUN_ID_PATTERN.fullmatch(self.run_id):
            raise ValueError("run_id contains unsafe characters.")
        if self.report_refresh_interval_days < 0:
            raise ValueError("report_refresh_interval_days must be non-negative.")
        for name in (
            "organization_limit",
            "report_discovery_limit",
            "report_detail_limit",
        ):
            value = getattr(self, name)
            if value is not None and int(value) <= 0:
                raise ValueError(f"{name} must be positive.")

        if self.mode is RunMode.INCREMENTAL:
            if self.organization_limit is None:
                raise ValueError("incremental mode requires organization_limit.")
            if self.confirm_full_replace:
                raise ValueError(
                    "confirm_full_replace is only valid in full-replace mode."
                )
            if self.publish:
                raise ValueError(
                    "incremental publication is not configured yet."
                )
        else:
            if not self.confirm_full_replace and not self.dry_run:
                raise ValueError(
                    "full-replace requires explicit confirm_full_replace."
                )
            if any(
                getattr(self, name) is not None
                for name in (
                    "organization_limit",
                    "report_discovery_limit",
                    "report_detail_limit",
                )
            ):
                raise ValueError("full-replace cannot use incremental limits.")

    def to_dict(self):
        payload = asdict(self)
        payload["mode"] = self.mode.value
        for name in (
            "change_set_path",
            "control_root",
            "run_root",
            "generation_root",
        ):
            payload[name] = str(payload[name])
        return payload


@dataclass(frozen=True)
class FullReplacePaths:
    run_dir: Path
    staging_dir: Path
    raw_dir: Path
    interim_dir: Path
    processed_dir: Path
    outputs_dir: Path

    def to_dict(self):
        return {name: str(value) for name, value in asdict(self).items()}


@dataclass
class GenerationManifest:
    generation_id: str
    run_id: str
    mode: str
    started_at_utc: str
    completed_at_utc: str
    status: str
    code_revision: str | None = None
    source_watermark: object = None
    row_counts: dict = field(default_factory=dict)
    artifact_checksums: dict = field(default_factory=dict)
    artifact_locations: dict = field(default_factory=dict)
    qa: object = None
    schema_version: int = RUN_MANIFEST_SCHEMA_VERSION

    def to_dict(self):
        return asdict(self)


class WriterLock:
    def __init__(self, path, *, run_id, mode):
        self.path = Path(path)
        self.run_id = run_id
        self.mode = mode
        self._owned = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "run_id": self.run_id,
                "mode": self.mode,
                "acquired_at_utc": _utc_now_iso(),
                "process_id": os.getpid(),
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise WriterLockError(
                f"Pipeline writer lock already exists: {self.path}"
            ) from error
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._owned = True
        return self

    def release(self):
        if not self._owned:
            return
        try:
            lock = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lock = {}
        if lock.get("run_id") == self.run_id:
            self.path.unlink(missing_ok=True)
        self._owned = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, _error_type, _error, _traceback):
        self.release()


def build_run_plan(config):
    """Return paths and guards without performing writes or online work."""

    config = config if isinstance(config, RunConfig) else RunConfig(**config)
    paths = None
    if config.mode is RunMode.FULL_REPLACE:
        paths = _full_replace_paths(config).to_dict()
    return {
        "status": "planned",
        "writes": 0,
        "network_requests": 0,
        "config": config.to_dict(),
        "full_replace_paths": paths,
    }


def _full_replace_paths(config):
    run_dir = config.run_root / config.run_id
    staging_dir = run_dir / "staging"
    return FullReplacePaths(
        run_dir=run_dir,
        staging_dir=staging_dir,
        raw_dir=staging_dir / "raw",
        interim_dir=staging_dir / "interim",
        processed_dir=staging_dir / "processed",
        outputs_dir=staging_dir / "outputs",
    )


def _prepare_full_replace_staging(paths):
    if paths.run_dir.exists():
        raise FileExistsError(paths.run_dir)
    for path in (
        paths.raw_dir,
        paths.interim_dir,
        paths.processed_dir,
        paths.outputs_dir,
    ):
        path.mkdir(parents=True, exist_ok=False)


def _qa_passed(value):
    if value is True:
        return True
    if isinstance(value, dict):
        return value.get("passed") is True or value.get("status") == "passed"
    return False


def _run_full_replace(config, *, stage_runner, started_at_utc, generation_store):
    if stage_runner is None:
        raise FullReplaceNotConfigured(
            "No complete full-replace RAW-to-output stage runner is configured."
        )

    paths = _full_replace_paths(config)
    _prepare_full_replace_staging(paths)
    stage_result = stage_runner(config=config, paths=paths)
    if not isinstance(stage_result, dict):
        raise TypeError("full-replace stage runner must return a dictionary.")
    if stage_result.get("status") != "completed":
        raise RuntimeError("full-replace stage runner did not complete.")
    if not _qa_passed(stage_result.get("qa")):
        raise RuntimeError("full-replace QA did not pass.")

    completed_at = _utc_now_iso()
    generation_id = config.run_id
    manifest = GenerationManifest(
        generation_id=generation_id,
        run_id=config.run_id,
        mode=config.mode.value,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at,
        status="validated",
        code_revision=config.code_revision,
        source_watermark=stage_result.get("source_watermark"),
        row_counts=dict(stage_result.get("row_counts") or {}),
        artifact_checksums=dict(stage_result.get("artifact_checksums") or {}),
        artifact_locations=dict(stage_result.get("artifact_locations") or {}),
        qa=stage_result.get("qa"),
    )
    _atomic_json(paths.staging_dir / "generation_manifest.json", manifest.to_dict())
    generation_location = generation_store.publish_generation(
        paths.staging_dir, generation_id
    )

    latest_location = None
    if config.publish:
        manifest_payload = manifest.to_dict()
        latest_location = generation_store.publish_latest(
            {
                "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
                "generation_id": generation_id,
                "generation_path": generation_location,
                "generation_manifest_hash": payload_hash(manifest_payload),
                "published_at_utc": completed_at,
            }
        )

    return {
        "status": "published" if config.publish else "validated",
        "generation_id": generation_id,
        "generation_path": generation_location,
        "latest_path": latest_location,
        "stage_result": stage_result,
    }


def _run_incremental(config, *, incremental_runner):
    return incremental_runner(
        organization_limit=int(config.organization_limit),
        change_set_path=config.change_set_path,
        run_downstream=config.run_downstream,
        report_limit=config.report_detail_limit,
        report_discovery_limit=config.report_discovery_limit,
        report_refresh_interval_days=config.report_refresh_interval_days,
    )


def run_pipeline(
    config,
    *,
    incremental_runner=run_limited_organization_ingestion,
    full_replace_stage_runner=_AUTO_FULL_REPLACE_STAGE,
    generation_store=None,
):
    """Execute one guarded pipeline run through a shared lifecycle."""

    config = config if isinstance(config, RunConfig) else RunConfig(**config)
    if config.dry_run:
        return build_run_plan(config)

    if generation_store is None:
        generation_store = LocalGenerationStore(
            config.generation_root,
            config.control_root / "latest.json",
        )

    lock_path = config.control_root / "writer.lock"
    journal_path = config.control_root / "runs" / f"{config.run_id}.json"
    started_at = _utc_now_iso()
    journal = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": config.run_id,
        "mode": config.mode.value,
        "status": "running",
        "started_at_utc": started_at,
        "finished_at_utc": None,
        "config": config.to_dict(),
    }

    with WriterLock(lock_path, run_id=config.run_id, mode=config.mode.value):
        _atomic_json(journal_path, journal)
        try:
            if config.mode is RunMode.INCREMENTAL:
                result = _run_incremental(
                    config,
                    incremental_runner=incremental_runner,
                )
            else:
                if full_replace_stage_runner is _AUTO_FULL_REPLACE_STAGE:
                    from .full_rebuild import run_full_replace_stage
                    full_replace_stage_runner = run_full_replace_stage
                result = _run_full_replace(
                    config,
                    stage_runner=full_replace_stage_runner,
                    started_at_utc=started_at,
                    generation_store=generation_store,
                )
        except Exception as error:
            journal.update(
                {
                    "status": "failed",
                    "finished_at_utc": _utc_now_iso(),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            _atomic_json(journal_path, journal)
            raise

        journal.update(
            {
                "status": "completed",
                "finished_at_utc": _utc_now_iso(),
                "result": result,
            }
        )
        _atomic_json(journal_path, journal)

    return {
        "run_id": config.run_id,
        "mode": config.mode.value,
        "status": "completed",
        "journal_path": str(journal_path),
        "result": result,
    }
