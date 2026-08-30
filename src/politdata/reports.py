
from datetime import datetime, timezone
import time

import pandas as pd
import requests

from .api import DEFAULT_BASE_URL


RETRYABLE_STATUS_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


def fetch_reports_page(
    organization_id,
    page=1,
    page_size=100,
    base_url=DEFAULT_BASE_URL,
    timeout=60,
    max_retries=4,
    retry_backoff=1.0,
    session=None,
):
    """
    Fetch one page of reports for a party or regional office.

    Retries temporary network/server failures.
    """

    owns_session = session is None

    if session is None:
        session = requests.Session()

    url = (
        f"{base_url}/party/"
        f"{organization_id}/reports"
    )

    payload = {
        "filters": {},
        "order": {},
        "pager": {
            "page": page,
            "size": page_size,
        },
    }

    try:

        for attempt in range(max_retries):

            try:

                response = session.post(
                    url,
                    json=payload,
                    timeout=timeout,
                )

                if (
                    response.status_code
                    in RETRYABLE_STATUS_CODES
                ):
                    response.raise_for_status()

                response.raise_for_status()

                data = response.json()

                results = data.get("results")

                if not isinstance(
                    results,
                    dict,
                ):
                    raise ValueError(
                        "Reports response has no "
                        "valid 'results' object."
                    )

                report_list = results.get(
                    "list"
                )

                if not isinstance(
                    report_list,
                    list,
                ):
                    raise ValueError(
                        "Reports response has no "
                        "valid 'results.list'."
                    )

                count = results.get(
                    "count"
                )

                if count is None:
                    raise ValueError(
                        "Reports response has no "
                        "'results.count'."
                    )

                return {
                    "list": report_list,
                    "count": int(count),
                }

            except (
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError,
            ):

                if attempt == max_retries - 1:
                    raise

                sleep_seconds = (
                    retry_backoff
                    * (2 ** attempt)
                )

                time.sleep(
                    sleep_seconds
                )

            except requests.exceptions.HTTPError:

                status_code = (
                    response.status_code
                )

                if (
                    status_code
                    not in RETRYABLE_STATUS_CODES
                    or attempt
                    == max_retries - 1
                ):
                    raise

                sleep_seconds = (
                    retry_backoff
                    * (2 ** attempt)
                )

                time.sleep(
                    sleep_seconds
                )

    finally:

        if owns_session:
            session.close()


def fetch_all_reports(
    organization_id,
    page_size=100,
    base_url=DEFAULT_BASE_URL,
    timeout=60,
    max_retries=4,
    retry_backoff=1.0,
    max_pages=100,
    return_metadata=False,
):
    """
    Fetch all available reports for one organization.

    Important:
    PolitData's declared `count` may be larger than the
    number of reports actually returned by the API.

    Therefore:
    - count mismatch is recorded as metadata;
    - available reports are NOT discarded;
    - pagination stops based on returned page length,
      not declared count alone.
    """

    session = requests.Session()

    try:

        all_rows = []
        declared_counts = []

        page = 1

        while True:

            page_result = fetch_reports_page(
                organization_id,
                page=page,
                page_size=page_size,
                base_url=base_url,
                timeout=timeout,
                max_retries=max_retries,
                retry_backoff=retry_backoff,
                session=session,
            )

            page_rows = (
                page_result["list"]
            )

            declared_counts.append(
                page_result["count"]
            )

            all_rows.extend(
                page_rows
            )

            # Stop when API returns a partial page.
            # This is safer than trusting `count`.
            if len(page_rows) < page_size:
                break

            page += 1

            if page > max_pages:
                raise RuntimeError(
                    "Maximum report pages exceeded "
                    f"for {organization_id}."
                )

        declared_count = (
            declared_counts[0]
            if declared_counts
            else 0
        )

        count_changed_during_fetch = (
            len(
                set(
                    declared_counts
                )
            )
            > 1
        )

        # ---------------------------------
        # Validate IDs + remove duplicates
        # ---------------------------------

        unique_reports = []
        seen_ids = set()
        duplicate_ids = []

        for report in all_rows:

            report_id = report.get(
                "id"
            )

            if not report_id:
                raise ValueError(
                    "Report without ID returned for "
                    f"{organization_id}."
                )

            if report_id in seen_ids:

                duplicate_ids.append(
                    report_id
                )

                continue

            seen_ids.add(
                report_id
            )

            unique_reports.append(
                report
            )

        fetched_count = len(
            unique_reports
        )

        metadata = {
            "organization_id":
                organization_id,

            "declared_count":
                declared_count,

            "raw_fetched_rows":
                len(all_rows),

            "fetched_count":
                fetched_count,

            "count_mismatch":
                (
                    declared_count
                    != fetched_count
                ),

            "count_difference":
                (
                    declared_count
                    - fetched_count
                ),

            "pages_requested":
                page,

            "page_size":
                page_size,

            "duplicate_report_count":
                len(
                    duplicate_ids
                ),

            "duplicate_report_ids":
                sorted(
                    set(
                        duplicate_ids
                    )
                ),

            "count_changed_during_fetch":
                count_changed_during_fetch,
        }

        if return_metadata:

            return (
                unique_reports,
                metadata,
            )

        return unique_reports

    finally:

        session.close()


