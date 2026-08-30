
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import copy
import hashlib
import json
import os
import time
import uuid

import pandas as pd
import requests

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    create_change_set,
    load_change_set,
    merge_change_set_changes,
    report_changes_from_states,
    save_change_set,
)


DEFAULT_BASE_URL = (
    "https://politdata.nazk.gov.ua/api/v2"
)

DEFAULT_RAW_DIR = Path(
    "data/raw/report_details"
)

DEFAULT_STATE_PATH = Path(
    "data/interim/state/"
    "report_detail_state.parquet"
)

RETRYABLE_STATUS_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


METADATA_COLUMNS = [
    "report_id",
    "organization_id",
    "root_party_id",
    "entity_type",
    "year",
    "quarter",
    "selection_method",
]


STATE_COLUMNS = [
    "status",
    "attempts",
    "last_checked_at_utc",
    "retrieved_at_utc",
    "raw_path",
    "raw_payload_hash",
    "content_hash",
    "file_size_bytes",
    "property_paper_count",
    "last_error",
]


# ============================================================
# TIME
# ============================================================

def utc_now_iso():
    return (
        datetime.now(
            timezone.utc
        )
        .isoformat()
    )


def file_mtime_utc(path):
    return (
        datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        )
        .isoformat()
    )


# ============================================================
# HASHING
# ============================================================

def _canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def raw_payload_hash(payload):
    """
    Exact hash of the entire API payload.

    public_summary IS included here because this hash is
    intended only to describe the exact stored response.
    """

    return hashlib.sha256(
        _canonical_json_bytes(
            payload
        )
    ).hexdigest()


def report_detail_content_hash(payload):
    """
    Semantic hash of source report detail.

    public_summary is explicitly excluded because it is
    a derived NACP layer and must not determine whether
    the underlying report content changed.
    """

    semantic = copy.deepcopy(
        payload
    )

    results = semantic.get(
        "results"
    )

    if isinstance(
        results,
        dict,
    ):
        results.pop(
            "public_summary",
            None,
        )

    return hashlib.sha256(
        _canonical_json_bytes(
            semantic
        )
    ).hexdigest()


# ============================================================
# VALIDATION
# ============================================================

def validate_report_detail_payload(
    payload,
    expected_report_id,
):
    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Report detail response "
            "is not a dictionary."
        )

    code = payload.get(
        "code"
    )

    if (
        code is not None
        and code != 0
    ):
        raise ValueError(
            f"Unexpected API code: {code}"
        )

    results = payload.get(
        "results"
    )

    if not isinstance(
        results,
        dict,
    ):
        raise ValueError(
            "Missing results object."
        )

    actual_id = results.get(
        "id"
    )

    if str(actual_id) != str(
        expected_report_id
    ):
        raise ValueError(
            "Report ID mismatch: "
            f"expected={expected_report_id}, "
            f"actual={actual_id}"
        )

    return results


def property_paper_count(
    payload,
):
    results = payload.get(
        "results"
    ) or {}

    properties = results.get(
        "properties"
    ) or {}

    rows = properties.get(
        "property_paper"
    ) or []

    return len(rows)


# ============================================================
# ATOMIC FILE WRITES
# ============================================================

def _atomic_replace(
    temp_path,
    final_path,
    max_retries=20,
):

    final_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    last_error = None

    for attempt in range(
        1,
        max_retries + 1,
    ):
        try:

            os.replace(
                temp_path,
                final_path,
            )

            return

        except PermissionError as exc:

            last_error = exc

            if attempt == max_retries:
                break

            time.sleep(
                0.25 * attempt
            )

    raise last_error


