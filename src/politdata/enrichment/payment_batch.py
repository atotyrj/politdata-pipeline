
from __future__ import annotations

from pathlib import Path

import pandas as pd

from politdata.enrichment.payments import (
    classify_party_account,
    classify_internal_transfer_direction,
    classify_internal_transfer_funding_source,
    classify_payment,
)


PAYMENT_SECTIONS = (
    "monetary_contributions",
    "other_contributions",
    "state_funding",
    "other_incomes",
    "budget_expenses",
    "outgoing_expenses",
    "return_expenses",
    "transfer_expenses",
)


DERIVED_COLUMNS = (
    "party_account_type",
    "party_account_type_analytical",
    "party_account_type_resolution_method",
    "internal_transfer_direction",
    "internal_transfer_funding_source",
    "analytical_payment_type",
    "was_reclassified",
    "reclassification_rule",
    "funding_source_analytical",
)


def _none_if_missing(value):

    if pd.isna(value):
        return None

    return value


def _bool_value(value) -> bool:

    if pd.isna(value):
        return False

    return bool(value)


def _source_payment_type(
    row,
    section: str,
):

    if (
        "source_payment_type"
        in row.index
        and
        pd.notna(
            row["source_payment_type"]
        )
    ):
        return str(
            row["source_payment_type"]
        )

    return section


def derive_payment_enrichment(
    df: pd.DataFrame,
    *,
    section: str,
) -> pd.DataFrame:
    """
    Recalculate analytical payment fields from resolved
    prerequisite facts.

    This is deliberately not responsible for resolving:
    - organization identities
    - EDRPOU counterparty matching
    - confirmed state-account joins

    Those belong to the future batch orchestration layer.
    """

    if section not in PAYMENT_SECTIONS:

        raise ValueError(
            f"Unknown payment section: {section}"
        )


    output = []


    for row in df.to_dict(
        orient="records"
    ):

        source_type = (
            row.get(
                "source_payment_type"
            )
        )

        if source_type is None:
            source_type = section


        account = classify_party_account(

            state_funding_account_confirmed=
                _bool_value(
                    row.get(
                        "state_funding_account_confirmed"
                    )
                ),

            state_funding_form_code=
                _none_if_missing(
                    row.get(
                        "state_funding_form_code"
                    )
                ),

            party_account_type_source=
                _none_if_missing(
                    row.get(
                        "party_account_type_source"
                    )
                ),
        )


        transfer_direction = (
            classify_internal_transfer_direction(

                internal_transfer=
                    _bool_value(
                        row.get(
                            "internal_transfer"
                        )
                    ),

                internal_counterparty_organization_id=
                    _none_if_missing(
                        row.get(
                            "internal_counterparty_organization_id"
                        )
                    ),

                source_organization_id=
                    _none_if_missing(
                        row.get(
                            "internal_transfer_source_organization_id"
                        )
                    ),

                source_organization_level=
                    _none_if_missing(
                        row.get(
                            "internal_transfer_source_organization_level"
                        )
                    ),

                destination_organization_id=
                    _none_if_missing(
                        row.get(
                            "internal_transfer_destination_organization_id"
                        )
                    ),

                destination_organization_level=
                    _none_if_missing(
                        row.get(
                            "internal_transfer_destination_organization_level"
                        )
                    ),
            )
        )


        transfer_funding = (
            classify_internal_transfer_funding_source(

                internal_transfer_direction=
                    transfer_direction,

                payment_direction=
                    _none_if_missing(
                        row.get(
                            "payment_direction"
                        )
                    ),

                state_funding_account_confirmed=
                    _bool_value(
                        row.get(
                            "state_funding_account_confirmed"
                        )
                    ),

                state_funding_form_code=
                    _none_if_missing(
                        row.get(
                            "state_funding_form_code"
                        )
                    ),

                party_account_type_analytical=
                    account.party_account_type_analytical,
            )
        )


        payment = classify_payment(

            source_payment_type=
                source_type,

            internal_transfer=
                _bool_value(
                    row.get(
                        "internal_transfer"
                    )
                ),

            organization_level=
                _none_if_missing(
                    row.get(
                        "organization_level"
                    )
                ),

            state_funding_account_confirmed=
                _bool_value(
                    row.get(
                        "state_funding_account_confirmed"
                    )
                ),

            state_funding_form_code=
                _none_if_missing(
                    row.get(
                        "state_funding_form_code"
                    )
                ),

            party_account_type_analytical=
                account.party_account_type_analytical,
        )


        output.append(
            {
                "party_account_type":
                    account.party_account_type,

                "party_account_type_analytical":
                    account.party_account_type_analytical,

                "party_account_type_resolution_method":
                    account.party_account_type_resolution_method,

                "internal_transfer_direction":
                    transfer_direction,

                "internal_transfer_funding_source":
                    transfer_funding,

                "analytical_payment_type":
                    payment.analytical_payment_type,

                "was_reclassified":
                    payment.was_reclassified,

                "reclassification_rule":
                    payment.reclassification_rule,

                "funding_source_analytical":
                    payment.funding_source_analytical,
            }
        )


    return pd.DataFrame(
        output,
        index=df.index,
        columns=DERIVED_COLUMNS,
    )


