"""Stable public facade for fail-closed geolocation qualification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import digest_json
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
)
from .qualification import qualify_geolocation as _qualify_geolocation


def _runtime_config_gate(metrics: Mapping[str, Any]) -> GateResult:
    config = metrics.get("runtime_config")
    passed = False
    if isinstance(config, Mapping):
        minimum = config.get("min_confidence")
        maximum = config.get("max_candidates")
        codes = config.get("country_codes")
        passed = (
            not isinstance(minimum, bool)
            and isinstance(minimum, int | float)
            and 0.0 <= float(minimum) <= 1.0
            and type(maximum) is int
            and 1 <= maximum <= 40
            and isinstance(codes, Sequence)
            and not isinstance(codes, str | bytes | bytearray)
            and tuple(codes) == ("ru",)
            and config.get("language") == "ru"
            and config.get("ranking") == "semantic-v1"
        )
    return GateResult(
        code="RUNTIME_CONFIG",
        state=GateState.PASSED if passed else GateState.BLOCKED,
        detail="runtime thresholds, candidate limit, language and ranking are pinned",
    )


@dataclass(frozen=True, slots=True)
class GeolocationProfileQualificationReport(GeolocationQualificationReport):
    """Qualification report whose registry activates the tested runtime config."""

    def registry_dict(self) -> dict[str, Any]:
        payload = GeolocationQualificationReport.registry_dict(self)
        payload.pop("registry_digest", None)
        payload["runtime_config"] = dict(self.metrics["runtime_config"])
        payload["registry_digest"] = digest_json(payload)
        return payload


def qualify_geolocation(
    *,
    model_audit: GeolocationModelAudit,
    validation: GeolocationValidationManifest,
    metrics: Mapping[str, Any],
    thresholds: GeolocationThresholds | None = None,
    provider_policy: Mapping[str, Any] | None = None,
) -> GeolocationProfileQualificationReport:
    base = _qualify_geolocation(
        model_audit=model_audit,
        validation=validation,
        metrics=metrics,
        thresholds=thresholds,
        provider_policy=provider_policy,
    )
    runtime_gate = _runtime_config_gate(metrics)
    gates = (*base.gates[:-1], runtime_gate, base.gates[-1])
    approved = all(gate.state is GateState.PASSED for gate in gates)
    base_payload = base.payload_without_digest()
    payload = {
        "approved_for_production": approved,
        "model_audit": base.model_audit.to_dict(),
        "validation_digest": base.validation_digest,
        "validation_version": base.validation_version,
        "approved_levels": [item.value for item in base.approved_levels],
        "thresholds": base.thresholds.to_dict(),
        "metrics": base_payload["metrics"],
        "gates": [gate.to_dict() for gate in gates],
        "provider_policy": base_payload["provider_policy"],
    }
    return GeolocationProfileQualificationReport(
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
    "FrozenDict",
    "GateResult",
    "GateState",
    "GeolocationModelAudit",
    "GeolocationProfileQualificationReport",
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
