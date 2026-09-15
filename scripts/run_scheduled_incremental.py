"""GitHub Actions entry point for one bounded automatic update."""

from __future__ import annotations

import argparse
import json

from politdata.github_releases import (
    GitHubOperationalCheckpointStore,
    GitHubReleaseGenerationStore,
)
from politdata.scheduled_incremental import run_scheduled_incremental


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--code-revision")
    parser.add_argument("--organization-limit", type=int, default=250)
    parser.add_argument("--report-discovery-limit", type=int, default=500)
    parser.add_argument("--all-due-report-discovery", action="store_true")
    parser.add_argument(
        "--report-refresh-interval-days",
        type=float,
        default=7,
    )
    parser.add_argument("--report-detail-limit", type=int, default=1000)
    args = parser.parse_args(argv)
    client_store = GitHubReleaseGenerationStore(args.repository)
    result = run_scheduled_incremental(
        client_store,
        args.work_root,
        args.generation_id,
        organization_limit=args.organization_limit,
        report_discovery_limit=(
            None
            if args.all_due_report_discovery
            else args.report_discovery_limit
        ),
        report_discovery_all_due=args.all_due_report_discovery,
        report_detail_limit=args.report_detail_limit,
        report_refresh_interval_days=args.report_refresh_interval_days,
        code_revision=args.code_revision,
        checkpoint_store=GitHubOperationalCheckpointStore(args.repository),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
