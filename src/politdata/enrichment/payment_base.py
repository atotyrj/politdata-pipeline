
from __future__ import annotations

from typing import Optional

import pandas as pd

from politdata.enrichment.payment_resolution import (
    INCOMING_SECTIONS,
    OUTGOING_SECTIONS,
    PAYMENT_SECTIONS,
    build_unique_organization_code_map,
)


REPORT_CONTEXT_COLUMNS = (
    "organization_code",
    "organization_level",
    "organization_name_current",
    "party_code",
    "party_name_current",
    "region",
    "region_resolution_method",
    "region_resolution_source",
    "region_source",
    "region_source_address_type",
)


BASE_ENRICHMENT_COLUMNS = (
    "analysis_selected",
    "official_selected",

    "payer_same_party_code_match",
    "receiver_same_party_code_match",

    "payer_type_analytical",
    "receiver_type_analytical",

    "internal_transfer",
    "internal_transfer_rule",

    "party_account_type_source",
)


INTERNAL_TRANSFER_RULE = (
    "same_root_party_organization_code"
)


def _clean_code_series(
    series: pd.Series,
) -> pd.Series:

    return (
        series
        .astype("string")
        .str.strip()
    )


def _same_root_code_matches(
    payments: pd.DataFrame,
    *,
    code_column: str,
    organization_reference: pd.DataFrame,
) -> pd.Series:
    """
    Strict same-root EDRPOU match.

    No fuzzy names.
    No IBAN inference.
    No code padding.
    """

    org_map = (
        build_unique_organization_code_map(
            organization_reference
        )
    )


    valid_keys = set(
        zip(
            org_map[
                "root_party_id"
            ].astype("string"),

            org_map[
                "organization_code"
            ].astype("string"),
        )
    )


    roots = (
        payments[
            "root_party_id"
        ]
        .astype("string")
    )


    codes = (
        _clean_code_series(
            payments[
                code_column
            ]
        )
    )


    values = []


    for root, code in zip(
        roots,
        codes,
    ):

        if (
            pd.isna(root)
            or
            pd.isna(code)
        ):

            values.append(
                False
            )

        else:

            values.append(
                (
                    root,
                    code,
                )
                in
                valid_keys
            )


    return pd.Series(
        values,
        index=payments.index,
        dtype="bool",
    )


def _prepare_report_context(
    report_context: pd.DataFrame,
) -> pd.DataFrame:

    required = {
        "source_report_id",
        *REPORT_CONTEXT_COLUMNS,
    }


    missing = (
        required
        -
        set(
            report_context.columns
        )
    )


    if missing:

        raise KeyError(
            "report_context missing columns: "
            f"{sorted(missing)}"
        )


    if (
        report_context[
            "source_report_id"
        ]
        .duplicated()
        .any()
    ):

        raise ValueError(
            "report_context source_report_id "
            "must be unique."
        )


    return (
        report_context[
            [
                "source_report_id",
                *REPORT_CONTEXT_COLUMNS,
            ]
        ]
        .copy()
    )


def _prepare_report_account_reference(
    report_account_reference: pd.DataFrame,
) -> pd.DataFrame:

    required = {
        "source_report_id",
        "organization_id",
        "party_account_iban",
        "party_account_type_source",
    }


    missing = (
        required
        -
        set(
            report_account_reference.columns
        )
    )


    if missing:

        raise KeyError(
            "report_account_reference missing columns: "
            f"{sorted(missing)}"
        )


    ref = (
        report_account_reference[
            [
                "source_report_id",
                "organization_id",
                "party_account_iban",
                "party_account_type_source",
            ]
        ]
        .copy()
    )


    # --------------------------------------------------------
    # Ensure duplicate keys, if any, do not contain
    # conflicting declared account types.
    # --------------------------------------------------------

    conflicts = (
        ref
        .groupby(
            [
                "source_report_id",
                "organization_id",
                "party_account_iban",
            ],
            dropna=False,
        )[
            "party_account_type_source"
        ]
        .nunique(
            dropna=True
        )
    )


    if (
        conflicts
        >
        1
    ).any():

        raise ValueError(
            "report_account_reference contains "
            "conflicting account types for the same key."
        )


    return (
        ref
        .drop_duplicates(
            subset=[
                "source_report_id",
                "organization_id",
                "party_account_iban",
            ]
        )
        .reset_index(
            drop=True
        )
    )


