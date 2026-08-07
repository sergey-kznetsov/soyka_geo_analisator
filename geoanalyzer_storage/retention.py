"""Explicit retention policies for shared storage."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import application_id
from .postgres import PostgresDatabase

_TERMINAL_STATUSES = (
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    completed_job_days: int = 90
    failed_job_days: int = 30
    cancelled_job_days: int = 30
    cleanup_batch_size: int = 5_000

    def __post_init__(self) -> None:
        for name in (
            "completed_job_days",
            "failed_job_days",
            "cancelled_job_days",
            "cleanup_batch_size",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


class RetentionManager:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        application: str,
        policy: RetentionPolicy = RetentionPolicy(),
    ) -> None:
        if not isinstance(database, PostgresDatabase):
            raise TypeError("database must be PostgresDatabase")
        if not isinstance(policy, RetentionPolicy):
            raise TypeError("policy must be RetentionPolicy")
        self.database = database
        self.application = application_id(application)
        self.policy = policy

    def purge_expired_cache(self) -> int:
        with self.database.connection() as connection:
            rows = connection.execute(
                "WITH doomed AS ("
                "SELECT ctid FROM ga_cache.entries "
                "WHERE application_id = %s AND expires_at <= clock_timestamp() "
                "ORDER BY expires_at LIMIT %s"
                ") DELETE FROM ga_cache.entries AS cache "
                "USING doomed WHERE cache.ctid = doomed.ctid RETURNING 1",
                (self.application, self.policy.cleanup_batch_size),
            ).fetchall()
        return len(rows)

    def purge_terminal_jobs(self) -> int:
        policy = self.policy
        with self.database.connection() as connection:
            rows = connection.execute(
                "WITH doomed AS ("
                "SELECT application_id, analysis_id FROM ga_core.jobs "
                "WHERE application_id = %s AND status = ANY(%s) AND ("
                "(status IN ('completed', 'completed_with_warnings') "
                "AND updated_at < clock_timestamp() - (%s * interval '1 day')) OR "
                "(status = 'failed' "
                "AND updated_at < clock_timestamp() - (%s * interval '1 day')) OR "
                "(status = 'cancelled' "
                "AND updated_at < clock_timestamp() - (%s * interval '1 day'))"
                ") ORDER BY updated_at LIMIT %s"
                ") DELETE FROM ga_core.jobs AS jobs USING doomed "
                "WHERE jobs.application_id = doomed.application_id "
                "AND jobs.analysis_id = doomed.analysis_id RETURNING 1",
                (
                    self.application,
                    list(_TERMINAL_STATUSES),
                    policy.completed_job_days,
                    policy.failed_job_days,
                    policy.cancelled_job_days,
                    policy.cleanup_batch_size,
                ),
            ).fetchall()
        return len(rows)


__all__ = ["RetentionManager", "RetentionPolicy"]