def atomic_write_json(
    path,
    payload,
):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        path.name
        + ".tmp."
        + uuid.uuid4().hex
    )

    try:

        with temp_path.open(
            "w",
            encoding="utf-8",
        ) as f:

            # Compact JSON materially reduces storage
            # for tens of thousands of reports.
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                separators=(",", ":"),
            )

            f.flush()

            os.fsync(
                f.fileno()
            )

        _atomic_replace(
            temp_path,
            path,
        )

    finally:

        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def atomic_write_parquet(
    df,
    path,
):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        path.stem
        + ".tmp."
        + uuid.uuid4().hex
        + path.suffix
    )

    try:

        df.to_parquet(
            temp_path,
            index=False,
        )

        _atomic_replace(
            temp_path,
            path,
        )

    finally:

        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def try_save_state(
    state,
    state_path,
):
    try:

        atomic_write_parquet(
            state,
            state_path,
        )

        return True

    except PermissionError as exc:

        print(
            "WARNING: state checkpoint "
            "could not be saved:"
        )

        print(
            repr(exc)
        )

        print(
            "RAW files already written "
            "remain recoverable."
        )

        return False


def update_report_change_set(
    previous_state,
    current_state,
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
):
    """Append semantic report changes from one batch to the current run."""

    if change_set_path is None:
        return None

    report_changes = report_changes_from_states(
        previous_state,
        current_state,
    )
    change_set_path = Path(change_set_path)

    if change_set_path.exists():
        change_set = load_change_set(
            change_set_path
        )
        change_set = merge_change_set_changes(
            change_set,
            report_changes=report_changes,
        )
    else:
        change_set = create_change_set(
            report_changes=report_changes,
        )

    save_change_set(
        change_set,
        change_set_path,
    )
    return change_set


# ============================================================
# STATE
# ============================================================

def initialize_report_detail_state(
    selected_reports,
    state_path=DEFAULT_STATE_PATH,
):

    missing = [
        col
        for col
        in METADATA_COLUMNS
        if col
        not in selected_reports.columns
    ]

    if missing:
        raise ValueError(
            "Missing selected-report columns: "
            + ", ".join(missing)
        )

    base = (
        selected_reports[
            METADATA_COLUMNS
        ]
        .drop_duplicates(
            subset=[
                "report_id"
            ]
        )
        .copy()
    )

    base[
        "report_id"
    ] = base[
        "report_id"
    ].astype(str)

    state_path = Path(
        state_path
    )

    if state_path.exists():

        existing = pd.read_parquet(
            state_path
        )

        existing[
            "report_id"
        ] = existing[
            "report_id"
        ].astype(str)

        for col in STATE_COLUMNS:

            if col not in (
                existing.columns
            ):
                existing[col] = None

        existing_ops = (
            existing[
                [
                    "report_id"
                ]
                + STATE_COLUMNS
            ]
            .drop_duplicates(
                subset=[
                    "report_id"
                ]
            )
        )

        state = base.merge(
            existing_ops,
            on="report_id",
            how="left",
        )

    else:

        state = base.copy()

        for col in STATE_COLUMNS:
            state[col] = None


    state[
        "status"
    ] = state[
        "status"
    ].fillna(
        "pending"
    )

    state[
        "attempts"
    ] = (
        pd.to_numeric(
            state[
                "attempts"
            ],
            errors="coerce",
        )
        .fillna(0)
        .astype(int)
    )

    return state


# ============================================================
# RAW RECOVERY
# ============================================================

def reconcile_state_from_raw(
    state,
    raw_dir=DEFAULT_RAW_DIR,
):

    raw_dir = Path(
        raw_dir
    )

    recovered = 0

    state = state.copy()

    candidate_mask = (
        state[
            "status"
        ]
        != "success"
    )

    candidate_indices = (
        state.index[
            candidate_mask
        ]
        .tolist()
    )

    for idx in candidate_indices:

        report_id = str(
            state.at[
                idx,
                "report_id",
            ]
        )

        path = (
            raw_dir
            / f"{report_id}.json"
        )

        if not path.exists():
            continue

        try:

            with path.open(
                "r",
                encoding="utf-8",
            ) as f:

                payload = json.load(f)

            validate_report_detail_payload(
                payload,
                report_id,
            )

            state.at[
                idx,
                "status",
            ] = "success"

            state.at[
                idx,
                "raw_path",
            ] = str(path)

            state.at[
                idx,
                "raw_payload_hash",
            ] = raw_payload_hash(
                payload
            )

            state.at[
                idx,
                "content_hash",
            ] = (
                report_detail_content_hash(
                    payload
                )
            )

            state.at[
                idx,
                "file_size_bytes",
            ] = path.stat().st_size

            state.at[
                idx,
                "property_paper_count",
            ] = property_paper_count(
                payload
            )

            state.at[
                idx,
                "retrieved_at_utc",
            ] = file_mtime_utc(
                path
            )

            state.at[
                idx,
                "last_error",
            ] = None

            recovered += 1

        except Exception:
            # Existing file is not trusted if it
            # cannot be parsed and validated.
            continue

    return state, recovered


