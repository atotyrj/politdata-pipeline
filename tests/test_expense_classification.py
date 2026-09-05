import pandas as pd

from politdata.expense_classification import (
    EXPENSE_CATEGORY_COLUMN,
    INTERNAL_TRANSFER_EXPENSE_CATEGORY,
    UNCLASSIFIED_EXPENSE_CATEGORY,
    classify_expense_categories,
    normalize_expense_text,
)


def test_normalize_expense_text_masks_variable_fragments():
    result = normalize_expense_text(
        "  Оплата за договором №AB-123 від 12.03.2025 р.  "
    )

    assert result == "оплата за договором <number> від <date> р."


def test_internal_transfer_uses_receiver_type_and_overrides_text():
    frame = pd.DataFrame(
        [
            {
                "receiver_type": "internal_party_transfer",
                "payment_purpose": "Оренда приміщення",
                "payment_reason": "Договір оренди",
            }
        ]
    )

    result = classify_expense_categories(frame)

    assert result.name == EXPENSE_CATEGORY_COLUMN
    assert result.iloc[0] == INTERNAL_TRANSFER_EXPENSE_CATEGORY


def test_text_categories_and_uncertain_fallback_are_conservative():
    frame = pd.DataFrame(
        [
            {
                "receiver_type": "Юридична особа",
                "payment_purpose": "Сплата ПДФО із заробітної плати",
                "payment_reason": "Податкове зобов'язання",
            },
            {
                "receiver_type": "Юридична особа",
                "payment_purpose": "Послуги з розміщення реклами в інтернеті",
                "payment_reason": "Договір про надання рекламних послуг",
            },
            {
                "receiver_type": "Юридична особа",
                "payment_purpose": "На підтримку ЗСУ: придбання дронів",
                "payment_reason": "Рішення партії",
            },
            {
                "receiver_type": "Юридична особа",
                "payment_purpose": "Оплата згідно з рахунком",
                "payment_reason": "Договір №15",
            },
        ]
    )

    assert classify_expense_categories(frame).tolist() == [
        "Податки та обов'язкові платежі",
        "Реклама та медіа",
        "Оборонна та благодійна допомога",
        UNCLASSIFIED_EXPENSE_CATEGORY,
    ]
