
import pandas as pd

from politdata.reports import (
    classify_period_type,
    reports_to_manifest,
    add_periodicity_flags,
)


def test_quarter_five_is_annual():

    assert (
        classify_period_type(1)
        ==
        "quarterly"
    )

    assert (
        classify_period_type(4)
        ==
        "quarterly"
    )

    assert (
        classify_period_type(5)
        ==
        "annual"
    )

    assert (
        classify_period_type(None)
        ==
        "missing"
    )


def test_reports_to_manifest_preserves_source_period():

    organization = {
        "organization_id": "org-1",
        "root_party_id": "party-1",
        "entity_type": "office",
    }

    reports = [
        {
            "id": "report-1",
            "party_id": "org-1",
            "is_party_office": True,
            "year": 2025,
            "quarter": 5,
            "public_summary": {
                "v": 2,
                "generated_at":
                    "2025-01-01",
            },
        }
    ]

    df = reports_to_manifest(
        organization,
        reports,
        discovered_at_utc=
            "2026-01-01T00:00:00+00:00",
    )

    assert len(df) == 1

    row = df.iloc[0]

    assert row["quarter"] == 5
    assert row["period_type"] == "annual"

    assert (
        row[
            "party_id_matches_organization"
        ]
        is True
        or
        bool(
            row[
                "party_id_matches_organization"
            ]
        )
        is True
    )


def test_annual_preference_does_not_delete_quarterlies():

    df = pd.DataFrame(
        [
            {
                "report_id": "q1",
                "organization_id": "o1",
                "year": 2025,
                "period_type": "quarterly",
            },
            {
                "report_id": "q2",
                "organization_id": "o1",
                "year": 2025,
                "period_type": "quarterly",
            },
            {
                "report_id": "annual",
                "organization_id": "o1",
                "year": 2025,
                "period_type": "annual",
            },
            {
                "report_id": "only-q1",
                "organization_id": "o2",
                "year": 2025,
                "period_type": "quarterly",
            },
        ]
    )

    result = add_periodicity_flags(
        df
    )

    # Nothing physically disappears.
    assert len(result) == 4

    selected_o1 = set(
        result.loc[
            (
                result["organization_id"]
                ==
                "o1"
            )
            &
            result[
                "include_by_annual_preference"
            ],
            "report_id",
        ]
    )

    assert selected_o1 == {
        "annual"
    }

    selected_o2 = set(
        result.loc[
            (
                result["organization_id"]
                ==
                "o2"
            )
            &
            result[
                "include_by_annual_preference"
            ],
            "report_id",
        ]
    )

    assert selected_o2 == {
        "only-q1"
    }
