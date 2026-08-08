"""Source-specific parser adapters prepared in stage 6B."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlsplit
from xml.etree import ElementTree

from defusedxml.ElementTree import fromstring as safe_xml_fromstring

from ...contracts import SourceMessage
from ..models import (
    AccessMethod,
    ParserPage,
    ParserRequest,
    PermanentParserError,
    SourcePolicy,
    SourcePolicyError,
)
from ..registry import ParserRegistry
from ..transport import ParserServices
from .definitions import prepared_connector_catalog
from .html_profiles import (
    DiscoveryMode,
    HtmlSelectors,
    HtmlSourceKind,
    HtmlSourceProfile,
)

PARSER_VERSION = "1.0.0"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PermanentParserError(
            f"{field_name} must be a non-empty string",
            code="INVALID_CONNECTOR_OPTION",
            details={"field": field_name},
        )
    return value.strip()


def _string_array(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise PermanentParserError(
            f"{field_name} must be an array",
            code="INVALID_CONNECTOR_OPTION",
            details={"field": field_name},
        )
    result = tuple(_required_text(item, f"{field_name}[]") for item in value)
    if not result:
        raise PermanentParserError(
            f"{field_name} must not be empty",
            code="INVALID_CONNECTOR_OPTION",
            details={"field": field_name},
        )
    return result


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PermanentParserError(
            f"{field_name} must be an object",
            code="INVALID_CONNECTOR_OPTION",
            details={"field": field_name},
        )
    return value


def _int_value(
    value: object,
    field_name: str,
    *,
    minimum: int | None = 0,
) -> int:
    if isinstance(value, bool):
        raise PermanentParserError(
            f"{field_name} must be an integer",
            code="INVALID_UPSTREAM_RESPONSE",
        )
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise PermanentParserError(
            f"{field_name} must be an integer",
            code="INVALID_UPSTREAM_RESPONSE",
        ) from error
    if minimum is not None and result < minimum:
        raise PermanentParserError(
            f"{field_name} must be at least {minimum}",
            code="INVALID_UPSTREAM_RESPONSE",
        )
    return result


def _timestamp(value: object, field_name: str) -> datetime:
    if isinstance(value, bool):
        raise PermanentParserError(
            f"{field_name} must be a timestamp",
            code="INVALID_UPSTREAM_TIMESTAMP",
        )
    if isinstance(value, int | float):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, OverflowError, ValueError) as error:
            raise PermanentParserError(
                f"{field_name} is outside supported range",
                code="INVALID_UPSTREAM_TIMESTAMP",
            ) from error
    if not isinstance(value, str) or not value.strip():
        raise PermanentParserError(
            f"{field_name} must be a timestamp",
            code="INVALID_UPSTREAM_TIMESTAMP",
        )
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError) as error:
            raise PermanentParserError(
                f"{field_name} is not a supported timestamp",
                code="INVALID_UPSTREAM_TIMESTAMP",
            ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _query_url(base: str, parameters: Mapping[str, object]) -> str:
    clean = {key: value for key, value in parameters.items() if value is not None}
    return f"{base}?{urlencode(clean, doseq=True)}"


def _json_object(response: object, source_id: str) -> Mapping[str, Any]:
    try:
        payload = response.json_value()
    except AttributeError as error:
        raise PermanentParserError(
            "connector transport response does not support JSON decoding",
            code="INVALID_TRANSPORT_RESPONSE",
        ) from error
    if not isinstance(payload, Mapping):
        raise PermanentParserError(
            f"{source_id} returned a non-object JSON document",
            code="INVALID_UPSTREAM_RESPONSE",
        )
    return payload


def _validate_policy(
    policy: SourcePolicy, source_id: str, access: AccessMethod
) -> None:
    if policy.source_id != source_id:
        raise SourcePolicyError("adapter source_id must equal policy source_id")
    if policy.parser_version != PARSER_VERSION:
        raise SourcePolicyError(
            f"policy parser_version must be {PARSER_VERSION} for {source_id}"
        )
    if policy.access_method is not access:
        raise SourcePolicyError(
            f"{source_id} requires policy access_method={access.value}"
        )


@dataclass(frozen=True, slots=True)
class VkApiAdapter:
    """Collect public VK wall posts and their comments through the official API."""

    _policy: SourcePolicy
    source_id: str = "vk"
    parser_version: str = PARSER_VERSION
    page_size: int = 100

    def __post_init__(self) -> None:
        _validate_policy(self._policy, self.source_id, AccessMethod.OFFICIAL_API)
        if not 1 <= self.page_size <= 100:
            raise SourcePolicyError("VK page_size must be in [1, 100]")

    def policy(self) -> SourcePolicy:
        return self._policy

    @staticmethod
    def _owner_id(value: str) -> int:
        try:
            owner_id = int(value)
        except ValueError as error:
            raise PermanentParserError(
                "VK community_ids must contain numeric identifiers",
                code="INVALID_CONNECTOR_OPTION",
            ) from error
        return -abs(owner_id)

    def _post_message(self, owner_id: int, item: Mapping[str, Any]) -> SourceMessage:
        post_id = _int_value(item.get("id"), "VK post.id", minimum=1)
        text = _required_text(item.get("text"), "VK post.text")
        return SourceMessage(
            source=self.source_id,
            external_id=f"post:{owner_id}:{post_id}",
            text=text,
            published_at=_timestamp(item.get("date"), "VK post.date"),
            url=f"https://vk.com/wall{owner_id}_{post_id}",
            author_id=str(item.get("from_id"))
            if item.get("from_id") is not None
            else None,
            metadata={"kind": "post", "owner_id": owner_id, "post_id": post_id},
        )

    def _comment_message(
        self,
        owner_id: int,
        post_id: int,
        item: Mapping[str, Any],
    ) -> SourceMessage:
        comment_id = _int_value(item.get("id"), "VK comment.id", minimum=1)
        text = _required_text(item.get("text"), "VK comment.text")
        metadata: dict[str, object] = {
            "kind": "comment",
            "owner_id": owner_id,
            "post_id": post_id,
        }
        if item.get("reply_to_comment") is not None:
            metadata["parent_external_id"] = (
                f"comment:{owner_id}:{post_id}:{item['reply_to_comment']}"
            )
        return SourceMessage(
            source=self.source_id,
            external_id=f"comment:{owner_id}:{post_id}:{comment_id}",
            text=text,
            published_at=_timestamp(item.get("date"), "VK comment.date"),
            url=f"https://vk.com/wall{owner_id}_{post_id}?reply={comment_id}",
            author_id=str(item.get("from_id"))
            if item.get("from_id") is not None
            else None,
            metadata=metadata,
        )

    @staticmethod
    def _vk_response(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        error = payload.get("error")
        if isinstance(error, Mapping):
            error_code = error.get("error_code", "unknown")
            raise PermanentParserError(
                str(error.get("error_msg", "VK API error")),
                code=f"VK_API_ERROR_{error_code}",
                details={"upstream_error_code": error_code},
            )
        response = payload.get("response")
        if not isinstance(response, Mapping):
            raise PermanentParserError(
                "VK response object is missing",
                code="INVALID_UPSTREAM_RESPONSE",
            )
        return response

    def fetch_page(
        self,
        request: ParserRequest,
        checkpoint: dict[str, object] | None,
        services: ParserServices,
    ) -> ParserPage:
        community_ids = _string_array(
            request.options.get("community_ids"),
            "options.community_ids",
        )
        api_version = str(request.options.get("api_version", "5.199"))
        include_comments = bool(request.options.get("include_comments", True))
        state = dict(checkpoint or {})
        community_index = _int_value(
            state.get("community_index", 0),
            "checkpoint.community_index",
        )
        wall_offset = _int_value(state.get("wall_offset", 0), "checkpoint.wall_offset")
        raw_queue = state.get("comment_queue", [])
        if not isinstance(raw_queue, list):
            raise PermanentParserError(
                "checkpoint.comment_queue must be an array",
                code="INVALID_CHECKPOINT",
            )
        comment_queue = [
            dict(_mapping(item, "checkpoint.comment_queue[]")) for item in raw_queue
        ]

        if comment_queue:
            target = comment_queue[0]
            owner_id = _int_value(
                target.get("owner_id"),
                "checkpoint.owner_id",
                minimum=None,
            )
            post_id = _int_value(target.get("post_id"), "checkpoint.post_id", minimum=1)
            offset = _int_value(target.get("offset", 0), "checkpoint.comment_offset")
            url = _query_url(
                "https://api.vk.com/method/wall.getComments",
                {
                    "owner_id": owner_id,
                    "post_id": post_id,
                    "offset": offset,
                    "count": self.page_size,
                    "thread_items_count": 10,
                    "need_likes": 0,
                    "v": api_version,
                },
            )
            payload = self._vk_response(
                _json_object(services.transport.get(url), self.source_id)
            )
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise PermanentParserError(
                    "VK comments items must be an array",
                    code="INVALID_UPSTREAM_RESPONSE",
                )
            messages = tuple(
                self._comment_message(owner_id, post_id, _mapping(item, "VK comment"))
                for item in items
                if isinstance(item, Mapping) and str(item.get("text", "")).strip()
            )
            if len(items) >= self.page_size:
                comment_queue[0] = {
                    "owner_id": owner_id,
                    "post_id": post_id,
                    "offset": offset + len(items),
                }
            else:
                comment_queue.pop(0)
            done = community_index >= len(community_ids) and not comment_queue
            return ParserPage(
                messages=messages,
                next_checkpoint={
                    "community_index": community_index,
                    "wall_offset": wall_offset,
                    "comment_queue": comment_queue,
                },
                done=done,
                raw_items_seen=len(items),
            )

        if community_index >= len(community_ids):
            return ParserPage(
                messages=(), next_checkpoint=state or None, done=True, raw_items_seen=0
            )

        owner_id = self._owner_id(community_ids[community_index])
        url = _query_url(
            "https://api.vk.com/method/wall.get",
            {
                "owner_id": owner_id,
                "offset": wall_offset,
                "count": self.page_size,
                "filter": "owner",
                "v": api_version,
            },
        )
        payload = self._vk_response(
            _json_object(services.transport.get(url), self.source_id)
        )
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise PermanentParserError(
                "VK wall items must be an array",
                code="INVALID_UPSTREAM_RESPONSE",
            )
        messages: list[SourceMessage] = []
        for item in items:
            if not isinstance(item, Mapping) or not str(item.get("text", "")).strip():
                continue
            message = self._post_message(owner_id, item)
            messages.append(message)
            if include_comments:
                comment_queue.append(
                    {
                        "owner_id": owner_id,
                        "post_id": message.metadata["post_id"],
                        "offset": 0,
                    }
                )
        if len(items) >= self.page_size:
            next_community_index = community_index
            next_wall_offset = wall_offset + len(items)
        else:
            next_community_index = community_index + 1
            next_wall_offset = 0
        done = next_community_index >= len(community_ids) and not comment_queue
        return ParserPage(
            messages=tuple(messages),
            next_checkpoint={
                "community_index": next_community_index,
                "wall_offset": next_wall_offset,
                "comment_queue": comment_queue,
            },
            done=done,
            raw_items_seen=len(items),
        )


class OkRequestSigner(Protocol):
    def signed_parameters(
        self, parameters: Mapping[str, object]
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class OkApiCredentials:
    application_key: str
    application_secret: str
    access_token: str

    def __post_init__(self) -> None:
        for field_name in ("application_key", "application_secret", "access_token"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )


@dataclass(frozen=True, slots=True)
class OkMd5Signer:
    credentials: OkApiCredentials

    def signed_parameters(
        self, parameters: Mapping[str, object]
    ) -> Mapping[str, object]:
        unsigned = {
            key: value for key, value in parameters.items() if value is not None
        }
        unsigned["application_key"] = self.credentials.application_key
        signature_source = "".join(f"{key}={unsigned[key]}" for key in sorted(unsigned))
        session_secret = hashlib.md5(
            f"{self.credentials.access_token}{self.credentials.application_secret}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        signature = hashlib.md5(
            f"{signature_source}{session_secret}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        return {
            **unsigned,
            "access_token": self.credentials.access_token,
            "sig": signature,
        }


class UnavailableOkSigner:
    def signed_parameters(
        self, parameters: Mapping[str, object]
    ) -> Mapping[str, object]:
        del parameters
        raise PermanentParserError(
            "OK API credentials are not configured",
            code="CREDENTIAL_UNAVAILABLE",
        )


@dataclass(frozen=True, slots=True)
class OkApiAdapter:
    """Collect public discussion comments through the official OK API."""

    _policy: SourcePolicy
    signer: OkRequestSigner
    source_id: str = "ok"
    parser_version: str = PARSER_VERSION
    page_size: int = 100

    def __post_init__(self) -> None:
        _validate_policy(self._policy, self.source_id, AccessMethod.OFFICIAL_API)
        if not 1 <= self.page_size <= 100:
            raise SourcePolicyError("OK page_size must be in [1, 100]")

    def policy(self) -> SourcePolicy:
        return self._policy

    @staticmethod
    def _ok_error(payload: Mapping[str, Any]) -> None:
        error_code = payload.get("error_code")
        if error_code is None and isinstance(payload.get("error"), Mapping):
            error_code = payload["error"].get("code")
        if error_code is not None:
            raise PermanentParserError(
                str(payload.get("error_msg") or payload.get("error") or "OK API error"),
                code=f"OK_API_ERROR_{error_code}",
                details={"upstream_error_code": error_code},
            )

    def _message(
        self,
        discussion_id: str,
        discussion_type: str,
        item: Mapping[str, Any],
    ) -> SourceMessage:
        comment_id = _required_text(item.get("id"), "OK comment.id")
        text = _required_text(
            item.get("message") or item.get("text"), "OK comment.message"
        )
        published = item.get("date_ms", item.get("created_ms", item.get("date")))
        metadata: dict[str, object] = {
            "kind": "comment",
            "discussion_id": discussion_id,
            "discussion_type": discussion_type,
        }
        parent = item.get("parent_id") or item.get("parentCommentId")
        if parent is not None:
            metadata["parent_external_id"] = (
                f"comment:{discussion_type}:{discussion_id}:{parent}"
            )
        return SourceMessage(
            source=self.source_id,
            external_id=f"comment:{discussion_type}:{discussion_id}:{comment_id}",
            text=text,
            published_at=_timestamp(published, "OK comment.date"),
            url=(str(item["link"]) if item.get("link") else None),
            author_id=(
                str(item.get("author_id") or item.get("author_ref"))
                if item.get("author_id") is not None
                or item.get("author_ref") is not None
                else None
            ),
            metadata=metadata,
        )

    def fetch_page(
        self,
        request: ParserRequest,
        checkpoint: dict[str, object] | None,
        services: ParserServices,
    ) -> ParserPage:
        discussion_ids = _string_array(
            request.options.get("discussion_ids"), "options.discussion_ids"
        )
        discussion_types = _string_array(
            request.options.get("discussion_types"),
            "options.discussion_types",
        )
        if len(discussion_types) not in {1, len(discussion_ids)}:
            raise PermanentParserError(
                "discussion_types must contain one value or match discussion_ids",
                code="INVALID_CONNECTOR_OPTION",
            )
        state = dict(checkpoint or {})
        index = _int_value(
            state.get("discussion_index", 0), "checkpoint.discussion_index"
        )
        anchor = state.get("anchor")
        if anchor is not None and not isinstance(anchor, str):
            raise PermanentParserError(
                "checkpoint.anchor must be a string or null",
                code="INVALID_CHECKPOINT",
            )
        if index >= len(discussion_ids):
            return ParserPage(
                messages=(), next_checkpoint=state or None, done=True, raw_items_seen=0
            )
        discussion_id = discussion_ids[index]
        discussion_type = discussion_types[0 if len(discussion_types) == 1 else index]
        parameters = self.signer.signed_parameters(
            {
                "method": "discussions.getComments",
                "discussionId": discussion_id,
                "discussionType": discussion_type,
                "anchor": anchor,
                "direction": "FORWARD",
                "count": self.page_size,
                "format": "json",
            }
        )
        payload = _json_object(
            services.transport.get(_query_url("https://api.ok.ru/fb.do", parameters)),
            self.source_id,
        )
        self._ok_error(payload)
        raw_comments = payload.get("comments", payload.get("data", []))
        if not isinstance(raw_comments, list):
            raise PermanentParserError(
                "OK comments must be an array",
                code="INVALID_UPSTREAM_RESPONSE",
            )
        messages = tuple(
            self._message(discussion_id, discussion_type, item)
            for item in raw_comments
            if isinstance(item, Mapping)
            and str(item.get("message") or item.get("text") or "").strip()
        )
        next_anchor = payload.get("anchor")
        has_more = bool(payload.get("has_more") or payload.get("hasMore"))
        if has_more and next_anchor:
            next_index = index
            next_checkpoint = {"discussion_index": index, "anchor": str(next_anchor)}
        else:
            next_index = index + 1
            next_checkpoint = {"discussion_index": next_index, "anchor": None}
        return ParserPage(
            messages=messages,
            next_checkpoint=next_checkpoint,
            done=next_index >= len(discussion_ids),
            raw_items_seen=len(raw_comments),
        )


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node]
    text_parts: list[str]
    parent: _Node | None = None

    def text(self) -> str:
        chunks = list(self.text_parts)
        for child in self.children:
            chunks.append(child.text())
        return " ".join(" ".join(chunks).split())


class _DomParser(HTMLParser):
    _VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {}, [], [])
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(
            tag.lower(),
            {key.lower(): value or "" for key, value in attrs},
            [],
            [],
            self._stack[-1],
        )
        self._stack[-1].children.append(node)
        if tag.lower() not in self._VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == lowered:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].text_parts.append(data)


def _walk(node: _Node) -> list[_Node]:
    result = [node]
    for child in node.children:
        result.extend(_walk(child))
    return result


_SELECTOR_RE = re.compile(
    r"^(?P<tag>[a-zA-Z][a-zA-Z0-9_-]*)?"
    r"(?P<id>#[a-zA-Z0-9_-]+)?"
    r"(?P<class>\.[a-zA-Z0-9_-]+)?"
    r"(?P<attr>\[[a-zA-Z0-9_:-]+(?:=(?:'[^']*'|\"[^\"]*\"|[^\]]+))?\])?$"
)


def _matches(node: _Node, selector: str) -> bool:
    match = _SELECTOR_RE.fullmatch(selector.strip())
    if match is None:
        raise PermanentParserError(
            f"unsupported CSS selector {selector!r}",
            code="UNSUPPORTED_HTML_SELECTOR",
        )
    tag = match.group("tag")
    if tag and node.tag != tag.lower():
        return False
    identifier = match.group("id")
    if identifier and node.attrs.get("id") != identifier[1:]:
        return False
    class_name = match.group("class")
    if class_name and class_name[1:] not in node.attrs.get("class", "").split():
        return False
    attribute = match.group("attr")
    if attribute:
        expression = attribute[1:-1]
        if "=" in expression:
            name, expected = expression.split("=", 1)
            expected = expected.strip("'\"")
            if node.attrs.get(name.lower()) != expected:
                return False
        elif expression.lower() not in node.attrs:
            return False
    return True


def _select(root: _Node, selector: str) -> list[_Node]:
    parts = tuple(part for part in selector.strip().split() if part)
    if not parts:
        return []
    candidates = _walk(root)
    current = [node for node in candidates if _matches(node, parts[0])]
    for part in parts[1:]:
        descendants: list[_Node] = []
        for node in current:
            for child in _walk(node)[1:]:
                if _matches(child, part):
                    descendants.append(child)
        current = descendants
    return current


def _first_value(
    root: _Node, selector: str, *, attribute: str | None = None
) -> str | None:
    nodes = _select(root, selector)
    if not nodes:
        return None
    if attribute is not None:
        return nodes[0].attrs.get(attribute)
    return nodes[0].text() or None


def _selector_value(
    root: _Node, selector: str, *, preferred_attributes: tuple[str, ...] = ()
) -> str | None:
    nodes = _select(root, selector)
    if not nodes:
        return None
    for attribute in preferred_attributes:
        value = nodes[0].attrs.get(attribute)
        if value:
            return value
    return nodes[0].text() or None


def _stable_external_id(source_id: str, url: str, suffix: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{url}\0{suffix}".encode()).hexdigest()
    return digest[:32]


def _profile_from_mapping(payload: Mapping[str, Any]) -> HtmlSourceProfile:
    selectors_payload = _mapping(payload.get("selectors"), "site_profile.selectors")
    selectors = HtmlSelectors(
        title=selectors_payload.get("title"),
        body=selectors_payload.get("body"),
        published_at=selectors_payload.get("published_at"),
        author=selectors_payload.get("author"),
        canonical_url=selectors_payload.get("canonical_url"),
        comment_item=selectors_payload.get("comment_item"),
        comment_text=selectors_payload.get("comment_text"),
        comment_id=selectors_payload.get("comment_id"),
        comment_author=selectors_payload.get("comment_author"),
        comment_published_at=selectors_payload.get("comment_published_at"),
        comment_parent_id=selectors_payload.get("comment_parent_id"),
    )
    return HtmlSourceProfile(
        source_id=payload.get("source_id"),
        display_name=payload.get("display_name"),
        kind=HtmlSourceKind(payload.get("kind")),
        base_url=payload.get("base_url"),
        region=payload.get("region"),
        municipalities=tuple(payload.get("municipalities", ())),
        discovery_mode=DiscoveryMode(payload.get("discovery_mode")),
        discovery_urls=tuple(payload.get("discovery_urls", ())),
        selectors=selectors,
        robots_url=payload.get("robots_url"),
        render_javascript=payload.get("render_javascript", False),
        rendering_justification=payload.get("rendering_justification"),
        enabled=payload.get("enabled", False),
    )


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ElementTree.Element, names: tuple[str, ...]) -> str | None:
    for child in element.iter():
        if _xml_local_name(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return None


@dataclass(frozen=True, slots=True)
class HtmlConnectorAdapter:
    """Parse allowlisted HTML, RSS/Atom, or sitemap documents without JS execution."""

    _policy: SourcePolicy
    source_id: str
    default_selectors: HtmlSelectors | None = None
    parser_version: str = PARSER_VERSION

    def __post_init__(self) -> None:
        _validate_policy(self._policy, self.source_id, AccessMethod.PUBLIC_WEB)

    def policy(self) -> SourcePolicy:
        return self._policy

    def _profile_and_urls(
        self, request: ParserRequest
    ) -> tuple[HtmlSourceProfile | None, list[str]]:
        profile: HtmlSourceProfile | None = None
        raw_profile = request.options.get("site_profile")
        if raw_profile is not None:
            profile = _profile_from_mapping(
                _mapping(raw_profile, "options.site_profile")
            )
        raw_urls = request.options.get("urls")
        if raw_urls is None:
            if self.source_id == "dzen":
                raw_urls = request.options.get("channel_urls")
            elif self.source_id == "pikabu":
                raw_urls = request.options.get("community_urls")
            elif self.source_id == "rutube":
                raw_urls = request.options.get("channel_urls")
        urls = (
            list(_string_array(raw_urls, "options.urls"))
            if raw_urls is not None
            else []
        )
        if not urls and profile is not None:
            urls = list(profile.discovery_urls)
        if not urls:
            raise PermanentParserError(
                "HTML connector requires URLs or a site profile with discovery URLs",
                code="INVALID_CONNECTOR_OPTION",
            )
        allowed_hosts = set(self._policy.security.allowed_domains)
        base_host = urlsplit(profile.base_url).hostname if profile is not None else None
        for url in urls:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise PermanentParserError(
                    "HTML connector URLs must use HTTPS",
                    code="INVALID_CONNECTOR_OPTION",
                )
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            if (
                allowed_hosts
                and host not in allowed_hosts
                and not any(
                    self._policy.security.allow_subdomains
                    and host.endswith(f".{allowed}")
                    for allowed in allowed_hosts
                )
            ):
                raise PermanentParserError(
                    "HTML connector URL is outside policy allowlist",
                    code="INVALID_CONNECTOR_OPTION",
                )
            if base_host and host != base_host and not host.endswith(f".{base_host}"):
                raise PermanentParserError(
                    "HTML connector URL is outside site-profile domain",
                    code="INVALID_CONNECTOR_OPTION",
                )
        return profile, urls

    def _html_messages(
        self,
        url: str,
        body: bytes,
        selectors: HtmlSelectors,
    ) -> tuple[SourceMessage, ...]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PermanentParserError(
                "HTML response is not UTF-8",
                code="INVALID_HTML_ENCODING",
            ) from error
        parser = _DomParser()
        parser.feed(text)
        root = parser.root
        title = _selector_value(root, selectors.title)
        article_text = _selector_value(root, selectors.body)
        published_value = _selector_value(
            root,
            selectors.published_at,
            preferred_attributes=("datetime", "content"),
        )
        canonical = (
            _selector_value(
                root,
                selectors.canonical_url,
                preferred_attributes=("href",),
            )
            if selectors.canonical_url
            else None
        )
        canonical_url = urljoin(url, canonical) if canonical else url
        messages: list[SourceMessage] = []
        if article_text and published_value:
            content = f"{title}. {article_text}" if title else article_text
            author = (
                _selector_value(root, selectors.author) if selectors.author else None
            )
            messages.append(
                SourceMessage(
                    source=self.source_id,
                    external_id=(
                        "document:"
                        + _stable_external_id(self.source_id, canonical_url, "document")
                    ),
                    text=content,
                    published_at=_timestamp(published_value, "HTML published_at"),
                    url=canonical_url,
                    author_id=author,
                    metadata={"kind": "document", "title": title or ""},
                )
            )
        if selectors.comment_item and selectors.comment_text:
            for index, comment_node in enumerate(_select(root, selectors.comment_item)):
                comment_text = _selector_value(comment_node, selectors.comment_text)
                if not comment_text:
                    continue
                comment_id = (
                    _selector_value(
                        comment_node,
                        selectors.comment_id,
                        preferred_attributes=("data-comment-id", "id"),
                    )
                    if selectors.comment_id
                    else None
                ) or str(index)
                comment_published = (
                    _selector_value(
                        comment_node,
                        selectors.comment_published_at,
                        preferred_attributes=("datetime", "content"),
                    )
                    if selectors.comment_published_at
                    else published_value
                )
                if not comment_published:
                    continue
                comment_author = (
                    _selector_value(comment_node, selectors.comment_author)
                    if selectors.comment_author
                    else None
                )
                metadata: dict[str, object] = {
                    "kind": "comment",
                    "document_url": canonical_url,
                }
                parent = (
                    _selector_value(
                        comment_node,
                        selectors.comment_parent_id,
                        preferred_attributes=("data-parent-id",),
                    )
                    if selectors.comment_parent_id
                    else None
                )
                if parent:
                    metadata["parent_external_id"] = f"comment:{parent}"
                messages.append(
                    SourceMessage(
                        source=self.source_id,
                        external_id=(
                            "comment:"
                            + _stable_external_id(
                                self.source_id, canonical_url, comment_id
                            )
                        ),
                        text=comment_text,
                        published_at=_timestamp(
                            comment_published, "HTML comment published_at"
                        ),
                        url=f"{canonical_url}#comment-{comment_id}",
                        author_id=comment_author,
                        metadata=metadata,
                    )
                )
        if not messages:
            raise PermanentParserError(
                "configured HTML selectors produced no messages",
                code="EMPTY_HTML_EXTRACTION",
                details={"url": url},
            )
        return tuple(messages)

    def _xml_messages(
        self,
        url: str,
        body: bytes,
    ) -> tuple[tuple[SourceMessage, ...], tuple[str, ...]]:
        try:
            root = safe_xml_fromstring(body)
        except ElementTree.ParseError as error:
            raise PermanentParserError(
                "upstream XML is invalid",
                code="INVALID_XML_RESPONSE",
            ) from error
        root_name = _xml_local_name(root.tag)
        if root_name in {"urlset", "sitemapindex"}:
            discovered = tuple(
                element.text.strip()
                for element in root.iter()
                if _xml_local_name(element.tag) == "loc"
                and element.text
                and element.text.strip()
            )
            return (), discovered
        entries = [
            element
            for element in root.iter()
            if _xml_local_name(element.tag) in {"item", "entry"}
        ]
        messages: list[SourceMessage] = []
        for index, entry in enumerate(entries):
            title = _child_text(entry, ("title",))
            description = _child_text(entry, ("description", "summary", "content"))
            published = _child_text(entry, ("pubdate", "published", "updated"))
            link = _child_text(entry, ("link", "guid", "id"))
            if link is None:
                for child in entry:
                    if _xml_local_name(child.tag) == "link" and child.attrib.get(
                        "href"
                    ):
                        link = child.attrib["href"]
                        break
            if not description or not published:
                continue
            item_url = urljoin(url, link) if link else url
            messages.append(
                SourceMessage(
                    source=self.source_id,
                    external_id=(
                        "feed:"
                        + _stable_external_id(self.source_id, item_url, str(index))
                    ),
                    text=f"{title}. {description}" if title else description,
                    published_at=_timestamp(published, "feed published_at"),
                    url=item_url,
                    metadata={"kind": "feed_entry", "title": title or ""},
                )
            )
        if not messages:
            raise PermanentParserError(
                "RSS/Atom document produced no messages",
                code="EMPTY_FEED_EXTRACTION",
                details={"url": url},
            )
        return tuple(messages), ()

    def fetch_page(
        self,
        request: ParserRequest,
        checkpoint: dict[str, object] | None,
        services: ParserServices,
    ) -> ParserPage:
        profile, initial_urls = self._profile_and_urls(request)
        state = dict(checkpoint or {})
        queue_value = state.get("queue", initial_urls)
        if not isinstance(queue_value, list):
            raise PermanentParserError(
                "checkpoint.queue must be an array",
                code="INVALID_CHECKPOINT",
            )
        queue = [str(item) for item in queue_value]
        if not queue:
            return ParserPage(
                messages=(), next_checkpoint={"queue": []}, done=True, raw_items_seen=0
            )
        url = queue.pop(0)
        response = services.transport.get(
            url,
            headers={
                "Accept": (
                    "text/html, application/rss+xml, "
                    "application/atom+xml, application/xml;q=0.9"
                )
            },
        )
        content_type = (response.content_type or "").split(";", 1)[0].strip().lower()
        if content_type in {
            "application/rss+xml",
            "application/atom+xml",
            "application/xml",
            "text/xml",
        } or body_looks_xml(response.body):
            messages, discovered = self._xml_messages(url, response.body)
            for discovered_url in discovered:
                if discovered_url not in queue:
                    queue.append(discovered_url)
        else:
            selectors = (
                profile.selectors if profile is not None else self.default_selectors
            )
            if selectors is None:
                raise PermanentParserError(
                    "HTML selector profile is required",
                    code="INVALID_CONNECTOR_OPTION",
                )
            messages = self._html_messages(url, response.body, selectors)
        return ParserPage(
            messages=messages,
            next_checkpoint={"queue": queue},
            done=not queue,
            raw_items_seen=max(1, len(messages)),
        )


def body_looks_xml(body: bytes) -> bool:
    return body.lstrip().startswith(b"<?xml") or body.lstrip().startswith(
        (b"<rss", b"<feed", b"<urlset", b"<sitemapindex")
    )


def _default_html_selectors() -> HtmlSelectors:
    return HtmlSelectors(
        title="h1",
        body="article",
        published_at="time[datetime]",
        author=".author",
        canonical_url="link[rel='canonical']",
        comment_item=".comment",
        comment_text=".comment-text",
        comment_id="[data-comment-id]",
        comment_author=".comment-author",
        comment_published_at="time[datetime]",
        comment_parent_id="[data-parent-id]",
    )


def create_connector_adapter(
    policy: SourcePolicy,
    *,
    ok_signer: OkRequestSigner | None = None,
) -> VkApiAdapter | OkApiAdapter | HtmlConnectorAdapter:
    """Create the source-specific adapter bound to one reviewed policy."""

    source_id = policy.source_id
    prepared_connector_catalog().get(source_id)
    if source_id == "vk":
        return VkApiAdapter(policy)
    if source_id == "ok":
        return OkApiAdapter(policy, ok_signer or UnavailableOkSigner())
    if source_id in {"local-media", "municipal-public", "dzen", "pikabu", "rutube"}:
        return HtmlConnectorAdapter(
            policy,
            source_id=source_id,
            default_selectors=_default_html_selectors(),
        )
    raise SourcePolicyError(f"prepared connector {source_id!r} has no adapter")


def build_prepared_parser_registry(
    policies: Mapping[str, SourcePolicy],
    *,
    ok_signer: OkRequestSigner | None = None,
    require_all: bool = True,
) -> ParserRegistry:
    """Register prepared connector adapters in the existing parser registry."""

    catalog_ids = set(prepared_connector_catalog().source_ids())
    normalized_policies = {
        key.strip().lower(): value for key, value in policies.items()
    }
    policy_ids = set(normalized_policies)
    unknown = sorted(policy_ids - catalog_ids)
    if unknown:
        raise SourcePolicyError(
            f"policies contain unknown prepared connectors: {', '.join(unknown)}"
        )
    if require_all:
        missing = sorted(catalog_ids - policy_ids)
        if missing:
            raise SourcePolicyError(
                f"policies are missing prepared connectors: {', '.join(missing)}"
            )
    adapters = [
        create_connector_adapter(normalized_policies[source_id], ok_signer=ok_signer)
        for source_id in sorted(policy_ids)
    ]
    return ParserRegistry(adapters)


@dataclass(frozen=True, slots=True)
class ConnectorSuiteResult:
    messages_by_source: Mapping[str, tuple[SourceMessage, ...]]
    coverage_by_source: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "messages_by_source",
            MappingProxyType(dict(self.messages_by_source)),
        )
        object.__setattr__(
            self,
            "coverage_by_source",
            MappingProxyType(dict(self.coverage_by_source)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sources": {
                source_id: {
                    "message_count": len(messages),
                    "external_ids": [message.external_id for message in messages],
                    "texts": [message.text for message in messages],
                    "coverage": dict(self.coverage_by_source[source_id]),
                }
                for source_id, messages in sorted(self.messages_by_source.items())
            }
        }
