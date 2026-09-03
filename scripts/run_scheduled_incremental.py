"""GitHub Actions entry point for one bounded automatic update."""

from __future__ import annotations

import argparse
import json

from politdata.github_releases import GitHubReleaseGenerationStore
from politdata.scheduled_incremental import run_scheduled_incremental


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--code-revision")
    parser.add_argument("--organization-limit", type=int, default=250)
    parser.add_argument("--report-discovery-limit", type=int, default=500)
    parser.add_argument("--report-detail-limit", type=int, default=1000)
    args = parser.parse_args(argv)
    result = run_scheduled_incremental(
        GitHubReleaseGenerationStore(args.repository),
        args.work_root,
        args.generation_id,
        organization_limit=args.organization_limit,
        report_discovery_limit=args.report_discovery_limit,
        report_detail_limit=args.report_detail_limit,
        code_revision=args.code_revision,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
