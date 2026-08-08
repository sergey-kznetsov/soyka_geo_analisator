from __future__ import annotations

from soika_uds.cli import build_parser


def test_probe_server_defaults_to_loopback() -> None:
    arguments = build_parser().parse_args(["serve-probes"])

    assert arguments.host == "127.0.0.1"
    assert arguments.port == 8080
