from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from soika_uds.contracts import SourceMessage
from soika_uds.parsers import (
    AccessMethod,
    AuthorIdentifierMode,
    AuthorPseudonymizer,
    ComplianceContext,
    ComplianceGate,
    DataCategory,
    DataProtectionPolicy,
    FileParserCheckpointStore,
    InMemoryAuditSink,
    InMemoryParserCheckpointStore,
    ParserPage,
    ParserRegistry,
    ParserRequest,
    ParserRunner,
    PermissionEvidence,
    PermissionEvidenceKind,
    PermissionStatus,
    RateLimitPolicy,
    RequirementDecision,
    RobotsDecision,
    RobotsRequirement,
    SecurityPolicy,
    SourceNotApprovedError,
    SourcePolicy,
    SourcePolicyError,
    SourceRegistrationError,
    SourceResearchRecord,
    TemporaryParserError,
    TransportRequest,
    TransportResponse,
    UnsafeOutboundRequestError,
    load_source_policy,
    validate_outbound_url,
)
from soika_uds.parsers.transport import SafeHttpTransport

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def approved_policy(
    *,
    source_id: str = "city-api",
    access_method: AccessMethod = AccessMethod.OFFICIAL_API,
    enabled: bool = True,
    permission_status: PermissionStatus = PermissionStatus.APPROVED,
    robots_requirement: RobotsRequirement = RobotsRequirement.NOT_APPLICABLE,
    review_due_at: datetime | None = None,
    author_mode: AuthorIdentifierMode = AuthorIdentifierMode.HMAC_PSEUDONYM,
    credential_reference: str | None = "secret://city-api",
) -> SourcePolicy:
    return SourcePolicy(
        source_id=source_id,
        display_name="City API",
        owner="City Portal",
        access_method=access_method,
        permission_status=permission_status,
        jurisdictions=("RU",),
        legal_basis="contract and reviewed API terms",
        terms_url="https://api.example.com/terms",
        privacy_url="https://api.example.com/privacy",
        official_docs_url=(
            "https://api.example.com/docs"
            if access_method is AccessMethod.OFFICIAL_API
            else None
        ),
        robots_requirement=robots_requirement,
        research=SourceResearchRecord(
            collection_plan="Use the documented endpoint with server-side credentials.",
            official_access_available=RequirementDecision.YES,
            permission_required=RequirementDecision.YES,
            permission_contact="legal@example.test",
            copyright_constraints="Store only approved fields; no republication.",
            terms_constraints="Use only for urban issue analysis.",
            personal_data_notes="Pseudonymize profile identifiers.",
            security_risks=("untrusted JSON", "credential leakage"),
            deletion_or_correction_process="Delete by source and external identifier.",
            rate_limit_source="Official API documentation.",
            reviewed_sources=(
                "https://api.example.com/docs",
                "https://api.example.com/terms",
            ),
        ),
        permission=(
            PermissionEvidence(
                kind=PermissionEvidenceKind.CONTRACT,
                reference="legal/contracts/city-api-2026",
                reviewed_by="legal@example.test",
                reviewed_at=NOW - timedelta(days=1),
                expires_at=NOW + timedelta(days=365),
                document_sha256="a" * 64,
            )
            if permission_status is PermissionStatus.APPROVED
            else None
        ),
        data=DataProtectionPolicy(
            categories=(
                DataCategory.PUBLIC_TEXT,
                DataCategory.PROFILE_IDENTIFIER,
                DataCategory.LOCATION,
            ),
            allowed_fields=(
                "external_id",
                "text",
                "published_at",
                "url",
                "author_id",
                "coordinates",
                "metadata.thread_id",
            ),
            retention_days=180,
            author_identifier_mode=author_mode,
            purpose="urban issue analysis",
        ),
        security=SecurityPolicy(
            allowed_domains=("api.example.com",),
            allowed_content_types=("application/json",),
            credential_reference=credential_reference,
        ),
        rate_limit=RateLimitPolicy(
            requests_per_minute=6000,
            burst=10,
            max_retries=2,
            backoff_seconds=0.01,
        ),
        allowed_purposes=("urban issue analysis",),
        parser_version="1.0.0",
        reviewed_at=NOW - timedelta(days=1),
        review_due_at=review_due_at or NOW + timedelta(days=90),
        enabled=enabled,
    )