def add_base_payment_enrichment(
    payments: pd.DataFrame,
    *,
    section: str,
    report_context: pd.DataFrame,
    organization_reference: pd.DataFrame,
    report_account_reference: pd.DataFrame,
) -> pd.DataFrame:
    """
    Add enrichment that is logically prior to payment
    resolution/business classification.

    Input:
        normalized payment rows.

    Adds:
        current organization/party context,
        region,
        official/analysis selection flags,
        strict same-party EDRPOU matches,
        counterparty analytical types,
        internal-transfer flag/rule,
        report-relative declared account type,
        source state-funding form.

    Important naming rule:
        organization_name_current
            = full organization name

        party_name_current
            = short/unified party name

    Existing stale derived values are discarded.
    """

    if section not in PAYMENT_SECTIONS:

        raise ValueError(
            f"Unknown payment section: {section}"
        )


    required = {
        "source_report_id",
        "official_selected_report_id",
        "organization_id",
        "root_party_id",
        "payer_code_normalized",
        "receiver_code_normalized",
        "payer_type_normalized",
        "receiver_type_normalized",
        "receiver_account_iban_canonical",
    }


    missing = (
        required
        -
        set(
            payments.columns
        )
    )


    if missing:

        raise KeyError(
            f"{section} missing normalized columns: "
            f"{sorted(missing)}"
        )


    df = (
        payments
        .reset_index(
            drop=True
        )
        .copy()
    )


    # --------------------------------------------------------
    # Remove any stale copies if function is accidentally
    # called on already-enriched rows.
    # --------------------------------------------------------

    stale_columns = [
        column
        for column
        in (
            *REPORT_CONTEXT_COLUMNS,
            *BASE_ENRICHMENT_COLUMNS,
            "state_funding_form",
        )
        if column in df.columns
    ]


    if stale_columns:

        df = df.drop(
            columns=
                stale_columns
        )


    # --------------------------------------------------------
    # CURRENT REPORT / ORGANIZATION CONTEXT
    # --------------------------------------------------------

    context = (
        _prepare_report_context(
            report_context
        )
    )


    before_rows = len(
        df
    )


    df = df.merge(
        context,
        on="source_report_id",
        how="left",
        validate="many_to_one",
        sort=False,
    )


    if len(df) != before_rows:

        raise RuntimeError(
            "report_context join changed row count."
        )


    if (
        df[
            "organization_level"
        ]
        .isna()
        .any()
    ):

        raise RuntimeError(
            "Some payment rows did not resolve "
            "report context."
        )


    # --------------------------------------------------------
    # REPORT SELECTION FLAGS
    # --------------------------------------------------------

    df[
        "analysis_selected"
    ] = True


    df[
        "official_selected"
    ] = (
        df[
            "source_report_id"
        ].astype("string")
        ==
        df[
            "official_selected_report_id"
        ].astype("string")
    )


    # --------------------------------------------------------
    # STRICT SAME-ROOT EDRPOU MATCHES
    # --------------------------------------------------------

    df[
        "payer_same_party_code_match"
    ] = (
        _same_root_code_matches(
            df,
            code_column=
                "payer_code_normalized",
            organization_reference=
                organization_reference,
        )
    )


    df[
        "receiver_same_party_code_match"
    ] = (
        _same_root_code_matches(
            df,
            code_column=
                "receiver_code_normalized",
            organization_reference=
                organization_reference,
        )
    )


    # --------------------------------------------------------
    # COUNTERPARTY ANALYTICAL TYPES
    # --------------------------------------------------------

    df[
        "payer_type_analytical"
    ] = (
        df[
            "payer_type_normalized"
        ]
        .astype("string")
    )


    df[
        "receiver_type_analytical"
    ] = (
        df[
            "receiver_type_normalized"
        ]
        .astype("string")
    )


    if section in INCOMING_SECTIONS:

        internal_mask = (
            df[
                "payer_same_party_code_match"
            ]
        )


        df.loc[
            internal_mask,
            "payer_type_analytical",
        ] = (
            "internal_party_transfer"
        )


    else:

        internal_mask = (
            df[
                "receiver_same_party_code_match"
            ]
        )


        df.loc[
            internal_mask,
            "receiver_type_analytical",
        ] = (
            "internal_party_transfer"
        )


    # --------------------------------------------------------
    # INTERNAL TRANSFER FLAG / RULE
    # --------------------------------------------------------

    df[
        "internal_transfer"
    ] = (
        internal_mask
        .fillna(False)
        .astype(bool)
    )


    df[
        "internal_transfer_rule"
    ] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="string",
    )


    df.loc[
        df[
            "internal_transfer"
        ],
        "internal_transfer_rule",
    ] = (
        INTERNAL_TRANSFER_RULE
    )


    # --------------------------------------------------------
    # REPORT-RELATIVE ACCOUNT TYPE SOURCE
    #
    # Auxiliary metadata only.
    # It is NOT authoritative evidence of funding origin.
    # --------------------------------------------------------

    account_ref = (
        _prepare_report_account_reference(
            report_account_reference
        )
    )


    account_join = (
        account_ref
        .rename(
            columns={
                "party_account_iban":
                    "receiver_account_iban_canonical",
            }
        )
    )


    before_rows = len(
        df
    )


    df = df.merge(
        account_join,
        on=[
            "source_report_id",
            "organization_id",
            "receiver_account_iban_canonical",
        ],
        how="left",
        validate="many_to_one",
        sort=False,
    )


    if len(df) != before_rows:

        raise RuntimeError(
            "report account join changed row count."
        )


    # --------------------------------------------------------
    # SOURCE STATE-FUNDING FORM
    #
    # Only the source state_funding section contains this
    # human-readable field.
    # --------------------------------------------------------

    if section == "state_funding":

        if (
            "payment_type_detail_source"
            not in df.columns
        ):

            raise KeyError(
                "state_funding missing "
                "payment_type_detail_source"
            )


        df[
            "state_funding_form"
        ] = (
            df[
                "payment_type_detail_source"
            ]
        )


    return df
