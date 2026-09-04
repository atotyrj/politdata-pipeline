from datetime import date

import pandas as pd

from politdata.analytical_excel import (
    HELPER_COLUMNS,
    REPORTING_MATRIX_FIXED_COLUMNS,
    build_party_information,
    build_report_context,
    build_reporting_organization_matrix,
    transform_payment_batch,
    transform_report_section_batch,
)


def report_context_frame():
    return pd.DataFrame(
        [
            {
                "source_report_id": "annual",
                "organization_id": "org-1",
                "organization_name_current": "Київська організація",
                "organization_code": "123",
                "party_name_current": "Партія",
                "party_code": "999",
                "year": 2023,
                "quarter": 5,
                "organization_level": "office",
                "region": "м. Київ",
                "data_recency_status": "historical_data",
            },
            {
                "source_report_id": "quarter",
                "organization_id": "org-1",
                "organization_name_current": "Київська організація",
                "organization_code": "123",
                "party_name_current": "Партія",
                "party_code": "999",
                "year": 2023,
                "quarter": 1,
                "organization_level": "office",
                "region": "м. Київ",
                "data_recency_status": "latest_data",
            },
        ]
    )


def test_report_context_has_compact_period_region_and_overlap():
    result = build_report_context(report_context_frame())

    assert tuple(result.columns[2:]) == HELPER_COLUMNS
    assert result["report_period"].tolist() == ["annual", "Q1"]
    assert result["organization_type"].eq("regional_office").all()
    assert result["region"].eq("м. Київ").all()
    assert result["potential_annual_overlap"].eq(True).all()


def test_report_section_keeps_original_names_and_only_clean_account_values():
    context = build_report_context(report_context_frame()).iloc[[0]]
    source = pd.DataFrame(
        [
            {
                "source_report_id": "annual",
                "source__id": "row-1",
                "source__account_number": " old iban ",
                "source__account_type": "old type",
                "source__begin_period_balance": "1 234,50",
                "party_account_iban": "UA123",
                "party_account_type_analytical": "ordinary_account",
                "source_row_json": "technical",
            }
        ]
    )

    result = transform_report_section_batch(
        source,
        context,
        source_columns=[
            "source__id",
            "source__account_number",
            "source__account_type",
            "source__begin_period_balance",
        ],
    )

    assert result.loc[0, "account_number"] == "UA123"
    assert result.loc[0, "account_type"] == "ordinary_account"
    assert result.loc[0, "begin_period_balance"] == 1234.5
    assert not any(column.startswith("source__") for column in result.columns)
    assert "source_row_json" not in result.columns


def test_payment_uses_clean_values_and_analytical_section_and_types():
    context = build_report_context(report_context_frame()).iloc[[1]]
    source = pd.DataFrame(
        [
            {
                "source_report_id": "quarter",
                "analytical_payment_type": "outgoing_expenses",
                "source_row_id": "payment-1",
                "payer_name_source": "ПЛАТНИК",
                "payer_name_normalized": "Платник",
                "payer_code_raw": " 123 ",
                "payer_code_normalized": "123",
                "payer_type_source": "old",
                "payer_type_analytical": "Фізична особа",
                "receiver_name_source": "ОСЕРЕДОК",
                "receiver_name_normalized": "Осередок",
                "receiver_type_analytical": "internal_party_transfer",
                "payment_amount_raw": "10,20",
                "payment_amount": 10.2,
                "payment_operation_date": date(2023, 1, 24),
            }
        ]
    )

    result = transform_payment_batch(
        source,
        context,
        analytical_payment_type="outgoing_expenses",
    )

    assert result.loc[0, "payer_name"] == "Платник"
    assert result.loc[0, "payer_type"] == "Фізична особа"
    assert result.loc[0, "receiver_type"] == "internal_party_transfer"
    assert result.loc[0, "payment_amount"] == 10.2
    assert "payer_name_source" not in result.columns
    assert "payment_amount_raw" not in result.columns
    assert "analytical_payment_type" not in result.columns


def test_internal_monetary_transfer_is_routed_only_to_other_incomes():
    context = build_report_context(report_context_frame()).iloc[[1]]
    source = pd.DataFrame(
        [
            {
                "source_report_id": "quarter",
                "analytical_payment_type": "monetary_contributions",
                "internal_transfer": True,
                "payment_amount": 25.0,
            }
        ]
    )

    monetary = transform_payment_batch(
        source,
        context,
        analytical_payment_type="monetary_contributions",
    )
    other_income = transform_payment_batch(
        source,
        context,
        analytical_payment_type="other_incomes",
    )

    assert monetary.empty
    assert len(other_income) == 1
    assert other_income.loc[0, "payment_amount"] == 25.0


