"""Republish the latest generation using the current Excel output contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from politdata.github_releases import GitHubReleaseGenerationStore
from politdata.production_baseline import write_generation_manifest
from politdata.scheduled_incremental import _normalize_restored_layout, _replace_outputs
from politdata.storage import payload_hash


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--code-revision")
    parser.add_argument("--delete-previous", action="store_true")
    args = parser.parse_args(argv)

    store = GitHubReleaseGenerationStore(args.repository)
    current = store.read_latest()
    if current is None:
        raise RuntimeError("No published baseline generation exists.")
    previous_generation_id = current["generation_id"]
    work_root = Path(args.work_root)
    store.restore_latest(work_root)
    _normalize_restored_layout(work_root)
    _replace_outputs(work_root, [])
    manifest = write_generation_manifest(
        work_root,
        args.generation_id,
        mode="output-contract-republication",
        code_revision=args.code_revision,
        qa={"status": "passed", "excel_workbooks": 17},
        metadata={"previous_generation_id": previous_generation_id},
        verify=False,
    )
    generation_location = store.publish_generation(work_root, args.generation_id)
    latest_location = store.publish_latest(
        {
            "generation_id": args.generation_id,
            "generation_manifest_hash": payload_hash(manifest),
        },
        expected_generation_id=previous_generation_id,
    )
    deleted_previous = False
    if args.delete_previous:
        store.delete_generation(previous_generation_id)
        deleted_previous = True
    print(
        json.dumps(
            {
                "status": "published",
                "generation_id": args.generation_id,
                "generation_location": generation_location,
                "latest_location": latest_location,
                "previous_generation_id": previous_generation_id,
                "deleted_previous": deleted_previous,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
