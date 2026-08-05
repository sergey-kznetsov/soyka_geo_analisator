from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from soika_uds import CoverageSummary, JobStatus, TerritoryContext
from soika_uds.integration import (
    AnalysisRequestV1,
    AnalysisResultV1,
    ContractIssue,
    ContractValidationError,
    IdempotencyConflictError,
    JobStatusV1,
    ResultProvenance,
    assert_idempotent_request,
    parse_contract_document,
    schema_bundle_digest,
)


def make_request(**overrides):
    territory = TerritoryContext(
        analysis_id="analysis-1",
        city="Ижевск",
        address="Ижевск, Пушкинская улица, 277",
        latitude=56.8701,
        longitude=53.2143,
        radius_meters=1500,
        period_from=date(2026, 5, 1),
        period_to=date(2026, 8, 5),
        sources=("city_portal",),
    )
    values = {
        "analysis_id": "analysis-1",
        "requested_at": datetime(2026, 8, 5, 8, 18, tzinfo=UTC),
        "territory": territory,
        "sources": ("city_portal",),
        "options": {"language": "ru"},
    }
    values.update(overrides)
    return AnalysisRequestV1(**values)


def test_request_round_trip_is_lossless():
    request = make_request()

    restored = AnalysisRequestV1.from_dict(request.to_dict())

    assert restored.to_dict() == request.to_dict()
    assert parse_contract_document(request.to_dict()) == restored


def test_generated_idempotency_key_is_stable_for_semantic_request():
    first = make_request()
    later = make_request(
        requested_at=datetime(2026, 8, 5, 9, 18, tzinfo=UTC),
    )

    assert first.fingerprint == later.fingerprint
    assert first.effective_idempotency_key == later.effective_idempotency_key


def test_fingerprint_changes_when_territory_changes():
    first = make_request()
    changed_territory = TerritoryContext(
        analysis_id="analysis-1",
        city="Ижевск",
        address="Ижевск, Пушкинская улица, 277",
        latitude=56.8701,
        longitude=53.2143,
        radius_meters=3000,
    )
    second = make_request(territory=changed_territory)

    assert first.fingerprint != second.fingerprint


def test_idempotency_guard_rejects_changed_request():
    request = make_request()

    with pytest.raises(IdempotencyConflictError, match="different semantic"):
        assert_idempotent_request(
            request,
            stored_idempotency_key=request.effective_idempotency_key,
            stored_fingerprint="0" * 64,
        )


def test_request_rejects_unknown_fields():
    payload = make_request().to_dict()
    payload["unexpected"] = True

    with pytest.raises(ContractValidationError, match="unknown fields"):
        AnalysisRequestV1.from_dict(payload)


def test_request_rejects_unsupported_contract_version():
    payload = make_request().to_dict()
    payload["contract_version"] = "2.0.0"

    with pytest.raises(ContractValidationError, match="unsupported"):
        AnalysisRequestV1.from_dict(payload)


def test_request_requires_timezone():
    with pytest.raises(ContractValidationError, match="UTC offset"):
        make_request(requested_at=datetime(2026, 8, 5, 8, 18))


def test_status_round_trip_and_progress_rules():
    status = JobStatusV1(
        analysis_id="analysis-1",
        status=JobStatus.GEOCODING,
        updated_at=datetime(2026, 8, 5, 8, 21, tzinfo=UTC),
        progress_percent=62,
        stage="geocoding",
        processed_items=177,
        total_items=286,
    )

    restored = JobStatusV1.from_dict(status.to_dict())

    assert restored.to_dict() == status.to_dict()


def test_failed_status_requires_structured_error():
    with pytest.raises(ContractValidationError, match="at least one error"):
        JobStatusV1(
            analysis_id="analysis-1",
            status=JobStatus.FAILED,
            updated_at=datetime.now(UTC),
            progress_percent=55,
            stage="collecting",
        )


def test_result_round_trip_preserves_partial_result():
    warning = ContractIssue(
        code="SOURCE_UNAVAILABLE",
        message="Источник недоступен",
        retryable=True,
        stage="collecting",
        details={"source": "public_comments"},
    )
    result = AnalysisResultV1(
        analysis_id="analysis-1",
        status=JobStatus.COMPLETED_WITH_WARNINGS,
        generated_at=datetime(2026, 8, 5, 8, 29, tzinfo=UTC),
        provenance=ResultProvenance(
            soika_version="0.4.0",
            schema_digest=schema_bundle_digest(),
            models={"classifier": "revision-1"},
            algorithms={"events": "1.0.0"},
        ),
        coverage=CoverageSummary(
            sources_requested=2,
            sources_available=1,
            messages_collected=100,
            messages_relevant=30,
            messages_geocoded=25,
            messages_low_confidence=2,
        ),
        categories=({"name": "ЖКХ", "count": 10},),
        risk_summary={"score": 3.4},
        warnings=(warning,),
        partial=True,
    )

    restored = AnalysisResultV1.from_dict(result.to_dict())

    assert restored.to_dict() == result.to_dict()
    assert restored.partial is True


def test_requested_at_offset_is_normalized_to_utc():
    request = make_request(
        requested_at=datetime(
            2026,
            8,
            5,
            12,
            18,
            tzinfo=timezone(timedelta(hours=4)),
        )
    )

    assert request.to_dict()["requested_at"] == "2026-08-05T08:18:00Z"
