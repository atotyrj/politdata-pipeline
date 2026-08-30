from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


# ============================================================
# CONSTANTS
# ============================================================

UKRAINIAN_IBAN_RE = re.compile(
    r"^UA\d{27}$"
)


ZERO_WIDTH_CHARS = {
    "\u200b",  # zero width space
    "\u200c",  # zero width non-joiner
    "\u200d",  # zero width joiner
    "\u2060",  # word joiner
    "\ufeff",  # BOM / zero width no-break space
}


SAFE_FORMATTING_CHARS = {
    "-",
    ".",
    "_",
    "/",
    "\\",
    ":",
}


# ============================================================
# RESULT OBJECT
# ============================================================

@dataclass(frozen=True)
class AccountNormalizationResult:
    """
    Result of conservative account-number normalization.

    raw:
        Original source value converted to string where possible.

    canonical:
        Checksum-valid canonical Ukrainian IBAN, or None.

    status:
        High-level normalization outcome.

    method:
        How canonical value was obtained.

    normalized_text:
        Conservative normalized representation useful for QA.
        This must NOT be used as a substitute for canonical IBAN.

    valid_iban:
        True only if canonical is a checksum-valid Ukrainian IBAN.

    candidate_count:
        Number of distinct checksum-valid IBANs detected.

    candidates:
        All distinct valid IBAN candidates detected in the source
        value. Normally zero or one. More than one is ambiguous.
    """

    raw: str | None
    canonical: str | None
    status: str
    method: str
    normalized_text: str | None
    valid_iban: bool
    candidate_count: int
    candidates: tuple[str, ...]


# ============================================================
# LOW-LEVEL NORMALIZATION
# ============================================================

def _remove_zero_width(text: str) -> str:
    return "".join(
        ch
        for ch in text
        if ch not in ZERO_WIDTH_CHARS
    )


def _unicode_normalize(text: str) -> str:
    return unicodedata.normalize(
        "NFKC",
        text,
    )


def _prepare_text(text: str) -> str:
    """
    Unicode-normalize, remove invisible formatting chars,
    uppercase, and strip outer whitespace.
    """

    text = _unicode_normalize(text)
    text = _remove_zero_width(text)
    text = text.upper()

    return text.strip()


def _conservative_display_text(
    text: str,
) -> str:
    """
    QA-friendly representation.

    Collapses whitespace but does NOT remove arbitrary punctuation
    or attempt to turn a non-IBAN identifier into an IBAN.
    """

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def _remove_safe_formatting(
    text: str,
) -> str:
    """
    Remove only formatting characters we have explicitly decided
    are safe to ignore inside a Ukrainian IBAN.
    """

    chars = []

    for ch in text:

        if ch.isspace():
            continue

        if ch in SAFE_FORMATTING_CHARS:
            continue

        chars.append(ch)

    return "".join(chars)


# ============================================================
# IBAN VALIDATION
# ============================================================

def is_valid_ua_iban(
    value: Any,
) -> bool:
    """
    Validate canonical Ukrainian IBAN using MOD-97.

    Ukrainian IBAN:
        UA + 27 digits
        total length = 29 characters.
    """

    if not isinstance(
        value,
        str,
    ):
        return False


    iban = value.upper()


    if not UKRAINIAN_IBAN_RE.fullmatch(
        iban
    ):
        return False


    rearranged = (
        iban[4:]
        + iban[:4]
    )


    remainder = 0


    for ch in rearranged:

        if ch.isdigit():
            digits = ch

        else:
            # A=10 ... Z=35
            digits = str(
                ord(ch) - 55
            )


        for digit in digits:

            remainder = (
                remainder * 10
                + int(digit)
            ) % 97


    return remainder == 1


# ============================================================
# CANDIDATE EXTRACTION
# ============================================================