def public_web_policy() -> SourcePolicy:
    return SourcePolicy(
        source_id="city-web",
        display_name="City Web",
        owner="City Portal",
        access_method=AccessMethod.PUBLIC_WEB,
        permission_status=PermissionStatus.APPROVED,
        jurisdictions=("RU",),
        legal_basis="written permission",
        terms_url="https://www.example.com/terms",
        privacy_url="https://www.example.com/privacy",
        official_docs_url=None,
        robots_requirement=RobotsRequirement.REQUIRED,
        research=SourceResearchRecord(
            collection_plan=(
                "Collect only allowlisted public pages permitted by robots."
            ),
            official_access_available=RequirementDecision.NO,
            permission_required=RequirementDecision.YES,
            permission_contact="legal@example.test",
            copyright_constraints="No republication of full pages.",
            terms_constraints="Written permission controls collection.",
            personal_data_notes="Do not retain author identifiers.",
            security_risks=("untrusted HTML", "SSRF", "oversized responses"),
            deletion_or_correction_process=(
                "Delete by source URL and external identifier."
            ),
            rate_limit_source="Written permission and robots policy.",
            reviewed_sources=(
                "https://www.example.com/terms",
                "https://www.example.com/robots.txt",
            ),
            robots_url="https://www.example.com/robots.txt",
        ),
        permission=PermissionEvidence(
            kind=PermissionEvidenceKind.WRITTEN_PERMISSION,
            reference="legal/permissions/city-web",
            reviewed_by="legal@example.test",
            reviewed_at=NOW - timedelta(days=2),
            expires_at=NOW + timedelta(days=30),
        ),
        data=DataProtectionPolicy(
            categories=(DataCategory.PUBLIC_TEXT,),
            allowed_fields=("external_id", "text", "published_at"),
            retention_days=30,
            author_identifier_mode=AuthorIdentifierMode.DROP,
            purpose="urban issue analysis",
        ),
        security=SecurityPolicy(
            allowed_domains=("www.example.com",),
            allowed_content_types=("text/html",),
            credential_reference=None,
        ),
        rate_limit=RateLimitPolicy(requests_per_minute=10),
        allowed_purposes=("urban issue analysis",),
        parser_version="1.0.0",
        reviewed_at=NOW - timedelta(days=2),
        review_due_at=NOW + timedelta(days=30),
        enabled=True,
    )


def context(
    *,
    robots: RobotsDecision = RobotsDecision.NOT_APPLICABLE,
    credential: bool = True,
    now: datetime = NOW,
) -> ComplianceContext:
    return ComplianceContext(
        purpose="urban issue analysis",
        robots_decision=robots,
        credential_available=credential,
        current_time=now,
    )


def test_policy_round_trip_is_strict() -> None:
    policy = approved_policy()
    restored = SourcePolicy.from_dict(policy.to_dict())
    assert restored == policy

    payload = policy.to_dict()
    payload["unknown"] = True
    with pytest.raises(SourcePolicyError, match="unknown fields"):
        SourcePolicy.from_dict(payload)


def test_public_web_requires_robots_and_allowlist() -> None:
    with pytest.raises(SourcePolicyError, match="robots"):
        approved_policy(
            source_id="city-web",
            access_method=AccessMethod.PUBLIC_WEB,
            robots_requirement=RobotsRequirement.NOT_APPLICABLE,
            credential_reference=None,
        )


def test_enabled_source_requires_approval_evidence() -> None:
    with pytest.raises(SourcePolicyError, match="enabled source"):
        approved_policy(
            permission_status=PermissionStatus.REVIEW_REQUIRED,
            enabled=True,
        )


def test_data_policy_requires_minimal_core_fields() -> None:
    with pytest.raises(SourcePolicyError, match="missing required fields"):
        DataProtectionPolicy(
            categories=(DataCategory.PUBLIC_TEXT,),
            allowed_fields=("text",),
            retention_days=30,
            purpose="analysis",
        )


def test_compliance_gate_blocks_expired_review() -> None:
    policy = approved_policy(review_due_at=NOW - timedelta(seconds=1))
    decision = ComplianceGate().evaluate(policy, context())
    assert not decision.allowed
    assert "POLICY_REVIEW_EXPIRED" in decision.reasons


def test_compliance_gate_blocks_web_when_robots_disallows() -> None:
    policy = public_web_policy()
    decision = ComplianceGate().evaluate(
        policy,
        context(robots=RobotsDecision.DISALLOWED, credential=False),
    )
    assert not decision.allowed
    assert "ROBOTS_DISALLOWED" in decision.reasons


