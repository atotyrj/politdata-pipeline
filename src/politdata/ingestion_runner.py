"""Explicit, bounded entry point for online organization ingestion."""

from __future__ import annotations

from .change_set import DEFAULT_CURRENT_CHANGE_SET_PATH
from .incremental_pipeline import run_incremental_downstream
from .sync import run_organization_sync


def run_limited_organization_ingestion(
    *,
    organization_limit,
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    run_downstream=True,
    sync_options=None,
):
    """Fetch at most ``organization_limit`` candidate cards, then process changes.

    This is an online operation. It is deliberately limited to organization
    cards: report-list refresh and report-selection policy are not silently
    inferred here, so newly published report instances cannot be missed.
    """

    organization_limit = int(organization_limit)
    if organization_limit <= 0:
        raise ValueError("organization_limit must be positive.")

    options = dict(sync_options or {})
    if "candidate_limit" in options:
        raise ValueError("Pass organization_limit, not sync_options.candidate_limit.")
    if "change_set_path" in options:
        raise ValueError("Pass change_set_path directly.")

    sync = run_organization_sync(
        candidate_limit=organization_limit,
        change_set_path=change_set_path,
        **options,
    )
    result = {
        "mode": "online_organization_sync",
        "organization_limit": organization_limit,
        "sync": sync,
        "change_set_path": str(change_set_path),
    }
    if run_downstream:
        result["downstream"] = run_incremental_downstream(
            change_set_path=change_set_path
        )
    else:
        result["downstream"] = {"status": "not_requested"}
    return result
