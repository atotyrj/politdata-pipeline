"""Small, deliberately safe command-line interface for PolitData runs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import uuid

from .change_set import DEFAULT_CURRENT_CHANGE_SET_PATH, load_change_set
from .ingestion_preflight import build_ingestion_preflight
from .incremental_pipeline import run_incremental_downstream
from .ingestion_runner import run_limited_organization_ingestion
from .generation_maintenance import (
    apply_retention_plan,
    build_retention_plan,
    rollback_latest,
    write_public_artifact_catalog,
)
from .orchestrator import RunConfig, WriterLock, run_pipeline
from .orchestrator import DEFAULT_CONTROL_ROOT, DEFAULT_GENERATION_ROOT
from .storage import LocalGenerationStore


def _add_generation_store_arguments(parser):
    parser.add_argument(
        "--generation-store",
        choices=("local", "github-releases"),
        default="local",
        help=(
            "Immutable generation backend (default: local). "
            "github-releases reads credentials only from GITHUB_TOKEN."
        ),
    )
    parser.add_argument(
        "--github-repository",
        help="GitHub repository in OWNER/REPO format; defaults to GITHUB_REPOSITORY.",
    )
    parser.add_argument(
        "--github-target-commitish",
        default="main",
        help="Commit or branch used when GitHub creates a generation tag.",
    )
    parser.add_argument(
        "--generation-root", type=Path, default=DEFAULT_GENERATION_ROOT
    )
    parser.add_argument(
        "--latest-pointer",
        type=Path,
        default=DEFAULT_CONTROL_ROOT / "latest.json",
    )


def _generation_store(args):
    if args.generation_store == "local":
        return LocalGenerationStore(args.generation_root, args.latest_pointer)
    repository = args.github_repository or os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise ValueError(
            "github-releases requires --github-repository OWNER/REPO "
            "or GITHUB_REPOSITORY."
        )
    from .github_releases import GitHubReleaseGenerationStore

    return GitHubReleaseGenerationStore(
        repository,
        target_commitish=args.github_target_commitish,
    )


def _maintenance_lock(latest_pointer, mode):
    return WriterLock(
        Path(latest_pointer).parent / "writer.lock",
        run_id=f"{mode}-{uuid.uuid4().hex[:12]}",
        mode=mode,
    )


def change_set_summary(change_set):
    """Return the operator-facing, non-sensitive state of one change set."""

    return {
        "run_id": change_set["run_id"],
        "status": change_set["status"],
        "created_at_utc": change_set["created_at_utc"],
        "organization_changes": len(change_set["organization_changes"]),
        "report_changes": len(change_set["report_changes"]),
        "affected_organization_ids": len(
            change_set["affected_organization_ids"]
        ),
        "affected_report_ids": len(change_set["affected_report_ids"]),
        "stages": {
            name: details["status"]
            for name, details in change_set["stages"].items()
        },
    }


def _change_set_path(value):
    return Path(value)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="politdata",
        description=(
            "Safe local controls for the PolitData incremental pipeline. "
            "Only the explicit ingest command performs online RAW ingestion."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("preflight", "Read-only readiness check before online ingestion."),
        ("status", "Read and summarize a change set without writing data."),
        (
            "downstream",
            "Run or resume downstream work for an existing change set.",
        ),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        if command != "preflight":
            command_parser.add_argument(
                "--change-set",
                type=_change_set_path,
                default=DEFAULT_CURRENT_CHANGE_SET_PATH,
                help=(
                    "Path to an existing change set "
                    f"(default: {DEFAULT_CURRENT_CHANGE_SET_PATH})."
                ),
            )
        command_parser.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON.",
        )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Explicit bounded online organization sync, then downstream.",
    )
    ingest_parser.add_argument(
        "--organization-limit",
        required=True,
        type=int,
        help="Maximum organization cards this online run may fetch.",
    )
    ingest_parser.add_argument(
        "--report-limit", type=int,
        help="Opt in to report details; maximum pending details to fetch.",
    )
    ingest_parser.add_argument(
        "--report-discovery-limit",
        type=int,
        help=(
            "Maximum due organization report lists to refresh. "
            "Defaults to --organization-limit when --report-limit is used."
        ),
    )
    ingest_parser.add_argument(
        "--report-refresh-interval-days",
        type=float,
        default=7,
        help="Days between successful report-list checks (default: 7).",
    )
    ingest_parser.add_argument(
        "--change-set",
        type=_change_set_path,
        default=DEFAULT_CURRENT_CHANGE_SET_PATH,
    )
    ingest_parser.add_argument(
        "--skip-downstream",
        action="store_true",
        help="Create the factual change set but do not process it yet.",
    )
    ingest_parser.add_argument("--json", action="store_true")

    run_parser = subparsers.add_parser(
        "run",
        help="Unified guarded orchestration for incremental or full-replace.",
    )
    run_parser.add_argument(
        "--mode",
        required=True,
        choices=("incremental", "full-replace"),
    )
    run_parser.add_argument("--organization-limit", type=int)
    run_parser.add_argument("--report-discovery-limit", type=int)
    run_parser.add_argument("--report-limit", type=int)
    run_parser.add_argument(
        "--report-refresh-interval-days",
        type=float,
        default=7,
    )
    run_parser.add_argument(
        "--change-set",
        type=_change_set_path,
        default=DEFAULT_CURRENT_CHANGE_SET_PATH,
    )
    run_parser.add_argument("--skip-downstream", action="store_true")
    run_parser.add_argument("--confirm-full-replace", action="store_true")
    run_parser.add_argument("--publish", action="store_true")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the plan without writes or network requests.",
    )
    run_parser.add_argument("--json", action="store_true")
    _add_generation_store_arguments(run_parser)

    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore and checksum-verify an immutable generation.",
    )
    selection = restore_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--latest", action="store_true", help="Restore the generation in latest.json."
    )
    selection.add_argument("--generation-id")
    restore_parser.add_argument("--destination", type=Path, required=True)
    _add_generation_store_arguments(restore_parser)
    restore_parser.add_argument("--json", action="store_true")

    retention_parser = subparsers.add_parser(
        "retention",
        help="Preview or explicitly apply immutable generation retention.",
    )
    retention_parser.add_argument("--keep-latest", type=int, default=3)
    retention_parser.add_argument(
        "--protect",
        action="append",
        default=[],
        metavar="GENERATION_ID",
        help="Additional generation ID to preserve; may be repeated.",
    )
    retention_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the previewed plan. Omit for a read-only preview.",
    )
    retention_parser.add_argument(
        "--expected-current",
        help="Required with --apply; guards against a stale latest pointer.",
    )
    _add_generation_store_arguments(retention_parser)
    retention_parser.add_argument("--json", action="store_true")

    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Atomically point latest at a verified earlier generation.",
    )
    rollback_parser.add_argument("--generation-id", required=True)
    rollback_parser.add_argument("--expected-current", required=True)
    _add_generation_store_arguments(rollback_parser)
    rollback_parser.add_argument("--json", action="store_true")

    catalog_parser = subparsers.add_parser(
        "catalog",
        help="Build a public catalog for verified analytical artifacts.",
    )
    catalog_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CONTROL_ROOT / "public_artifacts.json",
    )
    catalog_parser.add_argument(
        "--include-prefix",
        action="append",
        dest="include_prefixes",
        help="Public generation prefix; defaults to processed/ and outputs/.",
    )
    _add_generation_store_arguments(catalog_parser)
    catalog_parser.add_argument("--json", action="store_true")

    rehearsal_parser = subparsers.add_parser(
        "release-rehearsal",
        help=(
            "Upload and restore a tiny synthetic draft generation through "
            "GitHub Releases; never changes latest."
        ),
    )
    rehearsal_parser.add_argument("--generation-id", required=True)
    rehearsal_parser.add_argument(
        "--github-repository",
        help="GitHub repository in OWNER/REPO format; defaults to GITHUB_REPOSITORY.",
    )
    rehearsal_parser.add_argument(
        "--confirm-release-rehearsal",
        action="store_true",
        help="Required guard confirming creation of a synthetic draft release.",
    )
    rehearsal_parser.add_argument(
        "--delete-after-verification",
        action="store_true",
        help="Also delete the verified draft release and tag.",
    )
    rehearsal_parser.add_argument("--json", action="store_true")

    return parser


def _print_result(value, *, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return

    for key, item in value.items():
        if isinstance(item, dict):
            print(f"{key}:")
            for nested_key, nested_value in item.items():
                print(f"  {nested_key}: {nested_value}")
        else:
            print(f"{key}: {item}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "preflight":
        _print_result(build_ingestion_preflight(), as_json=args.json)
        return 0

    if args.command == "ingest":
        _print_result(
            run_limited_organization_ingestion(
                organization_limit=args.organization_limit,
                change_set_path=args.change_set,
                run_downstream=not args.skip_downstream,
                report_limit=args.report_limit,
                report_discovery_limit=args.report_discovery_limit,
                report_refresh_interval_days=args.report_refresh_interval_days,
            ),
            as_json=args.json,
        )
        return 0

    if args.command == "release-rehearsal":
        if not args.confirm_release_rehearsal:
            raise SystemExit(
                "release-rehearsal requires --confirm-release-rehearsal."
            )
        repository = args.github_repository or os.environ.get("GITHUB_REPOSITORY")
        if not repository:
            raise SystemExit(
                "release-rehearsal requires --github-repository OWNER/REPO "
                "or GITHUB_REPOSITORY."
            )
        try:
            from .release_rehearsal import run_release_rehearsal

            result = run_release_rehearsal(
                repository,
                args.generation_id,
                delete_after_verification=args.delete_after_verification,
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        _print_result(result, as_json=args.json)
        return 0

    if args.command == "run":
        try:
            generation_store = None if args.dry_run else _generation_store(args)
            result = run_pipeline(
                RunConfig(
                    mode=args.mode,
                    organization_limit=args.organization_limit,
                    report_discovery_limit=args.report_discovery_limit,
                    report_detail_limit=args.report_limit,
                    report_refresh_interval_days=(
                        args.report_refresh_interval_days
                    ),
                    change_set_path=args.change_set,
                    run_downstream=not args.skip_downstream,
                    confirm_full_replace=args.confirm_full_replace,
                    publish=args.publish,
                    dry_run=args.dry_run,
                    generation_root=args.generation_root,
                ),
                generation_store=generation_store,
            )
        except (ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        _print_result(result, as_json=args.json)
        return 0

    if args.command == "restore":
        try:
            store = _generation_store(args)
            if args.latest:
                pointer = store.read_latest()
                if pointer is None:
                    raise FileNotFoundError(args.latest_pointer)
                generation_id = pointer.get("generation_id")
                restored = store.restore_latest(args.destination)
            else:
                generation_id = args.generation_id
                restored = store.restore_generation(
                    generation_id, args.destination
                )
        except (OSError, ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        _print_result(
            {
                "status": "restored",
                "generation_id": generation_id,
                "destination": restored,
            },
            as_json=args.json,
        )
        return 0

    if args.command in {"retention", "rollback", "catalog"}:
        try:
            store = _generation_store(args)
            if args.command == "retention":
                plan = build_retention_plan(
                    store,
                    keep_latest=args.keep_latest,
                    protected_generation_ids=args.protect,
                )
                if not args.apply:
                    _print_result(plan, as_json=args.json)
                    return 0
                if not args.expected_current:
                    raise ValueError(
                        "retention --apply requires --expected-current."
                    )
                with _maintenance_lock(args.latest_pointer, "retention"):
                    result = apply_retention_plan(
                        store,
                        plan,
                        expected_current_generation_id=args.expected_current,
                    )
            elif args.command == "rollback":
                with _maintenance_lock(args.latest_pointer, "rollback"):
                    result = rollback_latest(
                        store,
                        args.generation_id,
                        expected_current_generation_id=args.expected_current,
                    )
            else:
                options = {}
                if args.include_prefixes:
                    options["include_prefixes"] = args.include_prefixes
                with _maintenance_lock(args.latest_pointer, "catalog"):
                    catalog = write_public_artifact_catalog(
                        store,
                        args.output,
                        **options,
                    )
                result = {
                    "status": "written",
                    "path": str(args.output),
                    "latest_generation_id": catalog[
                        "latest_generation_id"
                    ],
                    "generation_count": len(catalog["generations"]),
                }
        except (OSError, ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        _print_result(result, as_json=args.json)
        return 0

    path = args.change_set

    if not path.exists():
        raise SystemExit(
            "Change set not found: "
            f"{path}. Create it through committed ingestion first; "
            "this CLI will not start a RAW scan."
        )

    if args.command == "status":
        _print_result(
            change_set_summary(load_change_set(path)),
            as_json=args.json,
        )
        return 0

    result = run_incremental_downstream(change_set_path=path)
    _print_result(result, as_json=args.json)
    return 0


if __name__ == "__main__":
    main()
