"""Stable, fail-closed public facade for geolocation qualification."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import GeoPoint, digest_json


def _point_to_dict(point: GeoPoint) -> dict[str, float]:
    return {
        "longitude": point.longitude,
        "latitude": point.latitude,
    }


if not hasattr(GeoPoint, "to_dict"):
    GeoPoint.to_dict = _point_to_dict  # type: ignore[attr-defined]

from .qualification import (  # noqa: E402
    GateResult,
    GateState,
    GeolocationModelAudit,
    GeolocationQualificationReport,
    GeolocationThresholds,
    GeolocationValidationCase,
    GeolocationValidationManifest,
    extraction_exact_rate,
    load_model_audit,
    load_qualified_registry,
    load_validation_manifest,
    low_confidence_rate,
)
from .qualification import qualify_geolocation as _qualify_geolocation  # noqa: E402


def qualify_geolocation(
    *,
    model_audit: GeolocationModelAudit,
    validation: GeolocationValidationManifest,
    metrics: Mapping[str, Any],
    thresholds: GeolocationThresholds | None = None,
    provider_policy: Mapping[str, Any] | None = None,
) -> GeolocationQualificationReport:
    """Apply the base gates plus mandatory package-version/model smoke evidence."""

    base = _qualify_geolocation(
        model_audit=model_audit,
        validation=validation,
        metrics=metrics,
        thresholds=thresholds,
        provider_policy=provider_policy,
    )
    smoke_passed = metrics.get("model_smoke_passed") is True
    smoke_gate = GateResult(
        code="MODEL_SMOKE",
        state=GateState.PASSED if smoke_passed else GateState.BLOCKED,
        detail="the pinned package imports and produces a location mention",
    )
    gates = (base.gates[0], smoke_gate, *base.gates[1:])
    approved = all(gate.state is GateState.PASSED for gate in gates)
    payload = {
        "approved_for_production": approved,
        "model_audit": base.model_audit.to_dict(),
        "validation_digest": base.validation_digest,
        "validation_version": base.validation_version,
        "approved_levels": [item.value for item in base.approved_levels],
        "thresholds": base.thresholds.to_dict(),
        "metrics": dict(base.metrics),
        "gates": [gate.to_dict() for gate in gates],
        "provider_policy": dict(base.provider_policy),
    }
    return GeolocationQualificationReport(
        approved_for_production=approved,
        model_audit=base.model_audit,
        validation_digest=base.validation_digest,
        validation_version=base.validation_version,
        approved_levels=base.approved_levels,
        thresholds=base.thresholds,
        metrics=base.metrics,
        gates=gates,
        provider_policy=base.provider_policy,
        report_digest=digest_json(payload),
    )


__all__ = [
    "GateResult",
    "GateState",
    "GeolocationModelAudit",
    "GeolocationQualificationReport",
    "GeolocationThresholds",
    "GeolocationValidationCase",
    "GeolocationValidationManifest",
    "extraction_exact_rate",
    "load_model_audit",
    "load_qualified_registry",
    "load_validation_manifest",
    "low_confidence_rate",
    "qualify_geolocation",
]
