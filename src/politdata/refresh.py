
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

import pandas as pd

from .change_detection import organization_content_hash


DEFAULT_REFRESH_STATE_PATH = Path(
    "data/interim/state/organization_refresh_state.parquet"
)

DEFAULT_RAW_DIR = Path(
    "data/raw/party_accounts"
)


def save_refresh_state(
    state,
    state_path=DEFAULT_REFRESH_STATE_PATH,
):
    """
    Safely save operational refresh state.
    """

    state_path = Path(state_path)

    state_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = state_path.with_suffix(
        ".tmp.parquet"
    )

    state.to_parquet(
        temp_path,
        index=False,
    )

    temp_path.replace(state_path)

    return state_path


def initialize_refresh_state(
    manifest,
    raw_dir=DEFAULT_RAW_DIR,
    state_path=DEFAULT_REFRESH_STATE_PATH,
    overwrite=False,
):
    """
    Bootstrap refresh state from existing RAW files.

    File modification time is used only as an initial
    approximation of when the organization was last fetched.
    After bootstrap, the pipeline will maintain explicit
    last_checked_at_utc values itself.
    """

    state_path = Path(state_path)
    raw_dir = Path(raw_dir)

    if state_path.exists() and not overwrite:
        return pd.read_parquet(
            state_path
        )

    rows = []

    for row in manifest.itertuples(
        index=False
    ):
        organization_id = (
            row.organization_id
        )

        raw_path = (
            raw_dir
            / f"{organization_id}.json"
        )

        last_checked_at_utc = None
        content_hash = None

        if raw_path.exists():

            modified_at = datetime.fromtimestamp(
                raw_path.stat().st_mtime,
                tz=timezone.utc,
            )

            last_checked_at_utc = (
                modified_at.isoformat()
            )

            try:
                with raw_path.open(
                    "r",
                    encoding="utf-8",
                ) as f:
                    api_response = json.load(f)

                detail = api_response.get(
                    "results"
                )

                if detail is not None:
                    content_hash = (
                        organization_content_hash(
                            detail
                        )
                    )

            except Exception:
                content_hash = None

        rows.append({
            "organization_id":
                organization_id,

            "entity_type":
                row.entity_type,

            "last_checked_at_utc":
                last_checked_at_utc,

            "last_status":
                "bootstrap"
                if raw_path.exists()
                else None,

            "last_content_hash":
                content_hash,

            "last_meaningful_change_at_utc":
                None,
        })

    state = pd.DataFrame(rows)

    save_refresh_state(
        state,
        state_path=state_path,
    )

    return state


def select_rolling_refresh_candidates(
    manifest,
    state,
    refresh_interval_days=7,
    limit=1400,
    entity_type="office",
    now_utc=None,
):
    """
    Select the stalest organizations that are due
    for a periodic full-card refresh.

    Missing last_checked values are considered most urgent.
    """

    if now_utc is None:
        now_utc = datetime.now(
            timezone.utc
        )

    cutoff = (
        now_utc
        - timedelta(
            days=refresh_interval_days
        )
    )

    state_small = state[
        [
            "organization_id",
            "last_checked_at_utc",
        ]
    ].copy()

    merged = manifest.merge(
        state_small,
        on="organization_id",
        how="left",
    )

    candidates = merged[
        merged["entity_type"]
        == entity_type
    ].copy()

    candidates[
        "last_checked_parsed"
    ] = pd.to_datetime(
        candidates[
            "last_checked_at_utc"
        ],
        utc=True,
        errors="coerce",
    )

    due_mask = (
        candidates[
            "last_checked_parsed"
        ].isna()
        |
        (
            candidates[
                "last_checked_parsed"
            ]
            <= cutoff
        )
    )

    candidates = candidates[
        due_mask
    ].copy()

    candidates[
        "_missing_check"
    ] = candidates[
        "last_checked_parsed"
    ].isna()

    candidates = candidates.sort_values(
        by=[
            "_missing_check",
            "last_checked_parsed",
            "organization_id",
        ],
        ascending=[
            False,
            True,
            True,
        ],
    )

    if limit is not None:
        candidates = candidates.head(
            limit
        )

    return candidates.drop(
        columns=[
            "_missing_check",
            "last_checked_parsed",
        ]
    ).reset_index(
        drop=True
    )


def update_refresh_state(
    state,
    updates,
    state_path=DEFAULT_REFRESH_STATE_PATH,
):
    """
    Apply successful refresh results to operational state.

    Failed fetches should not be passed here, so they
    remain due for a later retry.
    """

    state = state.copy()

    state = state.set_index(
        "organization_id"
    )

    for update in updates:

        organization_id = update[
            "organization_id"
        ]

        if organization_id not in state.index:
            state.loc[
                organization_id,
                "entity_type",
            ] = update.get(
                "entity_type"
            )

        state.loc[
            organization_id,
            "last_checked_at_utc",
        ] = update.get(
            "last_checked_at_utc"
        )

        state.loc[
            organization_id,
            "last_status",
        ] = update.get(
            "last_status"
        )

        state.loc[
            organization_id,
            "last_content_hash",
        ] = update.get(
            "last_content_hash"
        )

        meaningful_at = update.get(
            "last_meaningful_change_at_utc"
        )

        if meaningful_at is not None:
            state.loc[
                organization_id,
                "last_meaningful_change_at_utc",
            ] = meaningful_at

    state = state.reset_index()

    save_refresh_state(
        state,
        state_path=state_path,
    )

    return state
