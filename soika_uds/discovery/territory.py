"""Resolve the target address into a geographic scope before any source search."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts import TerritoryContext
from ..geolocation.models import GeolocationBatchResult, LocationKind
from .models import GeoScope, SourceReasonCode


class TerritoryResolutionError(RuntimeError):
    def __init__(self, code: SourceReasonCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class TerritoryGeolocationEngine(Protocol):
    def geolocate(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        city: str | None = None,
    ) -> GeolocationBatchResult: ...


def _first_text(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _same_place(left: str, right: str) -> bool:
    normalize = lambda value: value.casefold().replace("ё", "е").strip()
    return normalize(left) == normalize(right)


@dataclass(frozen=True, slots=True)
class TerritoryResolver:
    """Use the qualified geocoder to make address geography authoritative."""

    engine: TerritoryGeolocationEngine

    def resolve(self, territory: TerritoryContext) -> GeoScope:
        if not isinstance(territory, TerritoryContext):
            raise TypeError("territory must be TerritoryContext")
        if not territory.address:
            raise TerritoryResolutionError(
                SourceReasonCode.TERRITORY_UNRESOLVED,
                "geo-first discovery currently requires an address; coordinate-only reverse resolution is not configured",
            )

        message = {
            "message_key": f"territory:{territory.analysis_id}",
            "model_text": territory.address,
            "included_for_analysis": True,
        }
        result = self.engine.geolocate((message,), city=None)
        if not result.results:
            raise TerritoryResolutionError(
                SourceReasonCode.TERRITORY_UNRESOLVED,
                "target address produced no geolocation result",
            )
        resolved = result.results[0]
        selected = resolved.selected
        if selected is None or not resolved.included_for_analysis:
            reason = ", ".join(resolved.reasons) or "geocoding candidate unavailable"
            raise TerritoryResolutionError(
                SourceReasonCode.TERRITORY_UNRESOLVED,
                f"target address could not be resolved with qualified precision: {reason}",
            )
        if resolved.mention is not None and resolved.mention.kind is LocationKind.HOUSE:
            if selected.kind is not LocationKind.HOUSE:
                raise TerritoryResolutionError(
                    SourceReasonCode.TERRITORY_UNRESOLVED,
                    "house-level input did not resolve to a house-level candidate",
                )

        address = selected.address
        city = _first_text(
            address,
            ("city", "town", "municipality", "village", "hamlet"),
        ) or territory.city
        region = _first_text(
            address,
            ("state", "region", "state_district"),
        )
        district = _first_text(
            address,
            ("city_district", "district", "borough", "suburb", "quarter"),
        )
        street = (
            resolved.mention.street
            if resolved.mention is not None and resolved.mention.street
            else _first_text(address, ("road", "pedestrian", "residential"))
        )
        house = (
            resolved.mention.house_number
            if resolved.mention is not None and resolved.mention.house_number
            else _first_text(address, ("house_number",))
        )
        aliases: list[str] = [territory.address, selected.label]
        if street:
            aliases.append(street)
            if house:
                aliases.append(f"{street} {house}")
        if city:
            aliases.append(city)
        if region:
            aliases.append(region)

        metadata: dict[str, Any] = {
            "requested_city_hint": territory.city,
            "city_hint_matches_resolved_city": _same_place(territory.city, city),
            "geolocation_reasons": list(resolved.reasons),
            "geolocation_provenance": dict(resolved.provenance),
            "candidate_address": dict(address),
        }
        if territory.latitude is not None:
            metadata["request_point"] = {
                "longitude": territory.longitude,
                "latitude": territory.latitude,
            }

        return GeoScope(
            raw_address=territory.address,
            city=city,
            region=region,
            district=district,
            street=street,
            house_number=house,
            longitude=selected.point.longitude,
            latitude=selected.point.latitude,
            precision=selected.kind.value,
            confidence=resolved.confidence,
            candidate_id=selected.candidate_id,
            label=selected.label,
            osm_type=selected.osm_type,
            osm_id=selected.osm_id,
            aliases=tuple(aliases),
            metadata=metadata,
        )


__all__ = [
    "TerritoryGeolocationEngine",
    "TerritoryResolutionError",
    "TerritoryResolver",
]
