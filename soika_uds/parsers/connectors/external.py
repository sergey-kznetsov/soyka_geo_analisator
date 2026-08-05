"""Controlled one-request external probes for prepared connector sources."""

from __future__ import annotations

import argparse
import json
import re
import ssl
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_MAX_BYTES = 1_000_000
_USER_AGENT = "SOIKA-UDS/0.8 controlled-external-probe"


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self.title_parts: list[str] = []
        self.description: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta":
            property_name = (
                attributes.get("property") or attributes.get("name") or ""
            ).lower()
            if property_name in {"og:description", "description"}:
                value = attributes.get("content", "").strip()
                if value and self.description is None:
                    self.description = value

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title_parts.append(data.strip())

    @property
    def title(self) -> str | None:
        value = " ".join(" ".join(self.title_parts).split())
        return value or None


@dataclass(frozen=True, slots=True)
class ExternalProbeResult:
    source_id: str
    url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    extracted: dict[str, Any]
    error: str | None
    fetched_at: str

    @property
    def received_response(self) -> bool:
        return self.status_code is not None


def _extract_json(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"json": False}
    if isinstance(value, dict):
        result: dict[str, Any] = {
            "json": True,
            "top_level_keys": sorted(value)[:20],
        }
        error = value.get("error")
        if isinstance(error, dict):
            result["error_code"] = error.get("error_code") or error.get("code")
            result["error_message"] = error.get("error_msg") or error.get("message")
        if value.get("error_code") is not None:
            result["error_code"] = value.get("error_code")
            result["error_message"] = value.get("error_msg")
        response = value.get("response")
        if isinstance(response, dict) and isinstance(response.get("items"), list):
            result["item_count"] = len(response["items"])
        return result
    return {"json": True, "value_type": type(value).__name__}


def _extract_html(body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    parser = _TitleParser()
    parser.feed(text)
    result: dict[str, Any] = {"html": True}
    if parser.title:
        result["title"] = parser.title[:300]
    if parser.description:
        result["description"] = parser.description[:500]
    visible = re.sub(r"<[^>]+>", " ", text)
    visible = " ".join(visible.split())
    if visible:
        result["text_sample"] = visible[:300]
    return result


def _extract(body: bytes, content_type: str | None) -> dict[str, Any]:
    normalized = (content_type or "").lower()
    if "json" in normalized or body.lstrip().startswith((b"{", b"[")):
        return _extract_json(body)
    return _extract_html(body)


def probe_target(
    target: dict[str, Any],
    *,
    timeout_seconds: float = 20.0,
) -> ExternalProbeResult:
    source_id = str(target["source_id"]).strip()
    url = str(target["url"]).strip()
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    request = Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json,text/html,application/xml;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    context = ssl.create_default_context()
    try:
        with urlopen(request, timeout=timeout_seconds, context=context) as response:
            body = response.read(_MAX_BYTES + 1)
            if len(body) > _MAX_BYTES:
                body = body[:_MAX_BYTES]
            content_type = response.headers.get("content-type")
            return ExternalProbeResult(
                source_id=source_id,
                url=url,
                final_url=response.geturl(),
                status_code=response.status,
                content_type=content_type,
                extracted=_extract(body, content_type),
                error=None,
                fetched_at=fetched_at,
            )
    except HTTPError as error:
        body = error.read(_MAX_BYTES)
        content_type = error.headers.get("content-type") if error.headers else None
        return ExternalProbeResult(
            source_id=source_id,
            url=url,
            final_url=error.geturl(),
            status_code=error.code,
            content_type=content_type,
            extracted=_extract(body, content_type),
            error=f"HTTP {error.code}: {error.reason}",
            fetched_at=fetched_at,
        )
    except (URLError, TimeoutError, OSError) as error:
        return ExternalProbeResult(
            source_id=source_id,
            url=url,
            final_url=None,
            status_code=None,
            content_type=None,
            extracted={},
            error=f"{type(error).__name__}: {error}",
            fetched_at=fetched_at,
        )


def run_external_probes(targets: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids = [str(target.get("source_id", "")).strip() for target in targets]
    expected = {
        "vk",
        "ok",
        "local-media",
        "municipal-public",
        "dzen",
        "pikabu",
        "rutube",
    }
    if set(source_ids) != expected or len(source_ids) != len(expected):
        raise ValueError(
            "external probe targets must cover every prepared connector exactly once"
        )
    results = [probe_target(target) for target in targets]
    return {
        "schema_version": "1.0.0",
        "probe_kind": "controlled_single_request_public_metadata",
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "all_sources_responded": all(result.received_response for result in results),
        "results": [
            {
                **asdict(result),
                "received_response": result.received_response,
            }
            for result in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    targets = json.loads(arguments.targets.read_text(encoding="utf-8"))
    if not isinstance(targets, list) or not all(
        isinstance(item, dict) for item in targets
    ):
        raise SystemExit("targets document must be an array of objects")
    report = run_external_probes(targets)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_sources_responded"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
