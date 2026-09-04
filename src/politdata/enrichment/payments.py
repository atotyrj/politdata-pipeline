
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


STATE_STATUTORY_FUNDING = (
    "state_statutory_funding"
)

STATE_CAMPAIGN_REIMBURSEMENT = (
    "state_campaign_reimbursement"
)


STATE_FUNDING_ACCOUNT_EVIDENCE = (
    "positive_transaction_in_state_funding_section"
)


STATE_ACCOUNT_HUMAN_LABELS = {

    STATE_STATUTORY_FUNDING:
        (
            "Рахунок державного фінансування "
            "статутної діяльності"
        ),

    STATE_CAMPAIGN_REIMBURSEMENT:
        (
            "Рахунок відшкодування витрат "
            "на передвиборну агітацію"
        ),
}


STATE_ACCOUNT_MACHINE_TYPES = {

    STATE_STATUTORY_FUNDING:
        "state_statutory_funding_account",

    STATE_CAMPAIGN_REIMBURSEMENT:
        "state_campaign_reimbursement_account",
}


ORDINARY_ACCOUNT_MARKERS = (
    "поточн",
    "розрахунк",
)


@dataclass(frozen=True)
class AccountClassification:
    """
    Analytical classification of the reporting
    party/office account attached to a payment row.
    """

    party_account_type: str

    party_account_type_analytical: str

    party_account_type_resolution_method: str


@dataclass(frozen=True)
class PaymentClassification:
    """
    Final analytical classification of a payment row.
    """

    analytical_payment_type: str

    was_reclassified: bool

    reclassification_rule: Optional[str]

    funding_source_analytical: Optional[str]


def _clean_optional_text(
    value,
) -> Optional[str]:

    if value is None:
        return None

    text = str(
        value
    ).strip()

    return (
        text
        if text
        else None
    )


def is_ordinary_account_type(
    party_account_type_source,
) -> bool:
    """
    Conservative textual classification used only when
    the account is NOT confirmed from state_funding receipts.

    This does not establish funding origin.
    """

    text = _clean_optional_text(
        party_account_type_source
    )

    if text is None:
        return False

    lowered = text.lower()

    return any(
        marker in lowered
        for marker
        in ORDINARY_ACCOUNT_MARKERS
    )


def classify_party_account(
    *,
    state_funding_account_confirmed: bool,
    state_funding_form_code=None,
    party_account_type_source=None,
) -> AccountClassification:
    """
    State-funding evidence has priority over property_moneys
    account labels.

    A confirmed state account means that a positive receipt
    was observed in the actual state_funding section.

    property_moneys remains auxiliary account metadata.
    """

    state_form = _clean_optional_text(
        state_funding_form_code
    )

    source_type = _clean_optional_text(
        party_account_type_source
    )


    if state_funding_account_confirmed:

        if state_form in STATE_ACCOUNT_HUMAN_LABELS:

            return AccountClassification(

                party_account_type=
                    STATE_ACCOUNT_HUMAN_LABELS[
                        state_form
                    ],

                party_account_type_analytical=
                    STATE_ACCOUNT_MACHINE_TYPES[
                        state_form
                    ],

                party_account_type_resolution_method=
                    STATE_FUNDING_ACCOUNT_EVIDENCE,
            )


        # This situation should normally be caught by QA:
        # confirmed state account without a known form.
        return AccountClassification(

            party_account_type=(
                source_type
                or
                "Тип рахунку не визначено"
            ),

            party_account_type_analytical=
                "other_declared_account"
                if source_type
                else
                "unknown",

            party_account_type_resolution_method=
                STATE_FUNDING_ACCOUNT_EVIDENCE,
        )


    if is_ordinary_account_type(
        source_type
    ):

        return AccountClassification(

            party_account_type=
                source_type,

            party_account_type_analytical=
                "ordinary_account",

            party_account_type_resolution_method=
                "property_moneys_source_type",
        )


    if source_type:

        return AccountClassification(

            party_account_type=
                source_type,

            party_account_type_analytical=
                "other_declared_account",

            party_account_type_resolution_method=
                "property_moneys_source_type",
        )


    return AccountClassification(

        party_account_type=
            "Тип рахунку не визначено",

        party_account_type_analytical=
            "unknown",

        party_account_type_resolution_method=
            "unresolved",
    )


def classify_internal_transfer_direction(
    *,
    internal_transfer: bool,
    internal_counterparty_organization_id=None,
    source_organization_id=None,
    source_organization_level=None,
    destination_organization_id=None,
    destination_organization_level=None,
) -> Optional[str]:
    """
    Classify physical transfer direction after a same-root
    organization has already been matched by strict EDRPOU.

    No fuzzy name or IBAN matching is performed here.
    """

    if not internal_transfer:
        return None


    if internal_counterparty_organization_id is None:
        return (
            "same_party_counterparty_unresolved"
        )


    if (
        source_organization_id is not None
        and
        destination_organization_id is not None
        and
        source_organization_id
        ==
        destination_organization_id
    ):

        return (
            "intra_organization"
        )


    if (
        source_organization_level
        ==
        "central"
        and
        destination_organization_level
        ==
        "office"
    ):

        return (
            "central_to_office"
        )


    if (
        source_organization_level
        ==
        "office"
        and
        destination_organization_level
        ==
        "central"
    ):

        return (
            "office_to_central"
        )


    if (
        source_organization_level
        ==
        "office"
        and
        destination_organization_level
        ==
        "office"
    ):

        return (
            "office_to_office"
        )


    return (
        "other_same_party_transfer"
    )


