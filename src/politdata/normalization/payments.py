
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import json
import os
import re
import unicodedata

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from politdata.normalization.accounts import (
    normalize_account_number,
)


NORMALIZATION_VERSION = "payments_v0_1"


# ============================================================
# PAYMENT SECTIONS
# ============================================================

PAYMENT_PATHS = {
    "monetary_contributions": (
        "payment_info",
        "incoming",
        "monetary_contributions",
    ),

    "other_contributions": (
        "payment_info",
        "incoming",
        "other_contributions",
    ),

    "state_funding": (
        "payment_info",
        "incoming",
        "state_funding",
    ),

    "other_incomes": (
        "payment_info",
        "incoming",
        "other_incomes",
    ),

    "budget_expenses": (
        "payment_info",
        "outgoing",
        "budget_expenses",
    ),

    "outgoing_expenses": (
        "payment_info",
        "outgoing",
        "outgoing_expenses",
    ),

    "return_expenses": (
        "payment_info",
        "outgoing",
        "return_expenses",
    ),

    "transfer_expenses": (
        "payment_info",
        "outgoing",
        "transfer_expenses",
    ),
}


INCOMING_TYPES = {
    "monetary_contributions",
    "other_contributions",
    "state_funding",
    "other_incomes",
}


OUTGOING_TYPES = {
    "budget_expenses",
    "outgoing_expenses",
    "return_expenses",
    "transfer_expenses",
}


# ============================================================
# SOURCE PAYMENT FIELDS OBSERVED IN POLITDATA
# ============================================================

KNOWN_SOURCE_FIELDS = {
    "created_at",
    "group_code",
    "id",
    "office_id",
    "party_id",

    "payer_account_iban",
    "payer_account_type",
    "payer_address",
    "payer_bank_address",
    "payer_bank_code",
    "payer_bank_name",
    "payer_birthday",
    "payer_code",
    "payer_name",
    "payer_type",

    "payment_amount",
    "payment_code",
    "payment_currency",
    "payment_description",
    "payment_instruction_date",
    "payment_number",
    "payment_operation_date",
    "payment_purpose",
    "payment_reason",
    "payment_type",

    "receiver_account_iban",
    "receiver_account_type",
    "receiver_address",
    "receiver_bank_address",
    "receiver_bank_code",
    "receiver_bank_name",
    "receiver_birthday",
    "receiver_code",
    "receiver_name",
    "receiver_type",

    "refund_amount",
    "refund_budget_amount",
    "refund_date",
    "refund_description",
    "refund_purpose",
    "refund_reason",

    "report_id",
    "updated_at",
}


# ============================================================
# BASIC NORMALIZATION
# ============================================================

ZERO_WIDTH_RE = re.compile(
    "[\u200b\u200c\u200d\u2060\ufeff]"
)


