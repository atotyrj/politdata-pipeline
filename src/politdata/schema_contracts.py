"""Versioned Arrow schemas required when a normalized dataset has zero rows."""

from __future__ import annotations

from importlib.resources import files
import base64
import json

import pyarrow as pa


EMPTY_SCHEMA_CONTRACT_VERSION = 1
DEFAULT_SCHEMA_RESOURCE = "schemas/normalized_v1.json"


def load_normalized_schemas(resource=DEFAULT_SCHEMA_RESOURCE):
    """Load packaged canonical schemas; never consult an earlier generation."""

    path = files("politdata").joinpath(resource)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != EMPTY_SCHEMA_CONTRACT_VERSION:
        raise ValueError("Unsupported empty normalized schema-contract version.")
    datasets = payload.get("datasets")
    if not isinstance(datasets, dict):
        raise ValueError("Empty normalized schema contract has no datasets mapping.")
    result = {}
    for name, definition in datasets.items():
        encoded = definition.get("arrow_schema_base64")
        if not encoded:
            raise ValueError(f"Normalized schema contract is missing Arrow schema: {name}")
        result[name] = pa.ipc.read_schema(
            pa.BufferReader(base64.b64decode(encoded))
        )
    return result
