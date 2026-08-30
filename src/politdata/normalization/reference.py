
from __future__ import annotations

import re
import unicodedata

import pandas as pd


# ============================================================
# TEXT
# ============================================================

def clean_text(value):

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = unicodedata.normalize(
        "NFKC",
        str(value),
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text or None


# ============================================================
# REGION NORMALIZATION
# ============================================================

REGION_RULES = [
    ("вінниц", "Вінницька область"),
    ("волин", "Волинська область"),
    ("дніпропетров", "Дніпропетровська область"),
    ("донець", "Донецька область"),
    ("житомир", "Житомирська область"),
    ("закарпат", "Закарпатська область"),
    ("запоріз", "Запорізька область"),
    ("івано-франків", "Івано-Франківська область"),
    ("київськ", "Київська область"),
    ("кіровоград", "Кіровоградська область"),
    ("луган", "Луганська область"),
    ("львів", "Львівська область"),
    ("миколаїв", "Миколаївська область"),
    ("одес", "Одеська область"),
    ("полтав", "Полтавська область"),
    ("рівнен", "Рівненська область"),
    ("сум", "Сумська область"),
    ("терноп", "Тернопільська область"),
    ("харків", "Харківська область"),
    ("херсон", "Херсонська область"),
    ("хмельниц", "Хмельницька область"),
    ("черкас", "Черкаська область"),
    ("чернів", "Чернівецька область"),
    ("черніг", "Чернігівська область"),
]


def normalize_region(value):
    """
    Conservative region normalization.

    Recognizes explicit oblast / Kyiv city /
    Crimea / Sevastopol signals.

    Does NOT infer oblast from locality names
    such as Біла Церква, Прилуки, Борислав etc.
    """

    text = clean_text(value)

    if text is None:
        return None

    lowered = text.casefold()


    # --------------------------------------------------------
    # KYIV CITY
    #
    # Must be checked before generic "київськ" oblast rule.
    #
    # Examples:
    # - м. Київ
    # - м.Києві
    # - в м. Києві
    # - Київська міська організація
    # --------------------------------------------------------

    if re.search(
        r"\bкиївськ\w*\s+міськ\w*",
        lowered,
    ):
        return "м. Київ"


    if re.search(
        r"\bм\.?\s*ки(?:їв|єв)\w*",
        lowered,
    ):
        return "м. Київ"


    if re.search(
        r"\bміст\w*\s+ки(?:їв|єв)\w*",
        lowered,
    ):
        return "м. Київ"


    if lowered == "київ":
        return "м. Київ"


    # --------------------------------------------------------
    # SEVASTOPOL
    # --------------------------------------------------------

    if "севастопол" in lowered:
        return "м. Севастополь"


    # --------------------------------------------------------
    # CRIMEA
    # --------------------------------------------------------

    if "крим" in lowered:
        return "Автономна Республіка Крим"


    # --------------------------------------------------------
    # OBLASTS
    # --------------------------------------------------------

    for token, canonical in REGION_RULES:

        if token in lowered:
            return canonical


    return None


# ============================================================
# PARTY NAME NORMALIZATION
# ============================================================

def normalize_party_name_short(value):
    """
    Human-friendly analytical party name.

    Based on the cleaning rule previously used
    in the exploratory notebooks.

    Examples:
        ПОЛІТИЧНА ПАРТІЯ «СЛУГА НАРОДУ»
        -> СЛУГА НАРОДУ

        ВСЕУКРАЇНСЬКЕ ОБ'ЄДНАННЯ «БАТЬКІВЩИНА»
        -> ВО БАТЬКІВЩИНА

    This function is ONLY for party names.
    Organization/office names are not changed.
    """

    text = clean_text(value)

    if text is None:
        return None

    # Remove quote characters used around party names.
    text = (
        text
        .replace('"', '')
        .replace('«', '')
        .replace('»', '')
    )

    # Remove leading generic legal label.
    text = re.sub(
        r"^\s*політична\s+партія\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Old analytical shortening rule.
    text = re.sub(
        r"\bвсеукраїнське\s+об['’ʼ`]єднання\b",
        "ВО",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text or None


# ============================================================
# ORGANIZATION REFERENCE
# ============================================================

def build_organization_reference(
    organizations: pd.DataFrame,
    addresses: pd.DataFrame,
) -> pd.DataFrame:

    org = organizations.copy()
    addr = addresses.copy()


    required = {
        "organization_id",
        "root_party_id",
        "entity_type",
        "code",
        "name",
    }

    missing = (
        required
        - set(org.columns)
    )

    if missing:
        raise ValueError(
            "organizations missing: "
            + ", ".join(
                sorted(missing)
            )
        )


    # --------------------------------------------------------
    # Stable identifiers
    # --------------------------------------------------------

    for col in [
        "organization_id",
        "root_party_id",
        "code",
        "name",
        "entity_type",
    ]:

        org[col] = (
            org[col]
            .astype("string")
        )


    # --------------------------------------------------------
    # CURRENT CENTRAL-PARTY REFERENCE
    #
    # Current name is intentionally propagated across
    # historical analytical data.
    #
    # Historical names will live in a separate history table.
    # --------------------------------------------------------

    parties = (
        org[
            org["entity_type"]
            == "party"
        ][
            [
                "organization_id",
                "code",
                "name",
            ]
        ]
        .rename(
            columns={
                "organization_id":
                    "root_party_id",

                "code":
                    "party_code",

                "name":
                    "party_name_current",
            }
        )
    )


    # Analytical party name:
    # short and unified.
    #
    # Office/organization names remain untouched.
    parties[
        "party_name_current"
    ] = (
        parties[
            "party_name_current"
        ]
        .map(
            normalize_party_name_short
        )
        .astype("string")
    )



    # --------------------------------------------------------
    # ANALYTICAL PARTY NAME
    #
    # party_name_current in analytical/reference data
    # is intentionally the SHORT unified form.
    #
    # Full official source name remains available in
    # normalized_v0_1/organizations.parquet.
    #
    # Office names are NOT modified.
    # --------------------------------------------------------

    parties[
        "party_name_current"
    ] = (
        parties[
            "party_name_current"
        ]
        .map(
            normalize_party_name_short
        )
        .astype("string")
    )


    if (
        parties[
            "party_name_current"
        ]
        .isna()
        .any()
    ):
        raise ValueError(
            "Party-name normalization produced "
            "missing analytical party names."
        )


    if (
        parties[
            "root_party_id"
        ]
        .duplicated()
        .any()
    ):
        raise ValueError(
            "Duplicate root party IDs."
        )


    # --------------------------------------------------------
    # ADDRESS SELECTION
    #
    # Priority:
    # 1. populated register region
    # 2. populated actual region
    # 3. other populated address type
    # 4. empty rows only if nothing else exists
    # --------------------------------------------------------

    addr["organization_id"] = (
        addr["organization_id"]
        .astype("string")
    )

    addr["region_source"] = (
        addr["region"]
        .astype("string")
    )


    region_present = (
        addr[
            "region_source"
        ].notna()
        &
        (
            addr[
                "region_source"
            ]
            .str.strip()
            != ""
        )
    )


    addr[
        "_missing_region"
    ] = (
        ~region_present
    ).astype(int)


    address_priority = {
        "register": 0,
        "actual": 1,
    }


    addr[
        "_address_priority"
    ] = (
        addr[
            "address_type"
        ]
        .map(
            address_priority
        )
        .fillna(2)
    )


    selected_addr = (
        addr
        .sort_values(
            [
                "organization_id",
                "_missing_region",
                "_address_priority",
            ]
        )
        .drop_duplicates(
            "organization_id",
            keep="first",
        )
        [
            [
                "organization_id",
                "address_type",
                "region_source",
            ]
        ]
        .rename(
            columns={
                "address_type":
                    "region_source_address_type",
            }
        )
    )


    # --------------------------------------------------------
    # MAIN REFERENCE
    # --------------------------------------------------------

    out = (
        org
        .rename(
            columns={
                "code":
                    "organization_code",

                "name":
                    "organization_name_current",
            }
        )
        .merge(
            parties,
            on="root_party_id",
            how="left",
            validate="many_to_one",
        )
        .merge(
            selected_addr,
            on="organization_id",
            how="left",
            validate="one_to_one",
        )
    )


    out[
        "organization_level"
    ] = (
        out[
            "entity_type"
        ]
        .map(
            {
                "party": "central",
                "office": "office",
            }
        )
        .astype("string")
    )


    # ========================================================
    # REGION RESOLUTION
    # ========================================================

    out[
        "region"
    ] = (
        out[
            "region_source"
        ]
        .map(
            normalize_region
        )
        .astype("string")
    )


    out[
        "region_resolution_method"
    ] = pd.Series(
        pd.NA,
        index=out.index,
        dtype="string",
    )


    out[
        "region_resolution_source"
    ] = pd.Series(
        pd.NA,
        index=out.index,
        dtype="string",
    )


    # --------------------------------------------------------
    # Address-based region
    # --------------------------------------------------------

    address_mask = (
        out[
            "organization_level"
        ]
        == "office"
    ) & (
        out["region"].notna()
    )


    out.loc[
        address_mask,
        "region_resolution_method",
    ] = (
        out.loc[
            address_mask,
            "region_source_address_type",
        ]
        .fillna("other")
        .astype("string")
        + "_address"
    )


    out.loc[
        address_mask,
        "region_resolution_source",
    ] = (
        out.loc[
            address_mask,
            "region_source",
        ]
        .astype("string")
    )


    # --------------------------------------------------------
    # SAFE NAME FALLBACK
    #
    # Only normalize_region().
    #
    # This means:
    #
    # "ЛЬВІВСЬКА ОБЛАСНА..." -> Львівська область
    # "КИЇВСЬКА МІСЬКА..."   -> м. Київ
    # "в м. Києві"           -> м. Київ
    #
    # but:
    #
    # "БІЛОЦЕРКІВСЬКА..."    -> unresolved
    # "ПРИЛУЦЬКА..."         -> unresolved
    # --------------------------------------------------------

    office_missing_region = (
        out[
            "organization_level"
        ]
        == "office"
    ) & (
        out[
            "region"
        ].isna()
    )


    region_from_name = (
        out.loc[
            office_missing_region,
            "organization_name_current",
        ]
        .map(
            normalize_region
        )
        .astype("string")
    )


    resolved_indexes = (
        region_from_name[
            region_from_name.notna()
        ]
        .index
    )


    out.loc[
        resolved_indexes,
        "region",
    ] = (
        region_from_name.loc[
            resolved_indexes
        ]
    )


    out.loc[
        resolved_indexes,
        "region_resolution_method",
    ] = (
        "explicit_organization_name"
    )


    out.loc[
        resolved_indexes,
        "region_resolution_source",
    ] = (
        out.loc[
            resolved_indexes,
            "organization_name_current",
        ]
        .astype("string")
    )


    # --------------------------------------------------------
    # Central organizations
    # --------------------------------------------------------

    central_mask = (
        out[
            "organization_level"
        ]
        == "central"
    )


    out.loc[
        central_mask,
        "region",
    ] = "Україна"


    out.loc[
        central_mask,
        "region_resolution_method",
    ] = "central_national"


    out.loc[
        central_mask,
        "region_resolution_source",
    ] = "entity_type=party"


    # --------------------------------------------------------
    # Remaining unresolved
    # --------------------------------------------------------

    unresolved_mask = (
        out[
            "organization_level"
        ]
        == "office"
    ) & (
        out[
            "region"
        ].isna()
    )


    out.loc[
        unresolved_mask,
        "region_resolution_method",
    ] = "unresolved"


    # ========================================================
    # QA
    # ========================================================

    if (
        out[
            "organization_id"
        ]
        .duplicated()
        .any()
    ):
        raise ValueError(
            "organization_reference "
            "not unique by organization_id."
        )


    if (
        out[
            "party_name_current"
        ]
        .isna()
        .any()
    ):
        raise ValueError(
            "Some organizations have "
            "no current party name."
        )


    if (
        out[
            "party_code"
        ]
        .isna()
        .any()
    ):
        raise ValueError(
            "Some organizations have "
            "no root-party code."
        )


    columns = [
        "organization_id",
        "root_party_id",

        "organization_level",

        "organization_code",
        "organization_name_current",

        "party_code",
        "party_name_current",

        "region",

        "region_source",
        "region_source_address_type",

        "region_resolution_method",
        "region_resolution_source",
    ]


    for optional in [
        "is_active",
        "parent_id",
    ]:

        if optional in out.columns:

            columns.append(
                optional
            )


    return (
        out[
            columns
        ]
        .reset_index(
            drop=True
        )
    )


# ============================================================
# REPORT CONTEXT
# ============================================================

def build_report_context(
    analysis_manifest: pd.DataFrame,
    organization_reference: pd.DataFrame,
) -> pd.DataFrame:

    reports = (
        analysis_manifest
        .copy()
    )


    required = {
        "report_id",
        "organization_id",
        "root_party_id",
        "year",
        "quarter",

        "official_selected_report_id",
        "analysis_selected_report_id",
        "analysis_selection_method",
        "analysis_override",
    }


    missing = (
        required
        - set(reports.columns)
    )

    if missing:

        raise ValueError(
            "analysis manifest missing: "
            + ", ".join(
                sorted(missing)
            )
        )


    for col in [
        "organization_id",
        "root_party_id",
        "official_selected_report_id",
        "analysis_selected_report_id",
    ]:

        reports[col] = (
            reports[col]
            .astype("string")
        )


    # --------------------------------------------------------
    # RAW report actually used by analytical pipeline
    # --------------------------------------------------------

    reports[
        "source_report_id"
    ] = (
        reports[
            "analysis_selected_report_id"
        ]
    )


    reports[
        "year"
    ] = (
        pd.to_numeric(
            reports["year"],
            errors="raise",
        )
        .astype(int)
    )


    reports[
        "quarter"
    ] = (
        pd.to_numeric(
            reports["quarter"],
            errors="raise",
        )
        .astype(int)
    )


    # --------------------------------------------------------
    # PERIOD ORDER
    #
    # quarter=5 = annual
    # --------------------------------------------------------

    reports[
        "report_period_order"
    ] = (
        reports["year"]
        * 10
        +
        reports["quarter"]
    )


    reports[
        "period_label"
    ] = (
        reports.apply(
            lambda r:
                (
                    f"{r['year']} annual"
                    if r["quarter"] == 5
                    else
                    f"{r['year']} Q{r['quarter']}"
                ),
            axis=1,
        )
        .astype("string")
    )


    # --------------------------------------------------------
    # LATEST DATA PER ORGANIZATION
    # --------------------------------------------------------

    latest_order = (
        reports
        .groupby(
            "organization_id"
        )[
            "report_period_order"
        ]
        .transform(
            "max"
        )
    )


    reports[
        "is_latest_report_for_organization"
    ] = (
        reports[
            "report_period_order"
        ]
        == latest_order
    )


    reports[
        "data_recency_status"
    ] = (
        reports[
            "is_latest_report_for_organization"
        ]
        .map(
            {
                True:
                    "latest_data",

                False:
                    "historical_data",
            }
        )
        .astype("string")
    )


    # --------------------------------------------------------
    # CURRENT ORGANIZATION/PARTY REFERENCE
    # --------------------------------------------------------

    ref_cols = [
        "organization_id",

        "organization_level",

        "organization_code",
        "organization_name_current",

        "party_code",
        "party_name_current",

        "region",

        "region_source",
        "region_source_address_type",

        "region_resolution_method",
        "region_resolution_source",
    ]


    out = reports.merge(
        organization_reference[
            ref_cols
        ],
        on="organization_id",
        how="left",
        validate="many_to_one",
    )


    # --------------------------------------------------------
    # QA
    # --------------------------------------------------------

    if (
        out[
            "source_report_id"
        ]
        .isna()
        .any()
    ):
        raise ValueError(
            "Missing analytical source_report_id."
        )


    if (
        out[
            "source_report_id"
        ]
        .duplicated()
        .any()
    ):
        raise ValueError(
            "Duplicate analytical source_report_id."
        )


    latest_counts = (
        out[
            out[
                "is_latest_report_for_organization"
            ]
        ]
        .groupby(
            "organization_id"
        )
        .size()
    )


    if (
        latest_counts > 1
    ).any():

        raise ValueError(
            "More than one latest period "
            "for an organization."
        )


    return (
        out
        .sort_values(
            [
                "organization_id",
                "report_period_order",
            ]
        )
        .reset_index(
            drop=True
        )
    )
