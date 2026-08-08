from __future__ import annotations

from pathlib import Path

import pytest

from scripts.export_stage16_locked_requirements import _locked_main_requirements


def _write_lock(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "poetry.lock"
    path.write_text(body, encoding="utf-8")
    return path


def test_export_preserves_disjoint_platform_markers(tmp_path: Path) -> None:
    lock = _write_lock(
        tmp_path,
        """
[[package]]
name = "example"
version = "1.0"
groups = ["main"]
markers = "sys_platform == 'linux'"

[[package]]
name = "example"
version = "2.0"
groups = ["main"]
markers = "sys_platform == 'win32'"

[[package]]
name = "unused-solver-branch"
version = "3.0"
groups = ["main"]
markers = "<empty>"
""".strip(),
    )

    assert _locked_main_requirements(lock) == (
        "example==1.0 ; sys_platform == 'linux'",
        "example==2.0 ; sys_platform == 'win32'",
    )


def test_export_uses_main_marker_from_group_mapping(tmp_path: Path) -> None:
    lock = _write_lock(
        tmp_path,
        """
[[package]]
name = "colorama"
version = "0.4.6"
groups = ["main", "dev"]

[package.markers]
main = "platform_system == 'Windows'"
dev = "sys_platform == 'win32'"
""".strip(),
    )

    assert _locked_main_requirements(lock) == (
        "colorama==0.4.6 ; platform_system == 'Windows'",
    )


def test_export_rejects_conflicting_unmarked_versions(tmp_path: Path) -> None:
    lock = _write_lock(
        tmp_path,
        """
[[package]]
name = "example"
version = "1.0"
groups = ["main"]

[[package]]
name = "example"
version = "2.0"
groups = ["main"]
markers = "sys_platform == 'linux'"
""".strip(),
    )

    with pytest.raises(ValueError, match="ambiguous unmarked"):
        _locked_main_requirements(lock)


def test_export_deduplicates_identical_lock_entries(tmp_path: Path) -> None:
    lock = _write_lock(
        tmp_path,
        """
[[package]]
name = "example"
version = "1.0"
groups = ["main"]

[[package]]
name = "example"
version = "1.0"
groups = ["main"]
""".strip(),
    )

    assert _locked_main_requirements(lock) == ("example==1.0",)
