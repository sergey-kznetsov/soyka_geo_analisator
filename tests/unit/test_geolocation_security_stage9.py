from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from soika_uds.geolocation import (
    AddressNormalizer,
    CompositeMentionExtractor,
    GeoPoint,
    LazyModelManager,
    LocalFlairAddressExtractor,
    MentionSource,
    OverpassClient,
    SQLiteResponseCache,
    verify_model_artifact,
)


class EmptyExtractor:
    identity = {"type": "empty"}

    def extract(self, text: str):
        return None


class FixedExtractor:
    identity = {"type": "fixed"}

    def extract(self, text: str):
        return AddressNormalizer().normalize(
            "ул. Мира",
            confidence=0.8,
            source=MentionSource.RULES,
        )


def test_composite_extractor_uses_first_successful_fallback() -> None:
    extractor = CompositeMentionExtractor((EmptyExtractor(), FixedExtractor()))
    mention = extractor.extract("текст")
    assert mention is not None
    assert mention.street == "Мира"


def test_local_flair_extractor_rejects_mutable_revision(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="40 hexadecimal"):
        LocalFlairAddressExtractor(
            tmp_path.resolve(),
            model_revision="main",
            weights_sha256="a" * 64,
            manager=LazyModelManager(),
            artifact_verifier=lambda path, digest: None,
        )


def test_local_flair_extractor_requires_absolute_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        LocalFlairAddressExtractor(
            Path("models/address"),
            model_revision="a" * 40,
            weights_sha256="b" * 64,
            manager=LazyModelManager(),
            artifact_verifier=lambda path, digest: None,
        )


def test_verify_model_artifact_checks_file_digest(tmp_path: Path) -> None:
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"stage-9-model")
    expected = hashlib.sha256(b"stage-9-model").hexdigest()
    verify_model_artifact(artifact, expected)
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_model_artifact(artifact, "0" * 64)


class FakeTransport:
    def __init__(self) -> None:
        self.data = None

    def request_json(self, method, url, *, params=None, data=None):
        self.data = data
        return {
            "elements": [
                {
                    "type": "node",
                    "id": 7,
                    "lat": 55.7559,
                    "lon": 37.6177,
                    "tags": {"name": "Парк (Центральный)"},
                }
            ]
        }


def test_overpass_escapes_regex_and_scores_distance_in_meters(tmp_path: Path) -> None:
    transport = FakeTransport()
    cache = SQLiteResponseCache(
        tmp_path / "overpass.sqlite3",
        namespace="overpass-test",
    )
    client = OverpassClient(transport, cache)
    mention = AddressNormalizer().normalize(
        "парк Парк (Центральный)",
        confidence=0.8,
        source=MentionSource.RULES,
    )
    candidates = client.nearby(
        mention,
        center=GeoPoint(37.6176, 55.7558),
        radius_m=1000,
    )
    assert candidates
    assert 0.2 <= candidates[0].confidence <= 0.62
    assert transport.data is not None
    assert "\\\\(" in transport.data["data"]
    assert "around:1000" in transport.data["data"]
