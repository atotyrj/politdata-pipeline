from pathlib import Path


def test_weekly_workflow_is_kyiv_local_bounded_and_serialized():
    workflow = Path(".github/workflows/weekly-incremental.yml").read_text(
        encoding="utf-8"
    )
    assert 'cron: "37 3 * * 1"' in workflow
    assert 'timezone: "Europe/Kyiv"' in workflow
    assert "contents: write" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "--organization-limit 250" in workflow
    assert "--report-discovery-limit 500" in workflow
    assert "--report-detail-limit 1000" in workflow
    assert "run_scheduled_incremental.py" in workflow
