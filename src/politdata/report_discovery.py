
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import time
import uuid

import pandas as pd
from tqdm.auto import tqdm

from .reports import (
    fetch_all_reports,
    reports_to_manifest,
    add_periodicity_flags,
)


DEFAULT_STATE_PATH = Path(
    "data/interim/state/"
    "report_discovery_state.parquet"
)

DEFAULT_SNAPSHOT_DIR = Path(
    "data/raw/report_lists"
)


def _write_json_atomic(path, data):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        f"{path.stem}.{uuid.uuid4().hex}.tmp"
        f"{path.suffix}"
    )

    try:

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

        os.replace(
            temp_path,
            path,
        )

    finally:

        if temp_path.exists():

            try:
                temp_path.unlink()
            except OSError:
                pass


def save_report_discovery_state(
    state,
    state_path=DEFAULT_STATE_PATH,
    max_retries=20,
    retry_delay=0.25,
):
    """
    Safely persist discovery state.

    Windows can temporarily lock a Parquet file
    during replacement. We therefore retry the
    atomic replacement several times.
    """

    state_path = Path(
        state_path
    )

    state_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = state_path.with_name(
        f"{state_path.stem}."
        f"{uuid.uuid4().hex}.tmp"
        f"{state_path.suffix}"
    )

    state.to_parquet(
        temp_path,
        index=False,
    )

    try:

        for attempt in range(
            1,
            max_retries + 1,
        ):

            try:

                os.replace(
                    temp_path,
                    state_path,
                )

                return state_path

            except PermissionError:

                if attempt == max_retries:
                    raise

                time.sleep(
                    retry_delay
                    * attempt
                )

    finally:

        if temp_path.exists():

            try:
                temp_path.unlink()
            except OSError:
                pass


def _try_save_state(
    state,
    state_path,
):
    """
    State persistence failure must not destroy
    a long discovery run.

    RAW snapshots remain recoverable and will
    be reconciled on the next run.
    """

    try:

        save_report_discovery_state(
            state,
            state_path=state_path,
        )

        return True

    except PermissionError as exc:

        print()
        print(
            "WARNING: state checkpoint "
            "could not be written."
        )

        print(
            "RAW snapshots remain safe."
        )

        print(
            "Windows error:",
            repr(exc)
        )

        return False


def initialize_report_discovery_state(
    organization_manifest,
    state_path=DEFAULT_STATE_PATH,
):
    """
    Initialize or update discovery state while
    preserving existing statuses.
    """

    state_path = Path(
        state_path
    )

    manifest = (
        organization_manifest[
            [
                "organization_id",
                "root_party_id",
                "entity_type",
                "name",
            ]
        ]
        .copy()
    )

    if state_path.exists():

        old_state = pd.read_parquet(
            state_path
        )

        old_state = old_state.set_index(
            "organization_id"
        )

        rows = []

        for row in manifest.to_dict(
            orient="records"
        ):

            organization_id = (
                row["organization_id"]
            )

            if (
                organization_id
                in old_state.index
            ):

                old = (
                    old_state.loc[
                        organization_id
                    ].to_dict()
                )

                old.update({
                    "organization_id":
                        organization_id,

                    "root_party_id":
                        row[
                            "root_party_id"
                        ],

                    "entity_type":
                        row[
                            "entity_type"
                        ],

                    "name":
                        row["name"],
                })

                rows.append(
                    old
                )

            else:

                rows.append({
                    **row,
                    "status":
                        "pending",

                    "last_checked_at_utc":
                        None,

                    "declared_count":
                        None,

                    "fetched_count":
                        None,

                    "count_difference":
                        None,

                    "count_mismatch":
                        None,

                    "snapshot_path":
                        None,

                    "error":
                        None,
                })

        state = pd.DataFrame(
            rows
        )

    else:

        state = manifest.copy()

        state[
            "status"
        ] = "pending"

        state[
            "last_checked_at_utc"
        ] = None

        state[
            "declared_count"
        ] = None

        state[
            "fetched_count"
        ] = None

        state[
            "count_difference"
        ] = None

        state[
            "count_mismatch"
        ] = None

        state[
            "snapshot_path"
        ] = None

        state[
            "error"
        ] = None

    return state


