"Backend parser runner with compliance gates, retries, checkpoints, and coverage."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from ..contracts import SourceMessage
from .compliance import ComplianceGate
from .models import (
    AuditEvent,
    AuditEventType,
    ComplianceContext,
    ParserCoverage,
    ParserExecutionError,
    ParserPage,
    ParserRequest,
    ParserRunResult,
    ParserRunStatus,
    PermanentParserError,
    SourcePolicy,
    SourcePolicyError,
    TemporaryParserError,
)
from .registry import ParserRegistry
from .security import (
    AuthorPseudonymizer,
    filter_metadata,
    transform_author_identifier,
)
from .store import AuditSink, ParserCheckpointStore
from .transport import ParserServices, SafeHttpTransport, UnavailableTransport


class _NullAuditSink:
    def write(self, event: AuditEvent) -> None:
        del event


class TokenBucketLimiter:
    """Small backend limiter shared by one source runner."""

    def __init__(
        self,
        requests_per_minute: int,
        burst: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        transport_factory: Callable[
            [SourcePolicy],
            SafeHttpTransport | UnavailableTransport,
        ]
        | None = None,
    ) -> None:
        self._rate = requests_per_minute / 60.0
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated_at = monotonic()
        self._monotonic = monotonic
        self._sleep = sleep

    def acquire(self) -> None:
        while True:
            now = self._monotonic()
            elapsed = max(0.0, now - self._updated_at)
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._rate,
            )
            self._updated_at = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait_seconds = (1.0 - self._tokens) / self._rate
            self._sleep(wait_seconds)


