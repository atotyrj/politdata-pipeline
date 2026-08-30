
import pandas as pd

from politdata.enrichment.payment_resolution import (
    classify_state_funding_form,
    payment_direction_for_section,
    build_unique_organization_code_map,
    resolve_payment_facts,
)


def test_state_funding_form_statutory():

    assert (
        classify_state_funding_form(
            "Державне фінансування статутної "
            "діяльності політичної партії"
        )
        ==
        "state_statutory_funding"
    )


def test_state_funding_form_campaign_reimbursement():

    assert (
        classify_state_funding_form(
            "Відшкодування витрат, пов'язаних "
            "з фінансуванням передвиборної агітації"
        )
        ==
        "state_campaign_reimbursement"
    )


def test_payment_direction():

    assert (
        payment_direction_for_section(
            "other_incomes"
        )
        ==
        "incoming"
    )

    assert (
        payment_direction_for_section(
            "outgoing_expenses"
        )
        ==
        "outgoing"
    )


def test_ambiguous_same_root_code_is_excluded():

    organizations = pd.DataFrame(
        [
            {
                "root_party_id": "p",
                "organization_id": "o1",
                "organization_code": "123",
                "organization_name_current": "A",
                "organization_level": "office",
            },
            {
                "root_party_id": "p",
                "organization_id": "o2",
                "organization_code": "123",
                "organization_name_current": "B",
                "organization_level": "office",
            },
            {
                "root_party_id": "p",
                "organization_id": "o3",
                "organization_code": "456",
                "organization_name_current": "C",
                "organization_level": "office",
            },
        ]
    )


    result = (
        build_unique_organization_code_map(
            organizations
        )
    )


    assert (
        set(
            result[
                "organization_code"
            ]
        )
        ==
        {"456"}
    )


def test_resolution_central_to_office_state_account():

    payments = pd.DataFrame(
        [
            {
                "root_party_id": "p",
                "organization_id": "central",
                "organization_level": "central",

                "receiver_code_normalized": "222",
                "internal_transfer": True,

                "receiver_account_iban_canonical":
                    "UA000000000000000000000000001",
            }
        ]
    )


    organizations = pd.DataFrame(
        [
            {
                "root_party_id": "p",
                "organization_id": "central",
                "organization_code": "111",
                "organization_name_current": "CENTRAL",
                "organization_level": "central",
            },
            {
                "root_party_id": "p",
                "organization_id": "office",
                "organization_code": "222",
                "organization_name_current": "OFFICE",
                "organization_level": "office",
            },
        ]
    )


    state_accounts = pd.DataFrame(
        [
            {
                "root_party_id": "p",
                "organization_id": "central",
                "party_account_iban":
                    "UA000000000000000000000000001",

                "state_funding_form_code":
                    "state_statutory_funding",

                "state_funding_source_forms_observed":
                    "Державне фінансування статутної "
                    "діяльності політичної партії",
            }
        ]
    )


    result = resolve_payment_facts(
        payments,
        section="outgoing_expenses",
        organization_reference=
            organizations,
        state_account_reference=
            state_accounts,
    )


    row = result.iloc[0]


    assert (
        bool(
            row[
                "state_funding_account_confirmed"
            ]
        )
        is True
    )

    assert (
        row[
            "internal_counterparty_organization_id"
        ]
        ==
        "office"
    )

    assert (
        row[
            "internal_transfer_direction"
        ]
        ==
        "central_to_office"
    )
