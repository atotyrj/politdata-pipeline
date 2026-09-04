
from decimal import Decimal

from politdata.normalization.payments import (
    normalize_code,
    normalize_counterparty_type,
    normalize_person_name,
    normalize_account_fields,
    normalize_report_payments,
)


def test_payment_code_normalization():

    assert (
        normalize_code(
            "  1234 5678 "
        )
        == "12345678"
    )


def test_fop_type_normalization():

    assert (
        normalize_counterparty_type(
            "ФОП"
        )
        == "Фізична особа"
    )


def test_person_name_fop_prefix():

    assert (
        normalize_person_name(
            "ФОП  Іваненко  Іван  Іванович",
            "Фізична особа",
        )
        ==
        "Іваненко Іван Іванович"
    )


def test_person_name_removes_fop_suffix_and_outer_punctuation():

    assert (
        normalize_person_name(
            "ЧИГРИН АННА ОЛЕКСАНДРІВНА, ФОП.",
            "Фізична особа",
        )
        ==
        "Чигрин Анна Олександрівна"
    )


def test_person_name_removes_full_fop_label_at_either_edge():

    assert (
        normalize_person_name(
            "Фізична особа-підприємець ІВАХІНА СВІТЛАНА МИКОЛАЇВНА "
            "фізична особа-підприємець",
            "Фізична особа",
        )
        ==
        "Івахіна Світлана Миколаївна"
    )


def test_person_name_removes_observed_malformed_fop_label():

    assert (
        normalize_person_name(
            "ФІЗИЧНА ОСОБО-ПІДПРИЄМЕЦЬ ЦЮРА НАТАЛІЯ МИКОЛАЇВНА",
            "Фізична особа",
        )
        ==
        "Цюра Наталія Миколаївна"
    )


def test_person_name_removes_professional_roles_at_either_edge():

    assert (
        normalize_person_name(
            "ПРИВАТНИЙ НОТАРІУС КОПАЧ ЮЛІЯ ОЛЕГІВНА, адвокат",
            "Фізична особа",
        )
        ==
        "Копач Юлія Олегівна"
    )


def test_person_name_removes_notary_district_suffix():

    assert (
        normalize_person_name(
            "КЛІМОВА Н. В. ПРИВАТНИЙ НОТАРІУС КИЇВ.МІСЬК.НОТ.ОКРУГ",
            "Фізична особа",
        )
        ==
        "Клімова Н. В."
    )


def test_explicit_fop_label_is_cleaned_despite_wrong_source_type():

    assert (
        normalize_person_name(
            "ФОП Карнаушенко М. В.",
            "Юридична особа",
        )
        ==
        "Карнаушенко М. В."
    )


def test_legal_name_with_fop_letters_is_unchanged():

    assert (
        normalize_person_name(
            'ТОВ "ПРОФОПТІМАСОЛЮШНЗ"',
            "Юридична особа",
        )
        ==
        'ТОВ "ПРОФОПТІМАСОЛЮШНЗ"'
    )


def test_person_name_latin_homoglyph():

    result = normalize_person_name(
        "IВАНOВ IВАН",
        "Фізична особа",
    )

    assert "I" not in result
    assert "O" not in result


def test_person_name_uses_canonical_case():

    assert (
        normalize_person_name(
            "ІВАНЕНКО іВАН іВАНОВИЧ",
            "Фізична особа",
        )
        ==
        "Іваненко Іван Іванович"
    )


def test_person_name_handles_hyphen_and_apostrophe():

    assert (
        normalize_person_name(
            "ЛУК'ЯНЕНКО-ПЕТРЕНКО МАРІЯ",
            "Фізична особа",
        )
        ==
        "Лук'яненко-Петренко Марія"
    )


def test_person_name_handles_quotes_and_initials():

    assert (
        normalize_person_name(
            '"ІВАНЕНКО І.І."',
            "Фізична особа",
        )
        ==
        "Іваненко І. І."
    )


def test_person_name_normalizes_double_quotes_as_apostrophe():

    assert (
        normalize_person_name(
            'Дерев""янко В.А.',
            "Фізична особа",
        )
        ==
        "Дерев'янко В. А."
    )


def test_legal_entity_name_case_is_unchanged():

    assert (
        normalize_person_name(
            "ТОВ ГОЛОС",
            "Юридична особа",
        )
        ==
        "ТОВ ГОЛОС"
    )


def test_account_wrapper_exact_real_iban():

    result = normalize_account_fields(
        "UA263223130000026004000045109"
    )

    assert (
        result["canonical"]
        ==
        "UA263223130000026004000045109"
    )

    assert result["valid"] is True


def test_synthetic_report_payment():

    detail = {
        "id": "r1",
        "year": 2025,
        "quarter": 2,
        "report_type": "main",
        "signed_date": None,

        "payment_info": {
            "incoming": {
                "monetary_contributions": [
                    {
                        "id": "row1",
                        "payer_code": "12345678",
                        "payer_name": "ТОВ Тест",
                        "payer_type": "Юридична особа",
                        "payment_amount": 100.25,
                        "payment_operation_date": "2025-05-01",
                    }
                ],

                "other_contributions": [],
                "state_funding": [],
                "other_incomes": [],
            },

            "outgoing": {
                "budget_expenses": [],
                "outgoing_expenses": [],
                "return_expenses": [],
                "transfer_expenses": [],
            },
        },
    }

    context = {
        "source_report_id": "r1",
        "official_selected_report_id": "r2",
        "analysis_selected_report_id": "r1",

        "organization_id": "o1",
        "root_party_id": "p1",

        "year": 2025,
        "quarter": 2,
        "period_label": "2025 Q2",
        "report_type": "main",

        "analysis_override": True,
        "analysis_selection_method": "test_override",
    }

    result = normalize_report_payments(
        detail,
        context,
    )

    rows = result[
        "monetary_contributions"
    ]

    assert len(rows) == 1

    row = rows[0]

    assert row["source_is_signed"] is False
    assert row["analysis_override"] is True

    assert (
        row["payment_amount"]
        ==
        Decimal("100.25")
    )

    assert (
        row["payer_code_normalized"]
        ==
        "12345678"
    )
