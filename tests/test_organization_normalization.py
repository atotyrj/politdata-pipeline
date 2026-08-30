import pyarrow as pa

from politdata.normalization.organizations import (
    ORGANIZATION_ADDRESS_SCHEMA,
    ORGANIZATION_HEAD_SCHEMA,
    ORGANIZATION_SCHEMA,
    normalize_organization_card,
)


def test_normalizes_office_card_to_validated_contract():
    normalized = normalize_organization_card({
        "id": "office-1",
        "parent": {
            "id": "party-1",
        },
        "code": "12345678",
        "name": "Office",
        "is_active": True,
        "created_at": "2025-01-01 10:00:00",
        "updated_at": "2026-01-01 11:00:00",
        "actual_address_same_register": False,
        "head_info": {
            "head_last_name": "Last",
            "head_first_name": "First",
            "head_middle_name": "Middle",
        },
        "register_address": {
            "country": "Україна",
            "post_index": "01001",
            "region_not_provided": False,
        },
        "actual_address": None,
    })

    organization = normalized["organizations"][0]
    assert organization["entity_type"] == "office"
    assert organization["root_party_id"] == "party-1"
    assert organization["parent_id"] == "party-1"
    assert len(normalized["organization_heads"]) == 1
    assert len(normalized["organization_addresses"]) == 1
    assert normalized["organization_addresses"][0][
        "address_type"
    ] == "register"

    assert pa.Table.from_pylist(
        normalized["organizations"],
        schema=ORGANIZATION_SCHEMA,
    ).schema == ORGANIZATION_SCHEMA
    assert pa.Table.from_pylist(
        normalized["organization_heads"],
        schema=ORGANIZATION_HEAD_SCHEMA,
    ).schema == ORGANIZATION_HEAD_SCHEMA
    assert pa.Table.from_pylist(
        normalized["organization_addresses"],
        schema=ORGANIZATION_ADDRESS_SCHEMA,
    ).schema == ORGANIZATION_ADDRESS_SCHEMA


def test_party_card_uses_own_id_as_root():
    normalized = normalize_organization_card({
        "id": "party-1",
        "parent": [],
    })

    organization = normalized["organizations"][0]
    assert organization["entity_type"] == "party"
    assert organization["root_party_id"] == "party-1"
    assert organization["parent_id"] is None
