
from politdata.normalization.report_sections import (
    extract_section_rows,
    normalize_source_row,
)


def test_extract_property_list():

    detail = {
        "properties": {
            "property_object": [
                {
                    "id": "x"
                },
                {
                    "id": "y"
                },
            ]
        }
    }

    rows = extract_section_rows(
        detail,
        "realty",
    )

    assert len(rows) == 2


def test_extract_head_info_dict():

    detail = {
        "head_info": {
            "name": "TEST"
        }
    }

    rows = extract_section_rows(
        detail,
        "head_info",
    )

    assert len(rows) == 1

    assert (
        rows[0]["name"]
        ==
        "TEST"
    )


def test_empty_section():

    detail = {
        "properties": {
            "transport": []
        }
    }

    assert (
        extract_section_rows(
            detail,
            "transport",
        )
        ==
        []
    )


def test_scalar_list_item():

    detail = {
        "organizations": [
            "123"
        ]
    }

    rows = extract_section_rows(
        detail,
        "organizations",
    )

    assert (
        rows
        ==
        [
            {
                "value": "123"
            }
        ]
    )


def test_normalize_preserves_source_fields():

    result = normalize_source_row(
        {
            "id": "a",
            "amount": 10,
            "nested": {
                "x": 1
            },
        },
        source_report_id="r1",
        source_section="obligations",
        source_row_index=0,
        organization_id="o1",
        root_party_id="p1",
        report_year=2025,
        report_quarter=1,
        source_is_signed=True,
        source_signed_date="2025-01-01",
        report_schema_version_source="1",
        report_type_source="main",
        is_party_office_source=False,
    )

    assert (
        result[
            "source__id"
        ]
        ==
        "a"
    )

    assert (
        result[
            "source__amount"
        ]
        ==
        10
    )

    assert (
        '"x": 1'
        in
        result[
            "source__nested"
        ]
    )


def test_confirmed_property_section_paths():

    from politdata.normalization.report_sections import (
        SECTION_PATHS,
    )

    assert SECTION_PATHS["realty"] == (
        "properties",
        "property_object",
    )

    assert SECTION_PATHS["transport"] == (
        "properties",
        "property_transport",
    )

    assert SECTION_PATHS["movable"] == (
        "properties",
        "property_movable",
    )

    assert SECTION_PATHS["intangible"] == (
        "properties",
        "property_intangible_asset",
    )

    assert SECTION_PATHS["paper"] == (
        "properties",
        "property_paper",
    )