def test_compliance_gate_allows_reviewed_api() -> None:
    policy = approved_policy()
    decision = ComplianceGate().assert_allowed(policy, context())
    assert decision.allowed


def test_compliance_gate_blocks_missing_secret_reference_resolution() -> None:
    policy = approved_policy()
    with pytest.raises(SourceNotApprovedError, match="CREDENTIAL_UNAVAILABLE"):
        ComplianceGate().assert_allowed(policy, context(credential=False))


def test_registry_binds_adapter_to_matching_policy() -> None:
    adapter = FakeAdapter(approved_policy())
    registry = ParserRegistry([adapter])
    assert registry.get("city-api") is adapter

    with pytest.raises(SourceRegistrationError, match="already registered"):
        registry.register(adapter)


def test_registry_rejects_version_mismatch() -> None:
    adapter = FakeAdapter(approved_policy())
    adapter.parser_version = "2.0.0"
    with pytest.raises(SourceRegistrationError, match="parser_version"):
        ParserRegistry([adapter])


def test_validate_outbound_url_blocks_ssrf_and_wrong_domain() -> None:
    policy = approved_policy().security

    safe = validate_outbound_url(
        "https://api.example.com/v1/messages",
        policy,
        resolver=lambda host: ("8.8.8.8",),
    )
    assert safe == "https://api.example.com/v1/messages"

    with pytest.raises(UnsafeOutboundRequestError, match="non-public"):
        validate_outbound_url(
            "https://api.example.com/v1/messages",
            policy,
            resolver=lambda host: ("127.0.0.1",),
        )

    with pytest.raises(UnsafeOutboundRequestError, match="allowlisted"):
        validate_outbound_url(
            "https://evil.example/v1/messages",
            policy,
            resolver=lambda host: ("8.8.8.8",),
        )

    with pytest.raises(UnsafeOutboundRequestError, match="scheme"):
        validate_outbound_url(
            "file:///etc/passwd",
            policy,
            resolver=lambda host: ("8.8.8.8",),
        )


def test_author_pseudonym_is_stable_and_source_scoped() -> None:
    pseudonymizer = AuthorPseudonymizer(b"x" * 32)
    first = pseudonymizer.pseudonymize("source-a", "user-1")
    second = pseudonymizer.pseudonymize("source-a", "user-1")
    other_source = pseudonymizer.pseudonymize("source-b", "user-1")
    assert first == second
    assert first != other_source
    assert "user-1" not in first


def test_file_checkpoint_store_is_atomic_and_rejects_unsafe_ids(
    tmp_path: Path,
) -> None:
    store = FileParserCheckpointStore(tmp_path)
    store.save(
        "analysis-1",
        "city-api",
        {"page": 2},
        completed=False,
    )
    payload = store.load("analysis-1", "city-api")
    assert payload is not None
    assert payload["checkpoint"] == {"page": 2}
    assert payload["completed"] is False

    with pytest.raises(Exception, match="unsafe"):
        store.save("../../escape", "city-api", {}, completed=False)


@dataclass
class FakeAdapter:
    _policy: SourcePolicy
    fail_once: bool = False

    def __post_init__(self) -> None:
        self.source_id = self._policy.source_id
        self.parser_version = self._policy.parser_version
        self.calls: list[dict[str, object] | None] = []

    def policy(self) -> SourcePolicy:
        return self._policy

    def fetch_page(
        self,
        request: ParserRequest,
        checkpoint: dict[str, object] | None,
        services,
    ) -> ParserPage:
        del services
        self.calls.append(checkpoint)
        if self.fail_once and len(self.calls) == 1:
            raise TemporaryParserError("temporary upstream failure")
        page = int((checkpoint or {}).get("page", 0))
        if page == 0:
            return ParserPage(
                messages=(
                    SourceMessage(
                        source=self.source_id,
                        external_id="1",
                        text="first",
                        published_at=NOW,
                        url="https://api.example.com/messages/1",
                        author_id="author-1",
                        latitude=55.75,
                        longitude=37.61,
                        metadata={
                            "thread_id": "thread-1",
                            "forbidden": "drop-me",
                        },
                    ),
                    SourceMessage(
                        source=self.source_id,
                        external_id="1",
                        text="duplicate",
                        published_at=NOW,
                    ),
                ),
                next_checkpoint={"page": 1},
                done=False,
                raw_items_seen=2,
            )
        return ParserPage(
            messages=(
                SourceMessage(
                    source=self.source_id,
                    external_id="2",
                    text="second",
                    published_at=NOW,
                    author_id="author-2",
                ),
            ),
            next_checkpoint={"page": 2},
            done=True,
            raw_items_seen=1,
        )


