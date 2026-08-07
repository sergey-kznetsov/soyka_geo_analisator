"""Registration of server applications sharing the storage platform."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import application_id
from .postgres import PostgresDatabase

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class ApplicationRegistration:
    application_id: str
    domain_schema: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", application_id(self.application_id))
        if self.domain_schema is not None:
            if (
                not isinstance(self.domain_schema, str)
                or _SCHEMA.fullmatch(self.domain_schema) is None
            ):
                raise ValueError("domain_schema must be a safe PostgreSQL identifier")


class PostgresApplicationRegistry:
    def __init__(self, database: PostgresDatabase) -> None:
        if not isinstance(database, PostgresDatabase):
            raise TypeError("database must be PostgresDatabase")
        self.database = database

    def register(self, registration: ApplicationRegistration) -> None:
        if not isinstance(registration, ApplicationRegistration):
            raise TypeError("registration must be ApplicationRegistration")
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO ga_meta.applications(application_id, domain_schema) "
                "VALUES (%s, %s) "
                "ON CONFLICT (application_id) DO UPDATE SET "
                "domain_schema = EXCLUDED.domain_schema, active = TRUE, "
                "updated_at = clock_timestamp()",
                (registration.application_id, registration.domain_schema),
            )

    def disable(self, application: str) -> bool:
        application = application_id(application)
        with self.database.connection() as connection:
            row = connection.execute(
                "UPDATE ga_meta.applications SET active = FALSE, "
                "updated_at = clock_timestamp() "
                "WHERE application_id = %s RETURNING application_id",
                (application,),
            ).fetchone()
        return row is not None


__all__ = ["ApplicationRegistration", "PostgresApplicationRegistry"]
