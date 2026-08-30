
from __future__ import annotations

import pandas as pd

from pathlib import Path

from politdata.enrichment.payment_batch import (
    enrich_normalized_payment_directory,
)

from politdata.enrichment.report_sections import (
    enrich_report_sections_directory,
)

from politdata.qa import (
    validate_enriched_output,
)


def rebuild_enriched_data_layers(
    normalized_root,
    output_root,
    *,
    reference_root,
    overwrite: bool = False,
):
    """
    Rebuild the currently productionized enrichment layers
    from normalized parquet plus the verified reference layer.

    Included:
        - all eight payment sections
        - all ten report-state/snapshot sections

    Required prebuilt references:
        - report_context
        - organization_reference
        - report_account_reference
        - state_funding_account_reference

    This function intentionally does NOT:
        - read RAW
        - call the API
        - rebuild the reference layer
        - promote output to production automatically
    """

    normalized_root = Path(
        normalized_root
    )

    output_root = Path(
        output_root
    )

    reference_root = Path(
        reference_root
    )


    report_context_path = (
        reference_root
        / "report_context.parquet"
    )

    organization_reference_path = (
        reference_root
        / "organization_reference.parquet"
    )

    report_account_reference_path = (
        reference_root
        / "report_account_reference.parquet"
    )

    state_account_reference_path = (
        reference_root
        / "state_funding_account_reference.parquet"
    )


    required_references = (
        report_context_path,
        organization_reference_path,
        report_account_reference_path,
        state_account_reference_path,
    )


    for path in required_references:

        if not path.exists():

            raise FileNotFoundError(
                path
            )


    # --------------------------------------------------------
    # PAYMENTS
    # --------------------------------------------------------

    payment_summary = (
        enrich_normalized_payment_directory(
            normalized_root
            / "payments",

            output_root
            / "payments",

            report_context=
                report_context_path,

            organization_reference=
                organization_reference_path,

            report_account_reference=
                report_account_reference_path,

            state_account_reference=
                state_account_reference_path,

            overwrite=
                overwrite,
        )
    )


    # --------------------------------------------------------
    # REPORT SECTIONS
    # --------------------------------------------------------

    section_summary = (
        enrich_report_sections_directory(
            normalized_root,
            output_root,

            report_context=
                report_context_path,

            overwrite=
                overwrite,
        )
    )


    # --------------------------------------------------------
    # CENTRAL REGRESSION QA
    # --------------------------------------------------------

    qa = validate_enriched_output(
        output_root,

        organization_reference=
            organization_reference_path,
    )


    return {
        "payments":
            payment_summary,

        "report_sections":
            section_summary,

        "qa":
            qa,
    }



# ============================================================
# FULL PROCESSED ANALYTICS PIPELINE V0.1
# ============================================================

def rebuild_processed_analytics(
    normalized_root,
    interim_root,
    output_root,
    *,
    overwrite: bool = False,
):
    """
    Rebuild the currently productionized analytical layers
    from normalized data plus the analysis-selected manifest.

    Flow:

        normalized organization tables
        analysis-selected report manifest
        normalized property_moneys
        normalized state_funding
                ↓
        reference layer
                ↓
        payment enrichment
        report-section enrichment
                ↓
        QA

    This function intentionally does NOT:
        - call the PolitData API
        - read RAW report JSON
        - perform report discovery/selection
        - normalize RAW data
        - promote validation output into production
    """

    from politdata.reference import (
        rebuild_reference_layer,
    )


    normalized_root = Path(
        normalized_root
    )

    interim_root = Path(
        interim_root
    )

    output_root = Path(
        output_root
    )


    reference_root = (
        output_root
        / "reference"
    )


    # --------------------------------------------------------
    # Required processed inputs
    # --------------------------------------------------------

    organizations_path = (
        normalized_root
        / "organizations.parquet"
    )

    addresses_path = (
        normalized_root
        / "organization_addresses.parquet"
    )

    analysis_manifest_path = (
        interim_root
        / "reports"
        / "analysis_selected_reports_manifest.parquet"
    )

    property_moneys_path = (
        normalized_root
        / "properties"
        / "property_moneys.parquet"
    )

    state_funding_path = (
        normalized_root
        / "payments"
        / "state_funding.parquet"
    )


    required_inputs = (
        organizations_path,
        addresses_path,
        analysis_manifest_path,
        property_moneys_path,
        state_funding_path,
    )


    for path in required_inputs:

        if not path.exists():

            raise FileNotFoundError(
                path
            )


    # --------------------------------------------------------
    # 1. REFERENCE LAYER
    # --------------------------------------------------------

    references = (
        rebuild_reference_layer(
            organizations=
                organizations_path,

            addresses=
                addresses_path,

            analysis_manifest=
                analysis_manifest_path,

            property_moneys=
                property_moneys_path,

            state_funding=
                state_funding_path,

            output_root=
                reference_root,

            overwrite=
                overwrite,
        )
    )


    # --------------------------------------------------------
    # 2. ENRICHMENT + QA
    # --------------------------------------------------------

    enrichment = (
        rebuild_enriched_data_layers(
            normalized_root,
            output_root,

            reference_root=
                reference_root,

            overwrite=
                overwrite,
        )
    )


    # --------------------------------------------------------
    # 3. REFERENCE SUMMARY
    # --------------------------------------------------------

    reference_summary = pd.DataFrame(
        [
            {
                "artifact":
                    filename.replace(
                        ".parquet",
                        ""
                    ),

                "rows":
                    len(
                        frame
                    ),
            }

            for filename, frame
            in references.items()
        ]
    )


    return {
        "references":
            reference_summary,

        "payments":
            enrichment[
                "payments"
            ],

        "report_sections":
            enrichment[
                "report_sections"
            ],

        "qa":
            enrichment[
                "qa"
            ],
    }

