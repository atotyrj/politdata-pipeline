
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import re
import unicodedata

from politdata.normalization.accounts import (
    normalize_account_number,
)


NORMALIZATION_VERSION = "property_moneys_v0_1"


ZERO_WIDTH_RE = re.compile(
    r"[\u200b\u200c\u200d\u2060\ufeff]"
)


ACCOUNT_NUMBER_KEYS = (
    "account_number",
    "accountNumber",
    "iban",
    "IBAN",
    "account",
    "number",
    "bank_account",
    "bankAccount",
    "bank_account_number",
    "bankAccountNumber",
)


ACCOUNT_TYPE_KEYS = (
    "account_type",
    "accountType",
    "type",
    "account_kind",
    "accountKind",
    "kind",
    "money_type",
    "moneyType",
    "account_purpose",
    "accountPurpose",
    "purpose",
)


def clean_text(value):

    if value is None:
        return None

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


def _is_scalar(value):

    return (
        value is None
        or isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
            ),
        )
    )


def extract_property_moneys(detail):
    """
    Extract account/money snapshot rows from a report detail.

    Expected primary location:
        detail["properties"]["moneys"]

    Conservative fallbacks are supported for schema drift.
    """

    if not isinstance(
        detail,
        dict,
    ):
        return []


    properties = detail.get(
        "properties"
    )

    if not isinstance(
        properties,
        dict,
    ):
        return []


    preferred_keys = (
        "moneys",
        "money",
        "property_moneys",
        "propertyMoneys",
        "monies",
    )


    for key in preferred_keys:

        value = properties.get(
            key
        )

        if isinstance(
            value,
            list,
        ):
            return value


    # Schema-drift fallback:
    # only search immediately under "properties".
    for key, value in properties.items():

        normalized_key = (
            str(key)
            .lower()
            .replace(
                "_",
                "",
            )
        )

        if (
            isinstance(
                value,
                list,
            )
            and
            (
                "money"
                in normalized_key
                or
                "monies"
                in normalized_key
            )
        ):
            return value


    return []


def get_account_number_source(row):

    if not isinstance(
        row,
        dict,
    ):
        return None


    for key in ACCOUNT_NUMBER_KEYS:

        value = row.get(
            key
        )

        value = clean_text(
            value
        )

        if value:
            return value


    # Conservative schema-drift fallback.
    for key, value in row.items():

        key_norm = (
            str(key)
            .lower()
            .replace(
                "_",
                "",
            )
        )

        if (
            "iban"
            in key_norm
            or
            "accountnumber"
            in key_norm
        ):

            value = clean_text(
                value
            )

            if value:
                return value


    return None


def get_account_type_source(row):

    if not isinstance(
        row,
        dict,
    ):
        return None


    for key in ACCOUNT_TYPE_KEYS:

        value = row.get(
            key
        )

        if isinstance(
            value,
            (
                dict,
                list,
            ),
        ):
            continue

        value = clean_text(
            value
        )

        if value:
            return value


    return None



ACCOUNT_TYPE_LABELS = {

    "state_statutory_funding_account":
        "Рахунок державного фінансування статутної діяльності",

    "state_campaign_reimbursement_account":
        "Рахунок відшкодування витрат на передвиборну агітацію",

    "ordinary_account":
        "Поточний рахунок",

    "budget_account_unspecified":
        "Бюджетний рахунок (неуточнений)",

    "social_insurance_account":
        "Рахунок соціальних / страхових виплат",

    "deposit_account":
        "Депозитний рахунок",

    "transit_account":
        "Транзитний рахунок",

    "card_account":
        "Картковий рахунок",

    "escrow_account":
        "Рахунок ескроу",

    "other_special_account":
        "Інший спеціальний рахунок",

    "unknown":
        "Тип рахунку не визначено",
}


def account_type_label(
    account_type_code,
):

    return ACCOUNT_TYPE_LABELS.get(
        account_type_code,
        ACCOUNT_TYPE_LABELS[
            "unknown"
        ],
    )


