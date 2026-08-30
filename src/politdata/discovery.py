
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


INDEX_COMPARE_COLUMNS = [
    "root_party_id",
    "parent_id",
    "entity_type",
    "code",
    "name",
    "is_active",
]

REFRESH_TRIGGER_COLUMNS = [
    "source_updated_at",
]


def build_organization_manifest(
    parties,
    discovered_at_utc=None,
):
    """
    Build a fresh organization manifest from the current /parties response.

    The manifest is rebuilt from scratch on every discovery run.
    """

    if discovered_at_utc is None:
        discovered_at_utc = datetime.now(
            timezone.utc
        ).isoformat()

    rows = []

    for party in parties:
        party_id = party["id"]

        # Central party
        rows.append({
            "organization_id": party_id,
            "root_party_id": party_id,
            "parent_id": None,
            "entity_type": "party",
            "code": party.get("code"),
            "name": party.get("name"),
            "is_active": party.get("is_active"),
            "source_created_at": party.get("created_at"),
            "source_updated_at": party.get("updated_at"),
            "discovered_at_utc": discovered_at_utc,
        })

        # Regional/local organizations
        for office in party.get("regional_offices") or []:
            rows.append({
                "organization_id": office["id"],
                "root_party_id": party_id,
                "parent_id": party_id,
                "entity_type": "office",
                "code": office.get("code"),
                "name": office.get("name"),
                "is_active": office.get("is_active"),

                # /parties does not expose these for offices
                "source_created_at": None,
                "source_updated_at": None,

                "discovered_at_utc": discovered_at_utc,
            })

    df = pd.DataFrame(rows)

    if df["organization_id"].isna().any():
        raise ValueError(
            "Manifest contains missing organization IDs."
        )

    if df["organization_id"].duplicated().any():
        duplicates = df.loc[
            df["organization_id"].duplicated(),
            "organization_id",
        ].tolist()

        raise ValueError(
            f"Duplicate organization IDs: {duplicates[:10]}"
        )

    return df


def _values_different(old_value, new_value):
    old_missing = pd.isna(old_value)
    new_missing = pd.isna(new_value)

    if old_missing and new_missing:
        return False

    if old_missing != new_missing:
        return True

    return old_value != new_value


def compare_manifests(
    current,
    previous,
):
    """
    Compare fresh discovery with a previous committed manifest.

    Separates:
    - new organizations
    - disappeared organizations
    - meaningful changes visible in /parties
    - refresh candidates triggered by technical/source fields
    """

    current = current.copy()
    previous = previous.copy()

    current_ids = set(
        current["organization_id"]
    )

    previous_ids = set(
        previous["organization_id"]
    )

    new_ids = current_ids - previous_ids
    disappeared_ids = previous_ids - current_ids
    common_ids = current_ids & previous_ids

    new_df = current[
        current["organization_id"].isin(new_ids)
    ].copy()

    disappeared_df = previous[
        previous["organization_id"].isin(
            disappeared_ids
        )
    ].copy()

    current_indexed = current.set_index(
        "organization_id"
    )

    previous_indexed = previous.set_index(
        "organization_id"
    )

    index_columns = [
        col
        for col in INDEX_COMPARE_COLUMNS
        if (
            col in current.columns
            and col in previous.columns
        )
    ]

    refresh_columns = [
        col
        for col in REFRESH_TRIGGER_COLUMNS
        if (
            col in current.columns
            and col in previous.columns
        )
    ]

    index_changed_rows = []
    refresh_candidate_rows = []

    for organization_id in sorted(common_ids):

        index_changed_fields = []
        refresh_trigger_fields = []

        for col in index_columns:
            old_value = previous_indexed.at[
                organization_id, col
            ]
            new_value = current_indexed.at[
                organization_id, col
            ]

            if _values_different(
                old_value,
                new_value,
            ):
                index_changed_fields.append(col)

        for col in refresh_columns:
            old_value = previous_indexed.at[
                organization_id, col
            ]
            new_value = current_indexed.at[
                organization_id, col
            ]

            if _values_different(
                old_value,
                new_value,
            ):
                refresh_trigger_fields.append(col)

        if index_changed_fields:
            row = current_indexed.loc[
                organization_id
            ].to_dict()

            row["organization_id"] = (
                organization_id
            )

            row["changed_fields"] = (
                index_changed_fields
            )

            index_changed_rows.append(row)

        if (
            index_changed_fields
            or refresh_trigger_fields
        ):
            row = current_indexed.loc[
                organization_id
            ].to_dict()

            row["organization_id"] = (
                organization_id
            )

            row["index_changed_fields"] = (
                index_changed_fields
            )

            row["refresh_trigger_fields"] = (
                refresh_trigger_fields
            )

            refresh_candidate_rows.append(row)

    index_changed_df = pd.DataFrame(
        index_changed_rows
    )

    refresh_candidates_df = pd.DataFrame(
        refresh_candidate_rows
    )

    summary = {
        "current": len(current_ids),
        "previous": len(previous_ids),
        "new": len(new_ids),
        "disappeared": len(disappeared_ids),
        "existing": len(common_ids),
        "index_changed": len(index_changed_rows),
        "refresh_candidates": len(
            refresh_candidate_rows
        ),
        "index_compared_columns": index_columns,
        "refresh_trigger_columns": refresh_columns,
    }

    return (
        summary,
        new_df,
        disappeared_df,
        index_changed_df,
        refresh_candidates_df,
    )


def save_discovery_snapshot(
    manifest,
    output_dir="data/interim/manifests",
):
    """
    Save a timestamped manifest produced by discovery.

    Does NOT change the committed baseline.
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d_%H-%M-%S_UTC"
    )

    snapshot_path = (
        output_dir
        / f"organization_manifest_{timestamp}.parquet"
    )

    manifest.to_parquet(
        snapshot_path,
        index=False,
    )

    return snapshot_path


def save_committed_manifest(
    manifest,
    output_dir="data/interim/manifests",
):
    """
    Update the committed baseline only after
    synchronization has completed successfully.
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    committed_path = (
        output_dir
        / "organization_manifest_committed.parquet"
    )

    manifest.to_parquet(
        committed_path,
        index=False,
    )

    return committed_path
