
from pathlib import Path

import politdata.pipeline as pipeline


def test_full_processed_pipeline_entry_point_exists():

    assert callable(
        pipeline.rebuild_processed_analytics
    )


def test_full_processed_pipeline_has_explicit_inputs():

    names = (
        pipeline
        .rebuild_processed_analytics
        .__code__
        .co_varnames
    )

    assert "normalized_root" in names
    assert "interim_root" in names
    assert "output_root" in names
