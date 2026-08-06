"""Fail-closed qualification contracts for production geolocation profiles."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .models import GeoPoint, LocationKind, MessageGeolocationResult, digest_json

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_LICENSES = frozenset({"MIT", "Apache-2.0", "ODbL-1.0", "CC-BY-4.0"})


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be boolean")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _probability(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return result


def _non_negative(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return result


def _sha256(value: object, field_name: str) -> str:
    normalized = _required(value, field_name).lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be an array")
    result = tuple(_required(item, f"{field_name}[]") for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must contain unique values")
    return result


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{field_name} keys must be strings")
    return value


def _strict_fields(
    payload: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    missing = sorted(required - set(payload))
    if unknown:
        raise ValueError(f"{field_name} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{field_name} is missing fields: {', '.join(missing)}")


class GateState(str, Enum):
    PASSED = "passed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class GateResult:
    code: str
    state: GateState
    detail: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required(self.code, "gate.code").upper())
        if not isinstance(self.state, GateState):
            object.__setattr__(self, "state", GateState(self.state))
        object.__setattr__(self, "detail", _required(self.detail, "gate.detail"))
        object.__setattr__(
            self,
            "evidence",
            _string_tuple(self.evidence, "gate.evidence"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "state": self.state.value,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class GeolocationModelAudit:
    component_id: str
    component_version: str
    artifact_kind: str
    artifact_sha256: str
    license_id: str
    license_reviewed: bool
    training_data_documented: bool
    training_data_reviewed: bool
    intended_use_documented: bool
    evidence_urls: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_id",
            _required(self.component_id, "audit.component_id"),
        )
        object.__setattr__(
            self,
            "component_version",
            _required(self.component_version, "audit.component_version"),
        )
        object.__setattr__(
            self,
            "artifact_kind",
            _required(self.artifact_kind, "audit.artifact_kind"),
        )
        object.__setattr__(
            self,
            "artifact_sha256",
            _sha256(self.artifact_sha256, "audit.artifact_sha256"),
        )
        object.__setattr__(
            self,
            "license_id",
            _required(self.license_id, "audit.license_id"),
        )
        for field_name in (
            "license_reviewed",
            "training_data_documented",
            "training_data_reviewed",
            "intended_use_documented",
        ):
            object.__setattr__(
                self,
                field_name,
                _bool(getattr(self, field_name), f"audit.{field_name}"),
            )
        object.__setattr__(
            self,
            "evidence_urls",
            _string_tuple(self.evidence_urls, "audit.evidence_urls"),
        )
        object.__setattr__(
            self,
            "limitations",
            _string_tuple(self.limitations, "audit.limitations"),
        )

    @property
    def approved(self) -> bool:
        return (
            self.license_id in _ALLOWED_LICENSES
            and self.license_reviewed
            and self.training_data_documented
            and self.training_data_reviewed
            and self.intended_use_documented
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "component_version": self.component_version,
            "artifact_kind": self.artifact_kind,
            "artifact_sha256": self.artifact_sha256,
            "license_id": self.license_id,
            "license_reviewed": self.license_reviewed,
            "training_data_documented": self.training_data_documented,
            "training_data_reviewed": self.training_data_reviewed,
            "intended_use_documented": self.intended_use_documented,
            "evidence_urls": list(self.evidence_urls),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> GeolocationModelAudit:
        data = _mapping(payload, "audit")
        _strict_fields(
            data,
            allowed={
                "component_id",
                "component_version",
                "artifact_kind",
                "artifact_sha256",
                "license_id",
                "license_reviewed",
                "training_data_documented",
                "training_data_reviewed",
                "intended_use_documented",
                "evidence_urls",
                "limitations",
            },
            required={
                "component_id",
                "component_version",
                "artifact_kind",
                "artifact_sha256",
                "license_id",
                "license_reviewed",
                "training_data_documented",
                "training_data_reviewed",
                "intended_use_documented",
                "evidence_urls",
            },
            field_name="audit",
        )
        return cls(
            component_id=data["component_id"],
            component_version=data["component_version"],
            artifact_kind=data["artifact_kind"],
            artifact_sha256=data["artifact_sha256"],
            license_id=data["license_id"],
            license_reviewed=data["license_reviewed"],
            training_data_documented=data["training_data_documented"],
            training_data_reviewed=data["training_data_reviewed"],
            intended_use_documented=data["intended_use_documented"],
            evidence_urls=tuple(data["evidence_urls"]),
            limitations=tuple(data.get("limitations", ())),
        )


@dataclass(frozen=True, slots=True)
class GeolocationValidationCase:
    message_key: str
    city: str
    model_text: str
    expected_mention: str
    expected_point: GeoPoint
    expected_kind: LocationKind
    tolerance_m: float
    evidence_urls: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("message_key", "city", "model_text", "expected_mention"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"case.{field_name}"),
            )
        if not isinstance(self.expected_kind, LocationKind):
            object.__setattr__(
                self,
                "expected_kind",
                LocationKind(self.expected_kind),
            )
        object.__setattr__(
            self,
            "tolerance_m",
            _non_negative(self.tolerance_m, "case.tolerance_m"),
        )
        if self.tolerance_m <= 0:
            raise ValueError("case.tolerance_m must be positive")
        object.__setattr__(
            self,
            "evidence_urls",
            _string_tuple(self.evidence_urls, "case.evidence_urls"),
        )
        if len(self.evidence_urls) < 2:
            raise ValueError("case.evidence_urls must contain at least two references")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_key": self.message_key,
            "city": self.city,
            "model_text": self.model_text,
            "expected_mention": self.expected_mention,
            "expected_point": self.expected_point.to_dict(),
            "expected_kind": self.expected_kind.value,
            "tolerance_m": self.tolerance_m,
            "evidence_urls": list(self.evidence_urls),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> GeolocationValidationCase:
        data = _mapping(payload, "case")
        _strict_fields(
            data,
            allowed={
                "message_key",
                "city",
                "model_text",
                "expected_mention",
                "expected_point",
                "expected_kind",
                "tolerance_m",
                "evidence_urls",
            },
            required={
                "message_key",
                "city",
                "model_text",
                "expected_mention",
                "expected_point",
                "expected_kind",
                "tolerance_m",
                "evidence_urls",
            },
            field_name="case",
        )
        point = _mapping(data["expected_point"], "case.expected_point")
        return cls(
            message_key=data["message_key"],
            city=data["city"],
            model_text=data["model_text"],
            expected_mention=data["expected_mention"],
            expected_point=GeoPoint(
                longitude=point["longitude"],
                latitude=point["latitude"],
            ),
            expected_kind=LocationKind(data["expected_kind"]),
            tolerance_m=data["tolerance_m"],
            evidence_urls=tuple(data["evidence_urls"]),
        )


@dataclass(frozen=True, slots=True)
class GeolocationValidationManifest:
    dataset_id: str
    version: str
    license_id: str
    attribution: str
    approval_reference: str
    approved_levels: tuple[LocationKind, ...]
    cases: tuple[GeolocationValidationCase, ...]
    declared_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "dataset_id",
            "version",
            "license_id",
            "attribution",
            "approval_reference",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"validation.{field_name}"),
            )
        levels = tuple(
            item if isinstance(item, LocationKind) else LocationKind(item)
            for item in self.approved_levels
        )
        if not levels or len(levels) != len(set(levels)):
            raise ValueError("validation.approved_levels must be non-empty and unique")
        object.__setattr__(self, "approved_levels", levels)
        cases = tuple(self.cases)
        if not cases:
            raise ValueError("validation.cases must not be empty")
        keys = tuple(item.message_key for item in cases)
        if len(keys) != len(set(keys)):
            raise ValueError("validation message_key values must be unique")
        if any(item.expected_kind not in levels for item in cases):
            raise ValueError("validation case kind must belong to approved_levels")
        object.__setattr__(self, "cases", cases)
        if self.declared_digest is not None:
            object.__setattr__(
                self,
                "declared_digest",
                _sha256(self.declared_digest, "validation.declared_digest"),
            )
            if self.declared_digest != self.digest:
                raise ValueError("validation declared_digest does not match content")

    @property
    def cities(self) -> tuple[str, ...]:
        return tuple(sorted({item.city for item in self.cases}))

    @property
    def counts_by_city(self) -> Mapping[str, int]:
        counts = Counter(item.city for item in self.cases)
        return MappingProxyType(dict(sorted(counts.items())))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "license_id": self.license_id,
            "attribution": self.attribution,
            "approval_reference": self.approval_reference,
            "approved_levels": [item.value for item in self.approved_levels],
            "cases": [item.to_dict() for item in self.cases],
        }

    @property
    def digest(self) -> str:
        return digest_json(self.canonical_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "declared_digest": self.digest}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> GeolocationValidationManifest:
        data = _mapping(payload, "validation")
        _strict_fields(
            data,
            allowed={
                "dataset_id",
                "version",
                "license_id",
                "attribution",
                "approval_reference",
                "approved_levels",
                "cases",
                "declared_digest",
            },
            required={
                "dataset_id",
                "version",
                "license_id",
                "attribution",
                "approval_reference",
                "approved_levels",
                "cases",
            },
            field_name="validation",
        )
        raw_cases = data["cases"]
        if isinstance(raw_cases, str | bytes | bytearray) or not isinstance(
            raw_cases,
            Sequence,
        ):
            raise TypeError("validation.cases must be an array")
        return cls(
            dataset_id=data["dataset_id"],
            version=data["version"],
            license_id=data["license_id"],
            attribution=data["attribution"],
            approval_reference=data["approval_reference"],
            approved_levels=tuple(LocationKind(item) for item in data["approved_levels"]),
            cases=tuple(
                GeolocationValidationCase.from_dict(_mapping(item, "validation.case"))
                for item in raw_cases
            ),
            declared_digest=data.get("declared_digest"),
        )


@dataclass(frozen=True, slots=True)
class GeolocationThresholds:
    min_samples: int = 24
    min_samples_per_city: int = 8
    min_extraction_exact_rate: float = 0.95
    max_low_confidence_rate: float = 0.25
    min_resolution_rate: float = 0.9
    min_within_tolerance_rate: float = 0.85
    min_kind_accuracy: float = 0.8
    max_median_distance_m: float = 300.0
    max_p95_distance_m: float = 1500.0
    min_city_resolution_rate: float = 0.75
    min_city_within_tolerance_rate: float = 0.75

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "min_samples",
            _positive_int(self.min_samples, "thresholds.min_samples"),
        )
        object.__setattr__(
            self,
            "min_samples_per_city",
            _positive_int(
                self.min_samples_per_city,
                "thresholds.min_samples_per_city",
            ),
        )
        for field_name in (
            "min_extraction_exact_rate",
            "max_low_confidence_rate",
            "min_resolution_rate",
            "min_within_tolerance_rate",
            "min_kind_accuracy",
            "min_city_resolution_rate",
            "min_city_within_tolerance_rate",
        ):
            object.__setattr__(
                self,
                field_name,
                _probability(getattr(self, field_name), f"thresholds.{field_name}"),
            )
        for field_name in ("max_median_distance_m", "max_p95_distance_m"):
            object.__setattr__(
                self,
                field_name,
                _non_negative(getattr(self, field_name), f"thresholds.{field_name}"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class GeolocationQualificationReport:
    approved_for_production: bool
    model_audit: GeolocationModelAudit
    validation_digest: str
    validation_version: str
    approved_levels: tuple[LocationKind, ...]
    thresholds: GeolocationThresholds
    metrics: Mapping[str, Any]
    gates: tuple[GateResult, ...]
    provider_policy: Mapping[str, Any]
    report_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "approved_for_production",
            _bool(self.approved_for_production, "report.approved_for_production"),
        )
        object.__setattr__(
            self,
            "validation_digest",
            _sha256(self.validation_digest, "report.validation_digest"),
        )
        object.__setattr__(
            self,
            "validation_version",
            _required(self.validation_version, "report.validation_version"),
        )
        object.__setattr__(
            self,
            "approved_levels",
            tuple(
                item if isinstance(item, LocationKind) else LocationKind(item)
                for item in self.approved_levels
            ),
        )
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(
            self,
            "provider_policy",
            MappingProxyType(dict(self.provider_policy)),
        )
        object.__setattr__(
            self,
            "report_digest",
            _sha256(self.report_digest, "report.report_digest"),
        )
        if self.report_digest != digest_json(self.payload_without_digest()):
            raise ValueError("report_digest does not match report content")
        if self.approved_for_production != all(
            gate.state is GateState.PASSED for gate in self.gates
        ):
            raise ValueError("approved_for_production must be derived from gates")

    def payload_without_digest(self) -> dict[str, Any]:
        return {
            "approved_for_production": self.approved_for_production,
            "model_audit": self.model_audit.to_dict(),
            "validation_digest": self.validation_digest,
            "validation_version": self.validation_version,
            "approved_levels": [item.value for item in self.approved_levels],
            "thresholds": self.thresholds.to_dict(),
            "metrics": dict(self.metrics),
            "gates": [gate.to_dict() for gate in self.gates],
            "provider_policy": dict(self.provider_policy),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_without_digest(), "report_digest": self.report_digest}

    def registry_dict(self) -> dict[str, Any]:
        if not self.approved_for_production:
            raise ValueError("blocked qualification report cannot activate registry")
        payload = {
            "schema_version": "1.0.0",
            "approved_for_production": True,
            "profile_id": "soika-geolocation-ru-v1",
            "model": self.model_audit.to_dict(),
            "validation": {
                "version": self.validation_version,
                "digest": self.validation_digest,
                "approved_levels": [item.value for item in self.approved_levels],
            },
            "thresholds": self.thresholds.to_dict(),
            "provider_policy": dict(self.provider_policy),
            "qualification_report_digest": self.report_digest,
        }
        payload["registry_digest"] = digest_json(payload)
        return payload


def _gate(
    code: str,
    passed: bool,
    detail: str,
    evidence: Sequence[str] = (),
) -> GateResult:
    return GateResult(
        code=code,
        state=GateState.PASSED if passed else GateState.BLOCKED,
        detail=detail,
        evidence=tuple(evidence),
    )


def qualify_geolocation(
    *,
    model_audit: GeolocationModelAudit,
    validation: GeolocationValidationManifest,
    metrics: Mapping[str, Any],
    thresholds: GeolocationThresholds | None = None,
    provider_policy: Mapping[str, Any] | None = None,
) -> GeolocationQualificationReport:
    resolved_thresholds = thresholds or GeolocationThresholds()
    policy = dict(
        provider_policy
        or {
            "qualification_endpoint": "public_nominatim",
            "production_public_endpoint_allowed": False,
            "https_required": True,
            "persistent_cache_required": True,
            "minimum_interval_seconds": 1.0,
            "attribution_required": True,
        }
    )
    sample_count = int(metrics.get("samples", 0))
    extraction_rate = float(metrics.get("extraction_exact_rate", 0.0))
    low_confidence = float(metrics.get("low_confidence_rate", 1.0))
    resolution_rate = float(metrics.get("resolution_rate", 0.0))
    tolerance_rate = float(metrics.get("within_tolerance_rate", 0.0))
    kind_accuracy = float(metrics.get("kind_accuracy", 0.0))
    median_distance = metrics.get("median_distance_m")
    p95_distance = metrics.get("p95_distance_m")
    cities = _mapping(metrics.get("cities", {}), "metrics.cities")
    counts = validation.counts_by_city
    gates = [
        _gate(
            "MODEL_AUDIT",
            model_audit.approved,
            "model package, licence, intended use and training provenance are reviewed",
            model_audit.evidence_urls,
        ),
        _gate(
            "VALIDATION_MANIFEST",
            validation.license_id in _ALLOWED_LICENSES
            and bool(validation.approval_reference)
            and all(len(item.evidence_urls) >= 2 for item in validation.cases),
            "validation cases are versioned, attributed and supported by two references",
            (validation.approval_reference,),
        ),
        _gate(
            "SAMPLE_SIZE",
            sample_count >= resolved_thresholds.min_samples
            and all(
                count >= resolved_thresholds.min_samples_per_city
                for count in counts.values()
            ),
            f"samples={sample_count}; cities={dict(counts)}",
        ),
        _gate(
            "EXTRACTION_QUALITY",
            extraction_rate >= resolved_thresholds.min_extraction_exact_rate,
            f"extraction_exact_rate={extraction_rate:.6f}",
        ),
        _gate(
            "CONFIDENCE_POLICY",
            low_confidence <= resolved_thresholds.max_low_confidence_rate,
            f"low_confidence_rate={low_confidence:.6f}",
        ),
        _gate(
            "RESOLUTION_QUALITY",
            resolution_rate >= resolved_thresholds.min_resolution_rate,
            f"resolution_rate={resolution_rate:.6f}",
        ),
        _gate(
            "DISTANCE_QUALITY",
            tolerance_rate >= resolved_thresholds.min_within_tolerance_rate
            and median_distance is not None
            and p95_distance is not None
            and float(median_distance) <= resolved_thresholds.max_median_distance_m
            and float(p95_distance) <= resolved_thresholds.max_p95_distance_m,
            (
                f"within_tolerance_rate={tolerance_rate:.6f}; "
                f"median={median_distance}; p95={p95_distance}"
            ),
        ),
        _gate(
            "KIND_QUALITY",
            kind_accuracy >= resolved_thresholds.min_kind_accuracy,
            f"kind_accuracy={kind_accuracy:.6f}",
        ),
        _gate(
            "CITY_QUALITY",
            bool(cities)
            and set(cities) == set(validation.cities)
            and all(
                float(
                    _mapping(row, f"metrics.cities.{city}").get(
                        "resolution_rate",
                        0.0,
                    )
                )
                >= resolved_thresholds.min_city_resolution_rate
                and float(
                    _mapping(row, f"metrics.cities.{city}").get(
                        "within_tolerance_rate",
                        0.0,
                    )
                )
                >= resolved_thresholds.min_city_within_tolerance_rate
                for city, row in cities.items()
            ),
            "every target city passes resolution and distance gates",
        ),
        _gate(
            "PROVIDER_POLICY",
            policy.get("https_required") is True
            and policy.get("persistent_cache_required") is True
            and float(policy.get("minimum_interval_seconds", 0.0)) >= 1.0
            and policy.get("attribution_required") is True
            and policy.get("production_public_endpoint_allowed") is False,
            (
                "qualification may use public Nominatim; production requires "
                "a switchable dedicated endpoint"
            ),
        ),
    ]
    approved = all(gate.state is GateState.PASSED for gate in gates)
    payload = {
        "approved_for_production": approved,
        "model_audit": model_audit.to_dict(),
        "validation_digest": validation.digest,
        "validation_version": validation.version,
        "approved_levels": [item.value for item in validation.approved_levels],
        "thresholds": resolved_thresholds.to_dict(),
        "metrics": dict(metrics),
        "gates": [gate.to_dict() for gate in gates],
        "provider_policy": policy,
    }
    return GeolocationQualificationReport(
        approved_for_production=approved,
        model_audit=model_audit,
        validation_digest=validation.digest,
        validation_version=validation.version,
        approved_levels=validation.approved_levels,
        thresholds=resolved_thresholds,
        metrics=dict(metrics),
        gates=tuple(gates),
        provider_policy=policy,
        report_digest=digest_json(payload),
    )


def extraction_exact_rate(
    validation: GeolocationValidationManifest,
    predictions: Sequence[MessageGeolocationResult],
) -> float:
    expected = {
        item.message_key: item.expected_mention.casefold()
        for item in validation.cases
    }
    predicted = {
        item.message_key: (item.mention.text.casefold() if item.mention else None)
        for item in predictions
    }
    matches = sum(predicted.get(key) == mention for key, mention in expected.items())
    return round(matches / len(expected), 6) if expected else 0.0


def low_confidence_rate(predictions: Sequence[MessageGeolocationResult]) -> float:
    selected = [item for item in predictions if item.selected is not None]
    if not selected:
        return 1.0
    low = sum(not item.included_for_analysis for item in selected)
    return round(low / len(selected), 6)


def load_model_audit(path: Path) -> GeolocationModelAudit:
    with Path(path).open("r", encoding="utf-8") as stream:
        return GeolocationModelAudit.from_dict(json.load(stream))


def load_validation_manifest(path: Path) -> GeolocationValidationManifest:
    with Path(path).open("r", encoding="utf-8") as stream:
        return GeolocationValidationManifest.from_dict(json.load(stream))


def load_qualified_registry(path: Path) -> Mapping[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        payload = _mapping(json.load(stream), "registry")
    if payload.get("approved_for_production") is not True:
        raise ValueError("geolocation registry is not approved for production")
    declared_digest = _sha256(
        payload.get("registry_digest"),
        "registry.registry_digest",
    )
    content = dict(payload)
    content.pop("registry_digest", None)
    if digest_json(content) != declared_digest:
        raise ValueError("geolocation registry digest mismatch")
    return MappingProxyType(dict(payload))


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
