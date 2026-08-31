
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

import pandas as pd

from .api import (
    fetch_all_parties,
    fetch_party_account,
)

from .discovery import (
    build_organization_manifest,
    compare_manifests,
    save_discovery_snapshot,
    save_committed_manifest,
)

from .change_detection import (
    classify_record_change,
    organization_content_hash,
)

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    create_change_set,
    organization_changes_from_sync_log,
    save_change_set,
)

from .refresh import (
    DEFAULT_REFRESH_STATE_PATH,
    initialize_refresh_state,
    select_rolling_refresh_candidates,
    update_refresh_state,
)


DEFAULT_COMMITTED_MANIFEST = Path(
    "data/interim/manifests/"
    "organization_manifest_committed.parquet"
)

DEFAULT_CURRENT_RAW_DIR = Path(
    "data/raw/party_accounts"
)

DEFAULT_VERSION_DIR = Path(
    "data/raw/party_account_versions"
)

DEFAULT_LOG_DIR = Path(
    "logs/sync_runs"
)


def _write_json(path, data):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temp_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    temp_path.replace(path)


def _read_json(path):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def _archive_baseline_if_needed(
    organization_id,
    current_raw_path,
    version_dir,
):
    """
    Preserve the pre-versioning RAW state once.
    """

    current_raw_path = Path(
        current_raw_path
    )

    if not current_raw_path.exists():
        return None

    org_version_dir = (
        Path(version_dir)
        / organization_id
    )

    org_version_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    baseline_path = (
        org_version_dir
        / "baseline_initial.json"
    )

    if not baseline_path.exists():
        shutil.copy2(
            current_raw_path,
            baseline_path,
        )

    return baseline_path


def _save_fetched_version(
    organization_id,
    api_response,
    retrieved_at,
    version_dir,
):
    """
    Save a historical version only when content
    is new or meaningfully changed.
    """

    org_version_dir = (
        Path(version_dir)
        / organization_id
    )

    org_version_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = retrieved_at.strftime(
        "%Y-%m-%d_%H-%M-%S_%f_UTC"
    )

    version_path = (
        org_version_dir
        / f"{timestamp}.json"
    )

    _write_json(
        version_path,
        api_response,
    )

    return version_path


