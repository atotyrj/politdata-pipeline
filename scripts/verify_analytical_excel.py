"""Structural QA for the lean analytical Excel review set."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

import pandas as pd
import pyarrow.parquet as pq

from politdata.analytical_excel import (
    HELPER_COLUMNS,
    PAYMENT_OUTPUTS,
    REPORT_NAME_HISTORY_EXTRA_COLUMNS,
    REPORT_OUTPUTS,
    REPORTING_MATRIX_FIXED_COLUMNS,
)


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _expected_rows(enriched_root: Path) -> dict[str, int]:
    expected = {
        "01_party_information.xlsx": pq.ParquetFile(
            enriched_root / "reference" / "report_context.parquet"
        ).metadata.num_rows,
    }
    report_context = pd.read_parquet(
        enriched_root / "reference" / "report_context.parquet",
        columns=["organization_id"],
    )
    expected["17_organizations__reporting_history.xlsx"] = int(
        report_context["organization_id"].nunique()
    )
    expected["18_organizations__report_name_history.xlsx"] = int(len(report_context))
    for number, group, section in REPORT_OUTPUTS:
        expected[f"{number}_{group}__{section}.xlsx"] = pq.ParquetFile(
            enriched_root / group / f"{section}.parquet"
        ).metadata.num_rows

    payment_counts = {section: 0 for _, section in PAYMENT_OUTPUTS}
    for path in (enriched_root / "payments").glob("*.parquet"):
        frame = pd.read_parquet(path, columns=["analytical_payment_type"])
        counts = frame["analytical_payment_type"].value_counts()
        for section, count in counts.items():
            payment_counts[str(section)] += int(count)
    for number, section in PAYMENT_OUTPUTS:
        expected[f"{number}_payments__{section}.xlsx"] = payment_counts[section]
    return expected


def _worksheet_details(archive: zipfile.ZipFile):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None or len(sheets) != 1:
        raise AssertionError("Workbook must contain exactly one sheet")
    sheet = sheets[0]
    sheet_name = sheet.attrib["name"]
    relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]

    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = None
    for relationship in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        if relationship.attrib["Id"] == relationship_id:
            target = relationship.attrib["Target"]
            break
    if target is None:
        raise AssertionError("Worksheet relationship is missing")
    target = target.replace("\\", "/")
    if target.startswith("/"):
        worksheet_path = target.lstrip("/")
    else:
        worksheet_path = "xl/" + target.lstrip("/")
    return sheet_name, worksheet_path


def _cell_text(cell: ET.Element) -> str:
    inline = cell.find(f"{{{MAIN_NS}}}is")
    if inline is not None:
        return "".join(inline.itertext())
    value = cell.find(f"{{{MAIN_NS}}}v")
    return value.text if value is not None and value.text is not None else ""


def _sheet_summary(archive: zipfile.ZipFile, worksheet_path: str):
    with archive.open(worksheet_path) as source:
        iterator = ET.iterparse(source, events=("start", "end"))
        dimension = None
        headers = []
        row_count = 0
        first_data_styles = {}
        for event, element in iterator:
            if event == "start" and element.tag == f"{{{MAIN_NS}}}dimension":
                dimension = element.attrib.get("ref")
            if event == "end" and element.tag == f"{{{MAIN_NS}}}row":
                row_count += 1
                row_number = int(element.attrib.get("r", row_count))
                cells = element.findall(f"{{{MAIN_NS}}}c")
                if row_number == 1:
                    headers = [_cell_text(cell) for cell in cells]
                elif row_number == 2:
                    for cell in cells:
                        reference = cell.attrib.get("r", "")
                        column = re.match(r"[A-Z]+", reference)
                        if column:
                            first_data_styles[column.group()] = int(
                                cell.attrib.get("s", "0")
                            )
                element.clear()
        return dimension, headers, row_count, first_data_styles


def verify_directory(output_dir: Path, enriched_root: Path) -> list[dict]:
    expected = _expected_rows(enriched_root)
    actual_files = sorted(
        path.name for path in output_dir.glob("*.xlsx") if not path.name.startswith("~$")
    )
    if actual_files != sorted(expected):
        raise AssertionError(
            f"Unexpected workbook set. expected={sorted(expected)} actual={actual_files}"
        )

    forbidden = {
        "source_report_id",
        "source_row_json",
        "analytical_payment_type",
        "analysis_selection_method",
        "official_selected_report_id",
        "analysis_selected_report_id",
    }
    results = []
    for filename, expected_data_rows in sorted(expected.items()):
        path = output_dir / filename
        with zipfile.ZipFile(path) as archive:
            corrupt = archive.testzip()
            if corrupt is not None:
                raise AssertionError(f"{filename}: corrupt member {corrupt}")
            sheet_name, worksheet_path = _worksheet_details(archive)
            dimension, headers, xml_rows, _ = _sheet_summary(archive, worksheet_path)

        expected_xml_rows = expected_data_rows + 1 if expected_data_rows else 2
        if xml_rows != expected_xml_rows:
            raise AssertionError(
                f"{filename}: expected {expected_xml_rows} XML rows, got {xml_rows}"
            )
        if filename == "17_organizations__reporting_history.xlsx":
            expected_fixed = REPORTING_MATRIX_FIXED_COLUMNS
        elif filename == "18_organizations__report_name_history.xlsx":
            expected_fixed = (*HELPER_COLUMNS, *REPORT_NAME_HISTORY_EXTRA_COLUMNS)
        else:
            expected_fixed = HELPER_COLUMNS
        if tuple(headers[: len(expected_fixed)]) != expected_fixed:
            raise AssertionError(f"{filename}: helper-column contract mismatch")
        if "region" not in headers:
            raise AssertionError(f"{filename}: region column is missing")
        if filename == "17_organizations__reporting_history.xlsx":
            period_headers = headers[len(REPORTING_MATRIX_FIXED_COLUMNS) :]
            if not period_headers or any(
                re.fullmatch(r"20\d{2} (?:Q[1-4]|annual)", header) is None
                for header in period_headers
            ):
                raise AssertionError(f"{filename}: invalid reporting-period columns")
        if expected_data_rows:
            if forbidden.intersection(headers):
                raise AssertionError(
                    f"{filename}: forbidden columns {sorted(forbidden.intersection(headers))}"
                )
            if any(header.startswith("source__") or header.endswith("_raw") for header in headers):
                raise AssertionError(f"{filename}: raw/technical header leaked")
        results.append(
            {
                "file": filename,
                "sheet": sheet_name,
                "rows": expected_data_rows,
                "columns": len(headers),
                "dimension": dimension,
                "size_bytes": path.stat().st_size,
            }
        )
    return results


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--enriched-root",
        type=Path,
        default=Path("data/processed/enriched_v0_1"),
    )
    args = parser.parse_args(argv)
    for item in verify_directory(args.output_dir, args.enriched_root):
        print(
            f"{item['file']} | {item['rows']} rows | {item['columns']} columns | "
            f"{item['size_bytes']} bytes | {item['sheet']} | {item['dimension']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
