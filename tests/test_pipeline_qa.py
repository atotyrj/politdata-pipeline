
import pytest

from politdata.qa import (
    PAYMENT_EXPECTED_ROWS,
    REPORT_SECTION_EXPECTED_ROWS,
    validate_expected_counts,
    validate_party_income_benchmark,
)

import pandas as pd


def test_payment_regression_baseline_total():

    assert (
        sum(
            PAYMENT_EXPECTED_ROWS.values()
        )
        ==
        402_028
    )


def test_report_section_regression_baseline_total():

    assert (
        sum(
            REPORT_SECTION_EXPECTED_ROWS.values()
        )
        ==
        308_657
    )


def test_validate_expected_counts_accepts_exact():

    validate_expected_counts(
        {
            "a": 1,
            "b": 2,
        },
        {
            "a": 1,
            "b": 2,
        },
    )


def test_validate_expected_counts_rejects_change():

    with pytest.raises(
        RuntimeError
    ):

        validate_expected_counts(
            {
                "a": 1,
                "b": 99,
            },
            {
                "a": 1,
                "b": 2,
            },
        )


def test_party_income_benchmark(tmp_path):
    rows = {
        "monetary_contributions": [("central", 10), ("office", 20)],
        "other_contributions": [("office", 30)],
        "other_incomes": [("central", 40)],
    }
    for section, values in rows.items():
        pd.DataFrame([
            {
                "party_code": "39651598",
                "organization_level": level,
                "payment_amount": amount,
            }
            for level, amount in values
        ]).to_parquet(tmp_path / f"{section}.parquet", index=False)

    result = validate_party_income_benchmark(
        tmp_path,
        expected_sections={
            "monetary_contributions": 2,
            "other_contributions": 1,
            "other_incomes": 1,
        },
        expected_levels={"central": 2, "office": 2},
    )
    assert result["rows"] == 4
