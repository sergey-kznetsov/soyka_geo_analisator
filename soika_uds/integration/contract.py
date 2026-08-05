"""Versioned transport-neutral contract for Geo Analyzer and SOIKA."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar

from ..contracts import CoverageSummary, JobStatus, TerritoryContext

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{15,199}$")


class ContractValidationError(ValueError):
    """Raised when a document does not satisfy the public contract."""


class IdempotencyConflictError(ContractValidationError):
    """Raised when one idempotency key is reused for another request."""


class MessageType(str, Enum):
    ANALYSIS_REQUEST = "analysis.request"
    JOB_STATUS = "analysis.status"
    ANALYSIS_RESULT = "analysis.result"


@dataclass(frozen=True, order=True, slots=True)
class ContractVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> ContractVersion:
        if not isinstance(value, str):
            raise ContractValidationError("contract_version must be a string")
        match = _VERSION_RE.fullmatch(value.strip())
        if match is None:
            raise ContractValidationError(
                "contract_version must use semantic version format MAJOR.MINOR.PATCH"
            )
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


CURRENT_CONTRACT_VERSION = ContractVersion(1, 0, 0)
SUPPORTED_CONTRACT_VERSIONS = (str(CURRENT_CONTRACT_VERSION),)


def ensure_supported_contract_version(value: str) -> str:
    parsed = ContractVersion.parse(value)
    if parsed != CURRENT_CONTRACT_VERSION:
        raise ContractValidationError(
            f"unsupported contract_version {parsed}; "
            f"supported versions: {', '.join(SUPPORTED_CONTRACT_VERSIONS)}"
        )
    return str(parsed)


def _clean_required(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ContractValidationError(f"{field_name} must not be empty")
    return cleaned


def _clean_optional(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _clean_required(value, field_name)


def _as_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{field_name} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ContractValidationError(f"{field_name} keys must be strings")
    return value


def _strict_keys(
    payload: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    context: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ContractValidationError(
            f"{context} contains unknown fields: {', '.join(unknown)}"
        )
    missing = sorted(required - set(payload))
    if missing:
        raise ContractValidationError(
            f"{context} is missing required fields: {', '.join(missing)}"
        )


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
            raise ContractValidationError(
                f"{field_name} must be an ISO 8601 datetime"
            ) from error
    else:
        raise ContractValidationError(f"{field_name} must be a datetime string")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    parsed = _parse_datetime(value, "datetime")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_date(value: object, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ContractValidationError(f"{field_name} must be a date, not datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be an ISO 8601 date")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise ContractValidationError(
            f"{field_name} must be an ISO 8601 date"
        ) from error


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError(f"{field_name} keys must be strings")
            normalized[key] = _json_value(item, f"{field_name}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ContractValidationError(
        f"{field_name} contains unsupported JSON value {type(value).__name__}"
    )


def _immutable_json_mapping(
    value: Mapping[str, Any] | None,
    field_name: str,
) -> Mapping[str, Any]:
    normalized = _json_value(value or {}, field_name)
    return MappingProxyType(normalized)


def canonical_json(payload: Mapping[str, Any]) -> str:
    normalized = _json_value(payload, "payload")
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def document_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ContractIssue:
    code: str
    message: str
    retryable: bool = False
    stage: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        code = _clean_required(self.code, "issue.code").upper()
        if _CODE_RE.fullmatch(code) is None:
            raise ContractValidationError(
                "issue.code must contain 3-64 uppercase letters, digits or underscores"
            )
        object.__setattr__(self, "code", code)
        object.__setattr__(
            self,
            "message",
            _clean_required(self.message, "issue.message"),
        )
        object.__setattr__(
            self,
            "stage",
            _clean_optional(self.stage, "issue.stage"),
        )
        object.__setattr__(
            self,
            "details",
            _immutable_json_mapping(self.details, "issue.details"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.stage is not None:
            payload["stage"] = self.stage
        if self.details:
            payload["details"] = dict(self.details)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ContractIssue:
        data = _as_mapping(payload, "issue")
        _strict_keys(
            data,
            allowed={"code", "message", "retryable", "stage", "details"},
            required={"code", "message"},
            context="issue",
        )
        retryable = data.get("retryable", False)
        if not isinstance(retryable, bool):
            raise ContractValidationError("issue.retryable must be a boolean")
        return cls(
            code=data["code"],
            message=data["message"],
            retryable=retryable,
            stage=data.get("stage"),
            details=_as_mapping(data.get("details", {}), "issue.details"),
        )


def _issues_from_value(value: object, field_name: str) -> tuple[ContractIssue, ...]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{field_name} must be an array")
    return tuple(
        ContractIssue.from_dict(_as_mapping(item, f"{field_name}[{index}]"))
        for index, item in enumerate(value)
    )


@dataclass(frozen=True, slots=True)
class AnalysisRequestV1:
    analysis_id: str
    requested_at: datetime
    territory: TerritoryContext
    sources: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)
    allow_partial: bool = True
    idempotency_key: str | None = None
    contract_version: str = str(CURRENT_CONTRACT_VERSION)

    message_type: ClassVar[MessageType] = MessageType.ANALYSIS_REQUEST

    def __post_init__(self) -> None:
        analysis_id = _clean_required(self.analysis_id, "analysis_id")
        object.__setattr__(self, "analysis_id", analysis_id)
        object.__setattr__(
            self,
            "contract_version",
            ensure_supported_contract_version(self.contract_version),
        )
        object.__setattr__(
            self,
            "requested_at",
            _parse_datetime(self.requested_at, "requested_at"),
        )
        if not isinstance(self.territory, TerritoryContext):
            raise ContractValidationError("territory must be a TerritoryContext")
        if self.territory.analysis_id != analysis_id:
            raise ContractValidationError(
                "territory.analysis_id must equal request analysis_id"
            )
        sources = tuple(_clean_required(source, "sources[]") for source in self.sources)
        if len(sources) != len(set(sources)):
            raise ContractValidationError("sources must not contain duplicates")
        object.__setattr__(self, "sources", sources)
        object.__setattr__(
            self,
            "options",
            _immutable_json_mapping(self.options, "options"),
        )
        if not isinstance(self.allow_partial, bool):
            raise ContractValidationError("allow_partial must be a boolean")
        if self.idempotency_key is not None:
            key = _clean_required(self.idempotency_key, "idempotency_key")
            if _IDEMPOTENCY_RE.fullmatch(key) is None:
                raise ContractValidationError(
                    "idempotency_key must contain 16-200 safe ASCII characters"
                )
            object.__setattr__(self, "idempotency_key", key)

    def semantic_payload(self) -> dict[str, Any]:
        payload = self.to_dict(include_idempotency_key=False)
        payload.pop("requested_at")
        return payload

    @property
    def fingerprint(self) -> str:
        return document_sha256(self.semantic_payload())

    @property
    def effective_idempotency_key(self) -> str:
        if self.idempotency_key is not None:
            return self.idempotency_key
        return f"soika-v1:{self.analysis_id}:{self.fingerprint}"

    def to_dict(self, *, include_idempotency_key: bool = True) -> dict[str, Any]:
        territory: dict[str, Any] = {"city": self.territory.city}
        if self.territory.address is not None:
            territory["address"] = self.territory.address
        if self.territory.latitude is not None:
            territory["point"] = {
                "latitude": self.territory.latitude,
                "longitude": self.territory.longitude,
            }
        if self.territory.radius_meters is not None:
            territory["radius_meters"] = self.territory.radius_meters
        if self.territory.territory_geojson is not None:
            territory["geometry"] = dict(self.territory.territory_geojson)

        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "message_type": self.message_type.value,
            "analysis_id": self.analysis_id,
            "requested_at": _format_datetime(self.requested_at),
            "territory": territory,
            "sources": list(self.sources),
            "options": dict(self.options),
            "allow_partial": self.allow_partial,
        }
        if self.territory.period_from is not None or self.territory.period_to is not None:
            period: dict[str, str] = {}
            if self.territory.period_from is not None:
                period["from"] = self.territory.period_from.isoformat()
            if self.territory.period_to is not None:
                period["to"] = self.territory.period_to.isoformat()
            payload["period"] = period
        if include_idempotency_key:
            payload["idempotency_key"] = self.effective_idempotency_key
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AnalysisRequestV1:
        data = _as_mapping(payload, "analysis request")
        _strict_keys(
            data,
            allowed={
                "contract_version",
                "message_type",
                "analysis_id",
                "requested_at",
                "idempotency_key",
                "territory",
                "period",
                "sources",
                "options",
                "allow_partial",
            },
            required={
                "contract_version",
                "message_type",
                "analysis_id",
                "requested_at",
                "territory",
            },
            context="analysis request",
        )
        if data["message_type"] != cls.message_type.value:
            raise ContractValidationError(
                f"message_type must be {cls.message_type.value}"
            )
        contract_version = ensure_supported_contract_version(
            data["contract_version"]
        )
        analysis_id = _clean_required(data["analysis_id"], "analysis_id")

        territory_data = _as_mapping(data["territory"], "territory")
        _strict_keys(
            territory_data,
            allowed={"city", "address", "point", "radius_meters", "geometry"},
            required={"city"},
            context="territory",
        )
        point = territory_data.get("point")
        latitude: float | None = None
        longitude: float | None = None
        if point is not None:
            point_data = _as_mapping(point, "territory.point")
            _strict_keys(
                point_data,
                allowed={"latitude", "longitude"},
                required={"latitude", "longitude"},
                context="territory.point",
            )
            latitude = point_data["latitude"]
            longitude = point_data["longitude"]

        period_from: date | None = None
        period_to: date | None = None
        if "period" in data:
            period_data = _as_mapping(data["period"], "period")
            _strict_keys(
                period_data,
                allowed={"from", "to"},
                required=set(),
                context="period",
            )
            if "from" in period_data:
                period_from = _parse_date(period_data["from"], "period.from")
            if "to" in period_data:
                period_to = _parse_date(period_data["to"], "period.to")

        sources_value = data.get("sources", [])
        if not isinstance(sources_value, list):
            raise ContractValidationError("sources must be an array")
        allow_partial = data.get("allow_partial", True)
        if not isinstance(allow_partial, bool):
            raise ContractValidationError("allow_partial must be a boolean")

        territory = TerritoryContext(
            analysis_id=analysis_id,
            city=territory_data["city"],
            address=territory_data.get("address"),
            latitude=latitude,
            longitude=longitude,
            radius_meters=territory_data.get("radius_meters"),
            territory_geojson=territory_data.get("geometry"),
            period_from=period_from,
            period_to=period_to,
            sources=tuple(sources_value),
            options=_as_mapping(data.get("options", {}), "options"),
        )
        return cls(
            analysis_id=analysis_id,
            requested_at=_parse_datetime(data["requested_at"], "requested_at"),
            territory=territory,
            sources=tuple(sources_value),
            options=_as_mapping(data.get("options", {}), "options"),
            allow_partial=allow_partial,
            idempotency_key=data.get("idempotency_key"),
            contract_version=contract_version,
        )


def assert_idempotent_request(
    request: AnalysisRequestV1,
    *,
    stored_idempotency_key: str,
    stored_fingerprint: str,
) -> None:
    if request.effective_idempotency_key != stored_idempotency_key:
        raise IdempotencyConflictError("idempotency key does not match stored request")
    if request.fingerprint != stored_fingerprint:
        raise IdempotencyConflictError(
            "idempotency key was reused with a different semantic request"
        )


@dataclass(frozen=True, slots=True)
class JobStatusV1:
    analysis_id: str
    status: JobStatus
    updated_at: datetime
    progress_percent: int
    stage: str
    message: str | None = None
    attempt: int = 1
    processed_items: int | None = None
    total_items: int | None = None
    warnings: tuple[ContractIssue, ...] = ()
    errors: tuple[ContractIssue, ...] = ()
    contract_version: str = str(CURRENT_CONTRACT_VERSION)

    message_type: ClassVar[MessageType] = MessageType.JOB_STATUS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "analysis_id",
            _clean_required(self.analysis_id, "analysis_id"),
        )
        object.__setattr__(
            self,
            "contract_version",
            ensure_supported_contract_version(self.contract_version),
        )
        if not isinstance(self.status, JobStatus):
            try:
                object.__setattr__(self, "status", JobStatus(self.status))
            except ValueError as error:
                raise ContractValidationError("status is not supported") from error
        object.__setattr__(
            self,
            "updated_at",
            _parse_datetime(self.updated_at, "updated_at"),
        )
        if not isinstance(self.progress_percent, int):
            raise ContractValidationError("progress_percent must be an integer")
        if not 0 <= self.progress_percent <= 100:
            raise ContractValidationError("progress_percent must be in [0, 100]")
        object.__setattr__(self, "stage", _clean_required(self.stage, "stage"))
        object.__setattr__(
            self,
            "message",
            _clean_optional(self.message, "message"),
        )
        if not isinstance(self.attempt, int) or self.attempt < 1:
            raise ContractValidationError("attempt must be a positive integer")
        for name in ("processed_items", "total_items"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ContractValidationError(f"{name} must be a non-negative integer")
        if (
            self.processed_items is not None
            and self.total_items is not None
            and self.processed_items > self.total_items
        ):
            raise ContractValidationError(
                "processed_items cannot exceed total_items"
            )
        if self.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS}:
            if self.progress_percent != 100:
                raise ContractValidationError(
                    "completed status requires progress_percent equal to 100"
                )
        if self.status is JobStatus.FAILED and not self.errors:
            raise ContractValidationError("failed status requires at least one error")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "message_type": self.message_type.value,
            "analysis_id": self.analysis_id,
            "status": self.status.value,
            "updated_at": _format_datetime(self.updated_at),
            "progress_percent": self.progress_percent,
            "stage": self.stage,
            "attempt": self.attempt,
            "warnings": [issue.to_dict() for issue in self.warnings],
            "errors": [issue.to_dict() for issue in self.errors],
        }
        if self.message is not None:
            payload["message"] = self.message
        if self.processed_items is not None:
            payload["processed_items"] = self.processed_items
        if self.total_items is not None:
            payload["total_items"] = self.total_items
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> JobStatusV1:
        data = _as_mapping(payload, "job status")
        _strict_keys(
            data,
            allowed={
                "contract_version",
                "message_type",
                "analysis_id",
                "status",
                "updated_at",
                "progress_percent",
                "stage",
                "message",
                "attempt",
                "processed_items",
                "total_items",
                "warnings",
                "errors",
            },
            required={
                "contract_version",
                "message_type",
                "analysis_id",
                "status",
                "updated_at",
                "progress_percent",
                "stage",
            },
            context="job status",
        )
        if data["message_type"] != cls.message_type.value:
            raise ContractValidationError(
                f"message_type must be {cls.message_type.value}"
            )
        try:
            status = JobStatus(data["status"])
        except (TypeError, ValueError) as error:
            raise ContractValidationError("status is not supported") from error
        return cls(
            analysis_id=data["analysis_id"],
            status=status,
            updated_at=_parse_datetime(data["updated_at"], "updated_at"),
            progress_percent=data["progress_percent"],
            stage=data["stage"],
            message=data.get("message"),
            attempt=data.get("attempt", 1),
            processed_items=data.get("processed_items"),
            total_items=data.get("total_items"),
            warnings=_issues_from_value(data.get("warnings", []), "warnings"),
            errors=_issues_from_value(data.get("errors", []), "errors"),
            contract_version=data["contract_version"],
        )


@dataclass(frozen=True, slots=True)
class ResultProvenance:
    soika_version: str
    schema_digest: str
    models: Mapping[str, str] = field(default_factory=dict)
    algorithms: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "soika_version",
            _clean_required(self.soika_version, "provenance.soika_version"),
        )
        digest = _clean_required(self.schema_digest, "provenance.schema_digest")
        if re.fullmatch(r"^[a-f0-9]{64}$", digest) is None:
            raise ContractValidationError(
                "provenance.schema_digest must be a lowercase SHA-256 digest"
            )
        object.__setattr__(self, "schema_digest", digest)
        for field_name in ("models", "algorithms"):
            raw = _as_mapping(getattr(self, field_name), f"provenance.{field_name}")
            normalized = {
                _clean_required(key, f"provenance.{field_name}.key"): _clean_required(
                    value,
                    f"provenance.{field_name}.{key}",
                )
                for key, value in raw.items()
            }
            object.__setattr__(self, field_name, MappingProxyType(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {
            "soika_version": self.soika_version,
            "schema_digest": self.schema_digest,
            "models": dict(self.models),
            "algorithms": dict(self.algorithms),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResultProvenance:
        data = _as_mapping(payload, "provenance")
        _strict_keys(
            data,
            allowed={"soika_version", "schema_digest", "models", "algorithms"},
            required={"soika_version", "schema_digest"},
            context="provenance",
        )
        return cls(
            soika_version=data["soika_version"],
            schema_digest=data["schema_digest"],
            models=_as_mapping(data.get("models", {}), "provenance.models"),
            algorithms=_as_mapping(
                data.get("algorithms", {}),
                "provenance.algorithms",
            ),
        )


def _mapping_tuple(value: object, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{field_name} must be an array")
    return tuple(
        MappingProxyType(
            _json_value(
                _as_mapping(item, f"{field_name}[{index}]"),
                f"{field_name}[{index}]",
            )
        )
        for index, item in enumerate(value)
    )


def _coverage_from_dict(payload: Mapping[str, Any]) -> CoverageSummary:
    data = _as_mapping(payload, "coverage")
    allowed = {
        "sources_requested",
        "sources_available",
        "messages_collected",
        "messages_relevant",
        "messages_geocoded",
        "messages_low_confidence",
    }
    _strict_keys(data, allowed=allowed, required=set(), context="coverage")
    return CoverageSummary(**{key: data.get(key, 0) for key in allowed})


@dataclass(frozen=True, slots=True)
class AnalysisResultV1:
    analysis_id: str
    status: JobStatus
    generated_at: datetime
    provenance: ResultProvenance
    coverage: CoverageSummary = field(default_factory=CoverageSummary)
    categories: tuple[Mapping[str, Any], ...] = ()
    topics: tuple[Mapping[str, Any], ...] = ()
    events: tuple[Mapping[str, Any], ...] = ()
    connections: tuple[Mapping[str, Any], ...] = ()
    timeline: tuple[Mapping[str, Any], ...] = ()
    messages: tuple[Mapping[str, Any], ...] = ()
    risk_summary: Mapping[str, Any] = field(default_factory=dict)
    geojson: Mapping[str, Any] = field(
        default_factory=lambda: {"type": "FeatureCollection", "features": []}
    )
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[ContractIssue, ...] = ()
    errors: tuple[ContractIssue, ...] = ()
    partial: bool = False
    contract_version: str = str(CURRENT_CONTRACT_VERSION)

    message_type: ClassVar[MessageType] = MessageType.ANALYSIS_RESULT

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "analysis_id",
            _clean_required(self.analysis_id, "analysis_id"),
        )
        object.__setattr__(
            self,
            "contract_version",
            ensure_supported_contract_version(self.contract_version),
        )
        if not isinstance(self.status, JobStatus):
            try:
                object.__setattr__(self, "status", JobStatus(self.status))
            except ValueError as error:
                raise ContractValidationError("status is not supported") from error
        terminal = {
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        if self.status not in terminal:
            raise ContractValidationError("analysis result requires a terminal status")
        object.__setattr__(
            self,
            "generated_at",
            _parse_datetime(self.generated_at, "generated_at"),
        )
        if not isinstance(self.provenance, ResultProvenance):
            raise ContractValidationError("provenance must be ResultProvenance")
        if self.status is JobStatus.COMPLETED and self.errors:
            raise ContractValidationError("completed result cannot contain errors")
        if self.status is JobStatus.FAILED and not self.errors:
            raise ContractValidationError("failed result requires at least one error")
        if not isinstance(self.partial, bool):
            raise ContractValidationError("partial must be a boolean")
        if self.status is JobStatus.COMPLETED and self.partial:
            raise ContractValidationError("completed result cannot be partial")
        for field_name in (
            "categories",
            "topics",
            "events",
            "connections",
            "timeline",
            "messages",
        ):
            normalized = tuple(
                MappingProxyType(_json_value(item, field_name))
                for item in getattr(self, field_name)
            )
            object.__setattr__(self, field_name, normalized)
        object.__setattr__(
            self,
            "risk_summary",
            _immutable_json_mapping(self.risk_summary, "risk_summary"),
        )
        geojson = _immutable_json_mapping(self.geojson, "geojson")
        if geojson.get("type") != "FeatureCollection":
            raise ContractValidationError("geojson.type must be FeatureCollection")
        if not isinstance(geojson.get("features"), list):
            raise ContractValidationError("geojson.features must be an array")
        object.__setattr__(self, "geojson", geojson)
        object.__setattr__(
            self,
            "metadata",
            _immutable_json_mapping(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "message_type": self.message_type.value,
            "analysis_id": self.analysis_id,
            "status": self.status.value,
            "generated_at": _format_datetime(self.generated_at),
            "partial": self.partial,
            "coverage": {
                field_name: getattr(self.coverage, field_name)
                for field_name in self.coverage.__dataclass_fields__
            },
            "categories": [dict(item) for item in self.categories],
            "topics": [dict(item) for item in self.topics],
            "events": [dict(item) for item in self.events],
            "connections": [dict(item) for item in self.connections],
            "timeline": [dict(item) for item in self.timeline],
            "messages": [dict(item) for item in self.messages],
            "risk_summary": dict(self.risk_summary),
            "geojson": dict(self.geojson),
            "metadata": dict(self.metadata),
            "warnings": [issue.to_dict() for issue in self.warnings],
            "errors": [issue.to_dict() for issue in self.errors],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AnalysisResultV1:
        data = _as_mapping(payload, "analysis result")
        allowed = {
            "contract_version",
            "message_type",
            "analysis_id",
            "status",
            "generated_at",
            "partial",
            "coverage",
            "categories",
            "topics",
            "events",
            "connections",
            "timeline",
            "messages",
            "risk_summary",
            "geojson",
            "metadata",
            "warnings",
            "errors",
            "provenance",
        }
        required = {
            "contract_version",
            "message_type",
            "analysis_id",
            "status",
            "generated_at",
            "coverage",
            "provenance",
        }
        _strict_keys(data, allowed=allowed, required=required, context="analysis result")
        if data["message_type"] != cls.message_type.value:
            raise ContractValidationError(
                f"message_type must be {cls.message_type.value}"
            )
        try:
            status = JobStatus(data["status"])
        except (TypeError, ValueError) as error:
            raise ContractValidationError("status is not supported") from error
        partial = data.get("partial", False)
        if not isinstance(partial, bool):
            raise ContractValidationError("partial must be a boolean")
        return cls(
            analysis_id=data["analysis_id"],
            status=status,
            generated_at=_parse_datetime(data["generated_at"], "generated_at"),
            provenance=ResultProvenance.from_dict(
                _as_mapping(data["provenance"], "provenance")
            ),
            coverage=_coverage_from_dict(
                _as_mapping(data["coverage"], "coverage")
            ),
            categories=_mapping_tuple(data.get("categories", []), "categories"),
            topics=_mapping_tuple(data.get("topics", []), "topics"),
            events=_mapping_tuple(data.get("events", []), "events"),
            connections=_mapping_tuple(
                data.get("connections", []),
                "connections",
            ),
            timeline=_mapping_tuple(data.get("timeline", []), "timeline"),
            messages=_mapping_tuple(data.get("messages", []), "messages"),
            risk_summary=_as_mapping(
                data.get("risk_summary", {}),
                "risk_summary",
            ),
            geojson=_as_mapping(
                data.get("geojson", {"type": "FeatureCollection", "features": []}),
                "geojson",
            ),
            metadata=_as_mapping(data.get("metadata", {}), "metadata"),
            warnings=_issues_from_value(data.get("warnings", []), "warnings"),
            errors=_issues_from_value(data.get("errors", []), "errors"),
            partial=partial,
            contract_version=data["contract_version"],
        )


ContractDocument = AnalysisRequestV1 | JobStatusV1 | AnalysisResultV1


def parse_contract_document(payload: Mapping[str, Any]) -> ContractDocument:
    data = _as_mapping(payload, "contract document")
    message_type = data.get("message_type")
    if message_type == MessageType.ANALYSIS_REQUEST.value:
        return AnalysisRequestV1.from_dict(data)
    if message_type == MessageType.JOB_STATUS.value:
        return JobStatusV1.from_dict(data)
    if message_type == MessageType.ANALYSIS_RESULT.value:
        return AnalysisResultV1.from_dict(data)
    raise ContractValidationError("message_type is missing or unsupported")
