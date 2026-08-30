import pandas as pd
import pytest

from politdata.change_set import create_change_set
from politdata.dependency_planner import (
    build_incremental_dependency_plan,
)


def _references():
    organizations = pd.DataFrame([
        {
            "organization_id": "p1",
            "root_party_id": "p1",
        },
        {
            "organization_id": "o1",
            "root_party_id": "p1",
        },
        {
            "organization_id": "o2",
            "root_party_id": "p2",
        },
    ])
    reports = pd.DataFrame([
        {
            "source_report_id": "rp1",
            "organization_id": "p1",
            "root_party_id": "p1",
        },
        {
            "source_report_id": "ro1-a",
            "organization_id": "o1",
            "root_party_id": "p1",
        },
        {
            "source_report_id": "ro1-b",
            "organization_id": "o1",
            "root_party_id": "p1",
        },
        {
            "source_report_id": "ro2",
            "organization_id": "o2",
            "root_party_id": "p2",
        },
    ])
    return organizations, reports


def test_direct_report_change_closes_over_same_organization():
    organizations, reports = _references()
    change_set = create_change_set(
        run_id="run-1",
        report_changes=[{
            "report_id": "ro1-a",
            "organization_id": "o1",
            "change_type": "meaningful_change",
        }],
    )

    plan = build_incremental_dependency_plan(
        change_set,
        organizations,
        reports,
    )

    assert plan["closure"]["affected_organization_ids"] == [
        "o1"
    ]
    assert plan["closure"]["affected_report_ids"] == [
        "ro1-a",
        "ro1-b",
    ]


def test_party_change_closes_over_offices_and_their_reports():
    organizations, reports = _references()
    change_set = create_change_set(
        run_id="run-2",
        organization_changes=[{
            "organization_id": "p1",
            "change_type": "meaningful_change",
        }],
    )

    plan = build_incremental_dependency_plan(
        change_set,
        organizations,
        reports,
    )

    assert plan["closure"][
        "root_dependent_organization_ids"
    ] == ["o1", "p1"]
    assert plan["closure"]["affected_organization_ids"] == [
        "o1",
        "p1",
    ]
    assert plan["closure"]["affected_report_ids"] == [
        "ro1-a",
        "ro1-b",
        "rp1",
    ]


def test_office_change_does_not_expand_to_sibling_offices():
    organizations, reports = _references()
    change_set = create_change_set(
        run_id="run-3",
        organization_changes=[{
            "organization_id": "o1",
            "change_type": "disappeared",
        }],
    )

    plan = build_incremental_dependency_plan(
        change_set,
        organizations,
        reports,
    )

    assert plan["roots"]["deleted_organization_ids"] == ["o1"]
    assert plan["closure"]["affected_organization_ids"] == [
        "o1"
    ]
    assert plan["closure"]["affected_report_ids"] == [
        "ro1-a",
        "ro1-b",
    ]


def test_requires_matching_promoted_entity_index():
    organizations, reports = _references()
    change_set = create_change_set(
        run_id="run-4",
        report_changes=[{
            "report_id": "ro2",
            "organization_id": "o2",
            "change_type": "new",
        }],
    )
    promotion_state = {
        "runs": [{"run_id": "run-4"}],
        "organizations": {},
        "reports": {},
    }

    with pytest.raises(ValueError):
        build_incremental_dependency_plan(
            change_set,
            organizations,
            reports,
            promotion_state=promotion_state,
        )
