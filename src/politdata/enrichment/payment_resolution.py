
from __future__ import annotations

from typing import Optional

import pandas as pd

from politdata.enrichment.payments import (
    classify_internal_transfer_direction,
)


INCOMING_SECTIONS = {
    "monetary_contributions",
    "other_contributions",
    "state_funding",
    "other_incomes",
}


OUTGOING_SECTIONS = {
    "budget_expenses",
    "outgoing_expenses",
    "return_expenses",
    "transfer_expenses",
}


PAYMENT_SECTIONS = (
    INCOMING_SECTIONS
    |
    OUTGOING_SECTIONS
)


RESOLVED_COLUMNS = (
    "payment_direction",
    "party_account_iban",

    "state_funding_account_confirmed",
    "state_funding_form_source",
    "state_funding_form_code",

    "internal_counterparty_organization_id",
    "internal_counterparty_organization_name",
    "internal_counterparty_organization_level",

    "internal_transfer_source_organization_id",
    "internal_transfer_source_organization_level",

    "internal_transfer_destination_organization_id",
    "internal_transfer_destination_organization_level",

    "internal_transfer_direction",
)


def _clean_optional_text(
    value,
) -> Optional[str]:

    if value is None:
        return None

    if pd.isna(value):
        return None

    text = str(
        value
    ).strip()

    return (
        text
        if text
        else None
    )


def classify_state_funding_form(
    value,
) -> Optional[str]:
    """
    Classify the source field
    'Форма державного фінансування'.

    This is separate from account-type labels.
    """

    text = _clean_optional_text(
        value
    )

    if text is None:
        return None

    lowered = text.lower()


    if (
        "статут" in lowered
        and
        (
            "держав" in lowered
            or
            "бюджет" in lowered
        )
    ):

        return (
            "state_statutory_funding"
        )


    if (
        "відшкодуван" in lowered
        and
        (
            "агітац" in lowered
            or
            "передвибор" in lowered
        )
    ):

        return (
            "state_campaign_reimbursement"
        )


    return None


def payment_direction_for_section(
    section: str,
) -> str:

    if section in INCOMING_SECTIONS:
        return "incoming"

    if section in OUTGOING_SECTIONS:
        return "outgoing"

    raise ValueError(
        f"Unknown payment section: {section}"
    )


def party_account_column(
    columns,
) -> str:
    """
    Prefer canonical analytical column if already present.

    Otherwise use canonical account field produced by
    payment normalization.
    """

    columns = set(
        columns
    )


    candidates = (
        "receiver_account_iban_canonical",
        "party_account_iban",
    )


    for column in candidates:

        if column in columns:
            return column


    raise KeyError(
        "No canonical party-account column found. "
        f"Tried: {candidates}"
    )


def counterparty_code_column(
    section: str,
) -> str:

    direction = (
        payment_direction_for_section(
            section
        )
    )


    if direction == "incoming":
        return "payer_code_normalized"

    return "receiver_code_normalized"


def build_unique_organization_code_map(
    organization_reference: pd.DataFrame,
) -> pd.DataFrame:
    """
    Strict same-root organization matching by EDRPOU only.

    If the same code maps to more than one organization
    within the same root party, it is excluded instead of
    being guessed/fanned out.
    """

    required = {
        "root_party_id",
        "organization_id",
        "organization_code",
        "organization_name_current",
        "organization_level",
    }


    missing = (
        required
        -
        set(
            organization_reference.columns
        )
    )


    if missing:

        raise KeyError(
            "organization_reference missing columns: "
            f"{sorted(missing)}"
        )


    df = (
        organization_reference[
            [
                "root_party_id",
                "organization_id",
                "organization_code",
                "organization_name_current",
                "organization_level",
            ]
        ]
        .copy()
    )


    df[
        "organization_code"
    ] = (
        df[
            "organization_code"
        ]
        .astype("string")
        .str.strip()
    )


    df = df[
        df[
            "organization_code"
        ].notna()
        &
        (
            df[
                "organization_code"
            ]
            !=
            ""
        )
    ]


    counts = (
        df
        .groupby(
            [
                "root_party_id",
                "organization_code",
            ],
            dropna=False,
        )[
            "organization_id"
        ]
        .nunique()
        .rename(
            "_organization_count"
        )
        .reset_index()
    )


    unique_keys = (
        counts[
            counts[
                "_organization_count"
            ]
            ==
            1
        ][
            [
                "root_party_id",
                "organization_code",
            ]
        ]
    )


    result = (
        df
        .merge(
            unique_keys,
            on=[
                "root_party_id",
                "organization_code",
            ],
            how="inner",
            validate="many_to_one",
        )
        .drop_duplicates(
            subset=[
                "root_party_id",
                "organization_code",
            ]
        )
        .reset_index(
            drop=True
        )
    )


    return result