def reconcile_state_from_snapshots(
    state,
    snapshot_dir=DEFAULT_SNAPSHOT_DIR,
):
    """
    Recover successful state from RAW snapshots.

    This handles the case where:
    1. API response was successfully saved to JSON,
    2. process failed before Parquet state was updated.

    Therefore a valid snapshot is authoritative evidence
    that discovery for that organization succeeded.
    """

    snapshot_dir = Path(
        snapshot_dir
    )

    state = state.copy()

    state = state.set_index(
        "organization_id"
    )

    recovered = 0

    for organization_id in state.index:

        if (
            state.at[
                organization_id,
                "status",
            ]
            == "success"
        ):
            continue

        snapshot_path = (
            snapshot_dir
            / f"{organization_id}.json"
        )

        if not snapshot_path.exists():
            continue

        try:

            with snapshot_path.open(
                "r",
                encoding="utf-8",
            ) as f:

                snapshot = json.load(
                    f
                )

            if (
                snapshot.get(
                    "organization_id"
                )
                != organization_id
            ):
                continue

            metadata = snapshot.get(
                "fetch_metadata",
                {}
            )

            reports = snapshot.get(
                "reports"
            )

            if not isinstance(
                reports,
                list,
            ):
                continue

            state.at[
                organization_id,
                "status",
            ] = "success"

            state.at[
                organization_id,
                "last_checked_at_utc",
            ] = snapshot.get(
                "retrieved_at_utc"
            )

            state.at[
                organization_id,
                "declared_count",
            ] = metadata.get(
                "declared_count"
            )

            state.at[
                organization_id,
                "fetched_count",
            ] = metadata.get(
                "fetched_count",
                len(reports),
            )

            state.at[
                organization_id,
                "count_difference",
            ] = metadata.get(
                "count_difference"
            )

            state.at[
                organization_id,
                "count_mismatch",
            ] = metadata.get(
                "count_mismatch"
            )

            state.at[
                organization_id,
                "snapshot_path",
            ] = str(
                snapshot_path
            )

            state.at[
                organization_id,
                "error",
            ] = None

            recovered += 1

        except Exception:
            # Invalid/incomplete snapshot is not
            # considered successful.
            continue

    return (
        state.reset_index(),
        recovered,
    )


def run_report_discovery_batch(
    organization_manifest,
    organization_ids=None,
    entity_type=None,
    limit=None,
    state_path=DEFAULT_STATE_PATH,
    snapshot_dir=DEFAULT_SNAPSHOT_DIR,
    timeout=60,
    max_retries=4,
    retry_errors=True,
    checkpoint_every=25,
):
    """
    Resumable report-list discovery.

    RAW snapshots are written per organization.

    Shared Parquet state is only a checkpoint layer;
    snapshots can recover progress after an interrupted
    or failed state write.
    """

    snapshot_dir = Path(
        snapshot_dir
    )

    snapshot_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    state = (
        initialize_report_discovery_state(
            organization_manifest,
            state_path=state_path,
        )
    )

    # Recover any snapshots written after
    # the last successful state checkpoint.
    (
        state,
        recovered_from_snapshots,
    ) = reconcile_state_from_snapshots(
        state,
        snapshot_dir=snapshot_dir,
    )

    if recovered_from_snapshots:

        print(
            "Recovered from RAW snapshots:",
            recovered_from_snapshots
        )

    # Try to checkpoint recovered state,
    # but do not abort if Windows locks it.
    _try_save_state(
        state,
        state_path,
    )

    candidates = state.copy()

    if organization_ids is not None:

        organization_ids = set(
            organization_ids
        )

        candidates = candidates[
            candidates[
                "organization_id"
            ].isin(
                organization_ids
            )
        ]

    if entity_type is not None:

        candidates = candidates[
            candidates[
                "entity_type"
            ]
            == entity_type
        ]

    candidates = candidates[
        candidates[
            "status"
        ]
        != "success"
    ]

    if not retry_errors:

        candidates = candidates[
            candidates[
                "status"
            ]
            != "error"
        ]

    if limit is not None:

        candidates = (
            candidates.head(
                limit
            )
        )

    selected_ids = (
        candidates[
            "organization_id"
        ].tolist()
    )

    state = state.set_index(
        "organization_id"
    )

    successful = 0
    failed = 0
    reports_fetched = 0
    attempts_since_checkpoint = 0

    for organization_id in tqdm(
        selected_ids,
        desc="Report discovery",
    ):

        retrieved_at = datetime.now(
            timezone.utc
        )

        try:

            (
                reports,
                metadata,
            ) = fetch_all_reports(
                organization_id,
                timeout=timeout,
                max_retries=max_retries,
                return_metadata=True,
            )

            snapshot_path = (
                snapshot_dir
                / f"{organization_id}.json"
            )

            snapshot = {
                "source":
                    "PolitData",

                "endpoint":
                    (
                        f"/party/"
                        f"{organization_id}"
                        f"/reports"
                    ),

                "organization_id":
                    organization_id,

                "retrieved_at_utc":
                    retrieved_at.isoformat(),

                "fetch_metadata":
                    metadata,

                "reports":
                    reports,
            }

            # RAW snapshot FIRST.
            _write_json_atomic(
                snapshot_path,
                snapshot,
            )

            state.at[
                organization_id,
                "status",
            ] = "success"

            state.at[
                organization_id,
                "last_checked_at_utc",
            ] = retrieved_at.isoformat()

            state.at[
                organization_id,
                "declared_count",
            ] = metadata[
                "declared_count"
            ]

            state.at[
                organization_id,
                "fetched_count",
            ] = metadata[
                "fetched_count"
            ]

            state.at[
                organization_id,
                "count_difference",
            ] = metadata[
                "count_difference"
            ]

            state.at[
                organization_id,
                "count_mismatch",
            ] = metadata[
                "count_mismatch"
            ]

            state.at[
                organization_id,
                "snapshot_path",
            ] = str(
                snapshot_path
            )

            state.at[
                organization_id,
                "error",
            ] = None

            successful += 1

            reports_fetched += len(
                reports
            )

        except Exception as exc:

            state.at[
                organization_id,
                "status",
            ] = "error"

            state.at[
                organization_id,
                "last_checked_at_utc",
            ] = retrieved_at.isoformat()

            state.at[
                organization_id,
                "error",
            ] = repr(
                exc
            )

            failed += 1

        attempts_since_checkpoint += 1

        # Do not rewrite the shared Parquet
        # thousands of times.
        if (
            attempts_since_checkpoint
            >= checkpoint_every
        ):

            current_state = (
                state.reset_index()
            )

            _try_save_state(
                current_state,
                state_path,
            )

            attempts_since_checkpoint = 0

    final_state = (
        state.reset_index()
    )

    # Final checkpoint.
    _try_save_state(
        final_state,
        state_path,
    )

    summary = {
        "selected":
            len(selected_ids),

        "successful":
            successful,

        "failed":
            failed,

        "reports_fetched":
            reports_fetched,

        "recovered_from_snapshots":
            recovered_from_snapshots,

        "total_success_in_state":
            int(
                (
                    final_state[
                        "status"
                    ]
                    == "success"
                ).sum()
            ),

        "total_errors_in_state":
            int(
                (
                    final_state[
                        "status"
                    ]
                    == "error"
                ).sum()
            ),

        "total_pending_in_state":
            int(
                (
                    final_state[
                        "status"
                    ]
                    == "pending"
                ).sum()
            ),
    }

    return (
        summary,
        final_state,
    )