def classify_period_type(
    quarter
):
    """
    Interpret PolitData quarter codes.

    Source value is preserved separately.

    1-4 = quarterly
    5   = annual
    """

    if quarter in {
        1,
        2,
        3,
        4,
    }:
        return "quarterly"

    if quarter == 5:
        return "annual"

    if quarter is None:
        return "missing"

    return "other"


def reports_to_manifest(
    organization_row,
    reports,
    discovered_at_utc=None,
):
    """
    Convert report-list records into lightweight
    report-manifest rows.
    """

    if discovered_at_utc is None:

        discovered_at_utc = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

    rows = []

    organization_id = (
        organization_row[
            "organization_id"
        ]
    )

    root_party_id = (
        organization_row[
            "root_party_id"
        ]
    )

    entity_type = (
        organization_row[
            "entity_type"
        ]
    )

    for report in reports:

        public_summary = (
            report.get(
                "public_summary"
            )
            or {}
        )

        rows.append({
            "report_id":
                report.get("id"),

            "organization_id":
                organization_id,

            "root_party_id":
                root_party_id,

            "entity_type":
                entity_type,

            "source_party_id":
                report.get(
                    "party_id"
                ),

            "party_id_matches_organization":
                (
                    report.get(
                        "party_id"
                    )
                    == organization_id
                ),

            "is_party_office":
                report.get(
                    "is_party_office"
                ),

            "schema_version":
                report.get(
                    "schema_version"
                ),

            "report_type":
                report.get(
                    "report_type"
                ),

            "year":
                report.get(
                    "year"
                ),

            # Original PolitData value.
            "quarter":
                report.get(
                    "quarter"
                ),

            # Derived interpretation.
            "period_type":
                classify_period_type(
                    report.get(
                        "quarter"
                    )
                ),

            "signed_date":
                report.get(
                    "signed_date"
                ),

            "created_date":
                report.get(
                    "created_date"
                ),

            "signatory_id":
                report.get(
                    "signatory_id"
                ),

            "special_status":
                report.get(
                    "special_status"
                ),

            "public_summary_version":
                public_summary.get(
                    "v"
                ),

            "public_summary_generated_at":
                public_summary.get(
                    "generated_at"
                ),

            "discovered_at_utc":
                discovered_at_utc,
        })

    return pd.DataFrame(
        rows
    )