def run_organization_sync(
    committed_manifest_path=DEFAULT_COMMITTED_MANIFEST,
    current_raw_dir=DEFAULT_CURRENT_RAW_DIR,
    version_dir=DEFAULT_VERSION_DIR,
    log_dir=DEFAULT_LOG_DIR,
    refresh_state_path=DEFAULT_REFRESH_STATE_PATH,
    enable_rolling_refresh=True,
    rolling_refresh_interval_days=7,
    rolling_refresh_limit=1400,
    candidate_limit=None,
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
):
    """
    Run one organization synchronization cycle.

    Discovery candidates:
    - new organizations
    - index changes
    - source_updated_at changes

    Rolling candidates:
    - offices whose full cards have not been checked
      within rolling_refresh_interval_days

    Historical versions are saved only for:
    - new organizations
    - meaningful content changes

    A failure of a discovery-critical fetch blocks
    committing the new manifest.

    A failure of a rolling-only fetch does NOT block
    committing the manifest; that organization remains
    due for another rolling refresh.
    """

    run_started = datetime.now(
        timezone.utc
    )

    committed_manifest_path = Path(
        committed_manifest_path
    )

    current_raw_dir = Path(
        current_raw_dir
    )

    version_dir = Path(
        version_dir
    )

    log_dir = Path(
        log_dir
    )

    log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not committed_manifest_path.exists():
        raise FileNotFoundError(
            "Committed manifest not found: "
            f"{committed_manifest_path}"
        )

    previous_manifest = pd.read_parquet(
        committed_manifest_path
    )

    # -------------------------------------------------
    # DISCOVERY
    # -------------------------------------------------

    fresh_parties = fetch_all_parties()

    current_manifest = (
        build_organization_manifest(
            fresh_parties
        )
    )

    discovery_snapshot = (
        save_discovery_snapshot(
            current_manifest
        )
    )

    (
        discovery_summary,
        new_orgs,
        disappeared_orgs,
        index_changed_orgs,
        refresh_candidates,
    ) = compare_manifests(
        current_manifest,
        previous_manifest,
    )

    # -------------------------------------------------
    # REFRESH STATE
    # -------------------------------------------------

    refresh_state = initialize_refresh_state(
        current_manifest,
        raw_dir=current_raw_dir,
        state_path=refresh_state_path,
    )

    if enable_rolling_refresh:
        rolling_candidates = (
            select_rolling_refresh_candidates(
                current_manifest,
                refresh_state,
                refresh_interval_days=(
                    rolling_refresh_interval_days
                ),
                limit=rolling_refresh_limit,
                entity_type="office",
            )
        )
    else:
        rolling_candidates = pd.DataFrame()

    # -------------------------------------------------
    # BUILD CANDIDATE SET + REASONS
    # -------------------------------------------------

    candidate_reasons = {}

    def add_reason(
        organization_ids,
        reason,
    ):
        for organization_id in organization_ids:
            candidate_reasons.setdefault(
                organization_id,
                set(),
            ).add(reason)

    if not new_orgs.empty:
        add_reason(
            new_orgs[
                "organization_id"
            ].tolist(),
            "new",
        )

    if not index_changed_orgs.empty:
        add_reason(
            index_changed_orgs[
                "organization_id"
            ].tolist(),
            "index_changed",
        )

    if not refresh_candidates.empty:
        add_reason(
            refresh_candidates[
                "organization_id"
            ].tolist(),
            "discovery_refresh",
        )

    if not rolling_candidates.empty:
        add_reason(
            rolling_candidates[
                "organization_id"
            ].tolist(),
            "rolling_refresh",
        )

    candidate_ids = sorted(
        candidate_reasons.keys()
    )

    if candidate_limit is not None:
        candidate_limit = int(candidate_limit)
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive.")
        candidate_ids = candidate_ids[:candidate_limit]

    # These fetches must succeed before
    # discovery may be committed.
    critical_candidate_ids = set()

    if not new_orgs.empty:
        critical_candidate_ids.update(
            new_orgs[
                "organization_id"
            ].tolist()
        )

    if not refresh_candidates.empty:
        critical_candidate_ids.update(
            refresh_candidates[
                "organization_id"
            ].tolist()
        )

    # Used for entity_type lookup.
    manifest_indexed = (
        current_manifest.set_index(
            "organization_id"
        )
    )

    # -------------------------------------------------
    # FETCH + COMPARE
    # -------------------------------------------------

    results = []
    failures = []
    refresh_state_updates = []

    for organization_id in candidate_ids:

        reasons = sorted(
            candidate_reasons[
                organization_id
            ]
        )

        is_critical = (
            organization_id
            in critical_candidate_ids
        )

        current_raw_path = (
            current_raw_dir
            / f"{organization_id}.json"
        )

        try:

            old_response = None

            if current_raw_path.exists():
                old_response = _read_json(
                    current_raw_path
                )

            new_response = (
                fetch_party_account(
                    organization_id
                )
            )

            retrieved_at = datetime.now(
                timezone.utc
            )

            new_detail = (
                new_response["results"]
            )

            old_detail = (
                old_response["results"]
                if old_response is not None
                else None
            )

            old_updated_at = (
                old_detail.get("updated_at")
                if old_detail is not None
                else None
            )

            new_updated_at = (
                new_detail.get(
                    "updated_at"
                )
            )

            if old_response is not None:
                _archive_baseline_if_needed(
                    organization_id,
                    current_raw_path,
                    version_dir,
                )

            # -----------------------------------------
            # CLASSIFY CONTENT
            # -----------------------------------------

            if old_detail is None:

                status = "new"

                old_content_hash = None

                new_content_hash = (
                    organization_content_hash(
                        new_detail
                    )
                )

                changed_fields = []

            else:

                change_info = (
                    classify_record_change(
                        old_detail,
                        new_detail,
                    )
                )

                old_content_hash = (
                    change_info[
                        "old_content_hash"
                    ]
                )

                new_content_hash = (
                    change_info[
                        "new_content_hash"
                    ]
                )

                changed_fields = (
                    change_info[
                        "changed_fields"
                    ]
                )

                if change_info[
                    "content_changed"
                ]:
                    status = (
                        "meaningful_change"
                    )

                elif changed_fields:
                    status = (
                        "technical_refresh"
                    )

                else:
                    status = (
                        "unchanged"
                    )

            # -----------------------------------------
            # HISTORICAL VERSION
            # -----------------------------------------

            version_path = None

            if status in {
                "new",
                "meaningful_change",
            }:

                version_path = (
                    _save_fetched_version(
                        organization_id,
                        new_response,
                        retrieved_at,
                        version_dir,
                    )
                )

            # -----------------------------------------
            # CURRENT RAW
            # -----------------------------------------

            _write_json(
                current_raw_path,
                new_response,
            )

            entity_type = (
                manifest_indexed.at[
                    organization_id,
                    "entity_type",
                ]
            )

            meaningful_change_at = None

            if status == "meaningful_change":
                meaningful_change_at = (
                    retrieved_at.isoformat()
                )

            refresh_state_updates.append({
                "organization_id":
                    organization_id,

                "entity_type":
                    entity_type,

                "last_checked_at_utc":
                    retrieved_at.isoformat(),

                "last_status":
                    status,

                "last_content_hash":
                    new_content_hash,

                "last_meaningful_change_at_utc":
                    meaningful_change_at,
            })

            results.append({
                "organization_id":
                    organization_id,

                "entity_type":
                    entity_type,

                "name":
                    new_detail.get("name"),

                "candidate_reasons":
                    reasons,

                "critical":
                    is_critical,

                "status":
                    status,

                "retrieved_at_utc":
                    retrieved_at.isoformat(),

                "old_updated_at":
                    old_updated_at,

                "new_updated_at":
                    new_updated_at,

                "version_path":
                    (
                        str(version_path)
                        if version_path
                        else None
                    ),

                "changed_fields":
                    changed_fields,

                "old_content_hash":
                    old_content_hash,

                "new_content_hash":
                    new_content_hash,
            })

        except Exception as exc:

            failures.append({
                "organization_id":
                    organization_id,

                "candidate_reasons":
                    reasons,

                "critical":
                    is_critical,

                "error":
                    repr(exc),
            })

    # -------------------------------------------------
    # UPDATE REFRESH STATE FOR SUCCESSFUL FETCHES
    # -------------------------------------------------

    if refresh_state_updates:

        refresh_state = (
            update_refresh_state(
                refresh_state,
                refresh_state_updates,
                state_path=refresh_state_path,
            )
        )

    # -------------------------------------------------
    # COMMIT DISCOVERY
    # -------------------------------------------------

    critical_failures = [
        failure
        for failure in failures
        if failure["critical"]
    ]

    rolling_only_failures = [
        failure
        for failure in failures
        if not failure["critical"]
    ]

    committed = False

    if not critical_failures:

        save_committed_manifest(
            current_manifest
        )

        committed = True

    # -------------------------------------------------
    # RUN SUMMARY
    # -------------------------------------------------

    run_finished = datetime.now(
        timezone.utc
    )

    status_counts = {}

    for item in results:

        status = item["status"]

        status_counts[status] = (
            status_counts.get(
                status,
                0,
            )
            + 1
        )

    run_log = {
        "run_started_at_utc":
            run_started.isoformat(),

        "run_finished_at_utc":
            run_finished.isoformat(),

        "committed":
            committed,

        "discovery_snapshot":
            str(discovery_snapshot),

        "discovery":
            discovery_summary,

        "rolling_refresh": {
            "enabled":
                enable_rolling_refresh,

            "interval_days":
                rolling_refresh_interval_days,

            "limit":
                rolling_refresh_limit,

            "selected":
                len(
                    rolling_candidates
                ),
        },

        "fetch_candidates":
            len(candidate_ids),

        "critical_candidates":
            len(
                critical_candidate_ids
            ),

        "status_counts":
            status_counts,

        "disappeared_ids":
            (
                disappeared_orgs[
                    "organization_id"
                ].tolist()
                if not disappeared_orgs.empty
                else []
            ),

        "index_changed_ids":
            (
                index_changed_orgs[
                    "organization_id"
                ].tolist()
                if not index_changed_orgs.empty
                else []
            ),

        "results":
            results,

        "failures":
            failures,

        "critical_failures":
            critical_failures,

        "rolling_only_failures":
            rolling_only_failures,
    }

    log_timestamp = (
        run_started.strftime(
            "%Y-%m-%d_%H-%M-%S_UTC"
        )
    )

    log_path = (
        log_dir
        / f"sync_{log_timestamp}.json"
    )

    if change_set_path is not None and committed:
        organization_changes = (
            organization_changes_from_sync_log(
                run_log
            )
        )
        change_set = create_change_set(
            organization_changes=organization_changes,
            created_at_utc=run_started.isoformat(),
        )
        save_change_set(
            change_set,
            change_set_path,
        )

        run_log["change_set_path"] = str(
            change_set_path
        )
        run_log["change_set_run_id"] = (
            change_set["run_id"]
        )
    else:
        run_log["change_set_path"] = None
        run_log["change_set_run_id"] = None

    _write_json(
        log_path,
        run_log,
    )

    return run_log