# ============================================================
# API FETCH
# ============================================================

def fetch_report_detail(
    report_id,
    base_url=DEFAULT_BASE_URL,
    timeout=180,
    max_retries=4,
    session=None,
):

    if session is None:
        session = requests.Session()

    url = (
        f"{base_url}"
        f"/party/report/{report_id}"
    )

    last_error = None

    for attempt in range(
        1,
        max_retries + 1,
    ):

        try:

            response = session.get(
                url,
                timeout=timeout,
            )

            if (
                response.status_code
                in RETRYABLE_STATUS_CODES
            ):

                raise requests.HTTPError(
                    "Retryable HTTP status "
                    f"{response.status_code}",
                    response=response,
                )

            response.raise_for_status()

            payload = response.json()

            validate_report_detail_payload(
                payload,
                report_id,
            )

            return payload

        except (
            requests.RequestException,
            ValueError,
            json.JSONDecodeError,
        ) as exc:

            last_error = exc

            retryable = True

            if isinstance(
                exc,
                requests.HTTPError,
            ):

                response = getattr(
                    exc,
                    "response",
                    None,
                )

                if (
                    response is not None
                    and response.status_code
                    not in RETRYABLE_STATUS_CODES
                ):
                    retryable = False

            if (
                not retryable
                or attempt
                == max_retries
            ):
                break

            sleep_seconds = (
                2 ** (attempt - 1)
            )

            time.sleep(
                sleep_seconds
            )

    raise last_error


# ============================================================
# MAIN BATCH RUNNER
# ============================================================

