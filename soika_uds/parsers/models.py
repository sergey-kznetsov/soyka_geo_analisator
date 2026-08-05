"Backend-only parser platform contracts and source compliance metadata."""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ParserPlatformError(RuntimeError):
    """Base error for the parser platform."""


class SourcePolicyError(ParserPlatformError, ValueError):
    """Raised when a source policy is incomplete or inconsistent."""


class SourceNotApprovedError(ParserPlatformError):
    """Raised when collection is attempted without an approved source policy."""


class SourceRegistrationError(ParserPlatformError):
    """Raised when a source adapter cannot be registered safely."""


class ParserExecutionError(ParserPlatformError):
    """Base class for parser execution failures."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str = "PARSER_EXECUTION_ERROR",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = MappingProxyType(dict(details or {}))


class TemporaryParserError(ParserExecutionError):
    retryable = True


class PermanentParserError(ParserExecutionError):
    retryable = False


class AccessMethod(str, Enum):
    OFFICIAL_API = "official_api"
    LICENSED_FEED = "licensed_feed"
    PUBLIC_WEB = "public_web"
    FILE_IMPORT = "file_import"
    MANUAL_EXPORT = "manual_export"


class PermissionStatus(str, Enum):
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class PermissionEvidenceKind(str, Enum):
    API_TERMS = "api_terms"
    WRITTEN_PERMISSION = "written_permission"
    CONTRACT = "contract"
    OPEN_LICENSE = "open_license"
    STATUTORY_BASIS = "statutory_basis"
    INTERNAL_LEGAL_MEMO = "internal_legal_memo"


class RobotsRequirement(str, Enum):
    REQUIRED = "required"
    NOT_APPLICABLE = "not_applicable"


class RobotsDecision(str, Enum):
    ALLOWED = "allowed"
    DISALLOWED = "disallowed"
    UNAVAILABLE = "unavailable"
    NOT_CHECKED = "not_checked"
    NOT_APPLICABLE = "not_applicable"


class RequirementDecision(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class AuthorIdentifierMode(str, Enum):
    DROP = "drop"
    HMAC_PSEUDONYM = "hmac_pseudonym"
    RAW = "raw"


class DataCategory(str, Enum):
    PUBLIC_TEXT = "public_text"
    USERNAME = "username"
    PROFILE_IDENTIFIER = "profile_identifier"
    CONTACT_DETAILS = "contact_details"
    LOCATION = "location"
    SPECIAL_CATEGORY = "special_category"
    BIOMETRIC = "biometric"
    CHILD_DATA = "child_data"


class AuditEventType(str, Enum):
    POLICY_CHECK = "policy_check"
    RUN_STARTED = "run_started"
    PAGE_COLLECTED = "page_collected"
    CHECKPOINT_SAVED = "checkpoint_saved"
    MESSAGE_REJECTED = "message_rejected"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class ParserRunStatus(str, Enum):
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    BLOCKED = "blocked"




def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SourcePolicyError(f"{field_name} must be a boolean")
    return value

def _clean_required(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise SourcePolicyError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise SourcePolicyError(f"{field_name} must not be empty")
    return cleaned


def _clean_optional(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _clean_required(value, field_name)


def _string_tuple(
    value: Sequence[object] | tuple[str, ...],
    field_name: str,
    *,
    pattern: re.Pattern[str] | None = None,
) -> tuple[str, ...]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise SourcePolicyError(f"{field_name} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        cleaned = _clean_required(item, f"{field_name}[{index}]")
        if pattern is not None and pattern.fullmatch(cleaned) is None:
            raise SourcePolicyError(f"{field_name}[{index}] has invalid format")
        result.append(cleaned)
    if len(result) != len(set(result)):
        raise SourcePolicyError(f"{field_name} must not contain duplicates")
    return tuple(result)


def _enum_tuple(
    enum_type: type[Enum],
    value: Sequence[object],
    field_name: str,
) -> tuple[Enum, ...]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise SourcePolicyError(f"{field_name} must be an array")
    result: list[Enum] = []
    for index, item in enumerate(value):
        try:
            result.append(enum_type(item))
        except (TypeError, ValueError) as error:
            raise SourcePolicyError(
                f"{field_name}[{index}] is not supported"
            ) from error
    if len(result) != len(set(result)):
        raise SourcePolicyError(f"{field_name} must not contain duplicates")
    return tuple(result)


def _parse_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as error:
            raise SourcePolicyError(
                f"{field_name} must be an ISO 8601 datetime"
            ) from error
    else:
        raise SourcePolicyError(f"{field_name} must be a datetime string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourcePolicyError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return _parse_datetime(value, "datetime").isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_date(value: object, field_name: str) -> date:
    if isinstance(value, datetime):
        raise SourcePolicyError(f"{field_name} must be a date, not datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise SourcePolicyError(f"{field_name} must be an ISO 8601 date")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise SourcePolicyError(f"{field_name} must be an ISO 8601 date") from error


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SourcePolicyError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SourcePolicyError(f"{field_name} keys must be strings")
            result[key] = _json_value(item, f"{field_name}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise SourcePolicyError(
        f"{field_name} contains unsupported value {type(value).__name__}"
    )


def _immutable_json(
    value: Mapping[str, Any] | None,
    field_name: str,
) -> Mapping[str, Any]:
    return MappingProxyType(_json_value(value or {}, field_name))


@dataclass(frozen=True, slots=True)
class SourceResearchRecord:
    """Mandatory dossier describing how and whether a source may be collected."""

    collection_plan: str
    official_access_available: RequirementDecision
    permission_required: RequirementDecision
    permission_contact: str | None
    copyright_constraints: str
    terms_constraints: str
    personal_data_notes: str
    security_risks: tuple[str, ...]
    deletion_or_correction_process: str
    rate_limit_source: str
    reviewed_sources: tuple[str, ...]
    unresolved_questions: tuple[str, ...] = ()
    robots_url: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "collection_plan",
            "copyright_constraints",
            "terms_constraints",
            "personal_data_notes",
            "deletion_or_correction_process",
            "rate_limit_source",
        ):
            object.__setattr__(
                self,
                field_name,
                _clean_required(getattr(self, field_name), f"research.{field_name}"),
            )
        for field_name in ("official_access_available", "permission_required"):
            value = getattr(self, field_name)
            if not isinstance(value, RequirementDecision):
                object.__setattr__(
                    self,
                    field_name,
                    RequirementDecision(value),
                )
        object.__setattr__(
            self,
            "permission_contact",
            _clean_optional(
                self.permission_contact,
                "research.permission_contact",
            ),
        )
        object.__setattr__(
            self,
            "robots_url",
            _clean_optional(self.robots_url, "research.robots_url"),
        )
        object.__setattr__(
            self,
            "security_risks",
            _string_tuple(self.security_risks, "research.security_risks"),
        )
        object.__setattr__(
            self,
            "reviewed_sources",
            _string_tuple(self.reviewed_sources, "research.reviewed_sources"),
        )
        object.__setattr__(
            self,
            "unresolved_questions",
            _string_tuple(
                self.unresolved_questions,
                "research.unresolved_questions",
            ),
        )
        if not self.reviewed_sources:
            raise SourcePolicyError(
                "research.reviewed_sources must contain at least one source"
            )
        if (
            self.permission_required is RequirementDecision.YES
            and self.permission_contact is None
        ):
            raise SourcePolicyError(
                "research.permission_contact is required when permission is required"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "collection_plan": self.collection_plan,
            "official_access_available": self.official_access_available.value,
            "permission_required": self.permission_required.value,
            "copyright_constraints": self.copyright_constraints,
            "terms_constraints": self.terms_constraints,
            "personal_data_notes": self.personal_data_notes,
            "security_risks": list(self.security_risks),
            "deletion_or_correction_process": self.deletion_or_correction_process,
            "rate_limit_source": self.rate_limit_source,
            "reviewed_sources": list(self.reviewed_sources),
            "unresolved_questions": list(self.unresolved_questions),
        }
        if self.permission_contact is not None:
            payload["permission_contact"] = self.permission_contact
        if self.robots_url is not None:
            payload["robots_url"] = self.robots_url
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SourceResearchRecord:
        allowed = {
            "collection_plan",
            "official_access_available",
            "permission_required",
            "permission_contact",
            "copyright_constraints",
            "terms_constraints",
            "personal_data_notes",
            "security_risks",
            "deletion_or_correction_process",
            "rate_limit_source",
            "reviewed_sources",
            "unresolved_questions",
            "robots_url",
        }
        required = {
            "collection_plan",
            "official_access_available",
            "permission_required",
            "copyright_constraints",
            "terms_constraints",
            "personal_data_notes",
            "security_risks",
            "deletion_or_correction_process",
            "rate_limit_source",
            "reviewed_sources",
        }
        unknown = sorted(set(payload) - allowed)
        missing = sorted(required - set(payload))
        if unknown:
            raise SourcePolicyError(
                f"research contains unknown fields: {', '.join(unknown)}"
            )
        if missing:
            raise SourcePolicyError(
                f"research is missing fields: {', '.join(missing)}"
            )
        return cls(
            collection_plan=payload["collection_plan"],
            official_access_available=RequirementDecision(
                payload["official_access_available"]
            ),
            permission_required=RequirementDecision(payload["permission_required"]),
            permission_contact=payload.get("permission_contact"),
            copyright_constraints=payload["copyright_constraints"],
            terms_constraints=payload["terms_constraints"],
            personal_data_notes=payload["personal_data_notes"],
            security_risks=_string_tuple(
                payload["security_risks"],
                "research.security_risks",
            ),
            deletion_or_correction_process=payload[
                "deletion_or_correction_process"
            ],
            rate_limit_source=payload["rate_limit_source"],
            reviewed_sources=_string_tuple(
                payload["reviewed_sources"],
                "research.reviewed_sources",
            ),
            unresolved_questions=_string_tuple(
                payload.get("unresolved_questions", []),
                "research.unresolved_questions",
            ),
            robots_url=payload.get("robots_url"),
        )


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    requests_per_minute: int
    burst: int = 1
    max_concurrency: int = 1
    timeout_seconds: float = 20.0
    max_retries: int = 3
    backoff_seconds: float = 1.0

    def __post_init__(self) -> None:
        for field_name in ("requests_per_minute", "burst", "max_concurrency"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise SourcePolicyError(f"{field_name} must be a positive integer")
        if self.requests_per_minute > 6000:
            raise SourcePolicyError("requests_per_minute exceeds platform safety limit")
        for field_name in ("timeout_seconds", "backoff_seconds"):
            value = getattr(self, field_name)
            if not isinstance(value, int | float) or value <= 0:
                raise SourcePolicyError(f"{field_name} must be positive")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 10:
            raise SourcePolicyError("max_retries must be in [0, 10]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "burst": self.burst,
            "max_concurrency": self.max_concurrency,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "backoff_seconds": self.backoff_seconds,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RateLimitPolicy:
        allowed = {
            "requests_per_minute",
            "burst",
            "max_concurrency",
            "timeout_seconds",
            "max_retries",
            "backoff_seconds",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise SourcePolicyError(
                f"rate_limit contains unknown fields: {', '.join(unknown)}"
            )
        if "requests_per_minute" not in payload:
            raise SourcePolicyError("rate_limit.requests_per_minute is required")
        return cls(
            requests_per_minute=payload["requests_per_minute"],
            burst=payload.get("burst", 1),
            max_concurrency=payload.get("max_concurrency", 1),
            timeout_seconds=payload.get("timeout_seconds", 20.0),
            max_retries=payload.get("max_retries", 3),
            backoff_seconds=payload.get("backoff_seconds", 1.0),
        )


@dataclass(frozen=True, slots=True)
class PermissionEvidence:
    kind: PermissionEvidenceKind
    reference: str
    reviewed_by: str
    reviewed_at: datetime
    expires_at: datetime | None = None
    document_sha256: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PermissionEvidenceKind):
            object.__setattr__(self, "kind", PermissionEvidenceKind(self.kind))
        object.__setattr__(
            self, "reference", _clean_required(self.reference, "permission.reference")
        )
        object.__setattr__(
            self,
            "reviewed_by",
            _clean_required(self.reviewed_by, "permission.reviewed_by"),
        )
        object.__setattr__(
            self,
            "reviewed_at",
            _parse_datetime(self.reviewed_at, "permission.reviewed_at"),
        )
        if self.expires_at is not None:
            expires_at = _parse_datetime(
                self.expires_at, "permission.expires_at"
            )
            if expires_at <= self.reviewed_at:
                raise SourcePolicyError(
                    "permission.expires_at must be later than reviewed_at"
                )
            object.__setattr__(self, "expires_at", expires_at)
        if self.document_sha256 is not None:
            digest = self.document_sha256.strip().lower()
            if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise SourcePolicyError(
                    "permission.document_sha256 must be a lowercase SHA-256 digest"
                )
            object.__setattr__(self, "document_sha256", digest)
        object.__setattr__(
            self, "notes", _clean_optional(self.notes, "permission.notes")
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind.value,
            "reference": self.reference,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": _format_datetime(self.reviewed_at),
        }
        if self.expires_at is not None:
            payload["expires_at"] = _format_datetime(self.expires_at)
        if self.document_sha256 is not None:
            payload["document_sha256"] = self.document_sha256
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PermissionEvidence:
        allowed = {
            "kind",
            "reference",
            "reviewed_by",
            "reviewed_at",
            "expires_at",
            "document_sha256",
            "notes",
        }
        required = {"kind", "reference", "reviewed_by", "reviewed_at"}
        unknown = sorted(set(payload) - allowed)
        missing = sorted(required - set(payload))
        if unknown:
            raise SourcePolicyError(
                f"permission contains unknown fields: {', '.join(unknown)}"
            )
        if missing:
            raise SourcePolicyError(
                f"permission is missing fields: {', '.join(missing)}"
            )
        return cls(
            kind=PermissionEvidenceKind(payload["kind"]),
            reference=payload["reference"],
            reviewed_by=payload["reviewed_by"],
            reviewed_at=_parse_datetime(
                payload["reviewed_at"], "permission.reviewed_at"
            ),
            expires_at=(
                _parse_datetime(payload["expires_at"], "permission.expires_at")
                if payload.get("expires_at") is not None
                else None
            ),
            document_sha256=payload.get("document_sha256"),
            notes=payload.get("notes"),
        )


@dataclass(frozen=True, slots=True)
class DataProtectionPolicy:
    categories: tuple[DataCategory, ...]
    allowed_fields: tuple[str, ...]
    retention_days: int
    author_identifier_mode: AuthorIdentifierMode = AuthorIdentifierMode.HMAC_PSEUDONYM
    store_raw_response: bool = False
    allow_special_categories: bool = False
    allow_biometric: bool = False
    allow_child_data: bool = False
    deletion_supported: bool = True
    purpose: str = ""

    def __post_init__(self) -> None:
        categories = tuple(
            item if isinstance(item, DataCategory) else DataCategory(item)
            for item in self.categories
        )
        if len(categories) != len(set(categories)):
            raise SourcePolicyError("data.categories must not contain duplicates")
        object.__setattr__(self, "categories", categories)
        object.__setattr__(
            self,
            "allowed_fields",
            _string_tuple(
                self.allowed_fields,
                "data.allowed_fields",
                pattern=_FIELD_RE,
            ),
        )
        required_fields = {"external_id", "text", "published_at"}
        missing_required = sorted(required_fields - set(self.allowed_fields))
        if missing_required:
            raise SourcePolicyError(
                "data.allowed_fields is missing required fields: "
                + ", ".join(missing_required)
            )
        if type(self.retention_days) is not int or not (
            1 <= self.retention_days <= 3650
        ):
            raise SourcePolicyError("data.retention_days must be in [1, 3650]")
        if not isinstance(self.author_identifier_mode, AuthorIdentifierMode):
            object.__setattr__(
                self,
                "author_identifier_mode",
                AuthorIdentifierMode(self.author_identifier_mode),
            )
        object.__setattr__(
            self,
            "purpose",
            _clean_required(self.purpose, "data.purpose"),
        )
        for field_name in (
            "store_raw_response",
            "allow_special_categories",
            "allow_biometric",
            "allow_child_data",
            "deletion_supported",
        ):
            _require_bool(getattr(self, field_name), f"data.{field_name}")
        if (
            DataCategory.SPECIAL_CATEGORY in categories
            and not self.allow_special_categories
        ):
            raise SourcePolicyError(
                "special-category data declared but allow_special_categories is false"
            )
        if DataCategory.BIOMETRIC in categories and not self.allow_biometric:
            raise SourcePolicyError(
                "biometric data declared but allow_biometric is false"
            )
        if DataCategory.CHILD_DATA in categories and not self.allow_child_data:
            raise SourcePolicyError(
                "child data declared but allow_child_data is false"
            )
        if self.author_identifier_mode is AuthorIdentifierMode.RAW:
            if DataCategory.PROFILE_IDENTIFIER not in categories:
                raise SourcePolicyError(
                    "raw author identifiers require profile_identifier category"
                )
            if not self.deletion_supported:
                raise SourcePolicyError(
                    "raw author identifiers require deletion support"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "categories": [item.value for item in self.categories],
            "allowed_fields": list(self.allowed_fields),
            "retention_days": self.retention_days,
            "author_identifier_mode": self.author_identifier_mode.value,
            "store_raw_response": self.store_raw_response,
            "allow_special_categories": self.allow_special_categories,
            "allow_biometric": self.allow_biometric,
            "allow_child_data": self.allow_child_data,
            "deletion_supported": self.deletion_supported,
            "purpose": self.purpose,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DataProtectionPolicy:
        allowed = {
            "categories",
            "allowed_fields",
            "retention_days",
            "author_identifier_mode",
            "store_raw_response",
            "allow_special_categories",
            "allow_biometric",
            "allow_child_data",
            "deletion_supported",
            "purpose",
        }
        required = {"categories", "allowed_fields", "retention_days", "purpose"}
        unknown = sorted(set(payload) - allowed)
        missing = sorted(required - set(payload))
        if unknown:
            raise SourcePolicyError(
                f"data contains unknown fields: {', '.join(unknown)}"
            )
        if missing:
            raise SourcePolicyError(f"data is missing fields: {', '.join(missing)}")
        return cls(
            categories=tuple(
                _enum_tuple(DataCategory, payload["categories"], "data.categories")
            ),
            allowed_fields=_string_tuple(
                payload["allowed_fields"],
                "data.allowed_fields",
                pattern=_FIELD_RE,
            ),
            retention_days=payload["retention_days"],
            author_identifier_mode=AuthorIdentifierMode(
                payload.get("author_identifier_mode", "hmac_pseudonym")
            ),
            store_raw_response=payload.get("store_raw_response", False),
            allow_special_categories=payload.get(
                "allow_special_categories", False
            ),
            allow_biometric=payload.get("allow_biometric", False),
            allow_child_data=payload.get("allow_child_data", False),
            deletion_supported=payload.get("deletion_supported", True),
            purpose=payload["purpose"],
        )


@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    allowed_domains: tuple[str, ...]
    allowed_content_types: tuple[str, ...]
    max_response_bytes: int = 5_000_000
    max_redirects: int = 3
    https_only: bool = True
    block_private_networks: bool = True
    allow_subdomains: bool = False
    user_agent: str = "SOIKA-UDS/0.6 parser"
    credential_reference: str | None = None

    def __post_init__(self) -> None:
        domains = _string_tuple(self.allowed_domains, "security.allowed_domains")
        normalized_domains: list[str] = []
        for domain in domains:
            try:
                cleaned = domain.encode("idna").decode("ascii").lower().rstrip(".")
            except UnicodeError as error:
                raise SourcePolicyError(
                    f"security.allowed_domains contains invalid domain {domain!r}"
                ) from error
            if (
                "/" in cleaned
                or ":" in cleaned
                or cleaned.startswith(".")
                or cleaned.endswith(".")
            ):
                raise SourcePolicyError(
                    f"security.allowed_domains contains invalid domain {domain!r}"
                )
            try:
                ipaddress.ip_address(cleaned)
            except ValueError:
                pass
            else:
                raise SourcePolicyError(
                    "security.allowed_domains must contain DNS names, not IP literals"
                )
            normalized_domains.append(cleaned)
        object.__setattr__(self, "allowed_domains", tuple(normalized_domains))
        content_types = tuple(
            item.lower()
            for item in _string_tuple(
                self.allowed_content_types, "security.allowed_content_types"
            )
        )
        object.__setattr__(self, "allowed_content_types", content_types)
        if type(self.max_response_bytes) is not int or not (
            1_024 <= self.max_response_bytes <= 100_000_000
        ):
            raise SourcePolicyError(
                "security.max_response_bytes must be in [1024, 100000000]"
            )
        if type(self.max_redirects) is not int or not 0 <= self.max_redirects <= 5:
            raise SourcePolicyError("security.max_redirects must be in [0, 5]")
        object.__setattr__(
            self,
            "user_agent",
            _clean_required(self.user_agent, "security.user_agent"),
        )
        object.__setattr__(
            self,
            "credential_reference",
            _clean_optional(
                self.credential_reference, "security.credential_reference"
            ),
        )
        for field_name in (
            "https_only",
            "block_private_networks",
            "allow_subdomains",
        ):
            _require_bool(
                getattr(self, field_name),
                f"security.{field_name}",
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "allowed_domains": list(self.allowed_domains),
            "allowed_content_types": list(self.allowed_content_types),
            "max_response_bytes": self.max_response_bytes,
            "max_redirects": self.max_redirects,
            "https_only": self.https_only,
            "block_private_networks": self.block_private_networks,
            "allow_subdomains": self.allow_subdomains,
            "user_agent": self.user_agent,
        }
        if self.credential_reference is not None:
            payload["credential_reference"] = self.credential_reference
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SecurityPolicy:
        allowed = {
            "allowed_domains",
            "allowed_content_types",
            "max_response_bytes",
            "max_redirects",
            "https_only",
            "block_private_networks",
            "allow_subdomains",
            "user_agent",
            "credential_reference",
        }
        required = {"allowed_domains", "allowed_content_types"}
        unknown = sorted(set(payload) - allowed)
        missing = sorted(required - set(payload))
        if unknown:
            raise SourcePolicyError(
                f"security contains unknown fields: {', '.join(unknown)}"
            )
        if missing:
            raise SourcePolicyError(
                f"security is missing fields: {', '.join(missing)}"
            )
        return cls(
            allowed_domains=_string_tuple(
                payload["allowed_domains"], "security.allowed_domains"
            ),
            allowed_content_types=_string_tuple(
                payload["allowed_content_types"],
                "security.allowed_content_types",
            ),
            max_response_bytes=payload.get("max_response_bytes", 5_000_000),
            max_redirects=payload.get("max_redirects", 3),
            https_only=payload.get("https_only", True),
            block_private_networks=payload.get("block_private_networks", True),
            allow_subdomains=payload.get("allow_subdomains", False),
            user_agent=payload.get("user_agent", "SOIKA-UDS/0.6 parser"),
            credential_reference=payload.get("credential_reference"),
        )


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    source_id: str
    display_name: str
    owner: str
    access_method: AccessMethod
    permission_status: PermissionStatus
    jurisdictions: tuple[str, ...]
    legal_basis: str
    terms_url: str | None
    privacy_url: str | None
    official_docs_url: str | None
    robots_requirement: RobotsRequirement
    research: SourceResearchRecord
    permission: PermissionEvidence | None
    data: DataProtectionPolicy
    security: SecurityPolicy
    rate_limit: RateLimitPolicy
    allowed_purposes: tuple[str, ...]
    parser_version: str
    reviewed_at: datetime | None = None
    review_due_at: datetime | None = None
    notes: str | None = None
    enabled: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_id = _clean_required(self.source_id, "source_id").lower()
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise SourcePolicyError("source_id has invalid format")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(
            self, "display_name", _clean_required(self.display_name, "display_name")
        )
        object.__setattr__(self, "owner", _clean_required(self.owner, "owner"))
        if not isinstance(self.access_method, AccessMethod):
            object.__setattr__(
                self, "access_method", AccessMethod(self.access_method)
            )
        if not isinstance(self.permission_status, PermissionStatus):
            object.__setattr__(
                self,
                "permission_status",
                PermissionStatus(self.permission_status),
            )
        object.__setattr__(
            self,
            "jurisdictions",
            _string_tuple(self.jurisdictions, "jurisdictions"),
        )
        object.__setattr__(
            self, "legal_basis", _clean_required(self.legal_basis, "legal_basis")
        )
        for field_name in ("terms_url", "privacy_url", "official_docs_url"):
            object.__setattr__(
                self,
                field_name,
                _clean_optional(getattr(self, field_name), field_name),
            )
        if not isinstance(self.robots_requirement, RobotsRequirement):
            object.__setattr__(
                self,
                "robots_requirement",
                RobotsRequirement(self.robots_requirement),
            )
        if not isinstance(self.research, SourceResearchRecord):
            raise SourcePolicyError("research must be SourceResearchRecord")
        if self.permission is not None and not isinstance(
            self.permission, PermissionEvidence
        ):
            raise SourcePolicyError("permission must be PermissionEvidence")
        if not isinstance(self.data, DataProtectionPolicy):
            raise SourcePolicyError("data must be DataProtectionPolicy")
        if not isinstance(self.security, SecurityPolicy):
            raise SourcePolicyError("security must be SecurityPolicy")
        if not isinstance(self.rate_limit, RateLimitPolicy):
            raise SourcePolicyError("rate_limit must be RateLimitPolicy")
        object.__setattr__(
            self,
            "allowed_purposes",
            _string_tuple(self.allowed_purposes, "allowed_purposes"),
        )
        object.__setattr__(
            self,
            "parser_version",
            _clean_required(self.parser_version, "parser_version"),
        )
        if self.reviewed_at is not None:
            object.__setattr__(
                self,
                "reviewed_at",
                _parse_datetime(self.reviewed_at, "reviewed_at"),
            )
        if self.review_due_at is not None:
            due = _parse_datetime(self.review_due_at, "review_due_at")
            if self.reviewed_at is not None and due <= self.reviewed_at:
                raise SourcePolicyError(
                    "review_due_at must be later than reviewed_at"
                )
            object.__setattr__(self, "review_due_at", due)
        object.__setattr__(self, "notes", _clean_optional(self.notes, "notes"))
        object.__setattr__(self, "metadata", _immutable_json(self.metadata, "metadata"))
        _require_bool(self.enabled, "enabled")

        if (
            self.research.permission_required is RequirementDecision.UNKNOWN
            and self.permission_status is PermissionStatus.APPROVED
        ):
            raise SourcePolicyError(
                "approved source cannot have unknown permission requirement"
            )
        if self.access_method is AccessMethod.PUBLIC_WEB:
            if self.research.robots_url is None:
                raise SourcePolicyError(
                    "public_web sources require research.robots_url"
                )
            if self.robots_requirement is not RobotsRequirement.REQUIRED:
                raise SourcePolicyError(
                    "public_web sources must require robots evaluation"
                )
            if not self.security.allowed_domains:
                raise SourcePolicyError(
                    "public_web sources require an explicit domain allowlist"
                )
        elif self.robots_requirement is RobotsRequirement.REQUIRED:
            raise SourcePolicyError(
                "robots evaluation is only valid for public_web sources"
            )
        if self.access_method is AccessMethod.OFFICIAL_API:
            if self.official_docs_url is None:
                raise SourcePolicyError(
                    "official_api sources require official_docs_url"
                )
            if self.terms_url is None:
                raise SourcePolicyError("official_api sources require terms_url")
        if self.permission_status is PermissionStatus.APPROVED:
            if self.permission is None:
                raise SourcePolicyError(
                    "approved source requires permission evidence"
                )
            if self.reviewed_at is None or self.review_due_at is None:
                raise SourcePolicyError(
                    "approved source requires reviewed_at and review_due_at"
                )
        if self.enabled and self.permission_status is not PermissionStatus.APPROVED:
            raise SourcePolicyError("enabled source must be approved")
        if (
            self.data.store_raw_response
            and self.permission_status is not PermissionStatus.APPROVED
        ):
            raise SourcePolicyError(
                "raw response storage requires approved source policy"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "owner": self.owner,
            "access_method": self.access_method.value,
            "permission_status": self.permission_status.value,
            "jurisdictions": list(self.jurisdictions),
            "legal_basis": self.legal_basis,
            "robots_requirement": self.robots_requirement.value,
            "research": self.research.to_dict(),
            "data": self.data.to_dict(),
            "security": self.security.to_dict(),
            "rate_limit": self.rate_limit.to_dict(),
            "allowed_purposes": list(self.allowed_purposes),
            "parser_version": self.parser_version,
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }
        for field_name in ("terms_url", "privacy_url", "official_docs_url", "notes"):
            value = getattr(self, field_name)
            if value is not None:
                payload[field_name] = value
        if self.permission is not None:
            payload["permission"] = self.permission.to_dict()
        if self.reviewed_at is not None:
            payload["reviewed_at"] = _format_datetime(self.reviewed_at)
        if self.review_due_at is not None:
            payload["review_due_at"] = _format_datetime(self.review_due_at)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SourcePolicy:
        allowed = {
            "source_id",
            "display_name",
            "owner",
            "access_method",
            "permission_status",
            "jurisdictions",
            "legal_basis",
            "terms_url",
            "privacy_url",
            "official_docs_url",
            "robots_requirement",
            "research",
            "permission",
            "data",
            "security",
            "rate_limit",
            "allowed_purposes",
            "parser_version",
            "reviewed_at",
            "review_due_at",
            "notes",
            "enabled",
            "metadata",
        }
        required = {
            "source_id",
            "display_name",
            "owner",
            "access_method",
            "permission_status",
            "jurisdictions",
            "legal_basis",
            "robots_requirement",
            "research",
            "data",
            "security",
            "rate_limit",
            "allowed_purposes",
            "parser_version",
        }
        unknown = sorted(set(payload) - allowed)
        missing = sorted(required - set(payload))
        if unknown:
            raise SourcePolicyError(
                f"source policy contains unknown fields: {', '.join(unknown)}"
            )
        if missing:
            raise SourcePolicyError(
                f"source policy is missing fields: {', '.join(missing)}"
            )
        permission_payload = payload.get("permission")
        if permission_payload is not None and not isinstance(
            permission_payload, Mapping
        ):
            raise SourcePolicyError("permission must be an object")
        for nested_name in ("research", "data", "security", "rate_limit"):
            if not isinstance(payload[nested_name], Mapping):
                raise SourcePolicyError(f"{nested_name} must be an object")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise SourcePolicyError("metadata must be an object")
        return cls(
            source_id=payload["source_id"],
            display_name=payload["display_name"],
            owner=payload["owner"],
            access_method=AccessMethod(payload["access_method"]),
            permission_status=PermissionStatus(payload["permission_status"]),
            jurisdictions=_string_tuple(
                payload["jurisdictions"], "jurisdictions"
            ),
            legal_basis=payload["legal_basis"],
            terms_url=payload.get("terms_url"),
            privacy_url=payload.get("privacy_url"),
            official_docs_url=payload.get("official_docs_url"),
            robots_requirement=RobotsRequirement(payload["robots_requirement"]),
            research=SourceResearchRecord.from_dict(payload["research"]),
            permission=(
                PermissionEvidence.from_dict(permission_payload)
                if permission_payload is not None
                else None
            ),
            data=DataProtectionPolicy.from_dict(payload["data"]),
            security=SecurityPolicy.from_dict(payload["security"]),
            rate_limit=RateLimitPolicy.from_dict(payload["rate_limit"]),
            allowed_purposes=_string_tuple(
                payload["allowed_purposes"], "allowed_purposes"
            ),
            parser_version=payload["parser_version"],
            reviewed_at=(
                _parse_datetime(payload["reviewed_at"], "reviewed_at")
                if payload.get("reviewed_at") is not None
                else None
            ),
            review_due_at=(
                _parse_datetime(payload["review_due_at"], "review_due_at")
                if payload.get("review_due_at") is not None
                else None
            ),
            notes=payload.get("notes"),
            enabled=payload.get("enabled", False),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class ParserRequest:
    analysis_id: str
    source_id: str
    purpose: str
    territory: Mapping[str, Any]
    period_from: date | None = None
    period_to: date | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    max_pages: int = 100
    max_messages: int = 100_000

    def __post_init__(self) -> None:
        analysis_id = _clean_required(self.analysis_id, "analysis_id")
        source_id = _clean_required(self.source_id, "source_id").lower()
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise SourcePolicyError("source_id has invalid format")
        object.__setattr__(self, "analysis_id", analysis_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "purpose", _clean_required(self.purpose, "purpose"))
        if not isinstance(self.territory, Mapping):
            raise SourcePolicyError("territory must be an object")
        object.__setattr__(
            self, "territory", _immutable_json(self.territory, "territory")
        )
        if self.period_from is not None:
            object.__setattr__(
                self, "period_from", _parse_date(self.period_from, "period_from")
            )
        if self.period_to is not None:
            object.__setattr__(
                self, "period_to", _parse_date(self.period_to, "period_to")
            )
        if (
            self.period_from is not None
            and self.period_to is not None
            and self.period_from > self.period_to
        ):
            raise SourcePolicyError("period_from must not be later than period_to")
        if not isinstance(self.options, Mapping):
            raise SourcePolicyError("options must be an object")
        object.__setattr__(self, "options", _immutable_json(self.options, "options"))
        for field_name in ("max_pages", "max_messages"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise SourcePolicyError(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True)
class ParserPage:
    messages: tuple[Any, ...]
    next_checkpoint: Mapping[str, Any] | None
    done: bool
    raw_items_seen: int
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        if self.next_checkpoint is not None:
            if not isinstance(self.next_checkpoint, Mapping):
                raise SourcePolicyError("next_checkpoint must be an object")
            object.__setattr__(
                self,
                "next_checkpoint",
                _immutable_json(self.next_checkpoint, "next_checkpoint"),
            )
        if not isinstance(self.done, bool):
            raise SourcePolicyError("done must be a boolean")
        if type(self.raw_items_seen) is not int or self.raw_items_seen < 0:
            raise SourcePolicyError("raw_items_seen must be non-negative")
        object.__setattr__(
            self,
            "warnings",
            tuple(_clean_required(item, "warnings[]") for item in self.warnings),
        )
        if not self.done and self.next_checkpoint is None:
            raise SourcePolicyError(
                "non-terminal parser page requires next_checkpoint"
            )


@dataclass(frozen=True, slots=True)
class ComplianceContext:
    purpose: str
    robots_decision: RobotsDecision = RobotsDecision.NOT_APPLICABLE
    credential_available: bool = False
    permission_reference_available: bool = True
    current_time: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "purpose", _clean_required(self.purpose, "purpose"))
        if not isinstance(self.robots_decision, RobotsDecision):
            object.__setattr__(
                self,
                "robots_decision",
                RobotsDecision(self.robots_decision),
            )
        object.__setattr__(
            self,
            "current_time",
            _parse_datetime(self.current_time, "current_time"),
        )
        _require_bool(self.credential_available, "credential_available")
        _require_bool(
            self.permission_reference_available,
            "permission_reference_available",
        )


@dataclass(frozen=True, slots=True)
class ComplianceDecision:
    allowed: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True, slots=True)
class ParserCoverage:
    source_id: str
    status: ParserRunStatus
    pages_collected: int
    raw_items_seen: int
    messages_emitted: int
    duplicate_messages: int
    rejected_messages: int
    started_at: datetime
    finished_at: datetime
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "pages_collected",
            "raw_items_seen",
            "messages_emitted",
            "duplicate_messages",
            "rejected_messages",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise SourcePolicyError(f"{field_name} must be non-negative")
        object.__setattr__(
            self, "started_at", _parse_datetime(self.started_at, "started_at")
        )
        object.__setattr__(
            self, "finished_at", _parse_datetime(self.finished_at, "finished_at")
        )
        if self.finished_at < self.started_at:
            raise SourcePolicyError("finished_at must not precede started_at")
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "errors", tuple(self.errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status.value,
            "pages_collected": self.pages_collected,
            "raw_items_seen": self.raw_items_seen,
            "messages_emitted": self.messages_emitted,
            "duplicate_messages": self.duplicate_messages,
            "rejected_messages": self.rejected_messages,
            "started_at": _format_datetime(self.started_at),
            "finished_at": _format_datetime(self.finished_at),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class ParserRunResult:
    messages: tuple[Any, ...]
    coverage: ParserCoverage
    final_checkpoint: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        if self.final_checkpoint is not None:
            object.__setattr__(
                self,
                "final_checkpoint",
                _immutable_json(self.final_checkpoint, "final_checkpoint"),
            )


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: AuditEventType
    source_id: str
    analysis_id: str
    occurred_at: datetime
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, AuditEventType):
            object.__setattr__(
                self, "event_type", AuditEventType(self.event_type)
            )
        object.__setattr__(
            self, "source_id", _clean_required(self.source_id, "source_id")
        )
        object.__setattr__(
            self, "analysis_id", _clean_required(self.analysis_id, "analysis_id")
        )
        object.__setattr__(
            self,
            "occurred_at",
            _parse_datetime(self.occurred_at, "occurred_at"),
        )
        object.__setattr__(
            self, "details", _immutable_json(self.details, "audit.details")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "source_id": self.source_id,
            "analysis_id": self.analysis_id,
            "occurred_at": _format_datetime(self.occurred_at),
            "details": dict(self.details),
        }
