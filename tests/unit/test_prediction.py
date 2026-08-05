import pytest

from soika_uds.prediction import (
    Prediction,
    PredictionFormatError,
    normalize_pipeline_output,
    to_legacy_pair,
)


def test_normalizes_flat_pipeline_output():
    result = normalize_pipeline_output(
        [{"label": "ЖКХ", "score": 0.9123}, {"label": "Дороги", "score": 0.05}]
    )

    assert result == [Prediction("ЖКХ", 0.9123), Prediction("Дороги", 0.05)]


def test_normalizes_nested_pipeline_output_and_applies_limit():
    result = normalize_pipeline_output(
        [[{"label": "ЖКХ", "score": 0.9}, {"label": "Дороги", "score": 0.1}]],
        limit=1,
    )

    assert result == [Prediction("ЖКХ", 0.9)]


def test_formats_historical_pair():
    result = to_legacy_pair([Prediction("ЖКХ", 0.9123), Prediction("Дороги", 0.05)])

    assert result == ["ЖКХ; Дороги", "0.912; 0.05"]


def test_rejects_invalid_payload():
    with pytest.raises(PredictionFormatError):
        normalize_pipeline_output([{"label": "ЖКХ"}])