def run_report_detail_batch(
    selected_reports,
    limit=None,
    retry_errors=True,
    checkpoint_every=25,
    timeout=180,
    max_retries=4,
    base_url=DEFAULT_BASE_URL,
    raw_dir=DEFAULT_RAW_DIR,
    state_path=DEFAULT_STATE_PATH,
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
):

    from tqdm.auto import tqdm

    raw_dir = Path(raw_dir)
    state_path = Path(state_path)

    raw_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    state = initialize_report_detail_state(
        selected_reports,
        state_path=state_path,
    )

    previous_state = state.copy()

    # Recover valid RAW files that may exist
    # after an interrupted run.
    state, recovered = reconcile_state_from_raw(
        state,
        raw_dir=raw_dir,
    )

    if recovered:

        try_save_state(
            state,
            state_path,
        )

    candidate_statuses = [
        "pending",
    ]

    if retry_errors:
        candidate_statuses.append(
            "error"
        )

    candidates = state[
        state["status"].isin(
            candidate_statuses
        )
    ]

    if limit is not None:

        candidates = candidates.head(
            int(limit)
        )

    report_ids = (
        candidates["report_id"]
        .astype(str)
        .tolist()
    )

    print(
        "Selected for download:",
        len(report_ids)
    )

    if not report_ids:

        summary = {
            "selected": 0,
            "successful": 0,
            "failed": 0,
            "recovered_from_raw":
                recovered,
            "bytes_written": 0,
            "total_success_in_state":
                int(
                    (
                        state["status"]
                        == "success"
                    ).sum()
                ),
            "total_errors_in_state":
                int(
                    (
                        state["status"]
                        == "error"
                    ).sum()
                ),
            "total_pending_in_state":
                int(
                    (
                        state["status"]
                        == "pending"
                    ).sum()
                ),
            "reports_with_paper_this_run":
                0,
        }

        update_report_change_set(
            previous_state,
            state,
            change_set_path=change_set_path,
        )

        return (
            summary,
            state,
        )

    state = state.set_index(
        "report_id",
        drop=False,
    )

    session = requests.Session()

    successful = 0
    failed = 0
    bytes_written = 0

    paper_reports = []

    progress = tqdm(
        report_ids,
        total=len(report_ids),
        desc="Report details",
        unit="report",
        dynamic_ncols=True,
        mininterval=1.0,
        smoothing=0.1,
    )

    try:

        for n, report_id in enumerate(
            progress,
            start=1,
        ):

            checked_at = utc_now_iso()

            state.at[
                report_id,
                "attempts",
            ] = (
                int(
                    state.at[
                        report_id,
                        "attempts",
                    ]
                )
                + 1
            )

            state.at[
                report_id,
                "last_checked_at_utc",
            ] = checked_at

            try:

                payload = fetch_report_detail(
                    report_id,
                    base_url=base_url,
                    timeout=timeout,
                    max_retries=max_retries,
                    session=session,
                )

                output_path = (
                    raw_dir
                    / f"{report_id}.json"
                )

                # RAW first, state second.
                atomic_write_json(
                    output_path,
                    payload,
                )

                retrieved_at = utc_now_iso()

                size_bytes = (
                    output_path
                    .stat()
                    .st_size
                )

                paper_count = (
                    property_paper_count(
                        payload
                    )
                )

                state.at[
                    report_id,
                    "status",
                ] = "success"

                state.at[
                    report_id,
                    "retrieved_at_utc",
                ] = retrieved_at

                state.at[
                    report_id,
                    "raw_path",
                ] = str(
                    output_path
                )

                state.at[
                    report_id,
                    "raw_payload_hash",
                ] = raw_payload_hash(
                    payload
                )

                state.at[
                    report_id,
                    "content_hash",
                ] = (
                    report_detail_content_hash(
                        payload
                    )
                )

                state.at[
                    report_id,
                    "file_size_bytes",
                ] = size_bytes

                state.at[
                    report_id,
                    "property_paper_count",
                ] = paper_count

                state.at[
                    report_id,
                    "last_error",
                ] = None

                successful += 1
                bytes_written += size_bytes

                if paper_count > 0:

                    paper_reports.append(
                        (
                            report_id,
                            paper_count,
                        )
                    )

            except Exception as exc:

                state.at[
                    report_id,
                    "status",
                ] = "error"

                state.at[
                    report_id,
                    "last_error",
                ] = repr(exc)

                failed += 1

            # Save state silently.
            if (
                n % checkpoint_every == 0
                or n == len(report_ids)
            ):

                state_for_save = (
                    state.reset_index(
                        drop=True
                    )
                )

                try_save_state(
                    state_for_save,
                    state_path,
                )

            # Update only the existing progress bar.
            if (
                n % 25 == 0
                or n == len(report_ids)
            ):

                progress.set_postfix(
                    success=successful,
                    errors=failed,
                    refresh=False,
                )

    finally:

        progress.close()
        session.close()

    state = state.reset_index(
        drop=True
    )

    try_save_state(
        state,
        state_path,
    )

    if paper_reports:

        print(
            "Non-empty property_paper:",
            len(paper_reports),
        )

        print(
            "First examples:",
            paper_reports[:5],
        )

    summary = {
        "selected":
            len(report_ids),

        "successful":
            successful,

        "failed":
            failed,

        "recovered_from_raw":
            recovered,

        "bytes_written":
            bytes_written,

        "total_success_in_state":
            int(
                (
                    state["status"]
                    == "success"
                ).sum()
            ),

        "total_errors_in_state":
            int(
                (
                    state["status"]
                    == "error"
                ).sum()
            ),

        "total_pending_in_state":
            int(
                (
                    state["status"]
                    == "pending"
                ).sum()
            ),

        "reports_with_paper_this_run":
            len(paper_reports),
    }

    update_report_change_set(
        previous_state,
        state,
        change_set_path=change_set_path,
    )

    return (
        summary,
        state,
    )
