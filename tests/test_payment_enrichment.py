
import pandas as pd

from politdata.enrichment.payment_batch import (
    DERIVED_COLUMNS,
    derive_payment_enrichment,
)
from politdata.enrichment.payments import (
    STATE_STATUTORY_FUNDING,
    classify_party_account,
    classify_internal_transfer_direction,
    classify_internal_transfer_funding_source,
    classify_payment,
)


def test_empty_derived_payment_frame_keeps_contract_columns():

    result = derive_payment_enrichment(
        pd.DataFrame(),
        section="monetary_contributions",
    )

    assert list(result.columns) == list(DERIVED_COLUMNS)
    assert result.empty


def test_confirmed_state_account_overrides_declared_current_type():

    result = classify_party_account(
        state_funding_account_confirmed=True,
        state_funding_form_code=
            STATE_STATUTORY_FUNDING,
        party_account_type_source=
            "Поточний рахунок",
    )

    assert (
        result.party_account_type_analytical
        ==
        "state_statutory_funding_account"
    )

    assert (
        result.party_account_type_resolution_method
        ==
        "positive_transaction_in_state_funding_section"
    )


def test_current_account_is_ordinary_but_not_state():

    result = classify_party_account(
        state_funding_account_confirmed=False,
        party_account_type_source=
            "Поточний рахунок",
    )

    assert (
        result.party_account_type_analytical
        ==
        "ordinary_account"
    )


def test_unknown_account_type_is_preserved_as_declared_other():

    result = classify_party_account(
        state_funding_account_confirmed=False,
        party_account_type_source=
            "Інше-Транзитний рахунок",
    )

    assert (
        result.party_account_type_analytical
        ==
        "other_declared_account"
    )

    assert (
        result.party_account_type
        ==
        "Інше-Транзитний рахунок"
    )


def test_missing_account_type_is_unresolved():

    result = classify_party_account(
        state_funding_account_confirmed=False,
        party_account_type_source=None,
    )

    assert (
        result.party_account_type_analytical
        ==
        "unknown"
    )

    assert (
        result.party_account_type_resolution_method
        ==
        "unresolved"
    )


def test_internal_transfer_direction_central_to_office():

    result = classify_internal_transfer_direction(
        internal_transfer=True,
        internal_counterparty_organization_id=
            "office-1",
        source_organization_id=
            "central-1",
        source_organization_level=
            "central",
        destination_organization_id=
            "office-1",
        destination_organization_level=
            "office",
    )

    assert result == "central_to_office"


def test_internal_transfer_direction_intra_organization():

    result = classify_internal_transfer_direction(
        internal_transfer=True,
        internal_counterparty_organization_id=
            "central-1",
        source_organization_id=
            "central-1",
        source_organization_level=
            "central",
        destination_organization_id=
            "central-1",
        destination_organization_level=
            "central",
    )

    assert result == "intra_organization"


def test_internal_transfer_direction_unresolved_counterparty():

    result = classify_internal_transfer_direction(
        internal_transfer=True,
        internal_counterparty_organization_id=None,
        source_organization_id=
            "central-1",
        source_organization_level=
            "central",
        destination_organization_id=None,
        destination_organization_level=None,
    )

    assert (
        result
        ==
        "same_party_counterparty_unresolved"
    )


def test_central_to_office_state_transfer():

    result = (
        classify_internal_transfer_funding_source(
            internal_transfer_direction=
                "central_to_office",
            payment_direction=
                "outgoing",
            state_funding_account_confirmed=
                True,
            state_funding_form_code=
                STATE_STATUTORY_FUNDING,
            party_account_type_analytical=
                "state_statutory_funding_account",
        )
    )

    assert (
        result
        ==
        STATE_STATUTORY_FUNDING
    )


def test_central_to_office_private_transfer():

    result = (
        classify_internal_transfer_funding_source(
            internal_transfer_direction=
                "central_to_office",
            payment_direction=
                "outgoing",
            state_funding_account_confirmed=
                False,
            party_account_type_analytical=
                "ordinary_account",
        )
    )

    assert result == "private_or_non_state"


