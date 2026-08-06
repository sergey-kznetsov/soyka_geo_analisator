from __future__ import annotations

import json
from pathlib import Path

import pytest

from soika_uds.geolocation import (
    AddressNormalizer,
    CandidateSource,
    GeocodingCandidate,
    GeolocationBatchResult,
    GeolocationStats,
    GeoPoint,
    MentionSource,
    MessageGeolocationResult,
    SQLiteResponseCache,
)
from soika_uds.geolocation.models import digest_json
from soika_uds.geolocation.production import (
    QualifiedGeolocationEngine,
    production_geolocation_engine,
)
from soika_uds.geolocation.qualification_api import (
    load_model_audit,
    load_validation_manifest,
    qualify_geolocation,
)

ROOT = Path(__file__).resolve().parents[2]


def _metrics() -> dict:
    return {
        "samples": 24,
        "resolved": 24,
        "resolution_rate": 1.0,
        "within_tolerance_rate": 1.0,
        "kind_accuracy": 1.0,
        "median_distance_m": 10.0,
        "p95_distance_m": 20.0,
        "cities": {
            city: {
                "samples": 8,
                "resolved": 8,
                "resolution_rate": 1.0,
                "within_tolerance_rate": 1.0,
                "kind_accuracy": 1.0,
            }
            for city in ("Казань", "Москва", "Санкт-Петербург")
        },
        "runtime_config": {
            "min_confidence": 0.25,
            "max_candidates": 5,
            "country_codes": ["ru"],
            "language": "ru",
            "ranking": "semantic-v1",
        },
        "extraction_exact_rate": 1.0,
        "low_confidence_rate": 0.0,
        "model_smoke_passed": True,
    }


def _registry_path(tmp_path: Path) -> Path:
    report = qualify_geolocation(
        model_audit=load_model_audit(ROOT / "models/geolocation_model_audit_v1.json"),
        validation=load_validation_manifest(
            ROOT / "models/geolocation_validation_v1.json"
        ),
        metrics=_metrics(),
    )
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(report.registry_dict()), encoding="utf-8")
    return path


def _result(key: str, mention_text: str) -> MessageGeolocationResult:
    mention = AddressNormalizer().normalize(
        mention_text,
        confidence=0.9,
        source=MentionSource.RULES,
    )
    candidate = GeocodingCandidate(
        candidate_id=f"candidate-{key}",
        label=mention.text,
        kind=mention.kind,
        point=GeoPoint(longitude=37.6, latitude=55.7),
        confidence=0.9,
        source=CandidateSource.NOMINATIM,
    )
    return MessageGeolocationResult(
        message_key=key,
        mention=mention,
        candidates=(candidate,),
        selected_candidate_id=candidate.candidate_id,
        confidence=0.81,
        included_for_analysis=True,
        reasons=(),
        metric_crs="EPSG:32637",
        provenance={"algorithm_version": "test"},
    )


class FakeEngine:
    def geolocate(self, messages, *, city=None):
        results = (
            _result("house", "ул. Тверская, д. 13"),
            _result("street", "ул. Тверская"),
        )
        stats = GeolocationStats(
            received=2,
            processed=2,
            resolved=2,
            low_confidence=0,
            unresolved=0,
            skipped=0,
        )
        core = {
            "results": [item.to_dict() for item in results],
            "stats": stats.to_dict(),
        }
        return GeolocationBatchResult(
            results=results,
            stats=stats,
            input_digest=digest_json({"messages": 2}),
            output_digest=digest_json(core),
            config_digest=digest_json({"config": "base"}),
        )


class EmptyExtractor:
    identity = {"type": "empty"}

    def extract(self, text: str):
        return None


def test_registry_scope_excludes_unqualified_levels(tmp_path: Path) -> None:
    registry = json.loads(_registry_path(tmp_path).read_text(encoding="utf-8"))
    engine = QualifiedGeolocationEngine(FakeEngine(), registry)
    result = engine.geolocate(({}, {}), city="Москва")
    by_key = {item.message_key: item for item in result.results}
    assert by_key["house"].included_for_analysis is True
    assert by_key["street"].included_for_analysis is False
    assert "geolocation_level_not_qualified" in by_key["street"].reasons
    assert result.stats.resolved == 1
    assert result.stats.low_confidence == 1
    assert by_key["house"].provenance["qualification"]["registry_digest"]


def test_production_factory_rejects_public_nominatim(tmp_path: Path) -> None:
    path = _registry_path(tmp_path)
    cache = SQLiteResponseCache(tmp_path / "cache.sqlite3", namespace="test")
    with pytest.raises(ValueError, match="public Nominatim"):
        production_geolocation_engine(
            registry_path=path,
            extractor=EmptyExtractor(),
            cache=cache,
            base_url="https://nominatim.openstreetmap.org",
            user_agent="SOIKA UDS test",
        )


def test_production_factory_accepts_dedicated_endpoint(tmp_path: Path) -> None:
    path = _registry_path(tmp_path)
    cache = SQLiteResponseCache(tmp_path / "cache.sqlite3", namespace="test")
    engine = production_geolocation_engine(
        registry_path=path,
        extractor=EmptyExtractor(),
        cache=cache,
        base_url="https://nominatim.example.org",
        user_agent="SOIKA UDS test",
    )
    assert {level.value for level in engine.approved_levels} == {
        "house",
        "poi",
        "landmark",
    }
