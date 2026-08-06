"""Confidence calibration primitives fitted outside runtime on validation data."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class ScoreCalibrator(Protocol):
    @property
    def descriptor(self) -> dict[str, object]: ...

    def calibrate(self, score: float) -> float: ...


@dataclass(frozen=True, slots=True)
class IdentityCalibrator:
    @property
    def descriptor(self) -> dict[str, object]:
        return {"type": "identity", "version": "1.0.0"}

    def calibrate(self, score: float) -> float:
        if not 0.0 <= score <= 1.0:
            raise ValueError("score must be in [0, 1]")
        return float(score)


@dataclass(frozen=True, slots=True)
class PiecewiseLinearCalibrator:
    """Monotonic calibration curve learned from an approved validation set."""

    points: tuple[tuple[float, float], ...]
    validation_digest: str
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        points = tuple((float(x), float(y)) for x, y in self.points)
        if len(points) < 2:
            raise ValueError("calibration curve requires at least two points")
        if points[0][0] != 0.0 or points[-1][0] != 1.0:
            raise ValueError("calibration curve must cover [0, 1]")
        previous_x = -1.0
        previous_y = -1.0
        for x_value, y_value in points:
            if not 0.0 <= x_value <= 1.0 or not 0.0 <= y_value <= 1.0:
                raise ValueError("calibration points must be in [0, 1]")
            if x_value <= previous_x or y_value < previous_y:
                raise ValueError("calibration curve must be monotonic")
            previous_x = x_value
            previous_y = y_value
        if len(self.validation_digest) != 64:
            raise ValueError("validation_digest must be SHA-256")
        if not self.version.strip():
            raise ValueError("calibration version must not be empty")
        object.__setattr__(self, "points", points)

    @property
    def descriptor(self) -> dict[str, object]:
        return {
            "type": "piecewise_linear",
            "version": self.version,
            "validation_digest": self.validation_digest,
            "points": [list(point) for point in self.points],
        }

    def calibrate(self, score: float) -> float:
        if not 0.0 <= score <= 1.0:
            raise ValueError("score must be in [0, 1]")
        for left, right in zip(self.points, self.points[1:], strict=True):
            left_x, left_y = left
            right_x, right_y = right
            if left_x <= score <= right_x:
                span = right_x - left_x
                ratio = (score - left_x) / span
                return round(left_y + ratio * (right_y - left_y), 6)
        return float(score)


def calibrate_scores(
    scores: Sequence[float],
    calibrator: ScoreCalibrator,
) -> tuple[float, ...]:
    return tuple(calibrator.calibrate(score) for score in scores)


__all__ = [
    "IdentityCalibrator",
    "PiecewiseLinearCalibrator",
    "ScoreCalibrator",
    "calibrate_scores",
]
