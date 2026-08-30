from __future__ import annotations

import pandas as pd
import pyarrow as pa


ADDRESS_STRING_FIELDS = (
    "country",
    "post_index",
    "region",
    "district",
    "city",
    "street",
    "building",
    "apartments",
    "common",
    "building_part_num",
    "address_uk",
    "address_en",
)

ADDRESS_BOOLEAN_FIELDS = (
    "post_index_not_provided",
    "district_not_provided",
    "common_not_provided",
    "city_not_provided",
    "region_not_provided",
    "street_not_provided",
    "building_not_provided",
    "building_part_num_not_provided",
    "apartments_not_provided",
    "address_en_not_provided",
    "address_uk_not_provided",
)

ORGANIZATION_SCHEMA = pa.schema([
    ("organization_id", pa.string()),
    ("root_party_id", pa.string()),
    ("parent_id", pa.string()),
    ("entity_type", pa.string()),
    ("code", pa.string()),
    ("name", pa.string()),
    ("is_active", pa.bool_()),
    ("created_at", pa.timestamp("ns")),
    ("updated_at", pa.timestamp("ns")),
    ("web_site_url", pa.string()),
    ("email", pa.string()),
    ("phone", pa.string()),
    ("actual_address_same_register", pa.bool_()),
])

ORGANIZATION_HEAD_SCHEMA = pa.schema([
    ("organization_id", pa.string()),
    ("head_last_name", pa.string()),
    ("head_first_name", pa.string()),
    ("head_middle_name", pa.string()),
])

ORGANIZATION_ADDRESS_SCHEMA = pa.schema(
    [
        ("organization_id", pa.string()),
        ("address_type", pa.string()),
    ]
    + [
        (field, pa.string())
        for field in ADDRESS_STRING_FIELDS
    ]
    + [
        (field, pa.bool_())
        for field in ADDRESS_BOOLEAN_FIELDS
    ]
)


def _string_or_none(value):
    if value is None:
        return None
    return str(value)


def _timestamp_or_none(value):
    if value is None or value == "":
        return None
    return pd.to_datetime(value, errors="raise")


def normalize_organization_card(record):
    """
    Normalize one organization-card ``results`` object.

    The output reproduces the validated normalized_v0_1 organization,
    head and address table contracts.
    """

    if not isinstance(record, dict):
        raise TypeError("Organization card must be a dictionary.")

    organization_id = _string_or_none(record.get("id"))
    if not organization_id:
        raise ValueError("Organization card missing id.")

    parent = record.get("parent")
    if isinstance(parent, dict):
        parent_id = _string_or_none(parent.get("id"))
        if not parent_id:
            raise ValueError(
                "Office organization card missing parent id."
            )
        entity_type = "office"
        root_party_id = parent_id
    else:
        parent_id = None
        entity_type = "party"
        root_party_id = organization_id

    organization = {
        "organization_id": organization_id,
        "root_party_id": root_party_id,
        "parent_id": parent_id,
        "entity_type": entity_type,
        "code": _string_or_none(record.get("code")),
        "name": _string_or_none(record.get("name")),
        "is_active": record.get("is_active"),
        "created_at": _timestamp_or_none(
            record.get("created_at")
        ),
        "updated_at": _timestamp_or_none(
            record.get("updated_at")
        ),
        "web_site_url": _string_or_none(
            record.get("web_site_url")
        ),
        "email": _string_or_none(record.get("email")),
        "phone": _string_or_none(record.get("phone")),
        "actual_address_same_register": record.get(
            "actual_address_same_register"
        ),
    }

    heads = []
    head = record.get("head_info")
    if isinstance(head, dict):
        heads.append({
            "organization_id": organization_id,
            "head_last_name": _string_or_none(
                head.get("head_last_name")
            ),
            "head_first_name": _string_or_none(
                head.get("head_first_name")
            ),
            "head_middle_name": _string_or_none(
                head.get("head_middle_name")
            ),
        })

    addresses = []
    for address_type, source_field in (
        ("register", "register_address"),
        ("actual", "actual_address"),
    ):
        address = record.get(source_field)
        if not isinstance(address, dict):
            continue

        row = {
            "organization_id": organization_id,
            "address_type": address_type,
        }
        for field in ADDRESS_STRING_FIELDS:
            row[field] = _string_or_none(address.get(field))
        for field in ADDRESS_BOOLEAN_FIELDS:
            row[field] = address.get(field)
        addresses.append(row)

    return {
        "organizations": [organization],
        "organization_heads": heads,
        "organization_addresses": addresses,
    }
