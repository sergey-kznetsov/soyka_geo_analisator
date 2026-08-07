"""Safe backup/restore command construction without password arguments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")


@dataclass(frozen=True, slots=True)
class BackupTarget:
    host: str
    port: int
    database: str
    user: str

    def __post_init__(self) -> None:
        for name in ("host", "database", "user"):
            value = getattr(self, name)
            if not isinstance(value, str) or _SAFE_NAME.fullmatch(value) is None:
                raise ValueError(f"{name} contains unsupported characters")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be in [1, 65535]")


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 6

    def __post_init__(self) -> None:
        for name in ("keep_daily", "keep_weekly", "keep_monthly"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.keep_daily + self.keep_weekly + self.keep_monthly < 1:
            raise ValueError("backup policy must retain at least one backup")


def pg_dump_command(target: BackupTarget, destination: Path | str) -> tuple[str, ...]:
    path = Path(destination)
    if path.name in {"", ".", ".."}:
        raise ValueError("backup destination must be a file path")
    return (
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--host",
        target.host,
        "--port",
        str(target.port),
        "--username",
        target.user,
        "--dbname",
        target.database,
        "--file",
        str(path),
    )


def pg_restore_command(target: BackupTarget, source: Path | str) -> tuple[str, ...]:
    path = Path(source)
    if path.name in {"", ".", ".."}:
        raise ValueError("restore source must be a file path")
    return (
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
        "--host",
        target.host,
        "--port",
        str(target.port),
        "--username",
        target.user,
        "--dbname",
        target.database,
        str(path),
    )


__all__ = [
    "BackupPolicy",
    "BackupTarget",
    "pg_dump_command",
    "pg_restore_command",
]
