
from politdata.change_detection import (
    canonicalize_record,
    organization_content_hash,
    deep_diff,
    classify_record_change,
)


def test_updated_at_is_excluded_from_content_hash():

    old = {
        "id": "x",
        "name": "Party",
        "updated_at": "2025-01-01",
    }

    new = {
        "id": "x",
        "name": "Party",
        "updated_at": "2026-01-01",
    }

    assert (
        organization_content_hash(old)
        ==
        organization_content_hash(new)
    )


def test_meaningful_change_changes_hash():

    old = {
        "id": "x",
        "name": "Old",
    }

    new = {
        "id": "x",
        "name": "New",
    }

    assert (
        organization_content_hash(old)
        !=
        organization_content_hash(new)
    )


def test_classification_distinguishes_technical_change():

    old = {
        "id": "x",
        "name": "Party",
        "updated_at": "2025-01-01",
    }

    new = {
        "id": "x",
        "name": "Party",
        "updated_at": "2026-01-01",
    }

    result = classify_record_change(
        old,
        new,
    )

    assert (
        result["content_changed"]
        is False
    )

    # Full diff still records the source-field change.
    assert (
        "updated_at"
        in
        result["changed_fields"]
    )


def test_deep_diff_nested_field():

    differences = deep_diff(
        {
            "a": {
                "b": 1
            }
        },
        {
            "a": {
                "b": 2
            }
        },
    )

    assert differences == [
        {
            "field": "a.b",
            "old": 1,
            "new": 2,
        }
    ]
