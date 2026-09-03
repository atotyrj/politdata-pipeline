"""Small, offline-data rehearsal for the GitHub Releases storage backend."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
from xml.sax.saxutils import escape
import zipfile

from .github_releases import GitHubReleaseGenerationStore
from .storage import atomic_json, file_hash, payload_hash, verify_generation


def _write_rehearsal_workbook(path, generation_id):
    """Write the smallest useful XLSX fixture using only the standard library."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    generation_id = escape(str(generation_id))
    parts = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="rehearsal" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>generation_id</t></is></c><c r="B1" t="inlineStr"><is><t>synthetic</t></is></c><c r="C1" t="inlineStr"><is><t>rows</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>{generation_id}</t></is></c><c r="B2" t="b"><v>1</v></c><c r="C2"><v>1</v></c></row>
  </sheetData>
</worksheet>""",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        for name, content in parts.items():
            workbook.writestr(name, content)


def build_rehearsal_generation(destination, generation_id):
    """Build a tiny synthetic generation without reading project data or NACP."""

    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)

    files = {
        "raw/rehearsal_source.json": {
            "synthetic": True,
            "purpose": "github-releases-storage-rehearsal",
        },
        "interim/rehearsal_checkpoint.json": {
            "synthetic": True,
            "cursor": "fixture-only",
        },
        "processed/rehearsal_result.json": {
            "synthetic": True,
            "rows": 1,
        },
    }
    for relative, payload in files.items():
        atomic_json(destination / relative, payload)

    workbook_path = destination / "outputs" / "rehearsal.xlsx"
    _write_rehearsal_workbook(workbook_path, generation_id)

    artifact_checksums = {}
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            artifact_checksums[path.relative_to(destination).as_posix()] = file_hash(path)

    manifest = {
        "schema_version": 1,
        "generation_id": str(generation_id),
        "run_id": str(generation_id),
        "mode": "release-rehearsal",
        "status": "validated",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_counts": {"rehearsal": 1},
        "qa": {"passed": True, "synthetic_fixture": True},
        "artifact_checksums": artifact_checksums,
    }
    atomic_json(destination / "generation_manifest.json", manifest)
    verify_generation(destination)
    return manifest


def run_release_rehearsal(
    repository,
    generation_id,
    *,
    token=None,
    client=None,
    delete_after_verification=False,
):
    """Upload, restore and verify a synthetic draft release generation."""

    store = GitHubReleaseGenerationStore(
        repository,
        token=token,
        client=client,
    )
    with tempfile.TemporaryDirectory(prefix="politdata-release-rehearsal-") as temporary:
        root = Path(temporary)
        source = root / "source"
        restored = root / "restored"
        manifest = build_rehearsal_generation(source, generation_id)
        release_location = store.publish_generation(source, generation_id)
        store.restore_generation(
            generation_id,
            restored,
            expected_manifest_hash=payload_hash(manifest),
        )
        restored_manifest = verify_generation(
            restored,
            expected_manifest_hash=payload_hash(manifest),
        )
        workbook = restored / "outputs" / "rehearsal.xlsx"
        if not workbook.is_file():
            raise RuntimeError("Rehearsal workbook was not restored.")

        deleted = False
        if delete_after_verification:
            store.delete_generation(
                generation_id,
                expected_manifest_hash=payload_hash(manifest),
            )
            deleted = True

    return {
        "status": "verified",
        "repository": str(repository),
        "generation_id": str(generation_id),
        "release_location": release_location,
        "manifest_hash": payload_hash(restored_manifest),
        "artifact_count": len(restored_manifest["artifact_checksums"]),
        "draft_deleted": deleted,
        "source": "synthetic_fixture_only",
        "latest_changed": False,
    }
