import pandas as pd
import pytest

from politdata.name_backfill import (
    standardize_person_names,
    validate_name_backfill,
)


def _frame():
    return pd.DataFrame([
        {
            "source_row_id": "1",
            "payer_name_source": "ІВАНЕНКО ІВАН",
            "payer_name_normalized": "ІВАНЕНКО ІВАН",
            "payer_type_normalized": "Фізична особа",
            "receiver_name_source": "ТОВ ГОЛОС",
            "receiver_name_normalized": "ТОВ ГОЛОС",
            "receiver_type_normalized": "Юридична особа",
            "payment_amount": 10.25,
        },
        {
            "source_row_id": "2",
            "payer_name_source": "ТОВ ТЕСТ",
            "payer_name_normalized": "ТОВ ТЕСТ",
            "payer_type_normalized": "Юридична особа",
            "receiver_name_source": "ПЕТРЕНКО МАРІЯ",
            "receiver_name_normalized": "ПЕТРЕНКО МАРІЯ",
            "receiver_type_normalized": "Фізична особа",
            "payment_amount": 20.00,
        },
    ])


def test_standardizes_only_normalized_person_names():
    before = _frame()
    after, stats = standardize_person_names(before)
    assert after.loc[0, "payer_name_normalized"] == "Іваненко Іван"
    assert after.loc[1, "receiver_name_normalized"] == "Петренко Марія"
    assert after.loc[1, "payer_name_normalized"] == "ТОВ ТЕСТ"
    assert after["payer_name_source"].equals(before["payer_name_source"])
    assert stats["payer_name_normalized"]["changed_rows"] == 1


def test_validation_rejects_any_other_column_change():
    before = _frame()
    after = before.copy()
    after.loc[0, "payment_amount"] = 99
    with pytest.raises(AssertionError):
        validate_name_backfill(before, after)
