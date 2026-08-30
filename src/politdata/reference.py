
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from politdata.normalization.reference import (
    build_organization_reference,
    build_report_context,
)


REPORT_ACCOUNT_RESOLUTION_METHOD = (
    "property_moneys_exact_report_org_iban"
)

STATE_FUNDING_ACCOUNT_EVIDENCE = (
    "positive_transaction_in_state_funding_section"
)


def _first_non_null(series):

    for value in series:

        if pd.notna(value):
            return value

    return pd.NA


def _unique_single(series):

    values = list(
        pd.unique(
            series.dropna()
        )
    )

    if not values:
        return pd.NA

    if len(values) != 1:

        raise ValueError(
            f"Expected one unique value: {values}"
        )

    return values[0]


def _unique_join(series):

    values = sorted(
        {
            str(value).strip()
            for value in series
            if (
                pd.notna(value)
                and str(value).strip()
            )
        }
    )

    if not values:
        return pd.NA

    return " | ".join(values)


def _to_decimal(value):

    if pd.isna(value):
        return None

    if isinstance(value, Decimal):
        return value

    text = (
        str(value)
        .strip()
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", ".")
    )

    if not text:
        return None

    try:

        return Decimal(text)

    except InvalidOperation as exc:

        raise ValueError(
            f"Cannot parse Decimal: {value!r}"
        ) from exc


def classify_state_funding_form(value):

    if pd.isna(value):
        return None

    text = (
        str(value)
        .strip()
        .lower()
    )

    if "статут" in text:

        return (
            "state_statutory_funding"
        )

    if (
        "відшкод" in text
        or
        "вибор" in text
    ):

        return (
            "state_campaign_reimbursement"
        )

    return None


def build_report_account_reference(
    property_moneys,
):

    keys = [
        "source_report_id",
        "organization_id",
        "root_party_id",
        "party_account_iban",
    ]

    required = {
        *keys,
        "party_account_iban_source",
        "party_account_type_source",
        "party_account_type_analytical",
    }

    missing = (
        required
        -
        set(
            property_moneys.columns
        )
    )

    if missing:

        raise KeyError(
            f"property_moneys missing: {sorted(missing)}"
        )

    result = (
        property_moneys
        .groupby(
            keys,
            dropna=False,
            sort=False,
            as_index=False,
        )
        .agg(
            party_account_iban_source=(
                "party_account_iban_source",
                _first_non_null,
            ),

            party_account_type_source=(
                "party_account_type_source",
                _first_non_null,
            ),

            party_account_type_analytical=(
                "party_account_type_analytical",
                _first_non_null,
            ),

            snapshot_rows=(
                "party_account_iban",
                "size",
            ),
        )
    )

    result[
        "party_account_type_resolution_method"
    ] = (
        REPORT_ACCOUNT_RESOLUTION_METHOD
    )

    return result[
        [
            "source_report_id",
            "organization_id",
            "root_party_id",
            "party_account_iban",
            "party_account_iban_source",
            "party_account_type_source",
            "party_account_type_analytical",
            "party_account_type_resolution_method",
            "snapshot_rows",
        ]
    ]


def build_state_funding_account_reference(
    state_funding,
    organization_reference,
):

    state = state_funding.copy()

    state[
        "_amount_decimal"
    ] = (
        state[
            "payment_amount"
        ]
        .map(
            _to_decimal
        )
    )

    positive = (
        state[
            "_amount_decimal"
        ]
        .map(
            lambda value:
                (
                    value is not None
                    and value > 0
                )
        )
        &
        state[
            "receiver_account_iban_canonical"
        ]
        .notna()
    )

    state = state[
        positive
    ].copy()

    state = state.rename(
        columns={
            "receiver_account_iban_canonical":
                "party_account_iban"
        }
    )

    state[
        "_form_code"
    ] = (
        state[
            "payment_type_detail_source"
        ]
        .map(
            classify_state_funding_form
        )
    )

    if state["_form_code"].isna().any():

        raise ValueError(
            "Unclassified positive state_funding row."
        )

    identity = (
        organization_reference[
            [
                "organization_id",
                "party_name_current",
                "organization_name_current",
                "organization_level",
            ]
        ]
    )

    state = state.merge(
        identity,
        on="organization_id",
        how="left",
        validate="many_to_one",
        sort=False,
    )

    keys = [
        "root_party_id",
        "organization_id",
        "party_account_iban",
    ]

    result = (
        state
        .groupby(
            keys,
            dropna=False,
            sort=False,
            as_index=False,
        )
        .agg(
            party_name_current=(
                "party_name_current",
                _unique_single,
            ),

            organization_name_current=(
                "organization_name_current",
                _unique_single,
            ),

            organization_level=(
                "organization_level",
                _unique_single,
            ),

            first_state_receipt_date=(
                "payment_operation_date",
                "min",
            ),

            last_state_receipt_date=(
                "payment_operation_date",
                "max",
            ),

            positive_state_receipt_rows=(
                "_amount_decimal",
                "size",
            ),

            positive_state_receipt_amount=(
                "_amount_decimal",
                "sum",
            ),

            state_funding_form_count=(
                "_form_code",
                "nunique",
            ),

            state_funding_forms_observed=(
                "_form_code",
                _unique_join,
            ),

            state_funding_source_forms_observed=(
                "payment_type_detail_source",
                _unique_join,
            ),

            state_funding_form_code=(
                "_form_code",
                _unique_single,
            ),
        )
    )

    result[
        "positive_state_receipt_amount"
    ] = (
        result[
            "positive_state_receipt_amount"
        ]
        .map(
            lambda value:
                (
                    value.quantize(
                        Decimal(
                            "0.0000000000"
                        )
                    )
                    if isinstance(
                        value,
                        Decimal,
                    )
                    else value
                )
        )
    )

    result[
        "state_funding_account_confirmed"
    ] = True

    result[
        "state_funding_account_evidence"
    ] = (
        STATE_FUNDING_ACCOUNT_EVIDENCE
    )

    return result


def _load(value):

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


def _atomic_write(frame, path):

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = path.with_suffix(
        ".tmp.parquet"
    )

    frame.to_parquet(
        temp,
        index=False,
    )

    temp.replace(path)


def rebuild_reference_layer(
    *,
    organizations,
    addresses,
    analysis_manifest,
    property_moneys,
    state_funding,
    output_root,
    overwrite=False,
):

    output_root = Path(
        output_root
    )

    organizations = _load(
        organizations
    )

    addresses = _load(
        addresses
    )

    analysis_manifest = _load(
        analysis_manifest
    )

    property_moneys = _load(
        property_moneys
    )

    state_funding = _load(
        state_funding
    )

    organization_reference = (
        build_organization_reference(
            organizations,
            addresses,
        )
    )

    report_context = (
        build_report_context(
            analysis_manifest,
            organization_reference,
        )
    )

    report_account_reference = (
        build_report_account_reference(
            property_moneys
        )
    )

    state_funding_account_reference = (
        build_state_funding_account_reference(
            state_funding,
            organization_reference,
        )
    )

    frames = {
        "organization_reference.parquet":
            organization_reference,

        "report_context.parquet":
            report_context,

        "report_account_reference.parquet":
            report_account_reference,

        "state_funding_account_reference.parquet":
            state_funding_account_reference,
    }

    for filename, frame in frames.items():

        path = (
            output_root
            / filename
        )

        if (
            path.exists()
            and
            not overwrite
        ):

            raise FileExistsError(path)

        _atomic_write(
            frame,
            path,
        )

    return frames
