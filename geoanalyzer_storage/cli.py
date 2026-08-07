"""Administrative entrypoint for shared Geo Analyzer storage."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from .migrations import MigrationRunner, discover_migrations
from .postgres import PostgresDatabase, PostgresSettings
from .retention import RetentionManager, RetentionPolicy


def _database(args: argparse.Namespace) -> PostgresDatabase:
    dsn = os.environ.get(args.dsn_env, "").strip()
    if not dsn:
        raise SystemExit(f"environment variable {args.dsn_env} is required")
    return PostgresDatabase(
        PostgresSettings(
            dsn=dsn,
            application_name=args.application_name,
        )
    )


def _migrate(args: argparse.Namespace) -> int:
    with _database(args) as database:
        applied = MigrationRunner(database).apply()
    print(
        json.dumps(
            {
                "applied": [
                    {
                        "version": item.version,
                        "name": item.name,
                        "checksum": item.checksum,
                    }
                    for item in applied
                ],
                "available": len(discover_migrations()),
            },
            sort_keys=True,
        )
    )
    return 0


def _check(args: argparse.Namespace) -> int:
    with _database(args) as database:
        with database.connection() as connection:
            server = connection.execute("SHOW server_version").fetchone()[0]
            postgis = connection.execute("SELECT postgis_full_version()").fetchone()[0]
            applied = connection.execute(
                "SELECT version, name, checksum FROM ga_meta.schema_migrations "
                "ORDER BY version"
            ).fetchall()
    print(
        json.dumps(
            {
                "postgresql": server,
                "postgis": postgis,
                "migrations": [
                    {"version": row[0], "name": row[1], "checksum": row[2]}
                    for row in applied
                ],
            },
            sort_keys=True,
        )
    )
    return 0


def _retention(args: argparse.Namespace) -> int:
    policy = RetentionPolicy(
        completed_job_days=args.completed_job_days,
        failed_job_days=args.failed_job_days,
        cancelled_job_days=args.cancelled_job_days,
        cleanup_batch_size=args.batch_size,
    )
    with _database(args) as database:
        manager = RetentionManager(
            database,
            application=args.application,
            policy=policy,
        )
        expired_cache = manager.purge_expired_cache()
        terminal_jobs = manager.purge_terminal_jobs()
    print(
        json.dumps(
            {
                "application": args.application,
                "expired_cache_deleted": expired_cache,
                "terminal_jobs_deleted": terminal_jobs,
            },
            sort_keys=True,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geoanalyzer-storage")
    parser.add_argument("--dsn-env", default="GEOANALYZER_DATABASE_DSN")
    parser.add_argument("--application-name", default="geoanalyzer-storage-admin")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate = commands.add_parser("migrate")
    migrate.set_defaults(handler=_migrate)

    check = commands.add_parser("check")
    check.set_defaults(handler=_check)

    retention = commands.add_parser("retention")
    retention.add_argument("--application", required=True)
    retention.add_argument("--completed-job-days", type=int, default=90)
    retention.add_argument("--failed-job-days", type=int, default=30)
    retention.add_argument("--cancelled-job-days", type=int, default=30)
    retention.add_argument("--batch-size", type=int, default=5_000)
    retention.set_defaults(handler=_retention)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
