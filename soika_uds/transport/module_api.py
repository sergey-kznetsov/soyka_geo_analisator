"""Stable server-to-server module protocol adapter for Geo Analyzer.

The adapter deliberately translates the generic Geo Analyzer module protocol into
SOIKA's existing transport-neutral orchestration contract. Geo Analyzer never
needs to import SOIKA Python code or know about its internal pipeline stages.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..contracts import JobStatus, TerritoryContext
from ..integration import (
    AnalysisRequestV1,
    ContractValidationError,
    IdempotencyConflictError,
    ResultProvenance,
)
from ..orchestration import JobNotFoundError, OrchestrationError, SoikaOrchestrator
from ..worker import ComputeClass, WorkerControl

MODULE_PROTOCOL_VERSION = "1.0.0"
SOIKA_MODULE_ID = "soyka.reviews"
SOIKA_MODULE_VERSION = "0.20.0"


class ModuleProtocolError(ValueError):
    """Raised when a Geo Analyzer module request violates the public protocol."""


class ModuleConflictError(ModuleProtocolError):
    """Raised when an otherwise valid request conflicts with persisted state."""


class ModuleResultNotReadyError(ModuleProtocolError):
    """Raised when a caller asks for a result before the job terminates."""


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModuleProtocolError(f"{field_name} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ModuleProtocolError(f"{field_name} keys must be strings")
    return value


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ModuleProtocolError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ModuleProtocolError(f"{field_name} must not be empty")
    return cleaned


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ModuleProtocolError(f"{field_name} must be an ISO 8601 datetime")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ModuleProtocolError(
            f"{field_name} must be an ISO 8601 datetime"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ModuleProtocolError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _protocol_header(payload: Mapping[str, Any]) -> None:
    protocol_version = _required_text(
        payload.get("protocol_version"), "protocol_version"
    )
    if protocol_version != MODULE_PROTOCOL_VERSION:
        raise ModuleProtocolError(
            f"unsupported protocol_version {protocol_version}; "
            f"expected {MODULE_PROTOCOL_VERSION}"
        )
    module_id = _required_text(payload.get("module_id"), "module_id")
    if module_id != SOIKA_MODULE_ID:
        raise ModuleProtocolError(
            f"request targets module {module_id!r}, expected {SOIKA_MODULE_ID!r}"
        )


def _public_status(status: JobStatus) -> str:
    if status is JobStatus.QUEUED:
        return "queued"
    if status is JobStatus.COMPLETED:
        return "completed"
    if status is JobStatus.COMPLETED_WITH_WARNINGS:
        return "completed_with_warnings"
    if status is JobStatus.FAILED:
        return "failed"
    if status is JobStatus.CANCELLED:
        return "cancelled"
    return "running"


def _safe_cell(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _records_section(
    *,
    title: str,
    sheet_name: str,
    records: object,
) -> dict[str, Any] | None:
    if not isinstance(records, list) or not records:
        return None
    normalized = [item for item in records if isinstance(item, Mapping)]
    if not normalized:
        return None
    columns: list[str] = []
    for item in normalized[:100]:
        for key in item:
            if isinstance(key, str) and key not in columns:
                columns.append(key)
            if len(columns) >= 32:
                break
        if len(columns) >= 32:
            break
    rows = [
        {column: _safe_cell(item.get(column)) for column in columns}
        for item in normalized
    ]
    return {
        "section_id": sheet_name.lower().replace(" ", "_"),
        "title": title,
        "sheet_name": sheet_name[:31],
        "columns": columns,
        "rows": rows,
    }


class SoikaModuleApi:
    """Translate the generic module protocol to ``WorkerControl`` operations."""

    def __init__(
        self,
        control: WorkerControl,
        *,
        provenance: ResultProvenance,
        module_version: str = SOIKA_MODULE_VERSION,
    ) -> None:
        if not isinstance(control, WorkerControl):
            raise TypeError("control must be WorkerControl")
        if not isinstance(provenance, ResultProvenance):
            raise TypeError("provenance must be ResultProvenance")
        self.control = control
        self.orchestrator: SoikaOrchestrator = control.orchestrator
        self.provenance = provenance
        self.module_version = _required_text(module_version, "module_version")

    def manifest(self) -> dict[str, Any]:
        return {
            "protocol_version": MODULE_PROTOCOL_VERSION,
            "module_id": SOIKA_MODULE_ID,
            "display_name": "Анализ отзывов через Сойку",
            "description": (
                "Сбор и анализ городских отзывов с классификацией, "
                "геолокацией, событиями и оценкой риска."
            ),
            "module_version": self.module_version,
            "capabilities": [
                "analysis.submit",
                "analysis.status",
                "analysis.cancel",
                "analysis.retry",
                "analysis.result",
            ],
            "result_formats": ["json", "geojson"],
            "supports_partial_result": True,
            "supports_warnings": True,
            "ui": {
                "optional": True,
                "default_enabled": False,
                "analysis_launch_toggle": True,
                "capability_card": True,
            },
        }

    def health(self) -> dict[str, Any]:
        checker = getattr(self.control.queue, "healthcheck", None)
        healthy = bool(checker()) if callable(checker) else True
        return {
            "protocol_version": MODULE_PROTOCOL_VERSION,
            "module_id": SOIKA_MODULE_ID,
            "status": "ok" if healthy else "unavailable",
        }

    def submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = _mapping(payload, "request")
        _protocol_header(data)
        analysis_id = _required_text(data.get("analysis_id"), "analysis_id")
        requested_at = _parse_datetime(data.get("requested_at"), "requested_at")
        territory_data = _mapping(data.get("territory"), "territory")
        city = _required_text(territory_data.get("city"), "territory.city")
        address = _optional_text(territory_data.get("address"), "territory.address")

        latitude: float | None = None
        longitude: float | None = None
        point = territory_data.get("point")
        if point is not None:
            point_data = _mapping(point, "territory.point")
            latitude = point_data.get("latitude")
            longitude = point_data.get("longitude")
            if isinstance(latitude, bool) or not isinstance(latitude, int | float):
                raise ModuleProtocolError("territory.point.latitude must be a number")
            if isinstance(longitude, bool) or not isinstance(longitude, int | float):
                raise ModuleProtocolError("territory.point.longitude must be a number")
            latitude = float(latitude)
            longitude = float(longitude)

        radius_meters = territory_data.get("radius_meters")
        if radius_meters is not None and (
            isinstance(radius_meters, bool) or not isinstance(radius_meters, int)
        ):
            raise ModuleProtocolError("territory.radius_meters must be an integer")

        geometry = territory_data.get("geometry")
        territory_geojson = (
            dict(_mapping(geometry, "territory.geometry"))
            if geometry is not None
            else None
        )
        options = dict(_mapping(data.get("options", {}), "options"))
        sources_value = data.get("sources", [])
        if not isinstance(sources_value, list) or not all(
            isinstance(item, str) and item.strip() for item in sources_value
        ):
            raise ModuleProtocolError("sources must be an array of non-empty strings")
        allow_partial = data.get("allow_partial", True)
        if not isinstance(allow_partial, bool):
            raise ModuleProtocolError("allow_partial must be a boolean")
        idempotency_key = _required_text(
            data.get("idempotency_key"), "idempotency_key"
        )

        try:
            territory = TerritoryContext(
                analysis_id=analysis_id,
                city=city,
                address=address,
                latitude=latitude,
                longitude=longitude,
                radius_meters=radius_meters,
                territory_geojson=territory_geojson,
                sources=tuple(item.strip() for item in sources_value),
                options=options,
            )
            request = AnalysisRequestV1(
                analysis_id=analysis_id,
                requested_at=requested_at,
                territory=territory,
                sources=tuple(item.strip() for item in sources_value),
                options=options,
                allow_partial=allow_partial,
                idempotency_key=idempotency_key,
            )
        except (ContractValidationError, ValueError) as error:
            raise ModuleProtocolError(str(error)) from error

        try:
            record, _queue_item = self.control.submit(
                request,
                compute_class=ComputeClass.CPU,
            )
        except IdempotencyConflictError as error:
            raise ModuleConflictError(str(error)) from error
        return self.status(record.analysis_id)

    def status(self, analysis_id: str) -> dict[str, Any]:
        status = self.orchestrator.status(_required_text(analysis_id, "analysis_id"))
        raw = status.to_dict()
        return {
            "protocol_version": MODULE_PROTOCOL_VERSION,
            "module_id": SOIKA_MODULE_ID,
            "module_version": self.module_version,
            "analysis_id": raw["analysis_id"],
            "status": _public_status(status.status),
            "raw_status": raw["status"],
            "updated_at": raw["updated_at"],
            "progress_percent": raw["progress_percent"],
            "stage": raw["stage"],
            "attempt": raw["attempt"],
            "warnings": raw.get("warnings", []),
            "errors": raw.get("errors", []),
        }

    def cancel(self, analysis_id: str) -> dict[str, Any]:
        record = self.control.cancel(_required_text(analysis_id, "analysis_id"))
        return self.status(record.analysis_id)

    def retry(self, analysis_id: str) -> dict[str, Any]:
        record = self.control.retry(_required_text(analysis_id, "analysis_id"))
        return self.status(record.analysis_id)

    def result(self, analysis_id: str) -> dict[str, Any]:
        analysis_id = _required_text(analysis_id, "analysis_id")
        try:
            result = self.orchestrator.materialize_result(
                analysis_id,
                self.provenance,
            )
        except JobNotFoundError:
            raise
        except OrchestrationError as error:
            raise ModuleResultNotReadyError(str(error)) from error
        raw = result.to_dict()
        result_payload = {
            key: raw.get(key)
            for key in (
                "categories",
                "topics",
                "events",
                "connections",
                "timeline",
                "messages",
                "risk_summary",
                "metadata",
                "provenance",
            )
        }
        coverage = raw.get("coverage", {})
        summary_rows = [
            {"Показатель": "Статус", "Значение": raw.get("status")},
            {"Показатель": "Частичный результат", "Значение": bool(raw.get("partial"))},
        ]
        if isinstance(coverage, Mapping):
            summary_rows.extend(
                {"Показатель": key, "Значение": value}
                for key, value in coverage.items()
            )
        summary_rows.extend(
            [
                {"Показатель": "Предупреждений", "Значение": len(raw.get("warnings", []))},
                {"Показатель": "Ошибок", "Значение": len(raw.get("errors", []))},
            ]
        )
        sections: list[dict[str, Any]] = [
            {
                "section_id": "soyka_summary",
                "title": "СОЙКА — сводка",
                "sheet_name": "СОЙКА",
                "columns": ["Показатель", "Значение"],
                "rows": summary_rows,
            }
        ]
        category_section = _records_section(
            title="СОЙКА — категории",
            sheet_name="СОЙКА категории",
            records=raw.get("categories"),
        )
        if category_section is not None:
            sections.append(category_section)

        return {
            "protocol_version": MODULE_PROTOCOL_VERSION,
            "module_id": SOIKA_MODULE_ID,
            "module_version": self.module_version,
            "analysis_id": raw["analysis_id"],
            "status": _public_status(result.status),
            "raw_status": raw["status"],
            "generated_at": raw["generated_at"],
            "partial": bool(raw.get("partial", False)),
            "coverage": coverage,
            "warnings": raw.get("warnings", []),
            "errors": raw.get("errors", []),
            "result": result_payload,
            "geojson": raw.get(
                "geojson",
                {"type": "FeatureCollection", "features": []},
            ),
            "report_sections": sections,
        }


__all__ = [
    "MODULE_PROTOCOL_VERSION",
    "SOIKA_MODULE_ID",
    "SOIKA_MODULE_VERSION",
    "ModuleConflictError",
    "ModuleProtocolError",
    "ModuleResultNotReadyError",
    "SoikaModuleApi",
]
