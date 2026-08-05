"Parser adapter interface isolated from the analytical core."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ParserPage, ParserRequest, SourcePolicy
from .transport import ParserServices


@runtime_checkable
class ParserAdapter(Protocol):
    """One source-specific adapter.

    Adapters perform source-specific retrieval and mapping to ``SourceMessage``.
    They do not classify, geocode, cluster, score, render UI, or mutate the
    Geo Analyzer.
    """

    source_id: str
    parser_version: str

    def policy(self) -> SourcePolicy:
        """Return the reviewed policy bound to this adapter."""

    def fetch_page(
        self,
        request: ParserRequest,
        checkpoint: dict[str, object] | None,
        services: ParserServices,
    ) -> ParserPage:
        """Fetch exactly one page using an opaque JSON checkpoint."""