def clean_text(value: Any) -> str | None:

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = unicodedata.normalize(
        "NFKC",
        str(value),
    )

    text = ZERO_WIDTH_RE.sub(
        "",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text or None


def normalize_code(value: Any) -> str | None:
    """
    Conservative identifier normalization.

    - NFKC
    - zero-width removal
    - whitespace removal

    No zero-padding.
    No digit guessing.
    """

    text = clean_text(value)

    if text is None:
        return None

    compact = re.sub(
        r"\s+",
        "",
        text,
    )

    return compact or None


def normalize_counterparty_type(
    value: Any,
) -> str | None:

    text = clean_text(value)

    if text is None:
        return None

    lowered = text.casefold()

    if (
        "фоп" in lowered
        or "фізична особа-підприємець"
        in lowered
        or "фізична особа підприємець"
        in lowered
        or "фізична особа" in lowered
    ):
        return "Фізична особа"

    if "юридична особа" in lowered:
        return "Юридична особа"

    return text


LATIN_HOMOGLYPHS = str.maketrans(
    {
        "A": "А",
        "a": "а",
        "I": "І",
        "i": "і",
        "C": "С",
        "c": "с",
        "O": "О",
        "o": "о",
    }
)


FOP_PREFIX_RE = re.compile(
    r"""^\s*
    (?:
        фоп
        |
        фізична\s+особа[\s\-–—]*підприємець
    )
    [\s:,\-–—]*
    """,
    flags=re.I | re.X,
)


def normalize_person_name(
    value: Any,
    counterparty_type: str | None = None,
) -> str | None:

    text = clean_text(value)

    if text is None:
        return None

    # Only apply person-specific transformations
    # to rows analytically recognized as natural persons.
    if counterparty_type != "Фізична особа":
        return text

    text = FOP_PREFIX_RE.sub(
        "",
        text,
    )

    # Old exploratory notebooks showed recurring
    # Latin homoglyphs inside Cyrillic personal names.
    text = text.translate(
        LATIN_HOMOGLYPHS
    )

    text = re.sub(
        r"\s*[-–—]\s*",
        "-",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text or None


# ============================================================
# DATE / TIMESTAMP / DECIMAL
# ============================================================

def parse_date(value: Any) -> date | None:

    text = clean_text(value)

    if text is None:
        return None

    parsed = pd.to_datetime(
        text,
        errors="coerce",
    )

    if pd.isna(parsed):
        return None

    return parsed.date()


def parse_timestamp(
    value: Any,
) -> datetime | None:

    text = clean_text(value)

    if text is None:
        return None

    parsed = pd.to_datetime(
        text,
        errors="coerce",
    )

    if pd.isna(parsed):
        return None

    if getattr(
        parsed,
        "tzinfo",
        None,
    ) is not None:

        parsed = (
            parsed
            .tz_convert(None)
        )

    return parsed.to_pydatetime()


def parse_decimal(
    value: Any,
) -> Decimal | None:

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = clean_text(value)

    if text is None:
        return None

    text = text.replace(
        "\u00a0",
        "",
    )

    text = text.replace(
        " ",
        "",
    )

    # Decimal comma only when no decimal point exists.
    if (
        "," in text
        and "." not in text
    ):
        text = text.replace(
            ",",
            ".",
        )

    try:
        return Decimal(text)

    except InvalidOperation:
        return None


# ============================================================
# ACCOUNT NORMALIZATION WRAPPER
#
# Robust to the exact dataclass field naming used
# in accounts.py.
# ============================================================

def _result_dict(result) -> dict:

    if result is None:
        return {}

    if isinstance(
        result,
        dict,
    ):
        return result

    if is_dataclass(result):
        return asdict(result)

    if hasattr(
        result,
        "_asdict",
    ):
        return result._asdict()

    if hasattr(
        result,
        "__dict__",
    ):
        return vars(result)

    return {}


def _first(
    mapping,
    *names,
):

    for name in names:

        if name in mapping:
            return mapping[name]

    return None


def normalize_account_fields(
    value: Any,
) -> dict:

    result = normalize_account_number(
        value
    )

    data = _result_dict(
        result
    )

    canonical = _first(
        data,
        "canonical",
        "account_canonical",
        "iban",
    )

    status = _first(
        data,
        "status",
        "account_normalization_status",
    )

    method = _first(
        data,
        "method",
        "account_normalization_method",
    )

    valid = _first(
        data,
        "valid_iban",
        "is_valid",
        "account_valid_iban",
    )

    candidate_count = _first(
        data,
        "candidate_count",
        "account_candidate_count",
    )

    candidates = _first(
        data,
        "candidates",
        "account_candidates",
    )

    normalized_text = _first(
        data,
        "normalized_text",
        "account_normalized_text",
    )

    if candidates is None:
        candidates_json = None

    elif isinstance(
        candidates,
        str,
    ):
        candidates_json = candidates

    else:
        candidates_json = json.dumps(
            candidates,
            ensure_ascii=False,
            default=str,
        )

    return {
        "raw":
            clean_text(value),

        "canonical":
            clean_text(canonical),

        "status":
            clean_text(status),

        "method":
            clean_text(method),

        "valid":
            (
                bool(valid)
                if valid is not None
                else None
            ),

        "candidate_count":
            (
                int(candidate_count)
                if candidate_count is not None
                else None
            ),

        "candidates_json":
            candidates_json,

        "normalized_text":
            clean_text(normalized_text),
    }


# ============================================================
# PYARROW SCHEMA
# ============================================================

MONEY_TYPE = pa.decimal128(
    38,
    10,
)


NORMALIZED_PAYMENT_SCHEMA = pa.schema(
    [
        # report / analytical provenance
        (
            "source_report_id",
            pa.string(),
        ),
        (
            "official_selected_report_id",
            pa.string(),
        ),
        (
            "analysis_selected_report_id",
            pa.string(),
        ),

        (
            "organization_id",
            pa.string(),
        ),
        (
            "root_party_id",
            pa.string(),
        ),

        (
            "report_year",
            pa.int32(),
        ),
        (
            "report_quarter",
            pa.int8(),
        ),
        (
            "report_period",
            pa.string(),
        ),
        (
            "report_type",
            pa.string(),
        ),

        (
            "source_is_signed",
            pa.bool_(),
        ),
        (
            "source_signed_date",
            pa.timestamp(
                "us"
            ),
        ),

        (
            "analysis_override",
            pa.bool_(),
        ),
        (
            "analysis_selection_method",
            pa.string(),
        ),

        # payment section
        (
            "source_payment_type",
            pa.string(),
        ),
        (
            "payment_direction",
            pa.string(),
        ),

        # source technical IDs
        (
            "source_row_id",
            pa.string(),
        ),
        (
            "source_party_id",
            pa.string(),
        ),
        (
            "source_office_id",
            pa.string(),
        ),
        (
            "source_report_id_in_row",
            pa.string(),
        ),

        (
            "source_created_at",
            pa.timestamp(
                "us"
            ),
        ),
        (
            "source_updated_at",
            pa.timestamp(
                "us"
            ),
        ),

        (
            "group_code",
            pa.string(),
        ),

        # payer
        (
            "payer_name_source",
            pa.string(),
        ),
        (
            "payer_name_normalized",
            pa.string(),
        ),

        (
            "payer_code_raw",
            pa.string(),
        ),
        (
            "payer_code_normalized",
            pa.string(),
        ),

        (
            "payer_type_source",
            pa.string(),
        ),
        (
            "payer_type_normalized",
            pa.string(),
        ),

        (
            "payer_address",
            pa.string(),
        ),

        (
            "payer_birthday_raw",
            pa.string(),
        ),
        (
            "payer_birthday",
            pa.date32(),
        ),

        (
            "payer_account_iban_raw",
            pa.string(),
        ),
        (
            "payer_account_iban_canonical",
            pa.string(),
        ),
        (
            "payer_account_normalization_status",
            pa.string(),
        ),
        (
            "payer_account_normalization_method",
            pa.string(),
        ),
        (
            "payer_account_valid_iban",
            pa.bool_(),
        ),
        (
            "payer_account_candidate_count",
            pa.int16(),
        ),
        (
            "payer_account_candidates",
            pa.string(),
        ),
        (
            "payer_account_normalized_text",
            pa.string(),
        ),

        (
            "payer_account_type_source",
            pa.string(),
        ),

        (
            "payer_bank_code",
            pa.string(),
        ),
        (
            "payer_bank_name",
            pa.string(),
        ),
        (
            "payer_bank_address",
            pa.string(),
        ),

        # payment
        (
            "payment_amount_raw",
            pa.string(),
        ),
        (
            "payment_amount",
            MONEY_TYPE,
        ),

        (
            "payment_code",
            pa.string(),
        ),
        (
            "payment_currency",
            pa.string(),
        ),
        (
            "payment_description",
            pa.string(),
        ),

        (
            "payment_instruction_date_raw",
            pa.string(),
        ),
        (
            "payment_instruction_date",
            pa.date32(),
        ),

        (
            "payment_number",
            pa.string(),
        ),

        (
            "payment_operation_date_raw",
            pa.string(),
        ),
        (
            "payment_operation_date",
            pa.date32(),
        ),

        (
            "payment_purpose",
            pa.string(),
        ),
        (
            "payment_reason",
            pa.string(),
        ),
        (
            "payment_type_detail_source",
            pa.string(),
        ),

        # receiver
        (
            "receiver_name_source",
            pa.string(),
        ),
        (
            "receiver_name_normalized",
            pa.string(),
        ),

        (
            "receiver_code_raw",
            pa.string(),
        ),
        (
            "receiver_code_normalized",
            pa.string(),
        ),

        (
            "receiver_type_source",
            pa.string(),
        ),
        (
            "receiver_type_normalized",
            pa.string(),
        ),

        (
            "receiver_address",
            pa.string(),
        ),

        (
            "receiver_birthday_raw",
            pa.string(),
        ),
        (
            "receiver_birthday",
            pa.date32(),
        ),

        (
            "receiver_account_iban_raw",
            pa.string(),
        ),
        (
            "receiver_account_iban_canonical",
            pa.string(),
        ),
        (
            "receiver_account_normalization_status",
            pa.string(),
        ),
        (
            "receiver_account_normalization_method",
            pa.string(),
        ),
        (
            "receiver_account_valid_iban",
            pa.bool_(),
        ),
        (
            "receiver_account_candidate_count",
            pa.int16(),
        ),
        (
            "receiver_account_candidates",
            pa.string(),
        ),
        (
            "receiver_account_normalized_text",
            pa.string(),
        ),

        (
            "receiver_account_type_source",
            pa.string(),
        ),

        (
            "receiver_bank_code",
            pa.string(),
        ),
        (
            "receiver_bank_name",
            pa.string(),
        ),
        (
            "receiver_bank_address",
            pa.string(),
        ),

        # refunds
        (
            "refund_amount_raw",
            pa.string(),
        ),
        (
            "refund_amount",
            MONEY_TYPE,
        ),

        (
            "refund_budget_amount_raw",
            pa.string(),
        ),
        (
            "refund_budget_amount",
            MONEY_TYPE,
        ),

        (
            "refund_date_raw",
            pa.string(),
        ),
        (
            "refund_date",
            pa.date32(),
        ),

        (
            "refund_description",
            pa.string(),
        ),
        (
            "refund_purpose",
            pa.string(),
        ),
        (
            "refund_reason",
            pa.string(),
        ),

        # schema drift safety
        (
            "source_extra_json",
            pa.string(),
        ),
    ]
)


# ============================================================
# HELPERS
# ============================================================

def get_nested_list(
    obj: dict,
    path: tuple,
) -> list:

    current = obj

    for key in path:

        if not isinstance(
            current,
            dict,
        ):
            return []

        current = current.get(
            key
        )

    return (
        current
        if isinstance(
            current,
            list,
        )
        else []
    )


def _safe_json(
    value,
) -> str | None:

    if not value:
        return None

    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )


# ============================================================
# NORMALIZE ONE PAYMENT ROW
# ============================================================

def normalize_payment_row(
    row: dict,
    section: str,
    detail: dict,
    context: dict,
) -> dict:

    direction = (
        "incoming"
        if section in INCOMING_TYPES
        else "outgoing"
    )

    payer_type = (
        normalize_counterparty_type(
            row.get(
                "payer_type"
            )
        )
    )

    receiver_type = (
        normalize_counterparty_type(
            row.get(
                "receiver_type"
            )
        )
    )

    payer_account = (
        normalize_account_fields(
            row.get(
                "payer_account_iban"
            )
        )
    )

    receiver_account = (
        normalize_account_fields(
            row.get(
                "receiver_account_iban"
            )
        )
    )

    extras = {
        key: value
        for key, value
        in row.items()
        if key
        not in KNOWN_SOURCE_FIELDS
    }

    signed_date = (
        detail.get(
            "signed_date"
        )
    )

    source_is_signed = (
        clean_text(
            signed_date
        )
        is not None
    )

    return {
        "source_report_id":
            clean_text(
                context.get(
                    "source_report_id"
                )
            ),

        "official_selected_report_id":
            clean_text(
                context.get(
                    "official_selected_report_id"
                )
            ),

        "analysis_selected_report_id":
            clean_text(
                context.get(
                    "analysis_selected_report_id"
                )
            ),

        "organization_id":
            clean_text(
                context.get(
                    "organization_id"
                )
            ),

        "root_party_id":
            clean_text(
                context.get(
                    "root_party_id"
                )
            ),

        "report_year":
            int(
                context.get(
                    "year"
                )
            ),

        "report_quarter":
            int(
                context.get(
                    "quarter"
                )
            ),

        "report_period":
            clean_text(
                context.get(
                    "period_label"
                )
            ),

        "report_type":
            clean_text(
                context.get(
                    "report_type"
                )
                or
                detail.get(
                    "report_type"
                )
            ),

        "source_is_signed":
            source_is_signed,

        "source_signed_date":
            parse_timestamp(
                signed_date
            ),

        "analysis_override":
            bool(
                context.get(
                    "analysis_override",
                    False,
                )
            ),

        "analysis_selection_method":
            clean_text(
                context.get(
                    "analysis_selection_method"
                )
            ),

        "source_payment_type":
            section,

        "payment_direction":
            direction,

        "source_row_id":
            clean_text(
                row.get(
                    "id"
                )
            ),

        "source_party_id":
            clean_text(
                row.get(
                    "party_id"
                )
            ),

        "source_office_id":
            clean_text(
                row.get(
                    "office_id"
                )
            ),

        "source_report_id_in_row":
            clean_text(
                row.get(
                    "report_id"
                )
            ),

        "source_created_at":
            parse_timestamp(
                row.get(
                    "created_at"
                )
            ),

        "source_updated_at":
            parse_timestamp(
                row.get(
                    "updated_at"
                )
            ),

        "group_code":
            clean_text(
                row.get(
                    "group_code"
                )
            ),

        # payer
        "payer_name_source":
            clean_text(
                row.get(
                    "payer_name"
                )
            ),

        "payer_name_normalized":
            normalize_person_name(
                row.get(
                    "payer_name"
                ),
                payer_type,
            ),

        "payer_code_raw":
            clean_text(
                row.get(
                    "payer_code"
                )
            ),

        "payer_code_normalized":
            normalize_code(
                row.get(
                    "payer_code"
                )
            ),

        "payer_type_source":
            clean_text(
                row.get(
                    "payer_type"
                )
            ),

        "payer_type_normalized":
            payer_type,

        "payer_address":
            clean_text(
                row.get(
                    "payer_address"
                )
            ),

        "payer_birthday_raw":
            clean_text(
                row.get(
                    "payer_birthday"
                )
            ),

        "payer_birthday":
            parse_date(
                row.get(
                    "payer_birthday"
                )
            ),

        "payer_account_iban_raw":
            payer_account[
                "raw"
            ],

        "payer_account_iban_canonical":
            payer_account[
                "canonical"
            ],

        "payer_account_normalization_status":
            payer_account[
                "status"
            ],

        "payer_account_normalization_method":
            payer_account[
                "method"
            ],

        "payer_account_valid_iban":
            payer_account[
                "valid"
            ],

        "payer_account_candidate_count":
            payer_account[
                "candidate_count"
            ],

        "payer_account_candidates":
            payer_account[
                "candidates_json"
            ],

        "payer_account_normalized_text":
            payer_account[
                "normalized_text"
            ],

        "payer_account_type_source":
            clean_text(
                row.get(
                    "payer_account_type"
                )
            ),

        "payer_bank_code":
            clean_text(
                row.get(
                    "payer_bank_code"
                )
            ),

        "payer_bank_name":
            clean_text(
                row.get(
                    "payer_bank_name"
                )
            ),

        "payer_bank_address":
            clean_text(
                row.get(
                    "payer_bank_address"
                )
            ),

        # payment
        "payment_amount_raw":
            clean_text(
                row.get(
                    "payment_amount"
                )
            ),

        "payment_amount":
            parse_decimal(
                row.get(
                    "payment_amount"
                )
            ),

        "payment_code":
            clean_text(
                row.get(
                    "payment_code"
                )
            ),

        "payment_currency":
            clean_text(
                row.get(
                    "payment_currency"
                )
            ),

        "payment_description":
            clean_text(
                row.get(
                    "payment_description"
                )
            ),

        "payment_instruction_date_raw":
            clean_text(
                row.get(
                    "payment_instruction_date"
                )
            ),

        "payment_instruction_date":
            parse_date(
                row.get(
                    "payment_instruction_date"
                )
            ),

        "payment_number":
            clean_text(
                row.get(
                    "payment_number"
                )
            ),

        "payment_operation_date_raw":
            clean_text(
                row.get(
                    "payment_operation_date"
                )
            ),

        "payment_operation_date":
            parse_date(
                row.get(
                    "payment_operation_date"
                )
            ),

        "payment_purpose":
            clean_text(
                row.get(
                    "payment_purpose"
                )
            ),

        "payment_reason":
            clean_text(
                row.get(
                    "payment_reason"
                )
            ),

        "payment_type_detail_source":
            clean_text(
                row.get(
                    "payment_type"
                )
            ),

        # receiver
        "receiver_name_source":
            clean_text(
                row.get(
                    "receiver_name"
                )
            ),

        "receiver_name_normalized":
            normalize_person_name(
                row.get(
                    "receiver_name"
                ),
                receiver_type,
            ),

        "receiver_code_raw":
            clean_text(
                row.get(
                    "receiver_code"
                )
            ),

        "receiver_code_normalized":
            normalize_code(
                row.get(
                    "receiver_code"
                )
            ),

        "receiver_type_source":
            clean_text(
                row.get(
                    "receiver_type"
                )
            ),

        "receiver_type_normalized":
            receiver_type,

        "receiver_address":
            clean_text(
                row.get(
                    "receiver_address"
                )
            ),

        "receiver_birthday_raw":
            clean_text(
                row.get(
                    "receiver_birthday"
                )
            ),

        "receiver_birthday":
            parse_date(
                row.get(
                    "receiver_birthday"
                )
            ),

        "receiver_account_iban_raw":
            receiver_account[
                "raw"
            ],

        "receiver_account_iban_canonical":
            receiver_account[
                "canonical"
            ],

        "receiver_account_normalization_status":
            receiver_account[
                "status"
            ],

        "receiver_account_normalization_method":
            receiver_account[
                "method"
            ],

        "receiver_account_valid_iban":
            receiver_account[
                "valid"
            ],

        "receiver_account_candidate_count":
            receiver_account[
                "candidate_count"
            ],

        "receiver_account_candidates":
            receiver_account[
                "candidates_json"
            ],

        "receiver_account_normalized_text":
            receiver_account[
                "normalized_text"
            ],

        "receiver_account_type_source":
            clean_text(
                row.get(
                    "receiver_account_type"
                )
            ),

        "receiver_bank_code":
            clean_text(
                row.get(
                    "receiver_bank_code"
                )
            ),

        "receiver_bank_name":
            clean_text(
                row.get(
                    "receiver_bank_name"
                )
            ),

        "receiver_bank_address":
            clean_text(
                row.get(
                    "receiver_bank_address"
                )
            ),

        # refunds
        "refund_amount_raw":
            clean_text(
                row.get(
                    "refund_amount"
                )
            ),

        "refund_amount":
            parse_decimal(
                row.get(
                    "refund_amount"
                )
            ),

        "refund_budget_amount_raw":
            clean_text(
                row.get(
                    "refund_budget_amount"
                )
            ),

        "refund_budget_amount":
            parse_decimal(
                row.get(
                    "refund_budget_amount"
                )
            ),

        "refund_date_raw":
            clean_text(
                row.get(
                    "refund_date"
                )
            ),

        "refund_date":
            parse_date(
                row.get(
                    "refund_date"
                )
            ),

        "refund_description":
            clean_text(
                row.get(
                    "refund_description"
                )
            ),

        "refund_purpose":
            clean_text(
                row.get(
                    "refund_purpose"
                )
            ),

        "refund_reason":
            clean_text(
                row.get(
                    "refund_reason"
                )
            ),

        "source_extra_json":
            _safe_json(
                extras
            ),
    }


# ============================================================
# NORMALIZE ONE REPORT
# ============================================================

def normalize_report_payments(
    detail: dict,
    context: dict,
) -> dict[str, list[dict]]:

    expected_report_id = clean_text(
        context.get(
            "source_report_id"
        )
    )

    actual_report_id = clean_text(
        detail.get(
            "id"
        )
    )

    if (
        actual_report_id is not None
        and
        expected_report_id is not None
        and
        actual_report_id
        != expected_report_id
    ):
        raise ValueError(
            "Report ID mismatch: "
            f"expected={expected_report_id}, "
            f"actual={actual_report_id}"
        )


    source_year = detail.get(
        "year"
    )

    if source_year is not None:

        if int(source_year) != int(
            context.get(
                "year"
            )
        ):
            raise ValueError(
                "Report year mismatch."
            )


    source_quarter = detail.get(
        "quarter"
    )

    if source_quarter is not None:

        if int(source_quarter) != int(
            context.get(
                "quarter"
            )
        ):
            raise ValueError(
                "Report quarter mismatch."
            )


    result = {}


    for section, path in (
        PAYMENT_PATHS.items()
    ):

        source_rows = (
            get_nested_list(
                detail,
                path,
            )
        )

        normalized_rows = []


        for source_row in source_rows:

            if not isinstance(
                source_row,
                dict,
            ):
                continue

            normalized_rows.append(
                normalize_payment_row(
                    source_row,
                    section,
                    detail,
                    context,
                )
            )


        result[
            section
        ] = normalized_rows


    return result


# ============================================================
# RAW READER
# ============================================================

def read_report_detail(
    path: Path,
) -> dict:

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        payload = json.load(
            f
        )

    results = payload.get(
        "results"
    )

    if not isinstance(
        results,
        dict,
    ):
        raise ValueError(
            "Invalid report-detail payload."
        )

    return results


# ============================================================
# PARQUET FRAGMENTS
# ============================================================

def write_fragment(
    path: Path,
    rows: list[dict],
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    table = pa.Table.from_pylist(
        rows,
        schema=NORMALIZED_PAYMENT_SCHEMA,
    )

    tmp = path.with_suffix(
        ".tmp.parquet"
    )

    pq.write_table(
        table,
        tmp,
        compression="zstd",
    )

    os.replace(
        tmp,
        path,
    )


def write_empty_normalized_table(
    path: Path,
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    table = pa.Table.from_pylist(
        [],
        schema=NORMALIZED_PAYMENT_SCHEMA,
    )

    pq.write_table(
        table,
        path,
        compression="zstd",
    )
