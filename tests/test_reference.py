
import pandas as pd

from politdata.normalization.reference import (
    normalize_region,
    normalize_party_name_short,
    build_organization_reference,
    build_report_context,
)


def test_region_pol_tava():
    assert (
        normalize_region(
            "Полтавська обл."
        )
        == "Полтавська область"
    )


def test_region_kyiv_city():
    assert (
        normalize_region(
            "м. Київ"
        )
        == "м. Київ"
    )


def test_region_kyiv_oblast():
    assert (
        normalize_region(
            "Київська область"
        )
        == "Київська область"
    )


def test_reference_and_latest():

    organizations = pd.DataFrame(
        [
            {
                "organization_id": "p1",
                "root_party_id": "p1",
                "parent_id": None,
                "entity_type": "party",
                "code": "11111111",
                "name": "Current Party",
                "is_active": True,
            },
            {
                "organization_id": "o1",
                "root_party_id": "p1",
                "parent_id": "p1",
                "entity_type": "office",
                "code": "22222222",
                "name": "Current Office",
                "is_active": True,
            },
        ]
    )


    addresses = pd.DataFrame(
        [
            {
                "organization_id": "p1",
                "address_type": "register",
                "region": "м. Київ",
            },
            {
                "organization_id": "o1",
                "address_type": "register",
                "region": "Полтавська обл.",
            },
        ]
    )


    ref = build_organization_reference(
        organizations,
        addresses,
    )


    indexed = ref.set_index(
        "organization_id"
    )


    assert (
        indexed.loc[
            "p1",
            "region",
        ]
        == "Україна"
    )


    assert (
        indexed.loc[
            "o1",
            "region",
        ]
        == "Полтавська область"
    )


    assert (
        indexed.loc[
            "o1",
            "party_name_current",
        ]
        == "Current Party"
    )


    manifest = pd.DataFrame(
        [
            {
                "report_id": "r1",
                "organization_id": "o1",
                "root_party_id": "p1",
                "year": 2025,
                "quarter": 4,
                "official_selected_report_id": "r1",
                "analysis_selected_report_id": "r1",
                "analysis_selection_method": "official",
                "analysis_override": False,
            },
            {
                "report_id": "r2",
                "organization_id": "o1",
                "root_party_id": "p1",
                "year": 2026,
                "quarter": 1,
                "official_selected_report_id": "r2",
                "analysis_selected_report_id": "r2",
                "analysis_selection_method": "official",
                "analysis_override": False,
            },
        ]
    )


    context = build_report_context(
        manifest,
        ref,
    )


    latest = context[
        context[
            "is_latest_report_for_organization"
        ]
    ]


    assert len(latest) == 1

    assert (
        latest.iloc[0][
            "source_report_id"
        ]
        == "r2"
    )

    assert (
        latest.iloc[0][
            "data_recency_status"
        ]
        == "latest_data"
    )



def test_region_kyiv_city_organization_name():

    assert (
        normalize_region(
            "КИЇВСЬКА МІСЬКА ОРГАНІЗАЦІЯ "
            "ПОЛІТИЧНОЇ ПАРТІЇ"
        )
        == "м. Київ"
    )


def test_region_kyiv_city_inflected():

    assert (
        normalize_region(
            "Дарницька районна в м. Києві організація"
        )
        == "м. Київ"
    )


def test_region_kyiv_oblast_organization():

    assert (
        normalize_region(
            "КИЇВСЬКА ОБЛАСНА ОРГАНІЗАЦІЯ "
            "ПОЛІТИЧНОЇ ПАРТІЇ"
        )
        == "Київська область"
    )



def test_party_name_short_political_party():

    assert (
        normalize_party_name_short(
            'ПОЛІТИЧНА ПАРТІЯ «СЛУГА НАРОДУ»'
        )
        ==
        'СЛУГА НАРОДУ'
    )


def test_party_name_short_quotes():

    assert (
        normalize_party_name_short(
            'Політична партія "ГОЛОС"'
        )
        ==
        'ГОЛОС'
    )


def test_party_name_short_vo():

    assert (
        normalize_party_name_short(
            "ВСЕУКРАЇНСЬКЕ ОБ'ЄДНАННЯ «БАТЬКІВЩИНА»"
        )
        ==
        "ВО БАТЬКІВЩИНА"
    )


def test_party_normalization_does_not_change_office_name():

    organizations = pd.DataFrame(
        [
            {
                "organization_id": "p1",
                "root_party_id": "p1",
                "parent_id": None,
                "entity_type": "party",
                "code": "11111111",
                "name": 'ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»',
                "is_active": True,
            },
            {
                "organization_id": "o1",
                "root_party_id": "p1",
                "parent_id": "p1",
                "entity_type": "office",
                "code": "22222222",
                "name": 'ПОЛІТИЧНА ПАРТІЯ КИЇВСЬКА МІСЬКА ОРГАНІЗАЦІЯ «ТЕСТ»',
                "is_active": True,
            },
        ]
    )

    addresses = pd.DataFrame(
        [
            {
                "organization_id": "p1",
                "address_type": "register",
                "region": "м. Київ",
            },
            {
                "organization_id": "o1",
                "address_type": "register",
                "region": "м. Київ",
            },
        ]
    )

    result = (
        build_organization_reference(
            organizations,
            addresses,
        )
        .set_index(
            "organization_id"
        )
    )

    assert (
        result.loc[
            "p1",
            "party_name_current",
        ]
        ==
        "ТЕСТ"
    )

    assert (
        result.loc[
            "o1",
            "party_name_current",
        ]
        ==
        "ТЕСТ"
    )

    # Office name MUST remain source/current form.
    assert (
        result.loc[
            "o1",
            "organization_name_current",
        ]
        ==
        'ПОЛІТИЧНА ПАРТІЯ КИЇВСЬКА МІСЬКА ОРГАНІЗАЦІЯ «ТЕСТ»'
    )
