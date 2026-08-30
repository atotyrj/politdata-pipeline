
import pytest

from politdata.qa import (
    PAYMENT_EXPECTED_ROWS,
    REPORT_SECTION_EXPECTED_ROWS,
    validate_expected_counts,
)


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
