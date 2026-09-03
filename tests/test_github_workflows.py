from pathlib import Path


def test_release_rehearsal_workflow_is_manual_synthetic_and_nondestructive_by_default():
    workflow = Path(".github/workflows/release-rehearsal.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "contents: write" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "release-rehearsal" in workflow
    assert "--confirm-release-rehearsal" in workflow
    assert "default: false" in workflow
    assert "no NACP API access" in workflow
    assert "Latest release: never changed" in workflow
    assert "--publish" not in workflow