def classify_account_type(
    account_type_source,
    source_row=None,
):
    """
    Classify the DECLARED source account type.

    This is intentionally conservative.

    Political state-financing accounts are split into:

    - state_statutory_funding_account
    - state_campaign_reimbursement_account

    A generic "Бюджетний рахунок" is NOT automatically
    treated as statutory state funding. It is resolved later
    using the history of the same organization + IBAN and
    direct state_funding receipts.

    Insurance/social-payment accounts are not treated as
    political state-financing accounts.
    """

    text = clean_text(
        account_type_source
    )

    text = (
        text.lower()
        if text
        else ""
    )


    # --------------------------------------------------------
    # 1. STATE REIMBURSEMENT OF ELECTION CAMPAIGN EXPENSES
    # --------------------------------------------------------

    if (
        re.search(
            r"відшкодув",
            text,
            flags=re.IGNORECASE,
        )
        and
        re.search(
            r"передвибор",
            text,
            flags=re.IGNORECASE,
        )
        and
        re.search(
            r"агітац",
            text,
            flags=re.IGNORECASE,
        )
    ):

        return (
            "state_campaign_reimbursement_account"
        )


    # --------------------------------------------------------
    # 2. STATE FUNDING OF STATUTORY ACTIVITY
    # --------------------------------------------------------

    if (
        (
            re.search(
                r"статутн",
                text,
                flags=re.IGNORECASE,
            )
            and
            (
                re.search(
                    r"держав",
                    text,
                    flags=re.IGNORECASE,
                )
                or
                re.search(
                    r"бюджет",
                    text,
                    flags=re.IGNORECASE,
                )
            )
        )

        or

        re.search(
            r"державн\w*\s+фінансув",
            text,
            flags=re.IGNORECASE,
        )
    ):

        return (
            "state_statutory_funding_account"
        )


    # --------------------------------------------------------
    # 3. GENERIC "BUDGET ACCOUNT"
    #
    # Do NOT decide source of financing here.
    # --------------------------------------------------------

    if re.fullmatch(
        r"\s*бюджетн\w*\s+рахунок\s*",
        text,
        flags=re.IGNORECASE,
    ):

        return (
            "budget_account_unspecified"
        )


    # --------------------------------------------------------
    # 4. SOCIAL / INSURANCE ACCOUNTS
    #
    # Explicitly NOT political state financing.
    # --------------------------------------------------------

    if re.search(
        r"""
        (
            соц
            |
            соціаль
            |
            страхов
            |
            лікарнян
            |
            фсс
        )
        """,
        text,
        flags=re.IGNORECASE | re.VERBOSE,
    ):

        return (
            "social_insurance_account"
        )


    # --------------------------------------------------------
    # 5. DEPOSIT
    # --------------------------------------------------------

    if re.search(
        r"""
        (
            депозит
            |
            вкладн
        )
        """,
        text,
        flags=re.IGNORECASE | re.VERBOSE,
    ):

        return (
            "deposit_account"
        )


    # --------------------------------------------------------
    # 6. TRANSIT
    # --------------------------------------------------------

    if re.search(
        r"транзит",
        text,
        flags=re.IGNORECASE,
    ):

        return (
            "transit_account"
        )


    # --------------------------------------------------------
    # 7. ESCROW
    # --------------------------------------------------------

    if re.search(
        r"ескро|умовн\w*\s+зберіган",
        text,
        flags=re.IGNORECASE,
    ):

        return (
            "escrow_account"
        )


    # --------------------------------------------------------
    # 8. CARD
    # --------------------------------------------------------

    if re.search(
        r"картков|корпоративн\w*\s+карт",
        text,
        flags=re.IGNORECASE,
    ):

        return (
            "card_account"
        )


    # --------------------------------------------------------
    # 9. ORDINARY CURRENT / SETTLEMENT ACCOUNT
    # --------------------------------------------------------

    if re.search(
        r"""
        (
            (^|\s)-?\s*поточн(ий|ого|ому|і)?(\s|$)
            |
            поточн\w*\s+рах
            |
            поточн\w*\s+р\/р
            |
            розрахунк\w*\s+рах
            |
            кредитн\w*\s+ліміт
        )
        """,
        text,
        flags=re.IGNORECASE | re.VERBOSE,
    ):

        return (
            "ordinary_account"
        )


    # --------------------------------------------------------
    # 10. OTHER EXPLICIT SPECIAL ACCOUNT
    # --------------------------------------------------------

    if re.search(
        r"спеціальн",
        text,
        flags=re.IGNORECASE,
    ):

        return (
            "other_special_account"
        )


    return "unknown"