def build_report_manifest_from_snapshots(
    organization_manifest,
    snapshot_dir=DEFAULT_SNAPSHOT_DIR,
):
    """
    Rebuild unified report manifest from saved
    per-organization RAW report-list snapshots.
    """

    snapshot_dir = Path(
        snapshot_dir
    )

    manifest_indexed = (
        organization_manifest
        .set_index(
            "organization_id"
        )
    )

    frames = []
    missing_organizations = []

    for snapshot_path in sorted(
        snapshot_dir.glob(
            "*.json"
        )
    ):

        with snapshot_path.open(
            "r",
            encoding="utf-8",
        ) as f:

            snapshot = json.load(
                f
            )

        organization_id = (
            snapshot[
                "organization_id"
            ]
        )

        if (
            organization_id
            not in manifest_indexed.index
        ):

            missing_organizations.append(
                organization_id
            )

            continue

        reports = snapshot.get(
            "reports",
            []
        )

        if not reports:
            continue

        organization_row = (
            manifest_indexed.loc[
                organization_id
            ].to_dict()
        )

        organization_row[
            "organization_id"
        ] = organization_id

        frame = reports_to_manifest(
            organization_row,
            reports,
            discovered_at_utc=(
                snapshot.get(
                    "retrieved_at_utc"
                )
            ),
        )

        frames.append(
            frame
        )

    if frames:

        reports_df = pd.concat(
            frames,
            ignore_index=True,
        )

        reports_df = (
            add_periodicity_flags(
                reports_df
            )
        )

    else:

        reports_df = pd.DataFrame()

    qa = {
        "rows":
            len(reports_df),

        "unique_report_ids":
            (
                reports_df[
                    "report_id"
                ].nunique()
                if not reports_df.empty
                else 0
            ),

        "duplicate_report_ids":
            (
                reports_df[
                    "report_id"
                ].duplicated().sum()
                if not reports_df.empty
                else 0
            ),

        "missing_report_ids":
            (
                reports_df[
                    "report_id"
                ].isna().sum()
                if not reports_df.empty
                else 0
            ),

        "party_id_mismatches":
            (
                (
                    ~reports_df[
                        "party_id_matches_organization"
                    ]
                ).sum()
                if not reports_df.empty
                else 0
            ),

        "snapshot_orgs_not_in_manifest":
            len(
                missing_organizations
            ),
    }

    return (
        reports_df,
        qa,
    )