def classify_internal_transfer_funding_source(
    *,
    internal_transfer_direction=None,
    payment_direction=None,
    state_funding_account_confirmed: bool = False,
    state_funding_form_code=None,
    party_account_type_analytical=None,
) -> Optional[str]:
    """
    Funding source can be established for central -> office
    only from the outgoing central row, because that row
    exposes the centre's source account.

    The corresponding incoming office row does not reveal
    that source account by itself.
    """

    if (
        internal_transfer_direction
        ==
        "central_to_office"
        and
        payment_direction
        ==
        "outgoing"
    ):

        if state_funding_account_confirmed:

            return (
                _clean_optional_text(
                    state_funding_form_code
                )
                or
                "unknown"
            )


        if (
            party_account_type_analytical
            ==
            "ordinary_account"
        ):

            return (
                "private_or_non_state"
            )


        return (
            "unknown"
        )


    if (
        internal_transfer_direction
        ==
        "central_to_office"
        and
        payment_direction
        ==
        "incoming"
    ):

        return (
            "unknown_source_account"
        )


    if (
        internal_transfer_direction
        in {
            "office_to_central",
            "office_to_office",
        }
    ):

        return (
            "mixed_or_unknown"
        )


    return None


def classify_payment(
    *,
    source_payment_type: str,
    internal_transfer: bool = False,
    organization_level=None,
    state_funding_account_confirmed: bool = False,
    state_funding_form_code=None,
    party_account_type_analytical=None,
) -> PaymentClassification:
    """
    Final source-preserving analytical payment classification.

    Source section remains unchanged elsewhere.

    Rules
    -----
    monetary_contributions + internal transfer:
        -> analytical other_incomes

    state_funding:
        stays state_funding and receives its state funding form.

    budget_expenses:
        central + confirmed state account
            -> stays budget_expenses

        all other cases
            -> analytical outgoing_expenses

    other payment sections:
        retain their source payment type.
    """

    source_type = _clean_optional_text(
        source_payment_type
    )


    if source_type is None:

        raise ValueError(
            "source_payment_type is required"
        )


    state_form = _clean_optional_text(
        state_funding_form_code
    )

    if source_type == "monetary_contributions" and internal_transfer:

        return PaymentClassification(

            analytical_payment_type=
                "other_incomes",

            was_reclassified=
                True,

            reclassification_rule=
                "internal_monetary_contribution_treated_as_other_income",

            funding_source_analytical=
                None,
        )


    # --------------------------------------------------------
    # Incoming state financing
    # --------------------------------------------------------

    if source_type == "state_funding":

        return PaymentClassification(

            analytical_payment_type=
                "state_funding",

            was_reclassified=
                False,

            reclassification_rule=
                None,

            funding_source_analytical=
                state_form
                or
                "unknown",
        )


    # --------------------------------------------------------
    # Source budget_expenses
    # --------------------------------------------------------

    if source_type == "budget_expenses":

        if (
            organization_level
            ==
            "central"
            and
            state_funding_account_confirmed
        ):

            return PaymentClassification(

                analytical_payment_type=
                    "budget_expenses",

                was_reclassified=
                    False,

                reclassification_rule=
                    None,

                funding_source_analytical=
                    state_form
                    or
                    "unknown",
            )


        if (
            organization_level
            ==
            "office"
        ):

            return PaymentClassification(

                analytical_payment_type=
                    "outgoing_expenses",

                was_reclassified=
                    True,

                reclassification_rule=(
                    "office_budget_expense_"
                    "treated_as_ordinary_or_mixed"
                ),

                funding_source_analytical=
                    "mixed_or_unknown",
            )


        if (
            party_account_type_analytical
            ==
            "ordinary_account"
        ):

            funding_source = (
                "private_or_non_state"
            )

        else:

            funding_source = (
                "unknown"
            )


        return PaymentClassification(

            analytical_payment_type=
                "outgoing_expenses",

            was_reclassified=
                True,

            reclassification_rule=(
                "central_budget_expense_without_"
                "confirmed_state_account"
            ),

            funding_source_analytical=
                funding_source,
        )


    # --------------------------------------------------------
    # All other source sections
    # --------------------------------------------------------

    return PaymentClassification(

        analytical_payment_type=
            source_type,

        was_reclassified=
            False,

        reclassification_rule=
            None,

        funding_source_analytical=
            None,
    )
