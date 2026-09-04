"""Lean analytical Excel exports built from the validated processed layers.

The exporter is presentation-only: it never reads RAW report files and never
rebuilds normalized, reference, or enriched data.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Iterator
import os
import re

import pandas as pd
import pyarrow.parquet as pq

from politdata.normalization.payments import normalize_person_name


HELPER_COLUMNS = (
    "organization_name",
    "organization_code",
    "party_name",
    "party_code",
    "report_year",
    "report_period",
    "organization_type",
    "region",
    "data_recency_status",
    "potential_annual_overlap",
)


REPORTING_MATRIX_FIXED_COLUMNS = (
    "organization_name",
    "organization_code",
    "party_name",
    "party_code",
    "organization_type",
    "region",
    "first_report_period",
    "latest_report_period",
    "potential_annual_overlap",
)


REPORT_OUTPUTS = (
    ("02", "properties", "intangible"),
    ("03", "properties", "movable"),
    ("04", "properties", "property_moneys"),
    ("05", "properties", "realty"),
    ("06", "properties", "paper"),
    ("07", "properties", "transport"),
    ("16", "obligations", "obligations"),
)


PAYMENT_OUTPUTS = (
    ("08", "other_contributions"),
    ("09", "monetary_contributions"),
    ("10", "state_funding"),
    ("11", "other_incomes"),
    ("12", "budget_expenses"),
    ("13", "outgoing_expenses"),
    ("14", "return_expenses"),
    ("15", "transfer_expenses"),
)


PAYMENT_FIELD_MAP = (
    ("source_row_id", "id"),
    ("source_party_id", "party_id"),
    ("source_office_id", "office_id"),
    ("source_report_id_in_row", "report_id"),
    ("source_created_at", "created_at"),
    ("source_updated_at", "updated_at"),
    ("group_code", "group_code"),
    ("payer_name_normalized", "payer_name"),
    ("payer_code_normalized", "payer_code"),
    ("payer_type_analytical", "payer_type"),
    ("payer_address", "payer_address"),
    ("payer_birthday", "payer_birthday"),
    ("payer_account_iban_canonical", "payer_account_iban"),
    ("payer_account_type_source", "payer_account_type"),
    ("payer_bank_code", "payer_bank_code"),
    ("payer_bank_name", "payer_bank_name"),
    ("payer_bank_address", "payer_bank_address"),
    ("payment_amount", "payment_amount"),
    ("payment_code", "payment_code"),
    ("payment_currency", "payment_currency"),
    ("payment_description", "payment_description"),
    ("payment_instruction_date", "payment_instruction_date"),
    ("payment_number", "payment_number"),
    ("payment_operation_date", "payment_operation_date"),
    ("payment_purpose", "payment_purpose"),
    ("payment_reason", "payment_reason"),
    ("payment_type_detail_source", "payment_type"),
    ("receiver_name_normalized", "receiver_name"),
    ("receiver_code_normalized", "receiver_code"),
    ("receiver_type_analytical", "receiver_type"),
    ("receiver_address", "receiver_address"),
    ("receiver_birthday", "receiver_birthday"),
    ("receiver_account_iban_canonical", "receiver_account_iban"),
    ("receiver_account_type_source", "receiver_account_type"),
    ("receiver_bank_code", "receiver_bank_code"),
    ("receiver_bank_name", "receiver_bank_name"),
    ("receiver_bank_address", "receiver_bank_address"),
    ("refund_amount", "refund_amount"),
    ("refund_budget_amount", "refund_budget_amount"),
    ("refund_date", "refund_date"),
    ("refund_description", "refund_description"),
    ("refund_purpose", "refund_purpose"),
    ("refund_reason", "refund_reason"),
)


MONEY_COLUMNS = {
    "owning_cost",
    "begin_period_balance",
    "end_period_balance",
    "report_period_income",
    "report_period_used_funds",
    "end_period_remains_cost",
    "payment_amount",
    "refund_amount",
    "refund_budget_amount",
}


DATE_COLUMNS = {
    "created_at",
    "updated_at",
    "owning_date",
    "substraction_date",
    "payer_birthday",
    "payment_instruction_date",
    "payment_operation_date",
    "receiver_birthday",
    "refund_date",
}


ADDRESS_FIELDS = (
    "country",
    "post_index",
    "region",
    "district",
    "city",
    "street",
    "building",
    "apartments",
    "common",
    "building_part_num",
    "address_uk",
    "address_en",
)


def _period_label(year, quarter) -> str:
    suffix = "annual" if int(quarter) == 5 else f"Q{int(quarter)}"
    return f"{int(year)} {suffix}"


def build_reporting_organization_matrix(
    report_context: pd.DataFrame,
    organization_reference: pd.DataFrame,
) -> pd.DataFrame:
    """Build one row per organization with selected-report period markers."""

    required_context = {
        "source_report_id",
        "organization_id",
        "root_party_id",
        "organization_code",
        "year",
        "quarter",
        "organization_level",
    }
    missing = required_context - set(report_context.columns)
    if missing:
        raise KeyError(f"report_context missing columns: {sorted(missing)}")
    if report_context["source_report_id"].duplicated().any():
        raise ValueError("report_context source_report_id must be unique")
    if report_context[["organization_id", "year", "quarter"]].duplicated().any():
        raise ValueError("report_context organization-period keys must be unique")

    context = report_context.copy()
    context["_period_label"] = [
        _period_label(year, quarter)
        for year, quarter in zip(context["year"], context["quarter"])
    ]
    context["_period_order"] = context["year"].astype(int) * 10 + context[
        "quarter"
    ].astype(int)
    period_order = (
        context[["year", "quarter", "_period_label"]]
        .drop_duplicates()
        .sort_values(["year", "quarter"], kind="stable")
    )
    period_columns = period_order["_period_label"].tolist()

    organization_ids = context["organization_id"].astype("string").unique()
    reference = organization_reference.loc[
        organization_reference["organization_id"].astype("string").isin(
            organization_ids
        )
    ].copy()
    if reference["organization_id"].duplicated().any():
        raise ValueError("organization_reference organization_id must be unique")
    if len(reference) != len(organization_ids):
        raise ValueError("Some reporting organizations are missing from reference")
    result = reference[
        [
            "organization_id",
            "organization_name_current",
            "organization_code",
            "party_name_current",
            "party_code",
            "organization_level",
            "region",
        ]
    ].rename(
        columns={
            "organization_name_current": "organization_name",
            "party_name_current": "party_name",
        }
    )
    result["organization_type"] = result["organization_level"].map(
        {"central": "central_office", "office": "regional_office"}
    )
    result = result.drop(columns="organization_level")

    sorted_context = context.sort_values(
        ["organization_id", "_period_order"], kind="stable"
    )
    period_bounds = (
        sorted_context.groupby("organization_id", sort=False)["_period_label"]
        .agg(first_report_period="first", latest_report_period="last")
        .reset_index()
    )
    overlap = (
        context.assign(
            _annual=context["quarter"].eq(5),
            _quarterly=context["quarter"].between(1, 4),
        )
        .groupby(["organization_id", "year"], dropna=False)[
            ["_annual", "_quarterly"]
        ]
        .any()
    )
    overlap["potential_annual_overlap"] = (
        overlap["_annual"] & overlap["_quarterly"]
    )
    overlap = (
        overlap.groupby(level="organization_id")["potential_annual_overlap"]
        .any()
        .reset_index()
    )

    coverage = (
        context.assign(_value=1)
        .pivot_table(
            index="organization_id",
            columns="_period_label",
            values="_value",
            aggfunc="max",
        )
        .reindex(columns=period_columns)
        .astype("Int64")
        .reset_index()
    )

    for addition in (period_bounds, overlap, coverage):
        result = result.merge(
            addition,
            on="organization_id",
            how="left",
            validate="one_to_one",
            sort=False,
        )
    result["potential_annual_overlap"] = result[
        "potential_annual_overlap"
    ].astype("Int64")
    result = result.sort_values(
        [
            "party_name",
            "party_code",
            "organization_type",
            "region",
            "organization_name",
            "organization_code",
        ],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)
    ordered = [*REPORTING_MATRIX_FIXED_COLUMNS, *period_columns]
    return result.loc[:, ordered]


def build_report_context(report_context: pd.DataFrame) -> pd.DataFrame:
    """Return one compact helper row for every selected report."""

    required = {
        "source_report_id",
        "organization_id",
        "organization_name_current",
        "organization_code",
        "party_name_current",
        "party_code",
        "year",
        "quarter",
        "organization_level",
        "region",
        "data_recency_status",
    }
    missing = required - set(report_context.columns)
    if missing:
        raise KeyError(f"report_context missing columns: {sorted(missing)}")
    if report_context["source_report_id"].duplicated().any():
        raise ValueError("report_context source_report_id must be unique")

    context = report_context.copy()
    periods = context["quarter"].map(
        {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4", 5: "annual"}
    )
    if periods.isna().any():
        unexpected = sorted(context.loc[periods.isna(), "quarter"].unique())
        raise ValueError(f"Unexpected report quarters: {unexpected}")

    overlap = (
        context.assign(
            _annual=context["quarter"].eq(5),
            _quarterly=context["quarter"].between(1, 4),
        )
        .groupby(["organization_id", "year"], dropna=False)[
            ["_annual", "_quarterly"]
        ]
        .any()
    )
    overlap["potential_annual_overlap"] = (
        overlap["_annual"] & overlap["_quarterly"]
    )
    overlap = overlap[["potential_annual_overlap"]].reset_index()

    result = pd.DataFrame(
        {
            "source_report_id": context["source_report_id"].astype("string"),
            "organization_id": context["organization_id"].astype("string"),
            "organization_name": context["organization_name_current"],
            "organization_code": context["organization_code"].astype("string"),
            "party_name": context["party_name_current"],
            "party_code": context["party_code"].astype("string"),
            "report_year": context["year"].astype("Int64"),
            "report_period": periods.astype("string"),
            "organization_type": context["organization_level"].map(
                {"central": "central_office", "office": "regional_office"}
            ),
            "region": context["region"],
            "data_recency_status": context["data_recency_status"],
        }
    )
    result = result.merge(
        overlap,
        left_on=["organization_id", "report_year"],
        right_on=["organization_id", "year"],
        how="left",
        validate="many_to_one",
    ).drop(columns="year")
    result["potential_annual_overlap"] = (
        result["potential_annual_overlap"].fillna(False).astype(bool)
    )
    return result


def _with_context(frame: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    before = len(frame)
    result = frame.merge(
        context,
        on="source_report_id",
        how="left",
        validate="many_to_one",
        sort=False,
    )
    if len(result) != before:
        raise RuntimeError("Report context join changed the row count")
    if before and result["organization_name"].isna().any():
        raise RuntimeError("Some rows did not resolve report context")
    return result


def _parse_number(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series.dtype):
        return pd.to_numeric(series, errors="coerce")
    text = series.astype("string").str.replace("\u00a0", "", regex=False)
    text = text.str.replace(" ", "", regex=False)
    comma_only = text.str.contains(",", na=False) & ~text.str.contains(
        ".", regex=False, na=False
    )
    text = text.where(~comma_only, text.str.replace(",", ".", regex=False))
    return pd.to_numeric(text, errors="coerce")


def _apply_display_types(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        if column in MONEY_COLUMNS:
            result[column] = _parse_number(result[column])
        elif column in DATE_COLUMNS or column.endswith("_date"):
            result[column] = pd.to_datetime(result[column], errors="coerce")
    return result


def transform_report_section_batch(
    frame: pd.DataFrame,
    context: pd.DataFrame,
    *,
    source_columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Strip technical columns and expose original report fields once."""

    merged = _with_context(frame, context)
    if source_columns is None:
        source_columns = [c for c in frame.columns if c.startswith("source__")]

    result = merged.loc[:, list(HELPER_COLUMNS)].copy()
    for source_column in source_columns:
        if source_column not in merged.columns:
            continue
        output_column = source_column.removeprefix("source__")
        if output_column == "account_number" and "party_account_iban" in merged:
            result[output_column] = merged["party_account_iban"]
        elif (
            output_column == "account_type"
            and "party_account_type_analytical" in merged
        ):
            result[output_column] = merged["party_account_type_analytical"]
        else:
            result[output_column] = merged[source_column]
    return _apply_display_types(result)


