import pandas as pd

from politdata.payment_identity_refresh import refresh_payment_identity
from politdata.qa import PAYMENT_EXPECTED_ROWS, REFERENCE_IDENTITY_COLUMNS


def test_refresh_payment_identity_replaces_only_reference_fields(tmp_path):
    payment_root = tmp_path / "payments"
    payment_root.mkdir()
    reference_path = tmp_path / "organization_reference.parquet"
    reference = {
        "organization_id": "o1",
        "organization_code": "12345678",
        "organization_level": "office",
        "organization_name_current": "Canonical office name",
        "party_code": "87654321",
        "party_name_current": "Canonical party",
        "region": "Kyiv",
    }
    pd.DataFrame([reference]).to_parquet(reference_path, index=False)
    original = {
        "organization_id": "o1",
        "organization_code": "12345678",
        "organization_level": "office",
        "organization_name_current": "Old short name",
        "party_code": "87654321",
        "party_name_current": "Canonical party",
        "region": "Kyiv",
        "payment_amount": 25.5,
    }
    for section in PAYMENT_EXPECTED_ROWS:
        pd.DataFrame([original]).to_parquet(
            payment_root / f"{section}.parquet", index=False
        )

    output = tmp_path / "refreshed"
    summary = refresh_payment_identity(payment_root, reference_path, output)

    assert len(summary) == len(PAYMENT_EXPECTED_ROWS)
    for section in PAYMENT_EXPECTED_ROWS:
        result = pd.read_parquet(output / f"{section}.parquet")
        assert result.loc[0, "organization_name_current"] == "Canonical office name"
        assert result.loc[0, "payment_amount"] == 25.5
        assert list(result.columns) == list(original)
    assert sum(
        item["changed_fields"]["organization_name_current"] for item in summary
    ) == len(PAYMENT_EXPECTED_ROWS)
    assert all(
        sum(item["changed_fields"][column] for item in summary) == 0
        for column in REFERENCE_IDENTITY_COLUMNS
        if column != "organization_name_current"
    )