def _candidate_from_ua_position(
    text: str,
    position: int,
) -> str | None:
    """
    Starting at a specific 'UA', conservatively read 27 digits.

    Safe formatting separators may occur between digits.

    If an unsupported character is encountered before 27 digits,
    extraction stops.

    If a 28th digit immediately follows the candidate, reject it
    instead of silently truncating the identifier.
    """

    if (
        position < 0
        or text[
            position:
            position + 2
        ] != "UA"
    ):
        return None


    digits = []

    i = position + 2


    while (
        i < len(text)
        and len(digits) < 27
    ):

        ch = text[i]


        if ch.isdigit():

            digits.append(ch)


        elif (
            ch.isspace()
            or ch in SAFE_FORMATTING_CHARS
        ):

            pass


        else:

            break


        i += 1


    if len(digits) != 27:
        return None


    # Reject identifiers that actually contain more than
    # 27 digits after UA.
    j = i

    while (
        j < len(text)
        and (
            text[j].isspace()
            or text[j]
            in SAFE_FORMATTING_CHARS
        )
    ):
        j += 1


    if (
        j < len(text)
        and text[j].isdigit()
    ):
        return None


    candidate = (
        "UA"
        + "".join(digits)
    )


    if is_valid_ua_iban(
        candidate
    ):
        return candidate


    return None


def _extract_valid_candidates(
    text: str,
) -> tuple[str, ...]:
    """
    Find all distinct checksum-valid Ukrainian IBANs in a value.

    This is deliberately conservative:
    only substrings beginning with explicit 'UA' are considered.
    """

    candidates = []


    for match in re.finditer(
        r"UA",
        text,
    ):

        candidate = (
            _candidate_from_ua_position(
                text,
                match.start(),
            )
        )


        if (
            candidate is not None
            and candidate not in candidates
        ):

            candidates.append(
                candidate
            )


    return tuple(
        candidates
    )


# ============================================================
# PUBLIC NORMALIZER
# ============================================================

def normalize_account_number(
    value: Any,
) -> AccountNormalizationResult:
    """
    Conservatively normalize an account-number field.

    Rules
    -----
    1. Preserve original source value.
    2. Unicode NFKC.
    3. Remove zero-width formatting characters.
    4. Uppercase.
    5. Accept only Ukrainian IBAN:
           UA + 27 digits.
    6. Validate using MOD-97.
    7. Safe formatting separators may be removed.
    8. Prefix before explicit UA may be discarded.
    9. If >1 distinct valid IBAN is detected:
           do NOT guess; return ambiguous.
    10. If validity cannot be proven:
           canonical remains None.

    The function normalizes representation, not financial meaning.
    """


    # --------------------------------------------------------
    # MISSING
    # --------------------------------------------------------

    if value is None:

        return AccountNormalizationResult(
            raw=None,
            canonical=None,
            status="missing",
            method="missing",
            normalized_text=None,
            valid_iban=False,
            candidate_count=0,
            candidates=(),
        )


    try:

        # pandas / numpy NA without importing pandas
        if value != value:

            return AccountNormalizationResult(
                raw=None,
                canonical=None,
                status="missing",
                method="missing",
                normalized_text=None,
                valid_iban=False,
                candidate_count=0,
                candidates=(),
            )

    except Exception:
        pass


    raw = str(value)


    if raw.strip() == "":

        return AccountNormalizationResult(
            raw=raw,
            canonical=None,
            status="missing",
            method="empty_string",
            normalized_text="",
            valid_iban=False,
            candidate_count=0,
            candidates=(),
        )


    prepared = _prepare_text(
        raw
    )


    display_text = (
        _conservative_display_text(
            prepared
        )
    )


    # --------------------------------------------------------
    # EXACT
    #
    # Raw source already exactly canonical.
    # --------------------------------------------------------

    raw_stripped = raw.strip()


    if (
        is_valid_ua_iban(
            raw_stripped
        )
        and raw_stripped
        ==
        raw_stripped.upper()
    ):

        canonical = (
            raw_stripped.upper()
        )

        return AccountNormalizationResult(
            raw=raw,
            canonical=canonical,
            status="valid",
            method="valid_exact",
            normalized_text=display_text,
            valid_iban=True,
            candidate_count=1,
            candidates=(
                canonical,
            ),
        )


    # --------------------------------------------------------
    # UNICODE / CASE NORMALIZATION ONLY
    # --------------------------------------------------------

    if is_valid_ua_iban(
        prepared
    ):

        return AccountNormalizationResult(
            raw=raw,
            canonical=prepared,
            status="valid",
            method="valid_normalized",
            normalized_text=display_text,
            valid_iban=True,
            candidate_count=1,
            candidates=(
                prepared,
            ),
        )


    # --------------------------------------------------------
    # SAFE FORMATTING CLEANUP
    #
    # Only use this shortcut when the normalized value itself
    # begins with UA. Prefix handling is classified separately.
    # --------------------------------------------------------

    if prepared.startswith(
        "UA"
    ):

        cleaned = (
            _remove_safe_formatting(
                prepared
            )
        )


        if is_valid_ua_iban(
            cleaned
        ):

            return AccountNormalizationResult(
                raw=raw,
                canonical=cleaned,
                status="valid",
                method=(
                    "valid_after_formatting_cleanup"
                ),
                normalized_text=display_text,
                valid_iban=True,
                candidate_count=1,
                candidates=(
                    cleaned,
                ),
            )


    # --------------------------------------------------------
    # EXPLICIT UA EXTRACTION
    #
    # Allows things such as:
    #
    #   :UA...
    #   № UA...
    #   рахунок UA...
    #
    # but never invents UA or repairs checksum errors.
    # --------------------------------------------------------

    candidates = (
        _extract_valid_candidates(
            prepared
        )
    )


    if len(candidates) == 1:

        canonical = candidates[0]

        return AccountNormalizationResult(
            raw=raw,
            canonical=canonical,
            status="valid",
            method=(
                "valid_after_prefix_removal"
            ),
            normalized_text=display_text,
            valid_iban=True,
            candidate_count=1,
            candidates=candidates,
        )


    # --------------------------------------------------------
    # AMBIGUOUS
    # --------------------------------------------------------

    if len(candidates) > 1:

        return AccountNormalizationResult(
            raw=raw,
            canonical=None,
            status=(
                "ambiguous_multiple_valid_ibans"
            ),
            method="ambiguous",
            normalized_text=display_text,
            valid_iban=False,
            candidate_count=len(
                candidates
            ),
            candidates=candidates,
        )


    # --------------------------------------------------------
    # INVALID / NON-STANDARD
    # --------------------------------------------------------

    return AccountNormalizationResult(
        raw=raw,
        canonical=None,
        status="invalid_or_nonstandard",
        method="no_valid_ua_iban",
        normalized_text=display_text,
        valid_iban=False,
        candidate_count=0,
        candidates=(),
    )


