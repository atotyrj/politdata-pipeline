from __future__ import annotations

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    load_change_set,
    save_change_set,
    set_change_set_stage_status,
)
from .dependency_planner import plan_incremental_dependencies
from .enrichment_promotion import promote_enrichment_delta
from .enrichment_runner import run_incremental_enrichment
from .normalization_runner import normalize_changed_fragments
from .promotion import promote_normalized_fragments
from .reference_runner import run_incremental_references


def _options(stage_options, name):
    return dict((stage_options or {}).get(name, {}))


def _require_resumable(change_set, stage):
    status = change_set["stages"][stage]["status"]
    if status == "running":
        raise RuntimeError(
            f"Stage {stage} is still marked running; reconcile it before resume."
        )
    return status


def run_incremental_downstream(
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    *,
    stage_options=None,
):
    """Run or resume change-set downstream stages without repeating success."""

    change_set = load_change_set(change_set_path)
    run_id = change_set["run_id"]
    has_changes = bool(
        change_set["organization_changes"] or change_set["report_changes"]
    )
    if not has_changes:
        for stage in ("normalization", "references", "enrichment", "qa"):
            if change_set["stages"][stage]["status"] == "pending":
                change_set = set_change_set_stage_status(
                    change_set, stage, "skipped"
                )
        save_change_set(change_set, change_set_path)
        return {"run_id": run_id, "status": "no_changes", "stages": {}}

    results = {}
    status = _require_resumable(change_set, "normalization")
    if status in {"pending", "failed"}:
        results["normalization"] = normalize_changed_fragments(
            change_set_path=change_set_path,
            **_options(stage_options, "normalization"),
        )
    results["normalized_promotion"] = promote_normalized_fragments(
        change_set_path=change_set_path,
        **_options(stage_options, "normalized_promotion"),
    )
    results["dependency_plan"] = plan_incremental_dependencies(
        change_set_path=change_set_path,
        **_options(stage_options, "dependency_plan"),
    )

    change_set = load_change_set(change_set_path)
    status = _require_resumable(change_set, "references")
    if status in {"pending", "failed"}:
        results["references"] = run_incremental_references(
            change_set_path=change_set_path,
            **_options(stage_options, "references"),
        )

    change_set = load_change_set(change_set_path)
    status = _require_resumable(change_set, "enrichment")
    if status in {"pending", "failed"}:
        results["enrichment"] = run_incremental_enrichment(
            change_set_path=change_set_path,
            **_options(stage_options, "enrichment"),
        )

    change_set = load_change_set(change_set_path)
    status = _require_resumable(change_set, "qa")
    if status in {"pending", "failed"}:
        results["qa_promotion"] = promote_enrichment_delta(
            change_set_path=change_set_path,
            **_options(stage_options, "qa_promotion"),
        )

    final = load_change_set(change_set_path)
    return {
        "run_id": run_id,
        "status": "completed",
        "stages": {
            name: item["status"]
            for name, item in final["stages"].items()
        },
        "results": results,
    }