def add_periodicity_flags(
    reports_df,
):
    """
    Add organization-year periodicity diagnostics.

    Annual-preference rule:

    If an annual report exists for an organization-year,
    quarterly reports from that organization-year are
    excluded from annualized aggregation.

    Nothing is physically deleted.
    """

    df = reports_df.copy()

    if df.empty:
        return df

    periodicity = (
        df.groupby(
            [
                "organization_id",
                "year",
            ],
            dropna=False,
        )
        .agg(
            has_annual_report=(
                "period_type",
                lambda x:
                    (
                        x
                        == "annual"
                    ).any(),
            ),

            has_quarterly_reports=(
                "period_type",
                lambda x:
                    (
                        x
                        == "quarterly"
                    ).any(),
            ),

            annual_report_count=(
                "period_type",
                lambda x:
                    (
                        x
                        == "annual"
                    ).sum(),
            ),

            quarterly_report_count=(
                "period_type",
                lambda x:
                    (
                        x
                        == "quarterly"
                    ).sum(),
            ),

            report_count=(
                "report_id",
                "count",
            ),
        )
        .reset_index()
    )

    periodicity[
        "has_mixed_periodicity"
    ] = (
        periodicity[
            "has_annual_report"
        ]
        &
        periodicity[
            "has_quarterly_reports"
        ]
    )

    df = df.merge(
        periodicity,
        on=[
            "organization_id",
            "year",
        ],
        how="left",
    )

    df[
        "include_by_annual_preference"
    ] = False

    annual_exists = (
        df[
            "has_annual_report"
        ]
    )

    # Annual exists -> prefer annual.
    df.loc[
        annual_exists
        &
        (
            df[
                "period_type"
            ]
            == "annual"
        ),
        "include_by_annual_preference",
    ] = True

    # No annual -> retain quarterlies.
    df.loc[
        (~annual_exists)
        &
        (
            df[
                "period_type"
            ]
            == "quarterly"
        ),
        "include_by_annual_preference",
    ] = True

    return df


def discover_reports(
    organization_manifest,
    organization_ids=None,
    request_delay=0.15,
    timeout=60,
    max_retries=4,
    return_diagnostics=False,
):
    """
    Discover reports for selected organizations.

    Count mismatches are diagnostics, not fatal errors.

    Actual network/API failures remain errors.
    """

    if organization_ids is None:

        selected = (
            organization_manifest.copy()
        )

    else:

        selected = (
            organization_manifest[
                organization_manifest[
                    "organization_id"
                ].isin(
                    organization_ids
                )
            ].copy()
        )

    discovered_at_utc = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    frames = []
    errors = []
    diagnostics = []

    for row in selected.to_dict(
        orient="records"
    ):

        organization_id = row[
            "organization_id"
        ]

        try:

            (
                reports,
                fetch_metadata,
            ) = fetch_all_reports(
                organization_id,
                timeout=timeout,
                max_retries=max_retries,
                return_metadata=True,
            )

            diagnostics.append({
                "organization_id":
                    organization_id,

                "entity_type":
                    row.get(
                        "entity_type"
                    ),

                "name":
                    row.get(
                        "name"
                    ),

                **fetch_metadata,
            })

            if reports:

                frame = reports_to_manifest(
                    row,
                    reports,
                    discovered_at_utc=(
                        discovered_at_utc
                    ),
                )

                frames.append(
                    frame
                )

        except Exception as exc:

            errors.append({
                "organization_id":
                    organization_id,

                "name":
                    row.get(
                        "name"
                    ),

                "error":
                    repr(
                        exc
                    ),
            })

        if request_delay:

            time.sleep(
                request_delay
            )

    if frames:

        reports_df = pd.concat(
            frames,
            ignore_index=True,
        )

    else:

        reports_df = (
            pd.DataFrame()
        )

    if not reports_df.empty:

        if reports_df[
            "report_id"
        ].isna().any():

            raise ValueError(
                "Discovered report "
                "without report_id."
            )

        reports_df = (
            add_periodicity_flags(
                reports_df
            )
        )

    diagnostics_df = (
        pd.DataFrame(
            diagnostics
        )
    )

    if return_diagnostics:

        return (
            reports_df,
            errors,
            diagnostics_df,
        )

    return (
        reports_df,
        errors,
    )
