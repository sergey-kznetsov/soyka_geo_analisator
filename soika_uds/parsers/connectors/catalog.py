"""Registry for prepared connector blueprints."""

from __future__ import annotations

from collections.abc import Iterable

from .models import ConnectorDefinition, ConnectorRegistrationError


class ConnectorCatalog:
    def __init__(self, definitions: Iterable[ConnectorDefinition] = ()) -> None:
        self._items: dict[str, ConnectorDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ConnectorDefinition) -> None:
        if not isinstance(definition, ConnectorDefinition):
            raise ConnectorRegistrationError(
                "connector catalog accepts only ConnectorDefinition values"
            )
        if definition.source_id in self._items:
            raise ConnectorRegistrationError(
                f"connector {definition.source_id!r} is already registered"
            )
        self._items[definition.source_id] = definition

    def get(self, source_id: str) -> ConnectorDefinition:
        try:
            return self._items[source_id]
        except KeyError as error:
            raise ConnectorRegistrationError(
                f"connector {source_id!r} is not registered"
            ) from error

    def list(self) -> tuple[ConnectorDefinition, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def source_ids(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.list())

    def assert_preparation_only(self) -> None:
        enabled = [item.source_id for item in self.list() if item.network_enabled]
        if enabled:
            raise ConnectorRegistrationError(
                "prepared catalog unexpectedly enables network access: "
                + ", ".join(enabled)
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "connectors": [item.to_dict() for item in self.list()],
        }
