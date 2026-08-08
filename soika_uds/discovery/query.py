"""Deterministic Russian query construction for geo-first source discovery."""

from __future__ import annotations

from dataclasses import dataclass

from .models import DiscoveryQuery, GeoScope, SourceKind


def _quote(value: str) -> str:
    cleaned = " ".join(value.replace('"', " ").split())
    return f'"{cleaned}"'


def _deduplicate(items: list[DiscoveryQuery], limit: int) -> tuple[DiscoveryQuery, ...]:
    unique: dict[str, DiscoveryQuery] = {}
    for item in items:
        key = item.text.casefold()
        unique.setdefault(key, item)
        if len(unique) >= limit:
            break
    return tuple(unique.values())


@dataclass(frozen=True, slots=True)
class GeoQueryBuilder:
    """Create bounded address/city queries; discovery remains region-first."""

    max_queries: int = 32

    def __post_init__(self) -> None:
        if not isinstance(self.max_queries, int) or not 8 <= self.max_queries <= 128:
            raise ValueError("max_queries must be in [8, 128]")

    def build(self, scope: GeoScope) -> tuple[DiscoveryQuery, ...]:
        city = scope.city
        region = scope.region
        address = scope.raw_address
        street = scope.street
        house = scope.house_number
        exact_place = " ".join(part for part in (street, house) if part)
        location = exact_place or address

        queries: list[DiscoveryQuery] = [
            DiscoveryQuery(
                text=f"{_quote(location)} {city}",
                purpose="exact_address_web",
            ),
            DiscoveryQuery(
                text=f"{_quote(location)} {city} отзывы",
                purpose="exact_address_reviews",
            ),
            DiscoveryQuery(
                text=f"{_quote(location)} {city} новости",
                purpose="exact_address_news",
                target_kind=SourceKind.LOCAL_MEDIA,
            ),
            DiscoveryQuery(
                text=f"{city} новости городские СМИ",
                purpose="local_media_discovery",
                target_kind=SourceKind.LOCAL_MEDIA,
            ),
            DiscoveryQuery(
                text=f"{city} администрация новости портал",
                purpose="municipal_discovery",
                target_kind=SourceKind.MUNICIPAL,
            ),
            DiscoveryQuery(
                text=f"{city} городской форум жители",
                purpose="local_forum_discovery",
                target_kind=SourceKind.LOCAL_FORUM,
            ),
            DiscoveryQuery(
                text=f"site:t.me {city} {location}",
                purpose="telegram_address",
                target_kind=SourceKind.TELEGRAM,
            ),
            DiscoveryQuery(
                text=f"site:t.me {city} новости",
                purpose="telegram_local_channels",
                target_kind=SourceKind.TELEGRAM,
            ),
            DiscoveryQuery(
                text=f"site:pikabu.ru {city} {location}",
                purpose="pikabu_address",
                target_kind=SourceKind.PIKABU,
            ),
            DiscoveryQuery(
                text=f"site:dzen.ru {city} {location}",
                purpose="dzen_address",
                target_kind=SourceKind.DZEN,
            ),
            DiscoveryQuery(
                text=f"site:yandex.ru/maps {city} {location}",
                purpose="yandex_maps_place",
                target_kind=SourceKind.YANDEX_MAPS,
            ),
            DiscoveryQuery(
                text=f"site:2gis.ru {city} {location}",
                purpose="two_gis_place",
                target_kind=SourceKind.TWO_GIS,
            ),
        ]
        if street:
            queries.extend(
                [
                    DiscoveryQuery(
                        text=f"{_quote(street)} {city} форум",
                        purpose="street_forum",
                        target_kind=SourceKind.LOCAL_FORUM,
                    ),
                    DiscoveryQuery(
                        text=f"{_quote(street)} {city} проблемы жители",
                        purpose="street_issues",
                    ),
                    DiscoveryQuery(
                        text=f"{_quote(street)} {city} благоустройство",
                        purpose="street_municipal",
                        target_kind=SourceKind.MUNICIPAL,
                    ),
                ]
            )
        if region and region.casefold() != city.casefold():
            queries.extend(
                [
                    DiscoveryQuery(
                        text=f"{region} {city} новости",
                        purpose="regional_media_discovery",
                        target_kind=SourceKind.LOCAL_MEDIA,
                    ),
                    DiscoveryQuery(
                        text=f"{region} {city} форум",
                        purpose="regional_forum_discovery",
                        target_kind=SourceKind.LOCAL_FORUM,
                    ),
                    DiscoveryQuery(
                        text=f"site:t.me {region} {city}",
                        purpose="telegram_region_channels",
                        target_kind=SourceKind.TELEGRAM,
                    ),
                ]
            )
        for alias in scope.aliases[:6]:
            if alias.casefold() in {address.casefold(), location.casefold()}:
                continue
            queries.append(
                DiscoveryQuery(
                    text=f"{_quote(alias)} {city}",
                    purpose="address_alias",
                )
            )
        return _deduplicate(queries, self.max_queries)
