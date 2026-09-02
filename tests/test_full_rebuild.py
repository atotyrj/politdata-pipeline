import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from politdata.full_rebuild import (
    _dataset_paths,
    download_organization_cards,
    materialize_normalized_fragments,
)
from politdata.schema_contracts import load_normalized_schemas


def test_download_organization_cards_writes_raw_and_resumes(tmp_path):
    manifest = pd.DataFrame([{
        "organization_id": "o1", "root_party_id": "o1", "entity_type": "party",
    }])
    calls = []

    def fetch(organization_id):
        calls.append(organization_id)
        return {"results": {"id": organization_id, "name": "Тест"}}

    kwargs = {
        "raw_dir": tmp_path / "raw",
        "state_path": tmp_path / "state.parquet",
        "fetch_fn": fetch,
    }
    first, state = download_organization_cards(manifest, **kwargs)
    second, _ = download_organization_cards(manifest, **kwargs)

    assert first["successful"] == 1
    assert second["successful"] == 1
    assert calls == ["o1"]
    assert json.loads((tmp_path / "raw" / "o1.json").read_text(encoding="utf-8"))["results"]["id"] == "o1"
    assert state.loc[0, "content_hash"]


def test_materialize_fragments_streams_rows_maps_outputs_and_uses_contract(tmp_path, monkeypatch):
    fragment_root = tmp_path / "fragments"
    normalized_root = tmp_path / "normalized"
    selected = fragment_root / "report_sections" / "realty"
    monkeypatch.setattr(
        "politdata.full_rebuild._dataset_paths",
        lambda: {"report_sections/realty": "properties/realty", "report_sections/paper": "properties/paper"},
    )
    selected.mkdir(parents=True)
    pd.DataFrame({"id": ["a"]}).to_parquet(selected / "a.parquet", index=False)
    pd.DataFrame({"id": ["b"]}).to_parquet(selected / "b.parquet", index=False)
    empty_schema = pa.schema([pa.field("source_report_id", pa.null())])

    counts = materialize_normalized_fragments(
        fragment_root,
        normalized_root,
        normalized_schemas={
            "properties/realty": pa.schema([pa.field("id", pa.string())]),
            "properties/paper": empty_schema,
        },
    )

    assert counts == {"properties/realty": 2, "properties/paper": 0}
    assert pd.read_parquet(normalized_root / "properties" / "realty.parquet")["id"].tolist() == ["a", "b"]
    assert pq.ParquetFile(normalized_root / "properties" / "paper.parquet").schema_arrow.field("source_report_id").type == pa.null()


def test_packaged_schema_contract_covers_every_materialized_dataset():
    schemas = load_normalized_schemas()

    assert set(schemas) == set(_dataset_paths().values())
    assert schemas["properties/paper"].field("source_report_id").type == pa.string()


def test_materialize_rejects_unversioned_source_fields(tmp_path, monkeypatch):
    fragment_root = tmp_path / "fragments"
    source = fragment_root / "report_sections" / "realty"
    source.mkdir(parents=True)
    pd.DataFrame({"id": ["a"], "new_api_field": ["x"]}).to_parquet(
        source / "a.parquet", index=False
    )
    monkeypatch.setattr(
        "politdata.full_rebuild._dataset_paths",
        lambda: {"report_sections/realty": "properties/realty"},
    )

    with pytest.raises(ValueError, match="outside versioned schema"):
        materialize_normalized_fragments(
            fragment_root,
            tmp_path / "normalized",
            normalized_schemas={
                "properties/realty": pa.schema([pa.field("id", pa.string())])
            },
        )
