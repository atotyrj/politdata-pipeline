
import pandas as pd

from politdata.enrichment.report_sections import (
    report_period_label,
    enrich_report_section_frame,
)


def _context():

    return pd.DataFrame(
        [
            {
                "source_report_id":
                    "r1",

                "organization_name_current":
                    "ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»",

                "organization_code":
                    "12345678",

                "organization_level":
                    "central",

                "region":
                    "Україна",

                "party_name_current":
                    "ТЕСТ",

                "party_code":
                    "12345678",

                "analysis_override":
                    False,

                "analysis_selection_method":
                    "single_signed",

                "official_selected_report_id":
                    "r1",

                "analysis_selected_report_id":
                    "r1",

                "continuity_exact":
                    False,

                "is_latest_report_for_organization":
                    True,
            }
        ]
    )


def test_report_period_quarters():

    assert (
        report_period_label(
            2025,
            1,
        )
        ==
        "2025Q1"
    )

    assert (
        report_period_label(
            2025,
            4,
        )
        ==
        "2025Q4"
    )


def test_report_period_annual():

    assert (
        report_period_label(
            2025,
            5,
        )
        ==
        "2025"
    )


def test_generic_report_section_enrichment():

    normalized = pd.DataFrame(
        [
            {
                "source_report_id":
                    "r1",

                "source_row_index":
                    0,

                "report_year":
                    2025,

                "report_quarter":
                    1,

                "source__id":
                    "asset-1",

                "source__object_type":
                    "Квартира",
            }
        ]
    )


    result = (
        enrich_report_section_frame(
            normalized,
            report_context=
                _context(),
        )
    )


    row = result.iloc[0]


    assert (
        row[
            "source__object_type"
        ]
        ==
        "Квартира"
    )

    assert (
        row[
            "organization_name_current"
        ]
        ==
        "ПОЛІТИЧНА ПАРТІЯ «ТЕСТ»"
    )

    assert (
        row[
            "party_name_current"
        ]
        ==
        "ТЕСТ"
    )

    assert (
        bool(
            row[
                "analysis_selected"
            ]
        )
        is True
    )

    assert (
        bool(
            row[
                "official_selected"
            ]
        )
        is True
    )

    assert (
        bool(
            row[
                "is_latest_data"
            ]
        )
        is True
    )

    assert (
        row[
            "data_recency_status"
        ]
        ==
        "latest_data"
    )

    assert (
        row[
            "report_period"
        ]
        ==
        "2025Q1"
    )


def test_nonofficial_analytical_report():

    context = _context()

    context.loc[
        0,
        "official_selected_report_id",
    ] = "official-other"


    normalized = pd.DataFrame(
        [
            {
                "source_report_id":
                    "r1",

                "source_row_index":
                    0,

                "report_year":
                    2025,

                "report_quarter":
                    2,
            }
        ]
    )


    result = (
        enrich_report_section_frame(
            normalized,
            report_context=
                context,
        )
    )


    row = result.iloc[0]


    assert (
        bool(
            row[
                "analysis_selected"
            ]
        )
        is True
    )

    assert (
        bool(
            row[
                "official_selected"
            ]
        )
        is False
    )


def test_historical_data():

    context = _context()

    context.loc[
        0,
        "is_latest_report_for_organization",
    ] = False


    normalized = pd.DataFrame(
        [
            {
                "source_report_id":
                    "r1",

                "source_row_index":
                    0,

                "report_year":
                    2024,

                "report_quarter":
                    5,
            }
        ]
    )


    result = (
        enrich_report_section_frame(
            normalized,
            report_context=
                context,
        )
    )


    row = result.iloc[0]


    assert (
        bool(
            row[
                "is_latest_data"
            ]
        )
        is False
    )

    assert (
        row[
            "data_recency_status"
        ]
        ==
        "historical_data"
    )

    assert (
        row[
            "report_period"
        ]
        ==
        "2024"
    )


def test_empty_section_preserves_enriched_schema():

    normalized = pd.DataFrame(
        {
            "source_report_id":
                pd.Series(
                    dtype="string"
                ),

            "source_section":
                pd.Series(
                    dtype="string"
                ),

            "source_row_index":
                pd.Series(
                    dtype="Int64"
                ),

            "report_year":
                pd.Series(
                    dtype="Int64"
                ),

            "report_quarter":
                pd.Series(
                    dtype="Int64"
                ),
        }
    )


    result = (
        enrich_report_section_frame(
            normalized,
            report_context=
                _context(),
        )
    )


    assert result.empty

    assert (
        "report_period"
        in result.columns
    )

    assert (
        "is_latest_data"
        in result.columns
    )
