"""Stable public facade for fail-closed geolocation qualification."""

from .qualification import (
    FrozenDict,
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
    qualify_geolocation,
)

__all__ = [
    "FrozenDict",
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