def prepare_state_account_reference(
    state_account_reference: pd.DataFrame,
) -> pd.DataFrame:
    """
    Normalize the canonical confirmed-state-account
    reference for joining to payment rows.
    """

    required = {
        "root_party_id",
        "organization_id",
        "party_account_iban",
        "state_funding_form_code",
    }


    missing = (
        required
        -
        set(
            state_account_reference.columns
        )
    )


    if missing:

        raise KeyError(
            "state account reference missing columns: "
            f"{sorted(missing)}"
        )


    df = (
        state_account_reference
        .copy()
    )


    if (
        "state_funding_source_forms_observed"
        in
        df.columns
    ):

        source_column = (
            "state_funding_source_forms_observed"
        )

    elif (
        "state_funding_form_source"
        in
        df.columns
    ):

        source_column = (
            "state_funding_form_source"
        )

    else:

        raise KeyError(
            "No state-funding source-form column found."
        )


    result = (
        df[
            [
                "root_party_id",
                "organization_id",
                "party_account_iban",
                "state_funding_form_code",
                source_column,
            ]
        ]
        .rename(
            columns={
                source_column:
                    "state_funding_form_source",
            }
        )
        .copy()
    )


    duplicate_keys = (
        result
        .duplicated(
            subset=[
                "root_party_id",
                "organization_id",
                "party_account_iban",
            ],
            keep=False,
        )
    )


    if duplicate_keys.any():

        raise ValueError(
            "Confirmed state-account reference "
            "contains duplicate join keys."
        )


    return result


