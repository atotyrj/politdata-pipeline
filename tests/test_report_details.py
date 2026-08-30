
import pytest

from politdata.report_details import (
    raw_payload_hash,
    report_detail_content_hash,
    validate_report_detail_payload,
    property_paper_count,
)


def test_public_summary_changes_raw_hash_but_not_semantic_hash():

    first = {
        "code": 0,
        "results": {
            "id": "r1",
            "year": 2025,
            "quarter": 1,
            "public_summary": {
                "v": 1,
                "text": "old",
            },
        },
    }

    second = {
        "code": 0,
        "results": {
            "id": "r1",
            "year": 2025,
            "quarter": 1,
            "public_summary": {
                "v": 2,
                "text": "new",
            },
        },
    }

    assert (
        raw_payload_hash(first)
        !=
        raw_payload_hash(second)
    )

    assert (
        report_detail_content_hash(first)
        ==
        report_detail_content_hash(second)
    )


def test_semantic_hash_changes_when_report_content_changes():

    first = {
        "code": 0,
        "results": {
            "id": "r1",
            "year": 2025,
        },
    }

    second = {
        "code": 0,
        "results": {
            "id": "r1",
            "year": 2026,
        },
    }

    assert (
        report_detail_content_hash(first)
        !=
        report_detail_content_hash(second)
    )


def test_validate_report_detail_payload():

    payload = {
        "code": 0,
        "results": {
            "id": "r1",
        },
    }

    results = (
        validate_report_detail_payload(
            payload,
            "r1",
        )
    )

    assert results["id"] == "r1"


def test_validate_report_detail_rejects_wrong_id():

    payload = {
        "code": 0,
        "results": {
            "id": "wrong",
        },
    }

    with pytest.raises(
        ValueError
    ):

        validate_report_detail_payload(
            payload,
            "expected",
        )


def test_property_paper_count_uses_source_section():

    payload = {
        "results": {
            "properties": {
                "property_paper": [
                    {"id": "a"},
                    {"id": "b"},
                ]
            }
        }
    }

    assert (
        property_paper_count(
            payload
        )
        ==
        2
    )
