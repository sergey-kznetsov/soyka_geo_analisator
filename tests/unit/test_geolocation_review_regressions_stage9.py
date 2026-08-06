from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from soika_uds.geolocation import (
    AddressNormalizer,
    HttpRetryPolicy,
    LazyModelManager,
    LocalFlairAddressExtractor,
    LocationKind,
    MentionSource,
    NominatimClient,
    RequestsJsonTransport,
    RuleBasedMentionExtractor,
    SQLiteResponseCache,
    TransportError,
)


def test_rule_fallback_stops_before_normal_prose() -> None:
    mention = RuleBasedMentionExtractor().extract(
        "На ул. Ленина ужасная яма и нет света во дворе"
    )
    assert mention is not None
    assert mention.text == "ул. Ленина"
    assert mention.kind is LocationKind.STREET


def test_numbered_street_prefers_explicit_house_marker() -> None:
    normalizer = AddressNormalizer()
    address = normalizer.normalize(
        "ул. 8 Марта, д. 10",
        confidence=0.9,
        source=MentionSource.RULES,
    )
    street_only = normalizer.normalize(
        "ул. 1905 года",
        confidence=0.9,
        source=MentionSource.RULES,
    )
    assert address.kind is LocationKind.HOUSE
    assert address.street == "8 Марта"
    assert address.house_number == "10"
    assert street_only.kind is LocationKind.STREET
    assert street_only.street == "1905 года"
    assert street_only.house_number is None


def test_model_cache_key_includes_expected_artifact_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_module = ModuleType("flair.data")

    class Sentence:
        def __init__(self, text: str) -> None:
            self.text = text

        def get_labels(self, label_type: str):
            return []

    data_module.Sentence = Sentence
    flair_module = ModuleType("flair")
    monkeypatch.setitem(sys.modules, "flair", flair_module)
    monkeypatch.setitem(sys.modules, "flair.data", data_module)

    manager = LazyModelManager()
    verified = []
    loaded = []

    class Model:
        def predict(self, sentence: Sentence) -> None:
            return None

    def verifier(path: Path, digest: str) -> None:
        verified.append(digest)

    def loader(path: str):
        loaded.append(path)
        return Model()

    common = {
        "model_path": tmp_path.resolve(),
        "model_revision": "a" * 40,
        "manager": manager,
        "artifact_verifier": verifier,
        "normalizer": AddressNormalizer(),
        "loader": loader,
    }
    first = LocalFlairAddressExtractor(weights_sha256="b" * 64, **common)
    second = LocalFlairAddressExtractor(weights_sha256="c" * 64, **common)

    assert first.extract("text") is None
    assert second.extract("text") is None
    assert verified == ["b" * 64, "c" * 64]
    assert len(loaded) == 2


class MalformedResponse:
    status_code = 200

    def json(self):
        raise ValueError("invalid JSON")


class OneResponseSession:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return MalformedResponse()


def test_malformed_json_is_non_retryable() -> None:
    session = OneResponseSession()
    transport = RequestsJsonTransport(
        user_agent="SOIKA UDS test@example.test",
        policy=HttpRetryPolicy(attempts=3),
        session=session,
        sleeper=lambda delay: None,
    )
    with pytest.raises(TransportError) as captured:
        transport.request_json("GET", "https://example.test")
    assert captured.value.retryable is False
    assert session.calls == 1


class SequenceTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def request_json(self, method, url, *, params=None, data=None):
        self.calls += 1
        return self.payloads.pop(0)


def test_invalid_nominatim_shape_does_not_poison_cache(tmp_path: Path) -> None:
    valid = [
        {
            "lat": "55.7558",
            "lon": "37.6176",
            "display_name": "Ленина, 10",
            "importance": 0.8,
            "osm_type": "way",
            "osm_id": 123,
            "addresstype": "house",
            "address": {"road": "Ленина", "house_number": "10"},
        }
    ]
    transport = SequenceTransport(({}, valid))
    cache = SQLiteResponseCache(
        tmp_path / "nominatim.sqlite3",
        namespace="nominatim-review",
    )
    client = NominatimClient(transport, cache)
    mention = AddressNormalizer().normalize(
        "ул. Ленина 10",
        confidence=0.9,
        source=MentionSource.RULES,
    )
    arguments = {
        "city": "Москва",
        "country_codes": ("ru",),
        "language": "ru",
        "limit": 5,
    }

    with pytest.raises(ValueError, match="must be an array"):
        client.search(mention, **arguments)
    candidates = client.search(mention, **arguments)

    assert candidates
    assert transport.calls == 2