def normalize_account_result(value):

    result = normalize_account_number(
        value
    )


    if is_dataclass(
        result
    ):

        data = asdict(
            result
        )

    elif isinstance(
        result,
        dict,
    ):

        data = dict(
            result
        )

    elif hasattr(
        result,
        "_asdict",
    ):

        data = dict(
            result._asdict()
        )

    elif hasattr(
        result,
        "__dict__",
    ):

        data = dict(
            vars(
                result
            )
        )

    else:

        data = {
            "value":
                result
        }


    canonical = None

    for key in (
        "canonical",
        "canonical_iban",
        "iban_canonical",
        "normalized",
        "normalized_account",
        "account_number_canonical",
    ):

        candidate = data.get(
            key
        )

        if candidate:

            canonical = str(
                candidate
            )

            break


    status = None

    for key in (
        "status",
        "normalization_status",
        "iban_status",
    ):

        candidate = data.get(
            key
        )

        if candidate is not None:

            status = str(
                candidate
            )

            break


    method = None

    for key in (
        "method",
        "normalization_method",
        "iban_method",
    ):

        candidate = data.get(
            key
        )

        if candidate is not None:

            method = str(
                candidate
            )

            break


    return {
        "account_iban_canonical":
            canonical,

        "account_iban_status":
            status,

        "account_iban_normalization_method":
            method,

        "account_normalization_result_json":
            json.dumps(
                data,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
    }


def normalize_property_money_row(
    row,
    *,
    source_report_id,
    organization_id,
    root_party_id,
    report_year,
    report_quarter,
):

    if not isinstance(
        row,
        dict,
    ):

        raise TypeError(
            "property_moneys row must be a dict"
        )


    account_number_source = (
        get_account_number_source(
            row
        )
    )


    account_type_source = (
        get_account_type_source(
            row
        )
    )


    normalized_account = (
        normalize_account_result(
            account_number_source
        )
    )


    account_type_analytical = (
        classify_account_type(
            account_type_source,
            row,
        )
    )


    result = {
        "source_report_id":
            str(
                source_report_id
            ),

        "organization_id":
            str(
                organization_id
            ),

        "root_party_id":
            str(
                root_party_id
            ),

        "report_year":
            report_year,

        "report_quarter":
            report_quarter,

        "party_account_iban_source":
            account_number_source,

        "party_account_iban":
            normalized_account[
                "account_iban_canonical"
            ],

        "party_account_iban_status":
            normalized_account[
                "account_iban_status"
            ],

        "party_account_iban_normalization_method":
            normalized_account[
                "account_iban_normalization_method"
            ],

        "party_account_type_source":
            account_type_source,

        "party_account_type_analytical":
            account_type_analytical,

        "party_account_type_resolution_method":
            (
                "property_moneys_declared_type"
                if
                account_type_analytical
                !=
                "unknown"
                else
                "property_moneys_unresolved_type"
            ),

        "source_row_json":
            json.dumps(
                row,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            ),
    }


    # Preserve all top-level source fields.
    # Scalar values get real columns.
    # Nested values are serialized.
    for key, value in row.items():

        safe_key = re.sub(
            r"[^0-9A-Za-zА-Яа-яІіЇїЄєҐґ_]+",
            "_",
            str(key),
        ).strip(
            "_"
        )


        if not safe_key:

            continue


        column = (
            "source__"
            + safe_key
        )


        if _is_scalar(
            value
        ):

            result[
                column
            ] = value

        else:

            result[
                column
            ] = json.dumps(
                value,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )


    return result