def resolve_payment_facts(
    payments: pd.DataFrame,
    *,
    section: str,
    organization_reference: pd.DataFrame,
    state_account_reference: pd.DataFrame,
) -> pd.DataFrame:
    """
    Resolve all technical facts needed by analytical
    payment enrichment.

    Row count and row order are preserved.
    """

    if section not in PAYMENT_SECTIONS:

        raise ValueError(
            f"Unknown payment section: {section}"
        )


    required_payment_columns = {
        "root_party_id",
        "organization_id",
        "organization_level",
        "internal_transfer",
    }


    code_column = (
        counterparty_code_column(
            section
        )
    )


    required_payment_columns.add(
        code_column
    )


    missing = (
        required_payment_columns
        -
        set(
            payments.columns
        )
    )


    if missing:

        raise KeyError(
            f"{section} missing payment columns: "
            f"{sorted(missing)}"
        )


    account_column = (
        party_account_column(
            payments.columns
        )
    )


    direction = (
        payment_direction_for_section(
            section
        )
    )


    df = payments.copy()


    # Avoid collisions when validating an already-enriched
    # production table.
    existing_derived = [
        column
        for column
        in RESOLVED_COLUMNS
        if column in df.columns
    ]


    if existing_derived:

        df = df.drop(
            columns=
                existing_derived
        )


    df[
        "payment_direction"
    ] = direction


    df[
        "party_account_iban"
    ] = (
        payments[
            account_column
        ]
    )


    # --------------------------------------------------------
    # CONFIRMED STATE ACCOUNT
    # --------------------------------------------------------

    state_ref = (
        prepare_state_account_reference(
            state_account_reference
        )
        .rename(
            columns={
                "state_funding_form_source":
                    "_state_funding_form_source_ref",

                "state_funding_form_code":
                    "_state_funding_form_code_ref",
            }
        )
    )


    before_rows = len(
        df
    )


    df = df.merge(
        state_ref,

        on=[
            "root_party_id",
            "organization_id",
            "party_account_iban",
        ],

        how="left",
        validate="many_to_one",
        sort=False,
    )


    if len(df) != before_rows:

        raise RuntimeError(
            "State-account join changed payment row count."
        )


    df[
        "state_funding_account_confirmed"
    ] = (
        df[
            "_state_funding_form_code_ref"
        ]
        .notna()
    )


    # --------------------------------------------------------
    # FUNDING FORM
    #
    # On confirmed accounts the reference wins.
    #
    # For source state_funding rows, retain the source
    # transaction form even if the row itself does not
    # establish a confirmed account.
    # --------------------------------------------------------

    if (
        section
        ==
        "state_funding"
        and
        "payment_type_detail_source"
        in payments.columns
    ):

        source_forms = (
            payments[
                "payment_type_detail_source"
            ]
        )

        source_codes = (
            source_forms
            .map(
                classify_state_funding_form
            )
        )

    else:

        source_forms = pd.Series(
            pd.NA,
            index=df.index,
            dtype="string",
        )

        source_codes = pd.Series(
            pd.NA,
            index=df.index,
            dtype="string",
        )


    state_source_ref = (
        df[
            "_state_funding_form_source_ref"
        ]
    )


    df[
        "state_funding_form_source"
    ] = (
        state_source_ref.where(
            state_source_ref.notna(),
            source_forms,
        )
    )


    state_code_ref = (
        df[
            "_state_funding_form_code_ref"
        ]
    )


    df[
        "state_funding_form_code"
    ] = (
        state_code_ref.where(
            state_code_ref.notna(),
            source_codes,
        )
    )


    df = df.drop(
        columns=[
            "_state_funding_form_source_ref",
            "_state_funding_form_code_ref",
        ]
    )


    # --------------------------------------------------------
    # SAME-PARTY ORGANIZATION MATCH
    # --------------------------------------------------------

    org_map = (
        build_unique_organization_code_map(
            organization_reference
        )
        .rename(
            columns={
                "organization_id":
                    "_counterparty_organization_id",

                "organization_name_current":
                    "_counterparty_organization_name",

                "organization_level":
                    "_counterparty_organization_level",

                "organization_code":
                    "_counterparty_code",
            }
        )
    )


    counterparty_codes = (
        payments[
            code_column
        ]
        .astype("string")
        .str.strip()
    )


    df[
        "_counterparty_code"
    ] = counterparty_codes


    before_rows = len(
        df
    )


    df = df.merge(
        org_map,

        on=[
            "root_party_id",
            "_counterparty_code",
        ],

        how="left",
        validate="many_to_one",
        sort=False,
    )


    if len(df) != before_rows:

        raise RuntimeError(
            "Organization-code join changed payment row count."
        )


    internal_mask = (
        payments[
            "internal_transfer"
        ]
        .fillna(False)
        .astype(bool)
        .to_numpy()
    )


    # Only internal transfers may receive an internal
    # counterparty organization.
    df[
        "internal_counterparty_organization_id"
    ] = (
        df[
            "_counterparty_organization_id"
        ]
        .where(
            internal_mask
        )
    )


    df[
        "internal_counterparty_organization_name"
    ] = (
        df[
            "_counterparty_organization_name"
        ]
        .where(
            internal_mask
        )
    )


    df[
        "internal_counterparty_organization_level"
    ] = (
        df[
            "_counterparty_organization_level"
        ]
        .where(
            internal_mask
        )
    )


    df = df.drop(
        columns=[
            "_counterparty_code",
            "_counterparty_organization_id",
            "_counterparty_organization_name",
            "_counterparty_organization_level",
        ]
    )


    # --------------------------------------------------------
    # PHYSICAL SOURCE / DESTINATION
    # --------------------------------------------------------

    if direction == "outgoing":

        df[
            "internal_transfer_source_organization_id"
        ] = (
            df[
                "organization_id"
            ]
            .where(
                internal_mask
            )
        )


        df[
            "internal_transfer_source_organization_level"
        ] = (
            df[
                "organization_level"
            ]
            .where(
                internal_mask
            )
        )


        df[
            "internal_transfer_destination_organization_id"
        ] = (
            df[
                "internal_counterparty_organization_id"
            ]
        )


        df[
            "internal_transfer_destination_organization_level"
        ] = (
            df[
                "internal_counterparty_organization_level"
            ]
        )


    else:

        df[
            "internal_transfer_source_organization_id"
        ] = (
            df[
                "internal_counterparty_organization_id"
            ]
        )


        df[
            "internal_transfer_source_organization_level"
        ] = (
            df[
                "internal_counterparty_organization_level"
            ]
        )


        df[
            "internal_transfer_destination_organization_id"
        ] = (
            df[
                "organization_id"
            ]
            .where(
                internal_mask
            )
        )


        df[
            "internal_transfer_destination_organization_level"
        ] = (
            df[
                "organization_level"
            ]
            .where(
                internal_mask
            )
        )


    # --------------------------------------------------------
    # TRANSFER DIRECTION
    #
    # Apply pure business function only to internal rows.
    # Typically ~18k rather than all ~402k.
    # --------------------------------------------------------

    df[
        "internal_transfer_direction"
    ] = pd.NA


    internal_indices = (
        df.index[
            internal_mask
        ]
    )


    for idx in internal_indices:

        df.at[
            idx,
            "internal_transfer_direction",
        ] = (
            classify_internal_transfer_direction(

                internal_transfer=True,

                internal_counterparty_organization_id=
                    _clean_optional_text(
                        df.at[
                            idx,
                            "internal_counterparty_organization_id",
                        ]
                    ),

                source_organization_id=
                    _clean_optional_text(
                        df.at[
                            idx,
                            "internal_transfer_source_organization_id",
                        ]
                    ),

                source_organization_level=
                    _clean_optional_text(
                        df.at[
                            idx,
                            "internal_transfer_source_organization_level",
                        ]
                    ),

                destination_organization_id=
                    _clean_optional_text(
                        df.at[
                            idx,
                            "internal_transfer_destination_organization_id",
                        ]
                    ),

                destination_organization_level=
                    _clean_optional_text(
                        df.at[
                            idx,
                            "internal_transfer_destination_organization_level",
                        ]
                    ),
            )
        )


    return df


