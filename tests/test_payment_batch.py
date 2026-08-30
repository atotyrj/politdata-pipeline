
import pandas as pd

from politdata.enrichment.payment_batch import (
    derive_payment_enrichment,
    validate_payment_enrichment_frame,
)


def test_frame_derivation_state_budget():

    df = pd.DataFrame(
        [
            {
                "source_payment_type":
                    "budget_expenses",

                "organization_level":
                    "central",

                "state_funding_account_confirmed":
                    True,

                "state_funding_form_code":
                    "state_statutory_funding",

                "party_account_type_source":
                    "Поточний рахунок",

                "internal_transfer":
                    False,

                "payment_direction":
                    "outgoing",
            }
        ]
    )


    result = derive_payment_enrichment(
        df,
        section="budget_expenses",
    )


    row = result.iloc[0]


    assert (
        row[
            "party_account_type_analytical"
        ]
        ==
        "state_statutory_funding_account"
    )

    assert (
        row[
            "analytical_payment_type"
        ]
        ==
        "budget_expenses"
    )

    assert (
        row[
            "funding_source_analytical"
        ]
        ==
        "state_statutory_funding"
    )


def test_frame_derivation_office_budget():

    df = pd.DataFrame(
        [
            {
                "source_payment_type":
                    "budget_expenses",

                "organization_level":
                    "office",

                "state_funding_account_confirmed":
                    False,

                "state_funding_form_code":
                    None,

                "party_account_type_source":
                    "Поточний рахунок",

                "internal_transfer":
                    False,

                "payment_direction":
                    "outgoing",
            }
        ]
    )


    result = derive_payment_enrichment(
        df,
        section="budget_expenses",
    )


    row = result.iloc[0]


    assert (
        row[
            "analytical_payment_type"
        ]
        ==
        "outgoing_expenses"
    )

    assert (
        bool(
            row[
                "was_reclassified"
            ]
        )
        is True
    )

    assert (
        row[
            "funding_source_analytical"
        ]
        ==
        "mixed_or_unknown"
    )


def test_parity_validator_reports_zero_mismatch():

    df = pd.DataFrame(
        [
            {
                "source_payment_type":
                    "outgoing_expenses",

                "organization_level":
                    "central",

                "state_funding_account_confirmed":
                    False,

                "state_funding_form_code":
                    None,

                "party_account_type_source":
                    "Поточний рахунок",

                "internal_transfer":
                    False,

                "payment_direction":
                    "outgoing",

                "party_account_type":
                    "Поточний рахунок",

                "party_account_type_analytical":
                    "ordinary_account",

                "party_account_type_resolution_method":
                    "property_moneys_source_type",

                "internal_transfer_direction":
                    None,

                "internal_transfer_funding_source":
                    None,

                "analytical_payment_type":
                    "outgoing_expenses",

                "was_reclassified":
                    False,

                "reclassification_rule":
                    None,

                "funding_source_analytical":
                    None,
            }
        ]
    )


    summary, samples = (
        validate_payment_enrichment_frame(
            df,
            section="outgoing_expenses",
        )
    )


    assert (
        summary[
            "mismatches"
        ].fillna(1).sum()
        ==
        0
    )

    assert samples.empty



def test_rebuild_overwrites_stale_payment_enrichment():

    from politdata.enrichment.payment_batch import (
        rebuild_payment_enrichment_frame,
    )


    payments = pd.DataFrame(
        [
            {
                "root_party_id":
                    "party",

                "organization_id":
                    "central",

                "organization_level":
                    "central",

                "receiver_code_normalized":
                    "222",

                "receiver_account_iban_canonical":
                    "UA000000000000000000000000001",

                "party_account_type_source":
                    "Поточний рахунок",

                "internal_transfer":
                    True,

                "source_payment_type":
                    "budget_expenses",

                # -------------------------------
                # Deliberately WRONG stale fields
                # -------------------------------

                "payment_direction":
                    "incoming",

                "party_account_iban":
                    "WRONG",

                "state_funding_account_confirmed":
                    False,

                "state_funding_form_source":
                    None,

                "state_funding_form_code":
                    None,

                "internal_counterparty_organization_id":
                    "WRONG",

                "internal_counterparty_organization_name":
                    "WRONG",

                "internal_counterparty_organization_level":
                    "central",

                "internal_transfer_source_organization_id":
                    "WRONG",

                "internal_transfer_source_organization_level":
                    "office",

                "internal_transfer_destination_organization_id":
                    "WRONG",

                "internal_transfer_destination_organization_level":
                    "central",

                "internal_transfer_direction":
                    "office_to_central",

                "internal_transfer_funding_source":
                    "unknown",

                "party_account_type":
                    "WRONG",

                "party_account_type_analytical":
                    "unknown",

                "party_account_type_resolution_method":
                    "unresolved",

                "analytical_payment_type":
                    "outgoing_expenses",

                "was_reclassified":
                    True,

                "reclassification_rule":
                    "WRONG",

                "funding_source_analytical":
                    "unknown",
            }
        ]
    )


    organizations = pd.DataFrame(
        [
            {
                "root_party_id":
                    "party",

                "organization_id":
                    "central",

                "organization_code":
                    "111",

                "organization_name_current":
                    "CENTRAL",

                "organization_level":
                    "central",
            },
            {
                "root_party_id":
                    "party",

                "organization_id":
                    "office",

                "organization_code":
                    "222",

                "organization_name_current":
                    "OFFICE",

                "organization_level":
                    "office",
            },
        ]
    )


    state_accounts = pd.DataFrame(
        [
            {
                "root_party_id":
                    "party",

                "organization_id":
                    "central",

                "party_account_iban":
                    "UA000000000000000000000000001",

                "state_funding_form_code":
                    "state_statutory_funding",

                "state_funding_source_forms_observed":
                    (
                        "Державне фінансування статутної "
                        "діяльності політичної партії"
                    ),
            }
        ]
    )


    result = (
        rebuild_payment_enrichment_frame(
            payments,
            section="budget_expenses",
            organization_reference=
                organizations,
            state_account_reference=
                state_accounts,
        )
    )


    row = result.iloc[0]


    assert (
        row[
            "payment_direction"
        ]
        ==
        "outgoing"
    )

    assert (
        row[
            "party_account_iban"
        ]
        ==
        "UA000000000000000000000000001"
    )

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

    assert (
        row[
            "internal_transfer_funding_source"
        ]
        ==
        "state_statutory_funding"
    )

    assert (
        row[
            "party_account_type_analytical"
        ]
        ==
        "state_statutory_funding_account"
    )

    assert (
        row[
            "analytical_payment_type"
        ]
        ==
        "budget_expenses"
    )

    assert (
        bool(
            row[
                "was_reclassified"
            ]
        )
        is False
    )

    assert (
        row[
            "funding_source_analytical"
        ]
        ==
        "state_statutory_funding"
    )

