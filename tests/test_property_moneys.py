
from politdata.normalization.property_moneys import (
    extract_property_moneys,
    get_account_number_source,
    get_account_type_source,
    classify_account_type,
    normalize_property_money_row,
)


def test_extract_property_moneys():

    detail = {
        "properties": {
            "moneys": [
                {
                    "account_number":
                        "UA263223130000026004000045109"
                }
            ]
        }
    }

    rows = extract_property_moneys(
        detail
    )

    assert len(rows) == 1


def test_get_account_number():

    row = {
        "account_number":
            "UA263223130000026004000045109"
    }

    assert (
        get_account_number_source(
            row
        )
        ==
        "UA263223130000026004000045109"
    )


def test_get_account_type_source():

    row = {
        "account_type":
            "Поточний рахунок"
    }

    assert (
        get_account_type_source(
            row
        )
        ==
        "Поточний рахунок"
    )


def test_state_funding_account():

    assert (
        classify_account_type(
            (
                "Рахунок для отримання коштів "
                "державного фінансування"
            )
        )
        ==
        "state_statutory_funding_account"
    )


def test_ordinary_account():

    assert (
        classify_account_type(
            "Поточний рахунок"
        )
        ==
        "ordinary_account"
    )


def test_normalized_property_money_row():

    row = {
        "account_number":
            "UA263223130000026004000045109",

        "account_type":
            "Поточний рахунок",
    }


    result = normalize_property_money_row(
        row,
        source_report_id="r1",
        organization_id="o1",
        root_party_id="p1",
        report_year=2025,
        report_quarter=1,
    )


    assert (
        result[
            "party_account_iban"
        ]
        ==
        "UA263223130000026004000045109"
    )

    assert (
        result[
            "party_account_type_analytical"
        ]
        ==
        "ordinary_account"
    )



def test_state_statutory_account_type():

    assert (
        classify_account_type(
            "Для зарахування коштів з державного бюджету "
            "на статутну діяльність"
        )
        ==
        "state_statutory_funding_account"
    )


def test_state_statutory_account_type_long_variant():

    assert (
        classify_account_type(
            "Рахунок для отримання коштів державного "
            "фінансування статутної діяльності політичної партії"
        )
        ==
        "state_statutory_funding_account"
    )


def test_campaign_reimbursement_account_type():

    assert (
        classify_account_type(
            "Рахунок для відшкодування витрат, пов’язаних "
            "із фінансуванням передвиборної агітації"
        )
        ==
        "state_campaign_reimbursement_account"
    )


def test_budget_account_remains_unspecified():

    assert (
        classify_account_type(
            "Бюджетний рахунок"
        )
        ==
        "budget_account_unspecified"
    )


def test_insurance_reimbursement_not_political_state_funding():

    assert (
        classify_account_type(
            "Поточний рахунок для виплат страхових відшкодувань"
        )
        ==
        "social_insurance_account"
    )


def test_bare_current_account():

    assert (
        classify_account_type(
            "поточний"
        )
        ==
        "ordinary_account"
    )
