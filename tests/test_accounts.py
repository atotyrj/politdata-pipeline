import pandas as pd
import pytest

from politdata.normalization.accounts import (
    add_normalized_account_columns,
    is_valid_ua_iban,
    normalize_account_number,
)


IBAN_1 = "UA063516290000000026003240342"
IBAN_2 = "UA783204780000026005924904762"
IBAN_3 = "UA333052990000026002011039403"


# ============================================================
# VALIDATION
# ============================================================

def test_valid_ua_iban():
    assert is_valid_ua_iban(IBAN_1)
    assert is_valid_ua_iban(IBAN_2)
    assert is_valid_ua_iban(IBAN_3)


def test_invalid_checksum():
    invalid = (
        IBAN_1[:-1]
        + (
            "0"
            if IBAN_1[-1] != "0"
            else "1"
        )
    )

    assert not is_valid_ua_iban(
        invalid
    )


# ============================================================
# EXACT / BASIC NORMALIZATION
# ============================================================

def test_exact():
    result = normalize_account_number(
        IBAN_1
    )

    assert result.canonical == IBAN_1
    assert result.valid_iban is True
    assert result.status == "valid"
    assert result.method == "valid_exact"


@pytest.mark.parametrize(
    "raw",
    [
        IBAN_1.lower(),
        "\u200b" + IBAN_1,
        "\ufeff" + IBAN_1,
    ],
)
def test_unicode_case_normalization(
    raw,
):
    result = normalize_account_number(
        raw
    )

    assert result.canonical == IBAN_1
    assert result.valid_iban is True


# ============================================================
# SAFE FORMATTING CLEANUP
#
# Important:
# dirty forms are generated from a known-valid IBAN,
# so we never accidentally mistype a digit in the test fixture.
# ============================================================

@pytest.mark.parametrize(
    "separator",
    [
        " ",
        "\n",
        "\t",
        "-",
        ".",
        "/",
        "\\",
        ":",
    ],
)
def test_formatting_cleanup(
    separator,
):
    parts = [
        IBAN_1[:4],
        IBAN_1[4:10],
        IBAN_1[10:17],
        IBAN_1[17:],
    ]

    raw = separator.join(
        parts
    )

    result = normalize_account_number(
        raw
    )

    assert result.canonical == IBAN_1
    assert result.valid_iban is True
    assert (
        result.method
        ==
        "valid_after_formatting_cleanup"
    )


# ============================================================
# PREFIX REMOVAL
# ============================================================

@pytest.mark.parametrize(
    "raw",
    [
        ":" + IBAN_1,
        "№" + IBAN_1,
        "№ " + IBAN_1,
        "рахунок " + IBAN_1,
        "IBAN: " + IBAN_1,
    ],
)
def test_prefix_removal(
    raw,
):
    result = normalize_account_number(
        raw
    )

    assert result.canonical == IBAN_1
    assert result.valid_iban is True
    assert (
        result.method
        ==
        "valid_after_prefix_removal"
    )


# ============================================================
# CONSERVATIVE SAFETY RULES
# ============================================================

def test_missing_digit_is_not_repaired():
    malformed = (
        IBAN_1[:10]
        + IBAN_1[11:]
    )

    result = normalize_account_number(
        malformed
    )

    assert result.canonical is None
    assert result.valid_iban is False
    assert (
        result.status
        ==
        "invalid_or_nonstandard"
    )


def test_extra_digit_is_not_truncated():
    malformed = (
        IBAN_1
        + "5"
    )

    result = normalize_account_number(
        malformed
    )

    assert result.canonical is None
    assert result.valid_iban is False


def test_does_not_guess_invalid_identifier():
    result = normalize_account_number(
        "26003240342"
    )

    assert result.canonical is None
    assert result.valid_iban is False
    assert (
        result.status
        ==
        "invalid_or_nonstandard"
    )


def test_does_not_repair_bad_checksum():
    invalid = (
        IBAN_1[:-1]
        + (
            "0"
            if IBAN_1[-1] != "0"
            else "1"
        )
    )

    result = normalize_account_number(
        invalid
    )

    assert result.canonical is None
    assert result.valid_iban is False


# ============================================================
# AMBIGUITY
# ============================================================

def test_ambiguous_multiple_valid_ibans():
    raw = (
        f"{IBAN_1}; {IBAN_2}"
    )

    result = normalize_account_number(
        raw
    )

    assert result.canonical is None
    assert result.valid_iban is False

    assert (
        result.status
        ==
        "ambiguous_multiple_valid_ibans"
    )

    assert result.candidate_count == 2

    assert set(
        result.candidates
    ) == {
        IBAN_1,
        IBAN_2,
    }


# ============================================================
# MISSING VALUES
# ============================================================

@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
    ],
)
def test_missing(
    raw,
):
    result = normalize_account_number(
        raw
    )

    assert result.canonical is None
    assert result.status == "missing"


# ============================================================
# DATAFRAME HELPER
# ============================================================

def test_dataframe_helper():
    df = pd.DataFrame(
        {
            "payer_account_iban": [
                IBAN_1,
                "№ " + IBAN_2,
                None,
            ]
        }
    )

    out = add_normalized_account_columns(
        df,
        source_col="payer_account_iban",
        prefix="payer_account",
    )

    assert (
        out.loc[
            0,
            "payer_account_canonical",
        ]
        ==
        IBAN_1
    )

    assert (
        out.loc[
            1,
            "payer_account_canonical",
        ]
        ==
        IBAN_2
    )

    assert pd.isna(
        out.loc[
            2,
            "payer_account_canonical",
        ]
    )

    assert (
        "payer_account_raw"
        in out.columns
    )

    assert (
        "payer_account_normalization_method"
        in out.columns
    )


def test_dataframe_helper_preserves_source_column():
    df = pd.DataFrame(
        {
            "account_number": [
                "№ " + IBAN_1,
            ]
        }
    )

    original = df[
        "account_number"
    ].copy()

    out = add_normalized_account_columns(
        df,
        source_col="account_number",
        prefix="account",
    )

    assert (
        out["account_number"]
        .equals(original)
    )

    assert (
        out.loc[
            0,
            "account_raw",
        ]
        ==
        "№ " + IBAN_1
    )

    assert (
        out.loc[
            0,
            "account_canonical",
        ]
        ==
        IBAN_1
    )



# ============================================================
# REAL-WORLD PREFIX VARIANTS FROM POLITDATA
# ============================================================

@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            ":UA093366770000026007052508112",
            "UA093366770000026007052508112",
        ),
        (
            "NOUA303052990000026002043302142",
            "UA303052990000026002043302142",
        ),
    ],
)
def test_real_world_prefix_variants(
    raw,
    expected,
):
    result = normalize_account_number(
        raw
    )

    assert result.canonical == expected
    assert result.valid_iban is True
    assert (
        result.method
        ==
        "valid_after_prefix_removal"
    )
