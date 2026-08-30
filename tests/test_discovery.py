
import pandas as pd

from politdata.discovery import (
    build_organization_manifest,
    compare_manifests,
)


def test_build_organization_manifest_party_and_office():

    parties = [
        {
            "id": "party-1",
            "code": "12345678",
            "name": "PARTY",
            "is_active": True,
            "created_at": "2020-01-01",
            "updated_at": "2025-01-01",
            "regional_offices": [
                {
                    "id": "office-1",
                    "code": "7654321",
                    "name": "OFFICE",
                    "is_active": True,
                }
            ],
        }
    ]

    df = build_organization_manifest(
        parties,
        discovered_at_utc=
            "2026-01-01T00:00:00+00:00",
    )

    assert len(df) == 2

    party = (
        df.loc[
            df["organization_id"]
            ==
            "party-1"
        ]
        .iloc[0]
    )

    office = (
        df.loc[
            df["organization_id"]
            ==
            "office-1"
        ]
        .iloc[0]
    )

    assert (
        party["entity_type"]
        ==
        "party"
    )

    assert (
        office["entity_type"]
        ==
        "office"
    )

    assert (
        office["root_party_id"]
        ==
        "party-1"
    )

    # Important production rule:
    # organization codes are identifiers,
    # not numbers to be padded.
    assert (
        office["code"]
        ==
        "7654321"
    )


def test_compare_manifests_separates_index_and_refresh_changes():

    previous = pd.DataFrame(
        [
            {
                "organization_id": "a",
                "root_party_id": "a",
                "parent_id": None,
                "entity_type": "party",
                "code": "1",
                "name": "OLD",
                "is_active": True,
                "source_updated_at": "2025-01-01",
            },
            {
                "organization_id": "b",
                "root_party_id": "b",
                "parent_id": None,
                "entity_type": "party",
                "code": "2",
                "name": "B",
                "is_active": True,
                "source_updated_at": "2025-01-01",
            },
        ]
    )

    current = previous.copy()

    current.loc[
        current["organization_id"] == "a",
        "name",
    ] = "NEW"

    current.loc[
        current["organization_id"] == "b",
        "source_updated_at",
    ] = "2026-01-01"

    (
        summary,
        new_df,
        disappeared_df,
        index_changed_df,
        refresh_candidates_df,
    ) = compare_manifests(
        current,
        previous,
    )

    assert summary["new"] == 0
    assert summary["disappeared"] == 0

    assert (
        set(
            index_changed_df[
                "organization_id"
            ]
        )
        ==
        {"a"}
    )

    assert (
        set(
            refresh_candidates_df[
                "organization_id"
            ]
        )
        ==
        {"a", "b"}
    )
