
from copy import deepcopy
import hashlib
import json


DEFAULT_IGNORED_FIELDS = {
    "updated_at",
}


def canonicalize_record(
    record,
    ignored_fields=None,
):
    """
    Return a canonical copy of a PolitData organization record
    for meaningful-content comparison.

    Technical fields such as updated_at may be excluded.
    """

    if ignored_fields is None:
        ignored_fields = DEFAULT_IGNORED_FIELDS

    clean = deepcopy(record)

    for field in ignored_fields:
        clean.pop(field, None)

    return clean


def organization_content_hash(
    record,
    ignored_fields=None,
):
    """
    Calculate a stable SHA-256 hash of meaningful organization content.
    """

    clean = canonicalize_record(
        record,
        ignored_fields=ignored_fields,
    )

    canonical_json = json.dumps(
        clean,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()


def deep_diff(old, new, path=""):
    """
    Recursively compare two JSON-compatible objects.
    """

    differences = []

    if type(old) != type(new):
        differences.append({
            "field": path,
            "old": old,
            "new": new,
        })
        return differences

    if isinstance(old, dict):

        keys = set(old) | set(new)

        for key in sorted(keys):

            new_path = (
                f"{path}.{key}"
                if path
                else key
            )

            if key not in old:
                differences.append({
                    "field": new_path,
                    "old": "<MISSING>",
                    "new": new[key],
                })

            elif key not in new:
                differences.append({
                    "field": new_path,
                    "old": old[key],
                    "new": "<MISSING>",
                })

            else:
                differences.extend(
                    deep_diff(
                        old[key],
                        new[key],
                        new_path,
                    )
                )

    elif isinstance(old, list):

        if old != new:
            differences.append({
                "field": path,
                "old": old,
                "new": new,
            })

    else:

        if old != new:
            differences.append({
                "field": path,
                "old": old,
                "new": new,
            })

    return differences


def classify_record_change(
    old_record,
    new_record,
    ignored_fields=None,
):
    """
    Classify whether a fetched organization record changed meaningfully.

    Returns both the full diff and the content hash comparison.
    """

    if ignored_fields is None:
        ignored_fields = DEFAULT_IGNORED_FIELDS

    all_differences = deep_diff(
        old_record,
        new_record,
    )

    old_hash = organization_content_hash(
        old_record,
        ignored_fields=ignored_fields,
    )

    new_hash = organization_content_hash(
        new_record,
        ignored_fields=ignored_fields,
    )

    content_changed = old_hash != new_hash

    return {
        "content_changed": content_changed,
        "old_content_hash": old_hash,
        "new_content_hash": new_hash,
        "changed_fields": [
            diff["field"]
            for diff in all_differences
        ],
        "differences": all_differences,
    }
