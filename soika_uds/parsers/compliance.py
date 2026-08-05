"Fail-closed legal, policy, privacy, and operational source gate."""

from __future__ import annotations

from datetime import UTC, datetime

from .models import (
    AccessMethod,
    AuthorIdentifierMode,
    ComplianceContext,
    ComplianceDecision,
    DataCategory,
    PermissionStatus,
    RobotsDecision,
    RobotsRequirement,
    SourceNotApprovedError,
    SourcePolicy,
)


class ComplianceGate:
    """Evaluate whether a source may run before any network access."""

    def evaluate(
        self,
        policy: SourcePolicy,
        context: ComplianceContext,
    ) -> ComplianceDecision:
        reasons: list[str] = []
        warnings: list[str] = []
        now = context.current_time.astimezone(UTC)

        if not policy.enabled:
            reasons.append("SOURCE_DISABLED")
        if policy.permission_status is not PermissionStatus.APPROVED:
            reasons.append(f"PERMISSION_{policy.permission_status.value.upper()}")
        if policy.permission is None:
            reasons.append("PERMISSION_EVIDENCE_MISSING")
        elif not context.permission_reference_available:
            reasons.append("PERMISSION_EVIDENCE_UNAVAILABLE")
        elif (
            policy.permission.expires_at is not None
            and policy.permission.expires_at <= now
        ):
            reasons.append("PERMISSION_EVIDENCE_EXPIRED")

        if policy.reviewed_at is None or policy.review_due_at is None:
            reasons.append("POLICY_REVIEW_DATES_MISSING")
        elif policy.review_due_at <= now:
            reasons.append("POLICY_REVIEW_EXPIRED")

        if context.purpose not in policy.allowed_purposes:
            reasons.append("PURPOSE_NOT_APPROVED")

        if (
            policy.security.credential_reference is not None
            and not context.credential_available
        ):
            reasons.append("CREDENTIAL_UNAVAILABLE")

        if policy.robots_requirement is RobotsRequirement.REQUIRED:
            if context.robots_decision is RobotsDecision.DISALLOWED:
                reasons.append("ROBOTS_DISALLOWED")
            elif context.robots_decision is RobotsDecision.UNAVAILABLE:
                reasons.append("ROBOTS_UNAVAILABLE")
            elif context.robots_decision is RobotsDecision.NOT_CHECKED:
                reasons.append("ROBOTS_NOT_CHECKED")
            elif context.robots_decision is not RobotsDecision.ALLOWED:
                reasons.append("ROBOTS_DECISION_INVALID")
        elif context.robots_decision not in {
            RobotsDecision.NOT_APPLICABLE,
            RobotsDecision.ALLOWED,
        }:
            warnings.append("ROBOTS_DECISION_IGNORED_FOR_NON_WEB_SOURCE")

        if policy.access_method is AccessMethod.PUBLIC_WEB:
            if not policy.terms_url:
                reasons.append("TERMS_URL_MISSING")
            if not policy.security.allowed_domains:
                reasons.append("DOMAIN_ALLOWLIST_MISSING")

        categories = set(policy.data.categories)
        if DataCategory.SPECIAL_CATEGORY in categories:
            reasons.append("SPECIAL_CATEGORY_REQUIRES_SEPARATE_RELEASE_GATE")
        if DataCategory.BIOMETRIC in categories:
            reasons.append("BIOMETRIC_DATA_FORBIDDEN_BY_PLATFORM")
        if DataCategory.CHILD_DATA in categories:
            reasons.append("CHILD_DATA_REQUIRES_SEPARATE_RELEASE_GATE")
        if policy.data.author_identifier_mode is AuthorIdentifierMode.RAW:
            reasons.append("RAW_AUTHOR_IDENTIFIERS_REQUIRE_SEPARATE_RELEASE_GATE")
        if policy.data.store_raw_response:
            warnings.append("RAW_RESPONSE_STORAGE_ENABLED")
        if policy.data.retention_days > 365:
            warnings.append("RETENTION_EXCEEDS_ONE_YEAR")

        return ComplianceDecision(
            allowed=not reasons,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def assert_allowed(
        self,
        policy: SourcePolicy,
        context: ComplianceContext,
    ) -> ComplianceDecision:
        decision = self.evaluate(policy, context)
        if not decision.allowed:
            raise SourceNotApprovedError(
                f"source {policy.source_id!r} blocked: {', '.join(decision.reasons)}"
            )
        return decision


def compliance_snapshot(
    policy: SourcePolicy,
    context: ComplianceContext,
) -> dict[str, object]:
    """Return a serializable backend diagnostic without exposing credentials."""

    decision = ComplianceGate().evaluate(policy, context)
    return {
        "source_id": policy.source_id,
        "enabled": policy.enabled,
        "permission_status": policy.permission_status.value,
        "access_method": policy.access_method.value,
        "review_due_at": (
            policy.review_due_at.isoformat().replace("+00:00", "Z")
            if policy.review_due_at is not None
            else None
        ),
        "allowed": decision.allowed,
        "reasons": list(decision.reasons),
        "warnings": list(decision.warnings),
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
