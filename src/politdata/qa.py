
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd
import pyarrow.parquet as pq


PAYMENT_EXPECTED_ROWS = {
    "monetary_contributions": 27_234,
    "other_contributions": 6_168,
    "state_funding": 96,
    "other_incomes": 19_007,
    "budget_expenses": 29_482,
    "outgoing_expenses": 319_901,
    "return_expenses": 137,
    "transfer_expenses": 3,
}


REPORT_SECTION_EXPECTED_ROWS = {
    "realty": 11_325,
    "transport": 1_325,
    "movable": 693,
    "intangible": 8_614,
    "paper": 0,
    "obligations": 26_132,
    "head_info": 78_791,
    "employee_counts": 78_791,
    "organizations": 308,
    "regional_offices": 102_678,
}


PAYMENT_PATHS = {
    section:
        Path("payments")
        / f"{section}.parquet"

    for section
    in PAYMENT_EXPECTED_ROWS
}


REPORT_SECTION_PATHS = {
    "realty":
        Path("properties/realty.parquet"),

    "transport":
        Path("properties/transport.parquet"),

    "movable":
        Path("properties/movable.parquet"),

    "intangible":
        Path("properties/intangible.parquet"),

    "paper":
        Path("properties/paper.parquet"),

    "obligations":
        Path("obligations/obligations.parquet"),

    "head_info":
        Path("report_state/head_info.parquet"),

    "employee_counts":
        Path("report_state/employee_counts.parquet"),

    "organizations":
        Path("report_state/organizations.parquet"),

    "regional_offices":
        Path("report_state/regional_offices.parquet"),
}


REFERENCE_IDENTITY_COLUMNS = (
    "organization_code",
    "organization_level",
    "organization_name_current",
    "party_code",
    "party_name_current",
    "region",
)


HOLOS_PARTY_CODE = "39651598"
HOLOS_PRIVATE_INCOME_EXPECTED = {
    "monetary_contributions": 1_194,
    "other_contributions": 182,
    "other_incomes": 251,
}
HOLOS_ORGANIZATION_LEVEL_EXPECTED = {
    "central": 947,
    "office": 680,
}


def validate_party_income_benchmark(
    payment_root,
    *,
    party_code=HOLOS_PARTY_CODE,
    expected_sections=HOLOS_PRIVATE_INCOME_EXPECTED,
    expected_levels=HOLOS_ORGANIZATION_LEVEL_EXPECTED,
):
    """Validate a known real-data coverage benchmark for private income."""

    payment_root = Path(payment_root)
    frames = []
    actual_sections = {}
    for section in expected_sections:
        frame = pd.read_parquet(
            payment_root / f"{section}.parquet",
            columns=[
                "party_code",
                "organization_level",
                "payment_amount",
            ],
        )
        selected = frame[
            frame["party_code"].astype("string").str.strip().eq(
                str(party_code)
            )
        ].copy()
        actual_sections[section] = len(selected)
        frames.append(selected)

    if actual_sections != dict(expected_sections):
        raise RuntimeError(
            "Party income section benchmark changed: "
            f"expected={dict(expected_sections)}, actual={actual_sections}"
        )
    combined = pd.concat(frames, ignore_index=True)
    actual_levels = {
        str(key): int(value)
        for key, value in combined["organization_level"]
        .value_counts(dropna=False).items()
    }
    if actual_levels != dict(expected_levels):
        raise RuntimeError(
            "Party income organization-level benchmark changed: "
            f"expected={dict(expected_levels)}, actual={actual_levels}"
        )
    return {
        "party_code": str(party_code),
        "rows": len(combined),
        "payment_amount_sum": str(
            combined["payment_amount"].sum(skipna=True)
        ),
        "sections": actual_sections,
        "organization_levels": actual_levels,
    }


def parquet_row_count(
    path,
) -> int:

    return int(
        pq.ParquetFile(
            path
        )
        .metadata
        .num_rows
    )


def validate_expected_counts(
    actual: Mapping[str, int],
    expected: Mapping[str, int],
) -> None:
    """
    Raise if any expected dataset count changed.
    """

    missing = (
        set(expected)
        -
        set(actual)
    )


    extra = (
        set(actual)
        -
        set(expected)
    )


    if missing or extra:

        raise RuntimeError(
            "Dataset set changed. "
            f"Missing={sorted(missing)}; "
            f"extra={sorted(extra)}"
        )


    bad = {
        name: {
            "expected": int(expected[name]),
            "actual": int(actual[name]),
        }

        for name in expected

        if (
            int(actual[name])
            !=
            int(expected[name])
        )
    }


    if bad:

        raise RuntimeError(
            "Regression row-count baseline changed: "
            f"{bad}"
        )


