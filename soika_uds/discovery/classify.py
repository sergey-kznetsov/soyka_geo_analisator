"""Conservative classification of discovered public-web sources."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .models import SearchHit, SourceKind

_MEDIA_RE = re.compile(
    r"\b(новост|сми|газет|информационн(?:ое|ый) агентств|телекомпан|редакц)\w*",
    re.I,
)
_MUNICIPAL_RE = re.compile(
    r"\b(администрац|мэри|правительств|муниципал|городск(?:ая|ой) дума|официальн(?:ый|ая) портал)\w*",
    re.I,
)
_FORUM_RE = re.compile(r"\b(форум|сообщество жителей|городское сообщество)\w*", re.I)


class SourceClassifier:
    """Classify known platforms first, then use bounded textual hints."""

    def classify(self, hit: SearchHit) -> SourceKind:
        parsed = urlsplit(hit.url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        text = f"{hit.title} {hit.snippet} {host}".strip()

        if host in {"t.me", "telegram.me"} or host.endswith(".t.me"):
            return SourceKind.TELEGRAM
        if host == "pikabu.ru" or host.endswith(".pikabu.ru"):
            return SourceKind.PIKABU
        if host == "dzen.ru" or host.endswith(".dzen.ru"):
            return SourceKind.DZEN
        if host in {"yandex.ru", "yandex.com"} and path.startswith("/maps"):
            return SourceKind.YANDEX_MAPS
        if host == "2gis.ru" or host.endswith(".2gis.ru"):
            return SourceKind.TWO_GIS
        if host == "rutube.ru" or host.endswith(".rutube.ru"):
            return SourceKind.RUTUBE
        if host in {"vk.com", "vk.ru"} or host.endswith((".vk.com", ".vk.ru")):
            return SourceKind.VK
        if host == "ok.ru" or host.endswith(".ok.ru"):
            return SourceKind.OK
        if host in {"max.ru", "maxapp.ru"} or host.endswith((".max.ru", ".maxapp.ru")):
            return SourceKind.MAX

        if host.endswith(".gov.ru") or host.endswith(".gosuslugi.ru") or _MUNICIPAL_RE.search(text):
            return SourceKind.MUNICIPAL
        if host.startswith("forum.") or "/forum" in path or _FORUM_RE.search(text):
            return SourceKind.LOCAL_FORUM
        if _MEDIA_RE.search(text):
            return SourceKind.LOCAL_MEDIA
        return SourceKind.OTHER_WEB


def geo_evidence(hit: SearchHit, *, city: str, region: str | None) -> tuple[str, ...]:
    """Record observed geo evidence without pretending it is final relevance."""

    haystack = f"{hit.title} {hit.snippet} {hit.url}".casefold().replace("ё", "е")
    evidence: list[str] = []
    city_key = city.casefold().replace("ё", "е")
    if city_key in haystack:
        evidence.append("city_text_match")
    if region:
        region_key = region.casefold().replace("ё", "е")
        if region_key in haystack:
            evidence.append("region_text_match")
    if not evidence:
        evidence.append("search_query_geo_bound_only")
    return tuple(evidence)
