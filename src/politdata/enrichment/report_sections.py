
from __future__ import annotations

from pathlib import Path

import pandas as pd


SECTION_PATHS = {
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


CONTEXT_COLUMNS = (
    "organization_name_current",
    "organization_code",
    "organization_level",
    "region",

    "party_name_current",
    "party_code",

    "analysis_override",
    "analysis_selection_method",

    "official_selected_report_id",
    "analysis_selected_report_id",

    "continuity_exact",
)


FINAL_ENRICHMENT_COLUMNS = (
    "organization_name_current",
    "organization_code",
    "organization_level",
    "region",

    "party_name_current",
    "party_code",

    "analysis_override",
    "analysis_selection_method",

    "official_selected_report_id",
    "analysis_selected_report_id",

    "continuity_exact",

    "analysis_selected",
    "official_selected",

    "is_latest_data",
    "data_recency_status",

    "report_period",
)


def report_period_label(
    year,
    quarter,
):
    """
    Canonical analytical period label.

    Quarterly:
        2025Q1 ... 2025Q4

    Annual (quarter=5):
        2025
    """

    if pd.isna(year):
        return None


    try:
        year = int(year)

    except Exception:
        return None


    try:
        quarter = int(quarter)

    except Exception:
        quarter = None


    if quarter == 5:
        return str(year)


    if quarter in (
        1,
        2,
        3,
        4,
    ):

        return (
            f"{year}Q{quarter}"
        )


    return str(year)


def _prepare_report_context(
    report_context: pd.DataFrame,
) -> pd.DataFrame:

    required = {
        "source_report_id",
        "is_latest_report_for_organization",
        *CONTEXT_COLUMNS,
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
                *CONTEXT_COLUMNS,
                "is_latest_report_for_organization",
            ]
        ]
        .copy()
    )


def enrich_report_section_frame(
    normalized: pd.DataFrame,
    *,
    report_context: pd.DataFrame,
) -> pd.DataFrame:
    """
    Generic normalized -> enriched transformation
    for all ten report-state / snapshot sections.

    Existing normalized fields are preserved unchanged.
    """

    required = {
        "source_report_id",
        "report_year",
        "report_quarter",
    }


    missing = (
        required
        -
        set(
            normalized.columns
        )
    )


    if missing:

        raise KeyError(
            "normalized section missing columns: "
            f"{sorted(missing)}"
        )


    original_columns = list(
        normalized.columns
    )


    # --------------------------------------------------------
    # Safe rebuild if called on stale enriched input.
    # --------------------------------------------------------

    stale = [
        column
        for column
        in FINAL_ENRICHMENT_COLUMNS
        if column
        in normalized.columns
    ]


    base = (
        normalized
        .drop(
            columns=stale,
            errors="ignore",
        )
        .reset_index(
            drop=True
        )
        .copy()
    )


    context = (
        _prepare_report_context(
            report_context
        )
    )


    before_rows = len(
        base
    )


    result = base.merge(
        context,
        on="source_report_id",
        how="left",
        validate="many_to_one",
        sort=False,
    )


    if len(result) != before_rows:

        raise RuntimeError(
            "report_context join changed "
            "section row count."
        )


    # --------------------------------------------------------
    # ANALYTICAL SELECTION
    # --------------------------------------------------------

    result[
        "analysis_selected"
    ] = True


    result[
        "official_selected"
    ] = (
        result[
            "source_report_id"
        ]
        .astype("string")
        ==
        result[
            "official_selected_report_id"
        ]
        .astype("string")
    )


    # --------------------------------------------------------
    # SNAPSHOT RECENCY
    # --------------------------------------------------------

    result[
        "is_latest_data"
    ] = (
        result[
            "is_latest_report_for_organization"
        ]
        .fillna(False)
        .astype(bool)
    )


    result[
        "data_recency_status"
    ] = (
        result[
            "is_latest_data"
        ]
        .map(
            {
                True:
                    "latest_data",

                False:
                    "historical_data",
            }
        )
    )


    result = result.drop(
        columns=[
            "is_latest_report_for_organization"
        ]
    )


    # --------------------------------------------------------
    # CANONICAL PERIOD LABEL
    # --------------------------------------------------------

    result[
        "report_period"
    ] = [
        report_period_label(
            year,
            quarter,
        )
        for year, quarter
        in zip(
            result[
                "report_year"
            ],
            result[
                "report_quarter"
            ],
        )
    ]


    # --------------------------------------------------------
    # Stable output schema:
    # normalized fields first, enrichment fields after.
    # --------------------------------------------------------

    output_columns = (
        [
            column
            for column
            in original_columns
            if column
            not in FINAL_ENRICHMENT_COLUMNS
        ]
        +
        list(
            FINAL_ENRICHMENT_COLUMNS
        )
    )


    return result[
        output_columns
    ]


def enrich_report_sections_directory(
    normalized_root,
    output_root,
    *,
    report_context,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Rebuild all ten enriched report sections from
    normalized parquet.

    No RAW access.
    No API access.
    """

    normalized_root = Path(
        normalized_root
    )

    output_root = Path(
        output_root
    )


    if isinstance(
        report_context,
        (
            str,
            Path,
        ),
    ):

        report_context = (
            pd.read_parquet(
                report_context
            )
        )


    rows = []


    for section, relative_path in (
        SECTION_PATHS.items()
    ):

        input_path = (
            normalized_root
            / relative_path
        )

        output_path = (
            output_root
            / relative_path
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
            enrich_report_section_frame(
                normalized,
                report_context=
                    report_context,
            )
        )


        if len(enriched) != len(normalized):

            raise RuntimeError(
                f"{section}: row count changed."
            )


        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
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
