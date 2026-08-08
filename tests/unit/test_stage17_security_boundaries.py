from __future__ import annotations

import pytest

from soika_uds.parsers.connectors.external import _validated_https_url


def test_external_probe_accepts_https_target() -> None:
    assert _validated_https_url("https://example.org/path?q=1") == (
        "https://example.org/path?q=1"
    )


@pytest.mark.parametrize(
    "url",
    (
        "http://example.org/path",
        "file:///etc/passwd",
        "ftp://example.org/file",
        "https:///missing-host",
        "https://user:secret@example.org/path",
    ),
)
def test_external_probe_rejects_non_https_or_credentialed_target(url: str) -> None:
    with pytest.raises(ValueError):
        _validated_https_url(url)
