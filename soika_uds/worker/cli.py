"""Standalone process entrypoint for private CPU/GPU workers."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import socket
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from geoanalyzer_storage import PostgresDatabase, PostgresSettings

from .models import ComputeClass, WorkerConfigurationError, WorkerSettings
from .observability import WorkerMetrics, configure_worker_logging, log_event
from .probes import WorkerProbeServer
from .queue import PostgresJobQueue
from .runtime import WorkerExecutor, WorkerRuntime

ExecutorFactory = Callable[[PostgresDatabase, WorkerSettings], WorkerExecutor]


def _default_worker_id(compute_class: ComputeClass) -> str:
    hostname = socket.gethostname().replace("_", "-")[:32] or "host"
    return f"soika-{compute_class.value}-{hostname}-{os.getpid()}"


def _postgres_application_name(worker_id: str) -> str:
    return worker_id[:63]


def _read_secret_file(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkerConfigurationError(
            f"cannot read database DSN secret file: {path}"
        ) from error
    if len(raw.encode("utf-8")) > 16_384:
        raise WorkerConfigurationError("database DSN secret file is unexpectedly large")
    value = raw.strip()
    if not value or "\x00" in value:
        raise WorkerConfigurationError("database DSN secret is empty or malformed")
    return value


def _load_executor_factory(spec: str) -> ExecutorFactory:
    if not isinstance(spec, str) or ":" not in spec:
        raise WorkerConfigurationError(
            "executor must use module.path:factory syntax"
        )
    module_name, attribute = spec.rsplit(":", 1)
    if not module_name or not attribute:
        raise WorkerConfigurationError(
            "executor must use module.path:factory syntax"
        )
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        raise WorkerConfigurationError(
            f"cannot load worker executor factory {spec!r}"
        ) from error
    if not callable(factory):
        raise WorkerConfigurationError("worker executor factory must be callable")
    return factory


def _cgroup_memory_limit_bytes() -> int | None:
    candidates = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    )
    for path in candidates:
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if not value or value == "max":
            return None
        try:
            parsed = int(value)
        except ValueError:
            continue
        if parsed > 0:
            return parsed
    return None


def _validate_memory_limit(required_max_mb: int | None) -> None:
    if required_max_mb is None:
        return
    if required_max_mb < 64:
        raise WorkerConfigurationError("required memory limit must be at least 64 MiB")
    actual = _cgroup_memory_limit_bytes()
    required = required_max_mb * 1024 * 1024
    if actual is None:
        raise WorkerConfigurationError(
            "worker requires a finite container/cgroup memory limit"
        )
    if actual > required:
        raise WorkerConfigurationError(
            "container memory limit exceeds the configured worker maximum"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m soika_uds.worker")
    parser.add_argument(
        "--compute-class",
        choices=tuple(item.value for item in ComputeClass),
        default=os.getenv("SOIKA_WORKER_COMPUTE_CLASS", "cpu"),
    )
    parser.add_argument(
        "--executor",
        default=os.getenv("SOIKA_WORKER_EXECUTOR"),
        help="executor factory as module.path:factory",
    )
    parser.add_argument(
        "--database-dsn-file",
        type=Path,
        default=(
            Path(os.environ["GEOANALYZER_DATABASE_DSN_FILE"])
            if "GEOANALYZER_DATABASE_DSN_FILE" in os.environ
            else None
        ),
    )
    parser.add_argument("--application", default="soika")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--queue-lease-seconds", type=float, default=600.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--job-timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--shutdown-grace-seconds", type=float, default=120.0)
    parser.add_argument("--probe-host", default="127.0.0.1")
    parser.add_argument("--probe-port", type=int, default=9090)
    parser.add_argument("--allow-remote-probes", action="store_true")
    parser.add_argument("--require-memory-limit-mb", type=int, default=None)
    return parser


def _settings(args: Any) -> WorkerSettings:
    compute_class = ComputeClass(args.compute_class)
    return WorkerSettings(
        worker_id=args.worker_id or _default_worker_id(compute_class),
        compute_class=compute_class,
        queue_lease_seconds=args.queue_lease_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
        poll_seconds=args.poll_seconds,
        wall_timeout_seconds=args.job_timeout_seconds,
        shutdown_grace_seconds=args.shutdown_grace_seconds,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = configure_worker_logging()
    database: PostgresDatabase | None = None
    runtime: WorkerRuntime | None = None
    try:
        if args.executor is None:
            raise WorkerConfigurationError("SOIKA_WORKER_EXECUTOR is required")
        if args.database_dsn_file is None:
            raise WorkerConfigurationError(
                "GEOANALYZER_DATABASE_DSN_FILE is required; DSN argv is not supported"
            )
        _validate_memory_limit(args.require_memory_limit_mb)
        settings = _settings(args)
        dsn = _read_secret_file(args.database_dsn_file)
        database = PostgresDatabase(
            PostgresSettings(
                dsn=dsn,
                application_name=_postgres_application_name(settings.worker_id),
                min_pool_size=1,
                max_pool_size=4,
            )
        )
        queue = PostgresJobQueue(database, application=args.application)
        if not queue.healthcheck():
            raise WorkerConfigurationError(
                "ga_core.job_queue is missing; apply platform and worker migrations first"
            )
        factory = _load_executor_factory(args.executor)
        executor = factory(database, settings)
        if not callable(executor):
            raise WorkerConfigurationError("executor factory returned a non-callable")
        metrics = WorkerMetrics(
            worker_id=settings.worker_id,
            compute_class=settings.compute_class.value,
        )
        runtime = WorkerRuntime(
            queue,
            executor,
            settings,
            metrics=metrics,
            logger=logger,
        )
        runtime.install_signal_handlers()
        with WorkerProbeServer(
            runtime,
            metrics,
            host=args.probe_host,
            port=args.probe_port,
            allow_remote=args.allow_remote_probes,
        ):
            return runtime.run_forever()
    except WorkerConfigurationError as error:
        log_event(
            logger,
            logging.ERROR,
            "worker.configuration.error",
            "worker refused unsafe or incomplete configuration",
            error_type=type(error).__name__,
        )
        return 2
    finally:
        if runtime is not None:
            runtime.restore_signal_handlers()
        if database is not None:
            database.close()


__all__ = ["build_parser", "main"]
