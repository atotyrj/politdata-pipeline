
import pandas as pd

from politdata.enrichment.payment_base import (
    add_base_payment_enrichment,
)


def _references():

    context = pd.DataFrame(
        [
            {
                "source_report_id": "r1",

                "organization_code": "111",
                "organization_level": "central",

                "organization_name_current":
                    "ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»",

                "party_code": "111",

                "party_name_current":
                    "ТЕСТ",

                "region": "Україна",
                "region_resolution_method":
                    "central_national",
                "region_resolution_source":
                    "organization_level",
                "region_source":
                    None,
                "region_source_address_type":
                    None,
            }
        ]
    )


    organizations = pd.DataFrame(
        [
            {
                "root_party_id": "p",
                "organization_id": "central",
                "organization_code": "111",

                "organization_name_current":
                    "ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»",

                "organization_level": "central",
            },
            {
                "root_party_id": "p",
                "organization_id": "office",
                "organization_code": "222",

                "organization_name_current":
                    "ТЕСТОВИЙ ОСЕРЕДОК",

                "organization_level": "office",
            },
        ]
    )


    accounts = pd.DataFrame(
        [
            {
                "source_report_id": "r1",
                "organization_id": "central",

                "party_account_iban":
                    "UA000000000000000000000000001",

                "party_account_type_source":
                    "Поточний рахунок",
            }
        ]
    )


    return (
        context,
        organizations,
        accounts,
    )


def test_base_keeps_full_organization_name_and_short_party_name():

    context, organizations, accounts = (
        _references()
    )


    payments = pd.DataFrame(
        [
            {
                "source_report_id": "r1",
                "official_selected_report_id": "r1",

                "organization_id": "central",
                "root_party_id": "p",

                "payer_code_normalized": None,
                "receiver_code_normalized": "222",

                "payer_type_normalized": None,
                "receiver_type_normalized":
                    "Юридична особа",

                "receiver_account_iban_canonical":
                    "UA000000000000000000000000001",
            }
        ]
    )


    result = add_base_payment_enrichment(
        payments,
        section="outgoing_expenses",
        report_context=context,
        organization_reference=
            organizations,
        report_account_reference=
            accounts,
    )


    row = result.iloc[0]


    assert (
        row[
            "organization_name_current"
        ]
        ==
        "ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»"
    )

    assert (
        row[
            "party_name_current"
        ]
        ==
        "ТЕСТ"
    )


def test_outgoing_internal_counterparty_type():

    context, organizations, accounts = (
        _references()
    )


    payments = pd.DataFrame(
        [
            {
                "source_report_id": "r1",
                "official_selected_report_id": "r1",

                "organization_id": "central",
                "root_party_id": "p",

                "payer_code_normalized": None,
                "receiver_code_normalized": "222",

                "payer_type_normalized": None,
                "receiver_type_normalized":
                    "Юридична особа",

                "receiver_account_iban_canonical":
                    "UA000000000000000000000000001",
            }
        ]
    )


    result = add_base_payment_enrichment(
        payments,
        section="outgoing_expenses",
        report_context=context,
        organization_reference=
            organizations,
        report_account_reference=
            accounts,
    )


    row = result.iloc[0]


    assert (
        bool(
            row[
                "receiver_same_party_code_match"
            ]
        )
        is True
    )

    assert (
        row[
            "receiver_type_analytical"
        ]
        ==
        "internal_party_transfer"
    )

    assert (
        bool(
            row[
                "internal_transfer"
            ]
        )
        is True
    )

    assert (
        row[
            "internal_transfer_rule"
        ]
        ==
        "same_root_party_organization_code"
    )

    assert (
        row[
            "party_account_type_source"
        ]
        ==
        "Поточний рахунок"
    )


def test_analysis_and_official_selection_flags():

    context, organizations, accounts = (
        _references()
    )


    payments = pd.DataFrame(
        [
            {
                "source_report_id": "r1",

                "official_selected_report_id":
                    "official-other",

                "organization_id": "central",
                "root_party_id": "p",

                "payer_code_normalized": None,
                "receiver_code_normalized": None,

                "payer_type_normalized": None,
                "receiver_type_normalized": None,

                "receiver_account_iban_canonical":
                    None,
            }
        ]
    )


    result = add_base_payment_enrichment(
        payments,
        section="other_incomes",
        report_context=context,
        organization_reference=
            organizations,
        report_account_reference=
            accounts,
    )


    row = result.iloc[0]


    assert (
        bool(
            row[
                "analysis_selected"
            ]
        )
        is True
    )

    assert (
        bool(
            row[
                "official_selected"
            ]
        )
        is False
    )
