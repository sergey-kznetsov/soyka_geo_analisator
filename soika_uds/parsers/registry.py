"Source registry binding adapters to reviewed source policies."""

from __future__ import annotations

from collections.abc import Iterable

from .base import ParserAdapter
from .models import (
    PermissionStatus,
    SourcePolicy,
    SourceRegistrationError,
)


class ParserRegistry:
    """In-memory registry used by backend workers."""

    def __init__(self, adapters: Iterable[ParserAdapter] = ()) -> None:
        self._adapters: dict[str, ParserAdapter] = {}
        self._policies: dict[str, SourcePolicy] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ParserAdapter) -> None:
        if not isinstance(adapter, ParserAdapter):
            raise SourceRegistrationError(
                "adapter does not implement ParserAdapter protocol"
            )
        source_id = adapter.source_id.strip().lower()
        if source_id in self._adapters:
            raise SourceRegistrationError(
                f"source {source_id!r} is already registered"
            )
        policy = adapter.policy()
        if policy.source_id != source_id:
            raise SourceRegistrationError(
                "adapter source_id must equal policy source_id"
            )
        if policy.parser_version != adapter.parser_version:
            raise SourceRegistrationError(
                "adapter parser_version must equal policy parser_version"
            )
        self._adapters[source_id] = adapter
        self._policies[source_id] = policy

    def get(self, source_id: str) -> ParserAdapter:
        key = source_id.strip().lower()
        try:
            return self._adapters[key]
        except KeyError as error:
            raise SourceRegistrationError(
                f"source {key!r} is not registered"
            ) from error

    def policy(self, source_id: str) -> SourcePolicy:
        key = source_id.strip().lower()
        try:
            return self._policies[key]
        except KeyError as error:
            raise SourceRegistrationError(
                f"source {key!r} is not registered"
            ) from error

    def list_policies(
        self,
        *,
        enabled_only: bool = False,
        approved_only: bool = False,
    ) -> tuple[SourcePolicy, ...]:
        policies = sorted(self._policies.values(), key=lambda item: item.source_id)
        if enabled_only:
            policies = [item for item in policies if item.enabled]
        if approved_only:
            policies = [
                item
                for item in policies
                if item.permission_status is PermissionStatus.APPROVED
            ]
        return tuple(policies)