def test_party_information_repeats_current_contact_data_per_report():
    context = build_report_context(report_context_frame())
    organizations = pd.DataFrame(
        [
            {
                "organization_id": "org-1",
                "is_active": True,
                "created_at": "2020-01-02",
                "updated_at": "2026-02-03",
                "web_site_url": "https://example.org",
                "email": "office@example.org",
                "phone": "+38000",
                "actual_address_same_register": True,
            }
        ]
    )
    addresses = pd.DataFrame(
        [
            {
                "organization_id": "org-1",
                "address_type": "register",
                **{field: None for field in (
                    "country", "post_index", "region", "district", "city",
                    "street", "building", "apartments", "common",
                    "building_part_num", "address_uk", "address_en",
                )},
                "city": "Київ",
            }
        ]
    )
    employees = pd.DataFrame(
        [
            {
                "source_report_id": report,
                "employees_by_civil_contract": 1,
                "employees_by_employment_contract": 2,
            }
            for report in ("annual", "quarter")
        ]
    )
    heads = pd.DataFrame(
        [
            {
                "source_report_id": report,
                "source__name": "ІГОР",
                "source__surname": "ІВАНЕНКО",
                "source__patronymic": "ІВАНОВИЧ",
            }
            for report in ("annual", "quarter")
        ]
    )

    result = build_party_information(
        context, organizations, addresses, employees, heads
    )

    assert len(result) == 2
    assert result["web_site_url"].eq("https://example.org").all()
    assert result["register_address_city"].eq("Київ").all()
    assert result["name"].eq("Ігор").all()
    assert "source_report_id" not in result.columns
    assert "public_summary" not in result.columns


def test_reporting_matrix_and_report_level_name_history():
    context = pd.DataFrame(
        [
            {
                "source_report_id": "r1",
                "organization_id": "office-1",
                "root_party_id": "party-1",
                "organization_code": "111",
                "organization_level": "office",
                "year": 2023,
                "quarter": 1,
                "signed_date_dt": "2023-04-01",
            },
            {
                "source_report_id": "r2",
                "organization_id": "office-1",
                "root_party_id": "party-1",
                "organization_code": "111",
                "organization_level": "office",
                "year": 2023,
                "quarter": 2,
                "signed_date_dt": "2023-07-01",
            },
            {
                "source_report_id": "r3",
                "organization_id": "office-1",
                "root_party_id": "party-1",
                "organization_code": "111",
                "organization_level": "office",
                "year": 2023,
                "quarter": 3,
                "signed_date_dt": "2023-10-01",
            },
            {
                "source_report_id": "party-r1",
                "organization_id": "party-1",
                "root_party_id": "party-1",
                "organization_code": "999",
                "organization_level": "central",
                "year": 2023,
                "quarter": 1,
                "signed_date_dt": "2023-04-02",
            },
            {
                "source_report_id": "party-annual",
                "organization_id": "party-1",
                "root_party_id": "party-1",
                "organization_code": "999",
                "organization_level": "central",
                "year": 2023,
                "quarter": 5,
                "signed_date_dt": "2024-01-02",
            },
        ]
    )
    reference = pd.DataFrame(
        [
            {
                "organization_id": "office-1",
                "organization_name_current": "Current office",
                "organization_code": "111",
                "party_name_current": "Current party",
                "party_code": "999",
                "organization_level": "office",
                "region": "Kyiv",
            },
            {
                "organization_id": "party-1",
                "organization_name_current": "Current party legal name",
                "organization_code": "999",
                "party_name_current": "Current party",
                "party_code": "999",
                "organization_level": "central",
                "region": "Ukraine",
            },
        ]
    )
    result = build_reporting_organization_matrix(
        context,
        reference,
    )

    assert tuple(result.columns[: len(REPORTING_MATRIX_FIXED_COLUMNS)]) == (
        REPORTING_MATRIX_FIXED_COLUMNS
    )
    assert "organization_id" not in result.columns
    assert len(result) == 2
    office = result.loc[result["organization_code"].eq("111")].iloc[0]
    assert office["first_report_period"] == "2023 Q1"
    assert office["latest_report_period"] == "2023 Q3"
    assert office["2023 Q1"] == 1
    assert office["2023 Q2"] == 1
    assert office["2023 Q3"] == 1
    assert pd.isna(office["2023 annual"])
    central = result.loc[result["organization_code"].eq("999")].iloc[0]
    assert central["potential_annual_overlap"] == 1
    assert central["2023 Q1"] == 1
    assert central["2023 annual"] == 1
