
from __future__ import annotations

import json
import re


NORMALIZATION_VERSION = (
    "report_sections_v0_1"
)


SECTION_PATHS = {

    "realty": ("properties", "property_object"),

    "transport": ("properties", "property_transport"),

    "movable": ("properties", "property_movable"),

    "intangible": ("properties", "property_intangible_asset"),

    "paper": ("properties", "property_paper"),

    "obligations":
        ("obligations",),

    "head_info":
        ("head_info",),

    "organizations":
        ("organizations",),

    "regional_offices":
        ("regional_offices",),
}


def get_nested(
    obj,
    path,
):
    """
    Conservative path lookup.
    """

    value = obj

    for key in path:

        if not isinstance(
            value,
            dict,
        ):
            return None

        value = value.get(
            key
        )

    return value


def coerce_rows(
    value,
):
    """
    Convert a report section into zero or more row objects.

    - list -> one row per item
    - dict -> one row
    - scalar -> {"value": scalar}
    - None -> []

    Conservative support for wrapper dictionaries with a
    single list-like member such as results/data/items/list.
    """

    if value is None:
        return []


    if isinstance(
        value,
        list,
    ):

        rows = []

        for item in value:

            if isinstance(
                item,
                dict,
            ):
                rows.append(
                    item
                )

            else:
                rows.append(
                    {
                        "value":
                            item
                    }
                )

        return rows


    if isinstance(
        value,
        dict,
    ):

        wrapper_keys = (
            "results",
            "data",
            "items",
            "list",
        )

        list_members = [
            key
            for key
            in wrapper_keys
            if isinstance(
                value.get(key),
                list,
            )
        ]


        if (
            len(value) == 1
            and
            len(list_members) == 1
        ):

            return coerce_rows(
                value[
                    list_members[0]
                ]
            )


        return [
            value
        ]


    return [
        {
            "value":
                value
        }
    ]


def extract_section_rows(
    detail,
    section,
):
    """
    Extract one known structural section from report detail.
    """

    if section not in SECTION_PATHS:

        raise KeyError(
            section
        )


    value = get_nested(
        detail,
        SECTION_PATHS[
            section
        ],
    )


    return coerce_rows(
        value
    )


def safe_source_key(
    key,
):
    """
    Stable column-safe source field name.
    Unicode letters are preserved.
    """

    text = str(
        key
    ).strip()

    text = re.sub(
        r"[^\w]+",
        "_",
        text,
        flags=re.UNICODE,
    )

    text = text.strip(
        "_"
    )

    return (
        text
        or
        "field"
    )


def scalar_or_json(
    value,
):
    """
    Preserve scalars as scalars and nested structures as JSON.
    """

    if isinstance(
        value,
        (
            dict,
            list,
        ),
    ):

        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    return value


def normalize_source_row(
    source_row,
    *,
    source_report_id,
    source_section,
    source_row_index,
    organization_id,
    root_party_id,
    report_year,
    report_quarter,
    source_is_signed,
    source_signed_date,
    report_schema_version_source,
    report_type_source,
    is_party_office_source,
):
    """
    Structural normalization.

    All source top-level fields are preserved under source__*.
    The full source row is also retained as JSON.
    """

    if not isinstance(
        source_row,
        dict,
    ):

        source_row = {
            "value":
                source_row
        }


    result = {

        "source_report_id":
            str(
                source_report_id
            ),

        "source_section":
            source_section,

        "source_row_index":
            int(
                source_row_index
            ),

        "organization_id":
            (
                str(
                    organization_id
                )
                if
                organization_id
                is not None
                else
                None
            ),

        "root_party_id":
            (
                str(
                    root_party_id
                )
                if
                root_party_id
                is not None
                else
                None
            ),

        "report_year":
            report_year,

        "report_quarter":
            report_quarter,

        "source_is_signed":
            bool(
                source_is_signed
            ),

        "source_signed_date":
            source_signed_date,

        "report_schema_version_source":
            report_schema_version_source,

        "report_type_source":
            report_type_source,

        "is_party_office_source":
            is_party_office_source,

        "source_row_json":
            json.dumps(
                source_row,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
    }


    for key, value in source_row.items():

        base_column = (
            "source__"
            +
            safe_source_key(
                key
            )
        )

        column = base_column

        suffix = 2

        while column in result:

            column = (
                f"{base_column}_{suffix}"
            )

            suffix += 1


        result[
            column
        ] = scalar_or_json(
            value
        )


    return result