class ParserRunner:
    """Run one approved adapter without exposing a separate user interface."""

    def __init__(
        self,
        registry: ParserRegistry,
        checkpoint_store: ParserCheckpointStore,
        *,
        audit_sink: AuditSink | None = None,
        compliance_gate: ComplianceGate | None = None,
        pseudonymizer: AuthorPseudonymizer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        transport_factory: Callable[
            [SourcePolicy],
            SafeHttpTransport | UnavailableTransport,
        ]
        | None = None,
    ) -> None:
        self.registry = registry
        self.checkpoint_store = checkpoint_store
        self.audit_sink = audit_sink or _NullAuditSink()
        self.compliance_gate = compliance_gate or ComplianceGate()
        self.pseudonymizer = pseudonymizer
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.transport_factory = transport_factory or (
            lambda policy: UnavailableTransport()
        )

    def _audit(
        self,
        event_type: AuditEventType,
        request: ParserRequest,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.audit_sink.write(
            AuditEvent(
                event_type=event_type,
                source_id=request.source_id,
                analysis_id=request.analysis_id,
                occurred_at=self.clock(),
                details=details or {},
            )
        )

    @staticmethod
    def _load_checkpoint_state(
        persisted: Mapping[str, object] | None,
    ) -> tuple[
        dict[str, object] | None,
        set[str],
        int,
        int,
        int,
        int,
        int,
    ]:
        if persisted is None:
            return None, set(), 0, 0, 0, 0, 0
        raw_state = persisted.get("checkpoint")
        if raw_state is None:
            return None, set(), 0, 0, 0, 0, 0
        if not isinstance(raw_state, dict):
            raise SourcePolicyError("persisted checkpoint must be an object")
        if raw_state.get("version") != 1:
            return dict(raw_state), set(), 0, 0, 0, 0, 0
        adapter_checkpoint = raw_state.get("adapter_checkpoint")
        if adapter_checkpoint is not None and not isinstance(
            adapter_checkpoint, dict
        ):
            raise SourcePolicyError(
                "adapter_checkpoint must be an object or null"
            )
        hashes = raw_state.get("seen_external_ids_sha256", [])
        if not isinstance(hashes, list) or not all(
            isinstance(item, str) and len(item) == 64 for item in hashes
        ):
            raise SourcePolicyError(
                "seen_external_ids_sha256 must be an array of SHA-256 values"
            )
        counters: list[int] = []
        for field_name in (
            "pages_collected",
            "raw_items_seen",
            "messages_emitted",
            "duplicate_messages",
            "rejected_messages",
        ):
            value = raw_state.get(field_name, 0)
            if not isinstance(value, int) or value < 0:
                raise SourcePolicyError(
                    f"checkpoint {field_name} must be non-negative"
                )
            counters.append(value)
        return (
            dict(adapter_checkpoint) if adapter_checkpoint is not None else None,
            set(hashes),
            *counters,
        )

    @staticmethod
    def _checkpoint_envelope(
        *,
        adapter_checkpoint: Mapping[str, object] | None,
        seen_external_ids_sha256: set[str],
        pages_collected: int,
        raw_items_seen: int,
        messages_emitted: int,
        duplicate_messages: int,
        rejected_messages: int,
    ) -> dict[str, object]:
        return {
            "version": 1,
            "adapter_checkpoint": (
                dict(adapter_checkpoint)
                if adapter_checkpoint is not None
                else None
            ),
            "seen_external_ids_sha256": sorted(seen_external_ids_sha256),
            "pages_collected": pages_collected,
            "raw_items_seen": raw_items_seen,
            "messages_emitted": messages_emitted,
            "duplicate_messages": duplicate_messages,
            "rejected_messages": rejected_messages,
        }

    @staticmethod
    def _dedup_key(source_id: str, external_id: str) -> str:
        return hashlib.sha256(
            f"{source_id}\0{external_id}".encode()
        ).hexdigest()

    @staticmethod
    def _metadata_fields(policy: SourcePolicy) -> tuple[str, ...]:
        prefix = "metadata."
        return tuple(
            field_name[len(prefix) :]
            for field_name in policy.data.allowed_fields
            if field_name.startswith(prefix)
        )

    def _sanitize_message(
        self,
        message: object,
        policy: SourcePolicy,
    ) -> SourceMessage:
        if not isinstance(message, SourceMessage):
            raise PermanentParserError(
                "adapter emitted a non-SourceMessage value",
                code="INVALID_SOURCE_MESSAGE",
            )
        if message.source != policy.source_id:
            raise PermanentParserError(
                "adapter emitted a message for another source",
                code="SOURCE_ID_MISMATCH",
            )
        if (
            message.published_at.tzinfo is None
            or message.published_at.utcoffset() is None
        ):
            raise PermanentParserError(
                "adapter emitted a timestamp without UTC offset",
                code="NAIVE_MESSAGE_TIMESTAMP",
            )

        allowed = set(policy.data.allowed_fields)
        author_id = (
            transform_author_identifier(
                policy,
                message.author_id,
                pseudonymizer=self.pseudonymizer,
            )
            if "author_id" in allowed
            else None
        )
        metadata = filter_metadata(
            message.metadata,
            self._metadata_fields(policy),
        )
        latitude = message.latitude if "coordinates" in allowed else None
        longitude = message.longitude if "coordinates" in allowed else None
        url = message.url if "url" in allowed else None

        return replace(
            message,
            url=url,
            author_id=author_id,
            latitude=latitude,
            longitude=longitude,
            metadata=metadata,
        )

    def _fetch_with_retry(
        self,
        *,
        adapter,
        request: ParserRequest,
        checkpoint: dict[str, object] | None,
        policy: SourcePolicy,
        limiter: TokenBucketLimiter,
        services: ParserServices,
    ) -> tuple[ParserPage, int]:
        max_attempts = policy.rate_limit.max_retries + 1
        for attempt in range(1, max_attempts + 1):
            limiter.acquire()
            try:
                page = adapter.fetch_page(request, checkpoint, services)
                if not isinstance(page, ParserPage):
                    raise PermanentParserError(
                        "adapter returned a non-ParserPage value",
                        code="INVALID_PARSER_PAGE",
                    )
                return page, attempt
            except TemporaryParserError:
                if attempt >= max_attempts:
                    raise
                self.sleep(
                    policy.rate_limit.backoff_seconds * (2 ** (attempt - 1))
                )
            except PermanentParserError:
                raise
            except ParserExecutionError:
                raise
            except Exception as error:
                raise PermanentParserError(
                    "adapter raised an undeclared exception",
                    code="UNDECLARED_ADAPTER_EXCEPTION",
                    details={"exception_type": type(error).__name__},
                ) from error
        raise RuntimeError("unreachable retry state")

    def run(
        self,
        request: ParserRequest,
        compliance_context: ComplianceContext,
    ) -> ParserRunResult:
        adapter = self.registry.get(request.source_id)
        policy = self.registry.policy(request.source_id)
        if request.purpose != compliance_context.purpose:
            raise SourcePolicyError(
                "request purpose must equal compliance context purpose"
            )

        started_at = self.clock()
        decision = self.compliance_gate.evaluate(policy, compliance_context)
        self._audit(
            AuditEventType.POLICY_CHECK,
            request,
            {
                "allowed": decision.allowed,
                "reasons": list(decision.reasons),
                "warnings": list(decision.warnings),
                "policy_review_due_at": (
                    policy.review_due_at.isoformat().replace("+00:00", "Z")
                    if policy.review_due_at is not None
                    else None
                ),
            },
        )
        self.compliance_gate.assert_allowed(policy, compliance_context)
        self._audit(
            AuditEventType.RUN_STARTED,
            request,
            {"parser_version": policy.parser_version},
        )

        persisted = self.checkpoint_store.load(
            request.analysis_id,
            request.source_id,
        )
        (
            checkpoint,
            seen_external_ids,
            pages_collected,
            raw_items_seen,
            messages_emitted_total,
            duplicate_messages,
            rejected_messages,
        ) = self._load_checkpoint_state(persisted)
        if persisted is not None and persisted.get("completed") is True:
            finished_at = self.clock()
            final_state = self._checkpoint_envelope(
                adapter_checkpoint=checkpoint,
                seen_external_ids_sha256=seen_external_ids,
                pages_collected=pages_collected,
                raw_items_seen=raw_items_seen,
                messages_emitted=messages_emitted_total,
                duplicate_messages=duplicate_messages,
                rejected_messages=rejected_messages,
            )
            coverage = ParserCoverage(
                source_id=request.source_id,
                status=ParserRunStatus.COMPLETED_WITH_WARNINGS,
                pages_collected=pages_collected,
                raw_items_seen=raw_items_seen,
                messages_emitted=messages_emitted_total,
                duplicate_messages=duplicate_messages,
                rejected_messages=rejected_messages,
                started_at=started_at,
                finished_at=finished_at,
                warnings=("SOURCE_ALREADY_COMPLETED",),
            )
            return ParserRunResult(
                messages=(),
                coverage=coverage,
                final_checkpoint=final_state,
            )

        limiter = TokenBucketLimiter(
            policy.rate_limit.requests_per_minute,
            policy.rate_limit.burst,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )
        services = ParserServices(
            transport=self.transport_factory(policy),
        )
        messages: list[SourceMessage] = []
        warnings = list(decision.warnings)
        if persisted is not None:
            warnings.append("RESUMED_FROM_CHECKPOINT")
        source_done = False

        try:
            while pages_collected < request.max_pages:
                page, attempts = self._fetch_with_retry(
                    adapter=adapter,
                    request=request,
                    checkpoint=checkpoint,
                    policy=policy,
                    limiter=limiter,
                    services=services,
                )
                if attempts > 1:
                    warnings.append("TEMPORARY_RETRY_USED")
                pages_collected += 1
                raw_items_seen += page.raw_items_seen
                warnings.extend(page.warnings)

                for raw_message in page.messages:
                    try:
                        message = self._sanitize_message(raw_message, policy)
                    except ParserExecutionError as error:
                        rejected_messages += 1
                        self._audit(
                            AuditEventType.MESSAGE_REJECTED,
                            request,
                            {"code": error.code, "message": str(error)},
                        )
                        continue
                    dedup_key = self._dedup_key(
                        message.source,
                        message.external_id,
                    )
                    if dedup_key in seen_external_ids:
                        duplicate_messages += 1
                        continue
                    seen_external_ids.add(dedup_key)
                    messages.append(message)
                    messages_emitted_total += 1
                    if messages_emitted_total >= request.max_messages:
                        warnings.append("MAX_MESSAGES_REACHED")
                        page = ParserPage(
                            messages=(),
                            next_checkpoint=page.next_checkpoint,
                            done=True,
                            raw_items_seen=0,
                        )
                        break

                checkpoint = (
                    dict(page.next_checkpoint)
                    if page.next_checkpoint is not None
                    else checkpoint
                )
                checkpoint_state = self._checkpoint_envelope(
                    adapter_checkpoint=checkpoint,
                    seen_external_ids_sha256=seen_external_ids,
                    pages_collected=pages_collected,
                    raw_items_seen=raw_items_seen,
                    messages_emitted=messages_emitted_total,
                    duplicate_messages=duplicate_messages,
                    rejected_messages=rejected_messages,
                )
                self.checkpoint_store.save(
                    request.analysis_id,
                    request.source_id,
                    checkpoint_state,
                    completed=page.done,
                )
                self._audit(
                    AuditEventType.CHECKPOINT_SAVED,
                    request,
                    {
                        "page": pages_collected,
                        "completed": page.done,
                    },
                )
                self._audit(
                    AuditEventType.PAGE_COLLECTED,
                    request,
                    {
                        "page": pages_collected,
                        "raw_items_seen": page.raw_items_seen,
                        "messages_total": len(messages),
                    },
                )
                if page.done:
                    source_done = True
                    break
            else:
                warnings.append("MAX_PAGES_REACHED")

            if not source_done:
                checkpoint_state = self._checkpoint_envelope(
                    adapter_checkpoint=checkpoint,
                    seen_external_ids_sha256=seen_external_ids,
                    pages_collected=pages_collected,
                    raw_items_seen=raw_items_seen,
                    messages_emitted=messages_emitted_total,
                    duplicate_messages=duplicate_messages,
                    rejected_messages=rejected_messages,
                )
                self.checkpoint_store.save(
                    request.analysis_id,
                    request.source_id,
                    checkpoint_state,
                    completed=False,
                )

            finished_at = self.clock()
            status = (
                ParserRunStatus.COMPLETED_WITH_WARNINGS
                if warnings or rejected_messages
                else ParserRunStatus.COMPLETED
            )
            coverage = ParserCoverage(
                source_id=request.source_id,
                status=status,
                pages_collected=pages_collected,
                raw_items_seen=raw_items_seen,
                messages_emitted=messages_emitted_total,
                duplicate_messages=duplicate_messages,
                rejected_messages=rejected_messages,
                started_at=started_at,
                finished_at=finished_at,
                warnings=tuple(dict.fromkeys(warnings)),
            )
            self._audit(
                AuditEventType.RUN_COMPLETED,
                request,
                coverage.to_dict(),
            )
            return ParserRunResult(
                messages=tuple(messages),
                coverage=coverage,
                final_checkpoint=self._checkpoint_envelope(
                    adapter_checkpoint=checkpoint,
                    seen_external_ids_sha256=seen_external_ids,
                    pages_collected=pages_collected,
                    raw_items_seen=raw_items_seen,
                    messages_emitted=messages_emitted_total,
                    duplicate_messages=duplicate_messages,
                    rejected_messages=rejected_messages,
                ),
            )
        except ParserExecutionError as error:
            finished_at = self.clock()
            self._audit(
                AuditEventType.RUN_FAILED,
                request,
                {
                    "code": error.code,
                    "message": str(error),
                    "retryable": error.retryable,
                },
            )
            raise