# ============================================================
# PANDAS HELPER
# ============================================================

def add_normalized_account_columns(
    df,
    source_col: str,
    prefix: str | None = None,
    *,
    copy: bool = True,
):
    """
    Add standard account-normalization columns to a pandas DataFrame.

    Example
    -------
    add_normalized_account_columns(
        df,
        source_col="payer_account_iban",
        prefix="payer_account",
    )

    Produces:
        payer_account_raw
        payer_account_canonical
        payer_account_normalization_status
        payer_account_normalization_method
        payer_account_valid_iban
        payer_account_candidate_count
        payer_account_candidates
        payer_account_normalized_text

    The original source column is preserved.
    """

    if source_col not in df.columns:

        raise KeyError(
            f"Column not found: {source_col}"
        )


    if prefix is None:

        prefix = source_col


    out = (
        df.copy()
        if copy
        else df
    )


    results = (
        out[source_col]
        .map(
            normalize_account_number
        )
    )


    out[
        f"{prefix}_raw"
    ] = results.map(
        lambda x:
            x.raw
    )


    out[
        f"{prefix}_canonical"
    ] = results.map(
        lambda x:
            x.canonical
    )


    out[
        f"{prefix}_normalization_status"
    ] = results.map(
        lambda x:
            x.status
    )


    out[
        f"{prefix}_normalization_method"
    ] = results.map(
        lambda x:
            x.method
    )


    out[
        f"{prefix}_valid_iban"
    ] = results.map(
        lambda x:
            x.valid_iban
    )


    out[
        f"{prefix}_candidate_count"
    ] = results.map(
        lambda x:
            x.candidate_count
    )


    out[
        f"{prefix}_candidates"
    ] = results.map(
        lambda x:
            list(
                x.candidates
            )
    )


    out[
        f"{prefix}_normalized_text"
    ] = results.map(
        lambda x:
            x.normalized_text
    )


    return out
