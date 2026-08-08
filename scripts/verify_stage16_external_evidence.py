from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_EXPECTED_SOURCES = {
    "vk",
    "ok",
    "local-media",
    "municipal-public",
    "dzen",
    "pikabu",
    "rutube",
}


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{field_name} must be a non-empty ISO 8601 timestamp")
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise SystemExit(f"{field_name} is not ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def main() -> int:
    path = Path("docs/STAGE_6B_EXTERNAL_RESULTS.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0.0":
        raise SystemExit("unexpected external evidence schema_version")
    if payload.get("probe_kind") != "controlled_single_request_public_metadata":
        raise SystemExit("unexpected external evidence probe_kind")
    if payload.get("all_sources_responded") is not True:
        raise SystemExit("controlled external evidence contains a non-responsive source")
    generated_at = _parse_utc(payload.get("generated_at"), "generated_at")

    results = payload.get("results")
    if not isinstance(results, list):
        raise SystemExit("external evidence results must be an array")
    source_ids: set[str] = set()
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            raise SystemExit(f"results[{index}] must be an object")
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise SystemExit(f"results[{index}].source_id is invalid")
        if source_id in source_ids:
            raise SystemExit(f"duplicate external evidence source_id: {source_id}")
        source_ids.add(source_id)
        if item.get("received_response") is not True:
            raise SystemExit(f"source {source_id} did not receive an HTTP response")
        status_code = item.get("status_code")
        if type(status_code) is not int or not 100 <= status_code <= 599:
            raise SystemExit(f"source {source_id} has invalid status_code")
        fetched_at = _parse_utc(item.get("fetched_at"), f"{source_id}.fetched_at")
        if fetched_at > generated_at:
            raise SystemExit(f"source {source_id} was fetched after evidence generation")

    if source_ids != _EXPECTED_SOURCES:
        missing = sorted(_EXPECTED_SOURCES - source_ids)
        unexpected = sorted(source_ids - _EXPECTED_SOURCES)
        raise SystemExit(
            f"external evidence source set differs; missing={missing}, unexpected={unexpected}"
        )

    print(
        json.dumps(
            {
                "status": "verified",
                "generated_at": generated_at.isoformat(),
                "sources": sorted(source_ids),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
