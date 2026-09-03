"""Publish an already validated generation to GitHub Releases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from politdata.github_releases import GitHubReleaseGenerationStore
from politdata.storage import payload_hash


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--confirm-publish", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_publish:
        raise SystemExit("Publishing requires --confirm-publish")

    manifest_path = Path(args.source) / "generation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("generation_id") != args.generation_id:
        raise SystemExit("Generation ID does not match generation_manifest.json")
    store = GitHubReleaseGenerationStore(args.repository)
    previous = store.read_latest()
    location = store.publish_generation(args.source, args.generation_id)
    latest = store.publish_latest(
        {
            "generation_id": args.generation_id,
            "generation_manifest_hash": payload_hash(manifest),
        },
        expected_generation_id=(
            previous.get("generation_id") if previous is not None else None
        ),
    )
    print(
        json.dumps(
            {
                "status": "published",
                "generation_id": args.generation_id,
                "generation_location": location,
                "latest_location": latest,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