def parser_request(source_id: str = "city-api") -> ParserRequest:
    return ParserRequest(
        analysis_id="analysis-1",
        source_id=source_id,
        purpose="urban issue analysis",
        territory={"city": "Moscow"},
        period_from=date(2026, 7, 1),
        period_to=date(2026, 8, 5),
    )


def test_runner_paginates_deduplicates_pseudonymizes_and_checkpoints() -> None:
    policy = approved_policy()
    adapter = FakeAdapter(policy)
    registry = ParserRegistry([adapter])
    store = InMemoryParserCheckpointStore()
    audit = InMemoryAuditSink()
    runner = ParserRunner(
        registry,
        store,
        audit_sink=audit,
        pseudonymizer=AuthorPseudonymizer(b"x" * 32),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
    )

    result = runner.run(parser_request(), context())
    assert [message.external_id for message in result.messages] == ["1", "2"]
    assert result.messages[0].author_id.startswith("hmac-sha256:")
    assert result.messages[0].metadata == {"thread_id": "thread-1"}
    assert result.coverage.pages_collected == 2
    assert result.coverage.raw_items_seen == 3
    assert result.coverage.duplicate_messages == 1
    assert result.coverage.messages_emitted == 2

    checkpoint = store.load("analysis-1", "city-api")
    assert checkpoint is not None
    assert checkpoint["completed"] is True
    assert checkpoint["checkpoint"]["adapter_checkpoint"] == {"page": 2}
    assert len(checkpoint["checkpoint"]["seen_external_ids_sha256"]) == 2
    assert any(event.event_type.value == "policy_check" for event in audit.events)
    assert any(event.event_type.value == "run_completed" for event in audit.events)


