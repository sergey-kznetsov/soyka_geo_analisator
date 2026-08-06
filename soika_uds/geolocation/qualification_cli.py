"""Command-line qualification of a concrete geolocation production profile."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from .cache import SQLiteResponseCache
from .evaluation import GeolocationValidationCase as EvaluationCase
from .evaluation import evaluate_geolocation
from .extraction import (
    CompositeMentionExtractor,
    NatashaAddressExtractor,
    RuleBasedMentionExtractor,
)
from .factory import public_nominatim_client
from .model_manager import LazyModelManager
from .models import GeolocationConfig, MessageGeolocationResult, digest_json
from .qualification_api import (
    extraction_exact_rate,
    load_model_audit,
    load_validation_manifest,
    low_confidence_rate,
    qualify_geolocation,
)
from .runtime import GeolocationEngine


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Qualify the SOIKA geolocation profile against a versioned dataset",
    )
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registry-output", required=True, type=Path)
    parser.add_argument("--predictions-output", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--user-agent", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--max-candidates", type=int, default=5)
    return parser


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _installed_natasha_version() -> str | None:
    try:
        return importlib.metadata.version("natasha")
    except importlib.metadata.PackageNotFoundError:
        return None


def _run_predictions(
    *,
    validation,
    audit,
    cache_path: Path,
    user_agent: str,
    min_confidence: float,
    max_candidates: int,
) -> tuple[tuple[MessageGeolocationResult, ...], bool, dict[str, Any]]:
    manager = LazyModelManager()
    rules = RuleBasedMentionExtractor()
    natasha = NatashaAddressExtractor(manager)
    model_smoke = natasha.extract("Сообщение поступило в Москве.") is not None
    extractor = CompositeMentionExtractor((rules, natasha))
    cache = SQLiteResponseCache(cache_path, namespace="qualification-v1")
    provider = public_nominatim_client(cache, user_agent=user_agent)
    config = GeolocationConfig(
        min_confidence=min_confidence,
        max_candidates=max_candidates,
        country_codes=("ru",),
        language="ru",
    )
    engine = GeolocationEngine(extractor, provider, config=config)
    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in validation.cases:
        by_city[case.city].append(
            {
                "message_key": case.message_key,
                "model_text": case.model_text,
                "included_for_analysis": True,
            }
        )
    predictions: list[MessageGeolocationResult] = []
    batch_digests: dict[str, str] = {}
    for city, messages in sorted(by_city.items()):
        batch = engine.geolocate(tuple(messages), city=city)
        predictions.extend(batch.results)
        batch_digests[city] = batch.output_digest
    installed_version = _installed_natasha_version()
    smoke = model_smoke and installed_version == audit.component_version
    provenance = {
        "installed_natasha_version": installed_version,
        "expected_component_version": audit.component_version,
        "extractor": dict(extractor.identity),
        "provider": dict(provider.identity),
        "batch_output_digests": batch_digests,
        "runtime_revision": os.getenv("GITHUB_SHA"),
    }
    return tuple(predictions), smoke, provenance


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    audit = load_model_audit(args.audit)
    validation = load_validation_manifest(args.validation)
    if not args.allow_network:
        raise SystemExit(
            "qualification requires --allow-network for one controlled Nominatim run"
        )
    predictions, model_smoke, runtime_provenance = _run_predictions(
        validation=validation,
        audit=audit,
        cache_path=args.cache,
        user_agent=args.user_agent,
        min_confidence=args.min_confidence,
        max_candidates=args.max_candidates,
    )
    prediction_payload = [
        item.to_dict()
        for item in sorted(predictions, key=lambda result: result.message_key)
    ]
    _write_json(args.predictions_output, prediction_payload)
    expected = tuple(
        EvaluationCase(
            message_key=case.message_key,
            city=case.city,
            expected_point=case.expected_point,
            expected_kind=case.expected_kind,
            tolerance_m=case.tolerance_m,
        )
        for case in validation.cases
    )
    metrics = evaluate_geolocation(expected, predictions)
    metrics.update(
        {
            "extraction_exact_rate": extraction_exact_rate(validation, predictions),
            "low_confidence_rate": low_confidence_rate(predictions),
            "model_smoke_passed": model_smoke,
            "validation_digest": validation.digest,
            "prediction_digest": digest_json(prediction_payload),
            "runtime_provenance": runtime_provenance,
        }
    )
    report = qualify_geolocation(
        model_audit=audit,
        validation=validation,
        metrics=metrics,
    )
    _write_json(args.output, report.to_dict())
    if report.approved_for_production:
        _write_json(args.registry_output, report.registry_dict())
        return 0
    args.registry_output.unlink(missing_ok=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