def test_central_to_office_incoming_does_not_infer_source():

    result = (
        classify_internal_transfer_funding_source(
            internal_transfer_direction=
                "central_to_office",
            payment_direction=
                "incoming",
            state_funding_account_confirmed=
                False,
            party_account_type_analytical=
                "ordinary_account",
        )
    )

    assert result == "unknown_source_account"


def test_office_to_office_is_mixed_or_unknown():

    result = (
        classify_internal_transfer_funding_source(
            internal_transfer_direction=
                "office_to_office",
            payment_direction=
                "outgoing",
        )
    )

    assert result == "mixed_or_unknown"


def test_state_funding_payment_keeps_source_type():

    result = classify_payment(
        source_payment_type=
            "state_funding",
        organization_level=
            "central",
        state_funding_account_confirmed=
            True,
        state_funding_form_code=
            STATE_STATUTORY_FUNDING,
        party_account_type_analytical=
            "state_statutory_funding_account",
    )

    assert (
        result.analytical_payment_type
        ==
        "state_funding"
    )

    assert (
        result.funding_source_analytical
        ==
        STATE_STATUTORY_FUNDING
    )

    assert result.was_reclassified is False


def test_central_budget_expense_on_confirmed_state_account_stays_budget():

    result = classify_payment(
        source_payment_type=
            "budget_expenses",
        organization_level=
            "central",
        state_funding_account_confirmed=
            True,
        state_funding_form_code=
            STATE_STATUTORY_FUNDING,
        party_account_type_analytical=
            "state_statutory_funding_account",
    )

    assert (
        result.analytical_payment_type
        ==
        "budget_expenses"
    )

    assert result.was_reclassified is False

    assert (
        result.funding_source_analytical
        ==
        STATE_STATUTORY_FUNDING
    )


def test_central_budget_expense_on_current_account_becomes_outgoing():

    result = classify_payment(
        source_payment_type=
            "budget_expenses",
        organization_level=
            "central",
        state_funding_account_confirmed=
            False,
        party_account_type_analytical=
            "ordinary_account",
    )

    assert (
        result.analytical_payment_type
        ==
        "outgoing_expenses"
    )

    assert result.was_reclassified is True

    assert (
        result.reclassification_rule
        ==
        "central_budget_expense_without_confirmed_state_account"
    )

    assert (
        result.funding_source_analytical
        ==
        "private_or_non_state"
    )


def test_office_budget_expense_becomes_outgoing_mixed():

    result = classify_payment(
        source_payment_type=
            "budget_expenses",
        organization_level=
            "office",
        state_funding_account_confirmed=
            False,
        party_account_type_analytical=
            "ordinary_account",
    )

    assert (
        result.analytical_payment_type
        ==
        "outgoing_expenses"
    )

    assert result.was_reclassified is True

    assert (
        result.funding_source_analytical
        ==
        "mixed_or_unknown"
    )

    assert (
        result.reclassification_rule
        ==
        "office_budget_expense_treated_as_ordinary_or_mixed"
    )


def test_other_payment_type_is_not_reclassified():

    result = classify_payment(
        source_payment_type=
            "outgoing_expenses",
        organization_level=
            "central",
        state_funding_account_confirmed=
            False,
        party_account_type_analytical=
            "ordinary_account",
    )

    assert (
        result.analytical_payment_type
        ==
        "outgoing_expenses"
    )

    assert result.was_reclassified is False

    assert (
        result.funding_source_analytical
        is None
    )


def test_internal_monetary_contribution_becomes_other_income():

    result = classify_payment(
        source_payment_type="monetary_contributions",
        internal_transfer=True,
        organization_level="central",
    )

    assert result.analytical_payment_type == "other_incomes"
    assert result.was_reclassified is True
    assert result.reclassification_rule == (
        "internal_monetary_contribution_treated_as_other_income"
    )