def _comparison_series(
    series: pd.Series,
) -> pd.Series:
    """
    Normalize null representation for parity comparison only.
    """

    return (
        series
        .astype("string")
        .fillna("<NULL>")
    )


def validate_payment_enrichment_frame(
    df: pd.DataFrame,
    *,
    section: str,
):
    """
    Compare recalculated analytical fields against the
    currently persisted enriched dataset.

    Returns:
        summary_df,
        mismatch_samples_df
    """

    recalculated = (
        derive_payment_enrichment(
            df,
            section=section,
        )
    )


    summaries = []
    samples = []


    for column in DERIVED_COLUMNS:

        if column not in df.columns:

            summaries.append(
                {
                    "section":
                        section,

                    "column":
                        column,

                    "rows":
                        len(df),

                    "mismatches":
                        None,

                    "status":
                        "missing_existing_column",
                }
            )

            continue


        old = _comparison_series(
            df[column]
        )

        new = _comparison_series(
            recalculated[column]
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
                    len(df),

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

            sample_indices = (
                mismatch[
                    mismatch
                ]
                .index[:20]
            )


            for idx in sample_indices:

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
                    "party_account_type_source",
                    "state_funding_account_confirmed",
                    "state_funding_form_code",
                    "internal_transfer",
                ):

                    if context_column in df.columns:

                        sample[
                            context_column
                        ] = df.loc[
                            idx,
                            context_column,
                        ]


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


def validate_payment_directory(
    payment_dir,
):
    """
    Full read-only parity check across all 8 enriched payment
    parquet files.
    """

    payment_dir = Path(
        payment_dir
    )


    summaries = []
    samples = []


    for section in PAYMENT_SECTIONS:

        path = (
            payment_dir
            / f"{section}.parquet"
        )


        if not path.exists():

            raise FileNotFoundError(
                path
            )


        df = pd.read_parquet(
            path
        )


        summary, mismatch_samples = (
            validate_payment_enrichment_frame(
                df,
                section=section,
            )
        )


        summaries.append(
            summary
        )


        if len(
            mismatch_samples
        ):

            samples.append(
                mismatch_samples
            )


    summary_df = pd.concat(
        summaries,
        ignore_index=True,
    )


    if samples:

        sample_df = pd.concat(
            samples,
            ignore_index=True,
        )

    else:

        sample_df = pd.DataFrame()


    return (
        summary_df,
        sample_df,
    )



# ============================================================
# PAYMENT DIRECTORY ORCHESTRATION V0.1
# ============================================================

def rebuild_payment_enrichment_frame(
    payments: pd.DataFrame,
    *,
    section: str,
    organization_reference: pd.DataFrame,
    state_account_reference: pd.DataFrame,
) -> pd.DataFrame:
    """
    Rebuild the complete technical + analytical payment
    enrichment for one already-normalized/base-enriched
    payment frame.

    Recomputed fields:
    - payment direction
    - party account
    - confirmed state-account facts
    - internal organization counterparties
    - physical transfer direction
    - analytical account classification
    - analytical payment classification
    - analytical funding source

    Existing values in those fields are not trusted:
    they are recalculated and overwritten.

    Other source/base-enrichment columns are preserved.
    """

    from politdata.enrichment.payment_resolution import (
        resolve_payment_facts,
    )


    original_columns = list(
        payments.columns
    )


    base = (
        payments
        .reset_index(
            drop=True
        )
        .copy()
    )


    resolved = resolve_payment_facts(
        base,
        section=section,
        organization_reference=
            organization_reference,
        state_account_reference=
            state_account_reference,
    )


    derived = derive_payment_enrichment(
        resolved,
        section=section,
    )


    result = resolved.copy()


    for column in DERIVED_COLUMNS:

        result[
            column
        ] = derived[
            column
        ].to_numpy()


    # If rebuilding an already-enriched dataset, restore
    # its exact column order.
    #
    # If a future input lacks some newly-derived fields,
    # append them at the end instead.
    extra_columns = [
        column
        for column
        in result.columns
        if column not in original_columns
    ]


    target_columns = (
        [
            column
            for column
            in original_columns
            if column in result.columns
        ]
        +
        extra_columns
    )


    result = result[
        target_columns
    ]


    if len(result) != len(payments):

        raise RuntimeError(
            f"{section}: payment enrichment changed "
            f"row count {len(payments)} -> {len(result)}"
        )


    return result


