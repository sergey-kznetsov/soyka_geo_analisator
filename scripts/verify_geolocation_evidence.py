#!/usr/bin/env python3
"""Verify committed stage-9 qualification evidence without network access."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "evidence/geolocation/v1/geolocation-qualification-report.json"
DEFAULT_REGISTRY = ROOT / "evidence/geolocation/v1/geolocation-production-registry.json"
DEFAULT_VALIDATION = ROOT / "models/geolocation_validation_v1.json"
DEFAULT_AUDIT = ROOT / "models/geolocation_model_audit_v1.json"


class EvidenceVerificationError(ValueError):
    """Raised when committed qualification evidence is inconsistent."""


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceVerificationError(f"cannot read {path}: {error}") from error
    if not isinstance(payload, dict):
        raise EvidenceVerificationError(f"{path} must contain a JSON object")
    return payload


def _digest(payload: dict[str, Any], excluded_field: str) -> str:
    canonical = copy.deepcopy(payload)
    canonical.pop(excluded_field, None)
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceVerificationError(message)


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvidenceVerificationError(f"{key} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise EvidenceVerificationError(f"{key} must be finite")
    return result


def _verify_metrics(
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    cases = validation.get("cases")
    _require(isinstance(cases, list), "validation cases must be an array")
    _require(
        int(_number(metrics, "samples")) == len(cases),
        "metric sample count must match validation cases",
    )
    lower_bounds = {
        "extraction_exact_rate": "min_extraction_exact_rate",
        "resolution_rate": "min_resolution_rate",
        "within_tolerance_rate": "min_within_tolerance_rate",
        "kind_accuracy": "min_kind_accuracy",
    }
    for metric_name, threshold_name in lower_bounds.items():
        _require(
            _number(metrics, metric_name) >= _number(thresholds, threshold_name),
            f"{metric_name} is below {threshold_name}",
        )
    upper_bounds = {
        "low_confidence_rate": "max_low_confidence_rate",
        "median_distance_m": "max_median_distance_m",
        "p95_distance_m": "max_p95_distance_m",
    }
    for metric_name, threshold_name in upper_bounds.items():
        _require(
            _number(metrics, metric_name) <= _number(thresholds, threshold_name),
            f"{metric_name} exceeds {threshold_name}",
        )
    _require(
        int(_number(metrics, "samples")) >= int(_number(thresholds, "min_samples")),
        "validation sample count is below the release threshold",
    )
    cities = metrics.get("cities")
    _require(isinstance(cities, dict) and cities, "city metrics must be present")
    for city, city_metrics in cities.items():
        _require(isinstance(city_metrics, dict), f"city metrics for {city} must be an object")
        _require(
            int(_number(city_metrics, "samples"))
            >= int(_number(thresholds, "min_samples_per_city")),
            f"{city} sample count is below threshold",
        )
        _require(
            _number(city_metrics, "resolution_rate")
            >= _number(thresholds, "min_city_resolution_rate"),
            f"{city} resolution rate is below threshold",
        )
        _require(
            _number(city_metrics, "within_tolerance_rate")
            >= _number(thresholds, "min_city_within_tolerance_rate"),
            f"{city} distance quality is below threshold",
        )


def verify_payloads(
    *,
    report: dict[str, Any],
    registry: dict[str, Any],
    validation: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    declared_validation_digest = validation.get("declared_digest")
    _require(
        isinstance(declared_validation_digest, str),
        "validation declared_digest is missing",
    )
    _require(
        _digest(validation, "declared_digest") == declared_validation_digest,
        "validation digest mismatch",
    )
    report_digest = report.get("report_digest")
    registry_digest = registry.get("registry_digest")
    _require(isinstance(report_digest, str), "report digest is missing")
    _require(isinstance(registry_digest, str), "registry digest is missing")
    _require(_digest(report, "report_digest") == report_digest, "report digest mismatch")
    _require(
        _digest(registry, "registry_digest") == registry_digest,
        "registry digest mismatch",
    )
    _require(report.get("approved_for_production") is True, "report is not approved")
    _require(registry.get("approved_for_production") is True, "registry is not approved")
    gates = report.get("gates")
    _require(isinstance(gates, list) and gates, "qualification gates are missing")
    _require(
        all(isinstance(gate, dict) and gate.get("state") == "passed" for gate in gates),
        "not all qualification gates passed",
    )
    _require(
        registry.get("qualification_report_digest") == report_digest,
        "registry is not bound to the qualification report",
    )
    _require(report.get("model_audit") == audit, "report model audit differs from source")
    _require(registry.get("model") == audit, "registry model audit differs from source")
    validation_version = validation.get("version")
    approved_levels = validation.get("approved_levels")
    _require(
        report.get("validation_digest") == declared_validation_digest,
        "report is not bound to the validation set",
    )
    _require(
        report.get("validation_version") == validation_version,
        "report validation version mismatch",
    )
    registry_validation = registry.get("validation")
    _require(isinstance(registry_validation, dict), "registry validation binding is missing")
    _require(
        registry_validation.get("digest") == declared_validation_digest,
        "registry validation digest mismatch",
    )
    _require(
        registry_validation.get("version") == validation_version,
        "registry validation version mismatch",
    )
    _require(
        report.get("approved_levels") == approved_levels
        == registry_validation.get("approved_levels"),
        "approved location levels are inconsistent",
    )
    metrics = report.get("metrics")
    thresholds = report.get("thresholds")
    _require(isinstance(metrics, dict), "report metrics are missing")
    _require(isinstance(thresholds, dict), "report thresholds are missing")
    _require(
        metrics.get("validation_digest") == declared_validation_digest,
        "metrics validation digest mismatch",
    )
    _require(registry.get("thresholds") == thresholds, "registry thresholds mismatch")
    _require(
        registry.get("runtime_config") == metrics.get("runtime_config"),
        "registry runtime config mismatch",
    )
    _require(
        registry.get("provider_policy") == report.get("provider_policy"),
        "registry provider policy mismatch",
    )
    provider_policy = registry.get("provider_policy")
    _require(isinstance(provider_policy, dict), "provider policy is missing")
    _require(provider_policy.get("https_required") is True, "HTTPS is not required")
    _require(
        provider_policy.get("persistent_cache_required") is True,
        "persistent cache is not required",
    )
    _require(
        provider_policy.get("production_public_endpoint_allowed") is False,
        "public Nominatim is allowed in production",
    )
    _verify_metrics(metrics, thresholds, validation)
    prediction_digest = metrics.get("prediction_digest")
    _require(
        isinstance(prediction_digest, str)
        and len(prediction_digest) == 64
        and all(character in "0123456789abcdef" for character in prediction_digest),
        "prediction digest is invalid",
    )
    return {
        "report_digest": report_digest,
        "registry_digest": registry_digest,
        "prediction_digest": prediction_digest,
        "validation_digest": declared_validation_digest,
        "samples": int(_number(metrics, "samples")),
    }


def verify_files(
    *,
    report_path: Path = DEFAULT_REPORT,
    registry_path: Path = DEFAULT_REGISTRY,
    validation_path: Path = DEFAULT_VALIDATION,
    audit_path: Path = DEFAULT_AUDIT,
) -> dict[str, Any]:
    return verify_payloads(
        report=_load(report_path),
        registry=_load(registry_path),
        validation=_load(validation_path),
        audit=_load(audit_path),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    summary = verify_files(
        report_path=args.report,
        registry_path=args.registry,
        validation_path=args.validation,
        audit_path=args.audit,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