def transform_payment_batch(
    frame: pd.DataFrame,
    context: pd.DataFrame,
    *,
    analytical_payment_type: str,
) -> pd.DataFrame:
    """Create one source-shaped payment table using only final clean values."""

    routing_type = frame["analytical_payment_type"].astype("string").copy()
    if "internal_transfer" in frame.columns:
        internal_monetary = (
            frame["internal_transfer"].fillna(False).astype(bool)
            & routing_type.eq("monetary_contributions")
        )
        routing_type.loc[internal_monetary] = "other_incomes"
    selected = frame.loc[routing_type.eq(analytical_payment_type)].copy()
    merged = _with_context(selected, context)
    result = merged.loc[:, list(HELPER_COLUMNS)].copy()
    for source_column, output_column in PAYMENT_FIELD_MAP:
        if source_column in merged.columns:
            result[output_column] = merged[source_column]
    return _apply_display_types(result)


def _clean_head_name(value):
    return normalize_person_name(value, "Фізична особа")


def build_party_information(
    context: pd.DataFrame,
    organizations: pd.DataFrame,
    addresses: pd.DataFrame,
    employee_counts: pd.DataFrame,
    head_info: pd.DataFrame,
) -> pd.DataFrame:
    """Build one organization/report-period row without NACP summaries."""

    result = context.copy()
    organization_fields = (
        "organization_id",
        "is_active",
        "created_at",
        "updated_at",
        "web_site_url",
        "email",
        "phone",
        "actual_address_same_register",
    )
    result = result.merge(
        organizations.loc[:, list(organization_fields)],
        on="organization_id",
        how="left",
        validate="many_to_one",
        sort=False,
    )

    for address_type in ("register", "actual"):
        address = addresses.loc[
            addresses["address_type"].eq(address_type),
            ["organization_id", *ADDRESS_FIELDS],
        ].copy()
        if address["organization_id"].duplicated().any():
            raise ValueError(f"Duplicate {address_type} organization address")
        address = address.rename(
            columns={
                field: f"{address_type}_address_{field}"
                for field in ADDRESS_FIELDS
            }
        )
        result = result.merge(
            address,
            on="organization_id",
            how="left",
            validate="many_to_one",
            sort=False,
        )

    employee_fields = (
        "source_report_id",
        "employees_by_civil_contract",
        "employees_by_employment_contract",
    )
    employees = employee_counts.loc[:, list(employee_fields)].copy()
    if employees["source_report_id"].duplicated().any():
        raise ValueError("Duplicate employee-count rows for a report")
    result = result.merge(
        employees,
        on="source_report_id",
        how="left",
        validate="one_to_one",
        sort=False,
    )

    head_fields = {
        "source__name": "name",
        "source__surname": "surname",
        "source__patronymic": "patronymic",
    }
    heads = head_info.loc[
        :, ["source_report_id", *head_fields.keys()]
    ].rename(columns=head_fields)
    if heads["source_report_id"].duplicated().any():
        raise ValueError("Duplicate head-info rows for a report")
    for column in head_fields.values():
        heads[column] = heads[column].map(_clean_head_name)
    result = result.merge(
        heads,
        on="source_report_id",
        how="left",
        validate="one_to_one",
        sort=False,
    )

    ordered = [
        *HELPER_COLUMNS,
        "is_active",
        "created_at",
        "updated_at",
        "web_site_url",
        "email",
        "phone",
        "actual_address_same_register",
        *(f"register_address_{field}" for field in ADDRESS_FIELDS),
        *(f"actual_address_{field}" for field in ADDRESS_FIELDS),
        "name",
        "surname",
        "patronymic",
        "employees_by_civil_contract",
        "employees_by_employment_contract",
    ]
    return _drop_empty_columns(_apply_display_types(result.loc[:, ordered]))


