
from decimal import Decimal

import pandas as pd

from politdata.reference import (
    build_report_account_reference,
    build_state_funding_account_reference,
)


def test_report_account_uses_normalized_analytical_type():

    source = pd.DataFrame(
        [
            {
                "source_report_id": "r1",
                "organization_id": "o1",
                "root_party_id": "p1",
                "party_account_iban": "UA1",

                "party_account_iban_source":
                    "UA1",

                "party_account_type_source":
                    "Транзитний рахунок",

                "party_account_type_analytical":
                    "transit_account",
            }
        ]
    )

    result = (
        build_report_account_reference(
            source
        )
    )

    assert (
        result.iloc[0][
            "party_account_type_analytical"
        ]
        ==
        "transit_account"
    )


def test_positive_state_receipt_confirms_account():

    transactions = pd.DataFrame(
        [
            {
                "root_party_id": "p1",
                "organization_id": "p1",

                "receiver_account_iban_canonical":
                    "UA1",

                "payment_amount":
                    Decimal("100.00"),

                "payment_operation_date":
                    "2025-01-01",

                "payment_type_detail_source":
                    (
                        "Державне фінансування "
                        "статутної діяльності "
                        "політичної партії"
                    ),
            }
        ]
    )

    orgs = pd.DataFrame(
        [
            {
                "organization_id": "p1",

                "party_name_current":
                    "ТЕСТ",

                "organization_name_current":
                    "ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»",

                "organization_level":
                    "central",
            }
        ]
    )

    result = (
        build_state_funding_account_reference(
            transactions,
            orgs,
        )
    )

    row = result.iloc[0]

    assert (
        row[
            "organization_name_current"
        ]
        ==
        "ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»"
    )

    assert (
        row[
            "positive_state_receipt_amount"
        ]
        ==
        Decimal(
            "100.0000000000"
        )
    )

    assert (
        bool(
            row[
                "state_funding_account_confirmed"
            ]
        )
        is True
    )