def enrich_payment_directory(
    input_dir,
    output_dir,
    *,
    organization_reference,
    state_account_reference,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Rebuild all eight payment files into a separate directory.

    This function does not access RAW or API.

    `organization_reference` and `state_account_reference`
    may be DataFrames or parquet paths.
    """

    input_dir = Path(
        input_dir
    )

    output_dir = Path(
        output_dir
    )


    if isinstance(
        organization_reference,
        (
            str,
            Path,
        ),
    ):

        organization_reference = (
            pd.read_parquet(
                organization_reference
            )
        )


    if isinstance(
        state_account_reference,
        (
            str,
            Path,
        ),
    ):

        state_account_reference = (
            pd.read_parquet(
                state_account_reference
            )
        )


    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )


    summary = []


    for section in PAYMENT_SECTIONS:

        input_path = (
            input_dir
            / f"{section}.parquet"
        )

        output_path = (
            output_dir
            / f"{section}.parquet"
        )


        if not input_path.exists():

            raise FileNotFoundError(
                input_path
            )


        if (
            output_path.exists()
            and
            not overwrite
        ):

            raise FileExistsError(
                output_path
            )


        source = pd.read_parquet(
            input_path
        )


        rebuilt = (
            rebuild_payment_enrichment_frame(
                source,
                section=section,
                organization_reference=
                    organization_reference,
                state_account_reference=
                    state_account_reference,
            )
        )


        temp_path = (
            output_path
            .with_suffix(
                ".tmp.parquet"
            )
        )


        rebuilt.to_parquet(
            temp_path,
            index=False,
        )


        temp_path.replace(
            output_path
        )


        summary.append(
            {
                "section":
                    section,

                "rows":
                    len(rebuilt),

                "columns":
                    len(rebuilt.columns),

                "output":
                    str(output_path),
            }
        )


    return pd.DataFrame(
        summary
    )



# ============================================================
# NORMALIZED PAYMENT PIPELINE V0.1
# ============================================================

def rebuild_payment_from_normalized_frame(
    normalized: pd.DataFrame,
    *,
    section: str,
    report_context: pd.DataFrame,
    organization_reference: pd.DataFrame,
    report_account_reference: pd.DataFrame,
    state_account_reference: pd.DataFrame,
) -> pd.DataFrame:
    """
    Complete normalized -> enriched transformation
    for one payment section.
    """

    from politdata.enrichment.payment_base import (
        add_base_payment_enrichment,
    )


    base = add_base_payment_enrichment(
        normalized,
        section=section,
        report_context=
            report_context,
        organization_reference=
            organization_reference,
        report_account_reference=
            report_account_reference,
    )


    return (
        rebuild_payment_enrichment_frame(
            base,
            section=section,
            organization_reference=
                organization_reference,
            state_account_reference=
                state_account_reference,
        )
    )


def enrich_normalized_payment_directory(
    input_dir,
    output_dir,
    *,
    report_context,
    organization_reference,
    report_account_reference,
    state_account_reference,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Build all enriched payment parquet files directly
    from normalized_v0_1/payments.

    No RAW access.
    No API access.
    """

    input_dir = Path(
        input_dir
    )

    output_dir = Path(
        output_dir
    )


    def load_if_path(value):

        if isinstance(
            value,
            (
                str,
                Path,
            ),
        ):

            return pd.read_parquet(
                value
            )

        return value


    report_context = load_if_path(
        report_context
    )

    organization_reference = load_if_path(
        organization_reference
    )

    report_account_reference = load_if_path(
        report_account_reference
    )

    state_account_reference = load_if_path(
        state_account_reference
    )


    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )


    rows = []


    for section in PAYMENT_SECTIONS:

        input_path = (
            input_dir
            / f"{section}.parquet"
        )

        output_path = (
            output_dir
            / f"{section}.parquet"
        )


        if not input_path.exists():

            raise FileNotFoundError(
                input_path
            )


        if (
            output_path.exists()
            and
            not overwrite
        ):

            raise FileExistsError(
                output_path
            )


        normalized = pd.read_parquet(
            input_path
        )


        enriched = (
            rebuild_payment_from_normalized_frame(
                normalized,
                section=section,

                report_context=
                    report_context,

                organization_reference=
                    organization_reference,

                report_account_reference=
                    report_account_reference,

                state_account_reference=
                    state_account_reference,
            )
        )


        if len(enriched) != len(normalized):

            raise RuntimeError(
                f"{section}: row count changed."
            )


        temp_path = (
            output_path
            .with_suffix(
                ".tmp.parquet"
            )
        )


        enriched.to_parquet(
            temp_path,
            index=False,
        )


        temp_path.replace(
            output_path
        )


        rows.append(
            {
                "section":
                    section,

                "rows":
                    len(enriched),

                "columns":
                    len(
                        enriched.columns
                    ),

                "output":
                    str(
                        output_path
                    ),
            }
        )


    return pd.DataFrame(
        rows
    )