def collect_enriched_row_counts(
    output_root,
) -> pd.DataFrame:

    output_root = Path(
        output_root
    )


    rows = []


    for section, relative_path in (
        PAYMENT_PATHS.items()
    ):

        path = (
            output_root
            / relative_path
        )


        if not path.exists():

            raise FileNotFoundError(
                path
            )


        rows.append(
            {
                "layer":
                    "payments",

                "section":
                    section,

                "rows":
                    parquet_row_count(
                        path
                    ),

                "expected_rows":
                    PAYMENT_EXPECTED_ROWS[
                        section
                    ],
            }
        )


    for section, relative_path in (
        REPORT_SECTION_PATHS.items()
    ):

        path = (
            output_root
            / relative_path
        )


        if not path.exists():

            raise FileNotFoundError(
                path
            )


        rows.append(
            {
                "layer":
                    "report_sections",

                "section":
                    section,

                "rows":
                    parquet_row_count(
                        path
                    ),

                "expected_rows":
                    REPORT_SECTION_EXPECTED_ROWS[
                        section
                    ],
            }
        )


    result = pd.DataFrame(
        rows
    )


    payment_actual = {
        row[
            "section"
        ]:
            int(
                row[
                    "rows"
                ]
            )

        for row
        in rows

        if (
            row[
                "layer"
            ]
            ==
            "payments"
        )
    }


    section_actual = {
        row[
            "section"
        ]:
            int(
                row[
                    "rows"
                ]
            )

        for row
        in rows

        if (
            row[
                "layer"
            ]
            ==
            "report_sections"
        )
    }


    validate_expected_counts(
        payment_actual,
        PAYMENT_EXPECTED_ROWS,
    )


    validate_expected_counts(
        section_actual,
        REPORT_SECTION_EXPECTED_ROWS,
    )


    result[
        "matches_baseline"
    ] = (
        result[
            "rows"
        ]
        ==
        result[
            "expected_rows"
        ]
    )


    return result


def _compare_series(
    left,
    right,
) -> int:

    left = (
        left
        .astype("string")
        .fillna("<NULL>")
    )

    right = (
        right
        .astype("string")
        .fillna("<NULL>")
    )


    return int(
        (
            left
            !=
            right
        ).sum()
    )


def validate_payment_reference_identity(
    payment_root,
    organization_reference,
) -> pd.DataFrame:
    """
    Confirm universal organization/party identity fields
    in enriched payments exactly match the current
    organization reference.

    This specifically protects the contract:

        organization_name_current
            = full organization name

        party_name_current
            = short unified party name
    """

    payment_root = Path(
        payment_root
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


    required = {
        "organization_id",
        *REFERENCE_IDENTITY_COLUMNS,
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


    if (
        organization_reference[
            "organization_id"
        ]
        .duplicated()
        .any()
    ):

        raise ValueError(
            "organization_reference organization_id "
            "must be unique."
        )


    reference = (
        organization_reference[
            [
                "organization_id",
                *REFERENCE_IDENTITY_COLUMNS,
            ]
        ]
        .rename(
            columns={
                column:
                    f"_reference_{column}"

                for column
                in REFERENCE_IDENTITY_COLUMNS
            }
        )
    )


    rows = []


    for section in PAYMENT_EXPECTED_ROWS:

        path = (
            payment_root
            / f"{section}.parquet"
        )


        payments = pd.read_parquet(
            path,
            columns=[
                "organization_id",
                *REFERENCE_IDENTITY_COLUMNS,
            ],
        )


        joined = payments.merge(
            reference,
            on="organization_id",
            how="left",
            validate="many_to_one",
            sort=False,
        )


        unresolved = int(
            joined[
                "_reference_organization_level"
            ]
            .isna()
            .sum()
        )


        if unresolved:

            raise RuntimeError(
                f"{section}: {unresolved:,} payment rows "
                "did not resolve organization_reference."
            )


        for column in REFERENCE_IDENTITY_COLUMNS:

            mismatches = (
                _compare_series(
                    joined[
                        column
                    ],
                    joined[
                        f"_reference_{column}"
                    ],
                )
            )


            rows.append(
                {
                    "section":
                        section,

                    "column":
                        column,

                    "rows":
                        len(joined),

                    "mismatches":
                        mismatches,
                }
            )


    result = pd.DataFrame(
        rows
    )


    total_mismatches = int(
        result[
            "mismatches"
        ].sum()
    )


    if total_mismatches:

        raise RuntimeError(
            "Payment organization-reference identity "
            f"has {total_mismatches:,} field-level mismatches."
        )


    return result


def validate_enriched_output(
    output_root,
    *,
    organization_reference,
):
    """
    Top-level QA for the currently consolidated
    enrichment layers.
    """

    output_root = Path(
        output_root
    )


    counts = (
        collect_enriched_row_counts(
            output_root
        )
    )


    payment_identity = (
        validate_payment_reference_identity(
            output_root
            / "payments",

            organization_reference=
                organization_reference,
        )
    )


    party_income_benchmark = (
        validate_party_income_benchmark(
            output_root
            / "payments"
        )
    )


    return {
        "row_counts":
            counts,

        "payment_reference_identity":
            payment_identity,

        "party_income_benchmark":
            party_income_benchmark,
    }