def compare_resolved_facts(
    existing: pd.DataFrame,
    recalculated: pd.DataFrame,
    *,
    section: str,
):
    """
    Compare technical resolution against current verified
    production enrichment.
    """

    summaries = []
    samples = []


    for column in RESOLVED_COLUMNS:

        if column not in existing.columns:

            summaries.append(
                {
                    "section":
                        section,

                    "column":
                        column,

                    "rows":
                        len(existing),

                    "mismatches":
                        None,

                    "status":
                        "missing_existing_column",
                }
            )

            continue


        old = (
            existing[
                column
            ]
            .astype("string")
            .fillna("<NULL>")
        )


        new = (
            recalculated[
                column
            ]
            .astype("string")
            .fillna("<NULL>")
        )


        mismatch = (
            old
            !=
            new
        )


        mismatch_count = int(
            mismatch.sum()
        )


        summaries.append(
            {
                "section":
                    section,

                "column":
                    column,

                "rows":
                    len(existing),

                "mismatches":
                    mismatch_count,

                "status":
                    (
                        "match"
                        if mismatch_count == 0
                        else
                        "mismatch"
                    ),
            }
        )


        if mismatch_count:

            for idx in (
                mismatch[
                    mismatch
                ]
                .index[:20]
            ):

                sample = {
                    "section":
                        section,

                    "row_index":
                        idx,

                    "column":
                        column,

                    "existing":
                        old.loc[idx],

                    "recalculated":
                        new.loc[idx],
                }


                for context_column in (
                    "source_report_id",
                    "party_name_current",
                    "organization_name_current",
                    "organization_level",
                    "source_payment_type",
                    "payment_direction",
                    "party_account_iban",
                    "payer_code_normalized",
                    "receiver_code_normalized",
                    "internal_transfer",
                ):

                    if (
                        context_column
                        in
                        existing.columns
                    ):

                        sample[
                            context_column
                        ] = (
                            existing.at[
                                idx,
                                context_column,
                            ]
                        )


                samples.append(
                    sample
                )


    return (
        pd.DataFrame(
            summaries
        ),
        pd.DataFrame(
            samples
        ),
    )
