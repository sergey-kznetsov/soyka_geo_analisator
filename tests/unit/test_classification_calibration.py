import pytest

from soika_uds.classification.calibration import (
    IdentityCalibrator,
    PiecewiseLinearCalibrator,
    calibrate_scores,
)


def test_identity_calibrator_preserves_score() -> None:
    calibrator = IdentityCalibrator()
    assert calibrator.calibrate(0.42) == 0.42
    assert calibrator.descriptor == {"type": "identity", "version": "1.0.0"}


def test_piecewise_linear_calibrator_interpolates() -> None:
    calibrator = PiecewiseLinearCalibrator(
        points=((0.0, 0.0), (0.5, 0.4), (1.0, 1.0)),
        validation_digest="a" * 64,
    )
    assert calibrator.calibrate(0.25) == 0.2
    assert calibrator.calibrate(0.75) == 0.7
    assert calibrate_scores((0.25, 0.75), calibrator) == (0.2, 0.7)


def test_calibration_curve_rejects_non_monotonic_points() -> None:
    with pytest.raises(ValueError, match="monotonic"):
        PiecewiseLinearCalibrator(
            points=((0.0, 0.0), (0.5, 0.8), (1.0, 0.7)),
            validation_digest="a" * 64,
        )
