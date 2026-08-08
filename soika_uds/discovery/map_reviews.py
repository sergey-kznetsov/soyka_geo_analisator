"""Explicit map-review collectors that never substitute metadata for review text."""

from __future__ import annotations

from dataclasses import dataclass

from .collection import CandidateCollectionResult
from .models import GeoScope, SourceCandidate, SourceKind, SourceOutcome, SourceReasonCode, SourceState


@dataclass(frozen=True, slots=True)
class MapReviewUnavailableCollector:
    """Report documented review-text limitations instead of scraping consumer UIs."""

    source_kind: SourceKind

    def __post_init__(self) -> None:
        if self.source_kind not in {SourceKind.YANDEX_MAPS, SourceKind.TWO_GIS}:
            raise ValueError("map review collector supports only Yandex Maps and 2GIS")

    def collect(
        self,
        candidate: SourceCandidate,
        scope: GeoScope,
    ) -> CandidateCollectionResult:
        del scope
        if candidate.kind is not self.source_kind:
            raise ValueError("candidate kind does not match map review collector")
        if self.source_kind is SourceKind.TWO_GIS:
            reason = (
                "2GIS Places API exposes organization data and review statistics, "
                "but the documented API does not provide review texts; automated "
                "consumer-page scraping is disabled"
            )
        else:
            reason = (
                "Yandex Maps documented organization APIs do not expose public review "
                "texts for collection; automated consumer-page scraping is disabled"
            )
        return CandidateCollectionResult(
            messages=(),
            outcome=SourceOutcome(
                source_id=candidate.candidate_id,
                kind=self.source_kind,
                state=SourceState.UNAVAILABLE,
                reason_code=SourceReasonCode.UNSUPPORTED_PAGE,
                reason=reason,
                attempted_urls=(candidate.url,),
                details={
                    "review_texts_collected": False,
                    "metadata_is_not_review_text": True,
                },
            ),
        )


__all__ = ["MapReviewUnavailableCollector"]