def test_runner_retries_declared_temporary_failure() -> None:
    adapter = FakeAdapter(approved_policy(), fail_once=True)
    runner = ParserRunner(
        ParserRegistry([adapter]),
        InMemoryParserCheckpointStore(),
        pseudonymizer=AuthorPseudonymizer(b"x" * 32),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    result = runner.run(parser_request(), context())
    assert result.coverage.status.value == "completed_with_warnings"
    assert len(adapter.calls) == 3


def test_runner_is_idempotent_after_completed_checkpoint() -> None:
    adapter = FakeAdapter(approved_policy())
    store = InMemoryParserCheckpointStore()
    runner = ParserRunner(
        ParserRegistry([adapter]),
        store,
        pseudonymizer=AuthorPseudonymizer(b"x" * 32),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    runner.run(parser_request(), context())
    second = runner.run(parser_request(), context())
    assert second.messages == ()
    assert second.coverage.warnings == ("SOURCE_ALREADY_COMPLETED",)
    assert len(adapter.calls) == 2


def test_runner_rejects_unreviewed_source_before_adapter_call() -> None:
    policy = approved_policy(enabled=False)
    adapter = FakeAdapter(policy)
    runner = ParserRunner(
        ParserRegistry([adapter]),
        InMemoryParserCheckpointStore(),
        clock=lambda: NOW,
    )
    with pytest.raises(SourceNotApprovedError):
        runner.run(parser_request(), context())
    assert adapter.calls == []


@dataclass
class ResumeAdapter:
    _policy: SourcePolicy

    def __post_init__(self) -> None:
        self.source_id = self._policy.source_id
        self.parser_version = self._policy.parser_version
        self.calls: list[dict[str, object] | None] = []

    def policy(self) -> SourcePolicy:
        return self._policy

    def fetch_page(
        self,
        request: ParserRequest,
        checkpoint: dict[str, object] | None,
        services,
    ) -> ParserPage:
        del services
        self.calls.append(checkpoint)
        page = int((checkpoint or {}).get("page", 0))
        if page == 0:
            return ParserPage(
                messages=(
                    SourceMessage(
                        source=self.source_id,
                        external_id="resume-1",
                        text="first",
                        published_at=NOW,
                    ),
                ),
                next_checkpoint={"page": 1},
                done=False,
                raw_items_seen=1,
            )
        return ParserPage(
            messages=(
                SourceMessage(
                    source=self.source_id,
                    external_id="resume-1",
                    text="duplicate after restart",
                    published_at=NOW,
                ),
                SourceMessage(
                    source=self.source_id,
                    external_id="resume-2",
                    text="new after restart",
                    published_at=NOW,
                ),
            ),
            next_checkpoint={"page": 2},
            done=True,
            raw_items_seen=2,
        )


def test_runner_persists_dedup_state_across_restart() -> None:
    policy = approved_policy()
    adapter = ResumeAdapter(policy)
    store = InMemoryParserCheckpointStore()
    first_runner = ParserRunner(
        ParserRegistry([adapter]),
        store,
        pseudonymizer=AuthorPseudonymizer(b"x" * 32),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    first_request = ParserRequest(
        analysis_id="analysis-resume",
        source_id="city-api",
        purpose="urban issue analysis",
        territory={"city": "Moscow"},
        max_pages=1,
    )
    first = first_runner.run(first_request, context())
    assert [item.external_id for item in first.messages] == ["resume-1"]
    assert first.coverage.warnings == ("MAX_PAGES_REACHED",)

    second_runner = ParserRunner(
        ParserRegistry([adapter]),
        store,
        pseudonymizer=AuthorPseudonymizer(b"x" * 32),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    second_request = ParserRequest(
        analysis_id="analysis-resume",
        source_id="city-api",
        purpose="urban issue analysis",
        territory={"city": "Moscow"},
        max_pages=2,
    )
    second = second_runner.run(second_request, context())
    assert [item.external_id for item in second.messages] == ["resume-2"]
    assert second.coverage.duplicate_messages == 1
    assert "RESUMED_FROM_CHECKPOINT" in second.coverage.warnings


class StaticCredentialProvider:
    def headers_for(
        self,
        credential_reference: str,
        source_id: str,
    ) -> dict[str, str]:
        assert credential_reference == "secret://city-api"
        assert source_id == "city-api"
        return {"Authorization": "Bearer hidden"}


class SequenceHttpClient:
    def __init__(self, responses: list[TransportResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[TransportRequest] = []

    def send(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def test_safe_transport_validates_redirects_and_attaches_secret() -> None:
    policy = approved_policy()
    client = SequenceHttpClient(
        [
            TransportResponse(
                status_code=302,
                url="https://api.example.com/start",
                headers={"location": "/v1/messages"},
                body=b"",
            ),
            TransportResponse(
                status_code=200,
                url="https://api.example.com/v1/messages",
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "content-length": "12",
                },
                body=b'{"ok": true}',
            ),
        ]
    )
    transport = SafeHttpTransport(
        policy,
        client,
        credential_provider=StaticCredentialProvider(),
        resolver=lambda host: ("8.8.8.8",),
    )
    response = transport.get("https://api.example.com/start")
    assert response.json_value() == {"ok": True}
    assert len(client.requests) == 2
    assert client.requests[0].headers["Authorization"] == "Bearer hidden"
    assert client.requests[1].url == "https://api.example.com/v1/messages"


def test_safe_transport_blocks_redirect_outside_allowlist() -> None:
    policy = approved_policy()
    client = SequenceHttpClient(
        [
            TransportResponse(
                status_code=302,
                url="https://api.example.com/start",
                headers={"location": "https://evil.example/steal"},
                body=b"",
            )
        ]
    )
    transport = SafeHttpTransport(
        policy,
        client,
        credential_provider=StaticCredentialProvider(),
        resolver=lambda host: ("8.8.8.8",),
    )
    with pytest.raises(UnsafeOutboundRequestError, match="allowlisted"):
        transport.get("https://api.example.com/start")


def test_safe_transport_blocks_adapter_supplied_authorization() -> None:
    policy = approved_policy()
    transport = SafeHttpTransport(
        policy,
        SequenceHttpClient([]),
        credential_provider=StaticCredentialProvider(),
        resolver=lambda host: ("8.8.8.8",),
    )
    with pytest.raises(Exception, match="protected header"):
        transport.get(
            "https://api.example.com/start",
            headers={"Authorization": "attacker-controlled"},
        )


def test_security_policy_rejects_ip_literal_allowlist() -> None:
    with pytest.raises(SourcePolicyError, match="IP literals"):
        SecurityPolicy(
            allowed_domains=("127.0.0.1",),
            allowed_content_types=("application/json",),
        )


def test_committed_policy_template_and_schema_are_valid() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    policy = load_source_policy(
        repository_root / "configs/sources/source-policy-template.json"
    )
    assert policy.permission_status is PermissionStatus.REVIEW_REQUIRED
    assert policy.enabled is False

    schema_path = (
        repository_root
        / "soika_uds"
        / "parsers"
        / "schemas"
        / "source-policy-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