def _nonempty_mask(series: pd.Series) -> pd.Series:
    mask = series.notna()
    if pd.api.types.is_string_dtype(series.dtype) or series.dtype == object:
        strings = series.astype("string")
        mask &= strings.str.strip().ne("").fillna(False)
    return mask


def _drop_empty_columns(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [c for c in frame.columns if _nonempty_mask(frame[c]).any()]
    return frame.loc[:, columns].copy()


def _parquet_batches(
    paths: Iterable[Path],
    columns: Iterable[str],
    *,
    batch_size: int = 25_000,
) -> Iterator[pd.DataFrame]:
    requested = list(dict.fromkeys(columns))
    for path in paths:
        parquet = pq.ParquetFile(path)
        available = set(parquet.schema_arrow.names)
        selected = [column for column in requested if column in available]
        for batch in parquet.iter_batches(batch_size=batch_size, columns=selected):
            yield batch.to_pandas()


def _excel_value(value):
    if value is None:
        return None
    try:
        missing = pd.isna(value)
        if bool(missing):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _column_width(column: str) -> int:
    if column in MONEY_COLUMNS:
        return 16
    if column in DATE_COLUMNS or column.endswith("_date"):
        return 13
    if column.endswith("_code") or column in {"party_id", "office_id"}:
        return 17
    if any(
        token in column
        for token in ("name", "address", "description", "purpose", "reason")
    ):
        return 28
    return min(max(len(column) + 2, 12), 22)


def _write_workbook(
    output_path: Path,
    sheet_name: str,
    columns: list[str],
    batches: Iterable[pd.DataFrame],
    *,
    empty_message: str = "У відібраних звітах дані відсутні",
) -> int:
    try:
        import xlsxwriter
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Excel export requires the declared xlsxwriter dependency"
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(output_path.stem + ".part.xlsx")
    workbook = xlsxwriter.Workbook(
        temporary_path,
        {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "nan_inf_to_errors": True,
        },
    )
    worksheet = workbook.add_worksheet(sheet_name[:31])
    header_format = workbook.add_format(
        {
            "bold": True,
            "bg_color": "#D9EAD3",
            "border": 1,
            "text_wrap": True,
            "valign": "top",
        }
    )
    date_format = workbook.add_format({"num_format": "dd.mm.yyyy"})
    money_format = workbook.add_format({"num_format": "#,##0.00"})

    if columns:
        worksheet.write_row(0, 0, columns, header_format)
        worksheet.freeze_panes(1, 0)
        for index, column in enumerate(columns):
            cell_format = None
            if column in MONEY_COLUMNS:
                cell_format = money_format
            elif column in DATE_COLUMNS or column.endswith("_date"):
                cell_format = date_format
            worksheet.set_column(index, index, _column_width(column), cell_format)

    row_number = 1
    cell_formats = []
    for column in columns:
        if column in MONEY_COLUMNS:
            cell_formats.append(money_format)
        elif column in DATE_COLUMNS or column.endswith("_date"):
            cell_formats.append(date_format)
        else:
            cell_formats.append(None)
    for batch in batches:
        if batch.empty:
            continue
        current = batch.reindex(columns=columns)
        for row in current.itertuples(index=False, name=None):
            for column_number, value in enumerate(row):
                worksheet.write(
                    row_number,
                    column_number,
                    _excel_value(value),
                    cell_formats[column_number],
                )
            row_number += 1

    data_rows = row_number - 1
    if data_rows == 0:
        if columns:
            worksheet.write(1, 0, empty_message)
        else:
            worksheet.write(0, 0, empty_message)
            worksheet.set_column(0, 0, 42)
    elif columns:
        worksheet.autofilter(0, 0, data_rows, len(columns) - 1)
    workbook.close()
    os.replace(temporary_path, output_path)
    return data_rows


def _active_stream_columns(
    batch_factory: Callable[[], Iterable[pd.DataFrame]],
) -> tuple[list[str], int]:
    order: list[str] = []
    active: set[str] = set()
    row_count = 0
    for batch in batch_factory():
        row_count += len(batch)
        for column in batch.columns:
            if column not in order:
                order.append(column)
            if column not in active and _nonempty_mask(batch[column]).any():
                active.add(column)
    return [column for column in order if column in active], row_count


def _section_batch_factory(
    path: Path,
    context: pd.DataFrame,
) -> Callable[[], Iterable[pd.DataFrame]]:
    schema_columns = pq.read_schema(path).names
    source_columns = [c for c in schema_columns if c.startswith("source__")]
    requested = ["source_report_id", *source_columns]
    if path.stem == "property_moneys":
        requested.extend(["party_account_iban", "party_account_type_analytical"])

    def batches():
        for frame in _parquet_batches([path], requested):
            yield transform_report_section_batch(
                frame,
                context,
                source_columns=source_columns,
            )

    return batches


def _payment_batch_factory(
    enriched_root: Path,
    context: pd.DataFrame,
    payment_type: str,
) -> Callable[[], Iterable[pd.DataFrame]]:
    sources = [enriched_root / "payments" / f"{payment_type}.parquet"]
    if payment_type == "outgoing_expenses":
        sources.insert(0, enriched_root / "payments" / "budget_expenses.parquet")
    elif payment_type == "other_incomes":
        sources.insert(0, enriched_root / "payments" / "monetary_contributions.parquet")
    requested = [
        "source_report_id",
        "analytical_payment_type",
        "internal_transfer",
        *(source for source, _ in PAYMENT_FIELD_MAP),
    ]

    def batches():
        for frame in _parquet_batches(sources, requested):
            yield transform_payment_batch(
                frame,
                context,
                analytical_payment_type=payment_type,
            )

    return batches


def export_analytical_workbooks(
    *,
    enriched_root: Path,
    normalized_root: Path,
    output_dir: Path,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Create all 17 numbered single-sheet analytical workbooks."""

    progress = progress or (lambda message: None)
    raw_context = pd.read_parquet(enriched_root / "reference" / "report_context.parquet")
    context = build_report_context(raw_context)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []

    party_information = build_party_information(
        context,
        pd.read_parquet(normalized_root / "organizations.parquet"),
        pd.read_parquet(normalized_root / "organization_addresses.parquet"),
        pd.read_parquet(enriched_root / "report_state" / "employee_counts.parquet"),
        pd.read_parquet(enriched_root / "report_state" / "head_info.parquet"),
    )
    party_path = output_dir / "01_party_information.xlsx"
    progress(f"writing {party_path.name}")
    rows = _write_workbook(
        party_path,
        "party_information",
        party_information.columns.tolist(),
        [party_information],
    )
    summary.append(
        {"file": party_path.name, "rows": rows, "columns": len(party_information.columns)}
    )

    for number, group, section in REPORT_OUTPUTS:
        source_path = enriched_root / group / f"{section}.parquet"
        batch_factory = _section_batch_factory(source_path, context)
        columns, expected_rows = _active_stream_columns(batch_factory)
        if expected_rows == 0:
            columns = list(HELPER_COLUMNS)
        output_path = output_dir / f"{number}_{group}__{section}.xlsx"
        progress(f"writing {output_path.name}")
        rows = _write_workbook(output_path, section, columns, batch_factory())
        if rows != expected_rows:
            raise RuntimeError(f"Row-count mismatch while writing {output_path.name}")
        summary.append({"file": output_path.name, "rows": rows, "columns": len(columns)})

    for number, payment_type in PAYMENT_OUTPUTS:
        batch_factory = _payment_batch_factory(enriched_root, context, payment_type)
        columns, expected_rows = _active_stream_columns(batch_factory)
        output_path = output_dir / f"{number}_payments__{payment_type}.xlsx"
        progress(f"writing {output_path.name}")
        rows = _write_workbook(output_path, payment_type, columns, batch_factory())
        if rows != expected_rows:
            raise RuntimeError(f"Row-count mismatch while writing {output_path.name}")
        summary.append({"file": output_path.name, "rows": rows, "columns": len(columns)})

    reporting_matrix = build_reporting_organization_matrix(
        raw_context,
        pd.read_parquet(
            enriched_root / "reference" / "organization_reference.parquet"
        ),
    )
    reporting_path = output_dir / "17_organizations__reporting_history.xlsx"
    progress(f"writing {reporting_path.name}")
    rows = _write_workbook(
        reporting_path,
        "reporting_organizations",
        reporting_matrix.columns.tolist(),
        [reporting_matrix],
    )
    summary.append(
        {
            "file": reporting_path.name,
            "rows": rows,
            "columns": len(reporting_matrix.columns),
        }
    )

    return sorted(summary, key=lambda item: item["file"])
