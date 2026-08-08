"""Telegram public-channel collection through an authenticated MTProto user session.

Workers never perform interactive login. Deployment supplies an already-authorized
service-account session through secret files. Author identifiers are not retained.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from ..contracts import SourceMessage
from .collection import CandidateCollectionError, CandidateCollectionResult
from .models import (
    GeoScope,
    SourceCandidate,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
)

_RELEVANT_HINTS = frozenset({"house", "street", "district"})
_IGNORED_REPLY_ERRORS = frozenset(
    {"PeerIdInvalidError", "MsgIdInvalidError", "ChannelPrivateError"}
)


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    api_id: int
    api_hash: str
    session_string: str

    def __post_init__(self) -> None:
        invalid_api_id = (
            not isinstance(self.api_id, int)
            or isinstance(self.api_id, bool)
            or self.api_id <= 0
        )
        if invalid_api_id:
            raise ValueError("Telegram api_id must be a positive integer")
        for name in ("api_hash", "session_string"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Telegram {name} must not be empty")
            object.__setattr__(self, name, value.strip())

    @classmethod
    def from_secret_files(
        cls,
        *,
        api_id_file: Path,
        api_hash_file: Path,
        session_file: Path,
    ) -> TelegramCredentials:
        try:
            raw_id = Path(api_id_file).read_text(encoding="utf-8").strip()
            api_hash = Path(api_hash_file).read_text(encoding="utf-8").strip()
            session = Path(session_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise CandidateCollectionError(
                SourceReasonCode.API_CREDENTIALS_MISSING,
                "Telegram MTProto credential secret file is unavailable",
                state=SourceState.CONFIGURATION_MISSING,
            ) from error
        try:
            api_id = int(raw_id)
            return cls(
                api_id=api_id,
                api_hash=api_hash,
                session_string=session,
            )
        except ValueError as error:
            raise CandidateCollectionError(
                SourceReasonCode.API_CREDENTIALS_MISSING,
                f"Telegram credential secret is malformed: {error}",
                state=SourceState.CONFIGURATION_MISSING,
            ) from error


@dataclass(frozen=True, slots=True)
class TelegramTarget:
    username: str
    message_id: int | None = None

    def __post_init__(self) -> None:
        username = self.username.strip().lstrip("@").lower()
        if not username or not username.replace("_", "a").isalnum():
            raise ValueError("Telegram public username is invalid")
        object.__setattr__(self, "username", username)
        if self.message_id is not None and (
            not isinstance(self.message_id, int) or self.message_id <= 0
        ):
            raise ValueError("Telegram message_id must be a positive integer")

    @property
    def channel_url(self) -> str:
        return f"https://t.me/{self.username}"


def parse_telegram_target(url: str) -> TelegramTarget:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in {"t.me", "telegram.me"}:
        raise CandidateCollectionError(
            SourceReasonCode.UNSUPPORTED_PAGE,
            "Telegram candidate URL is not a public t.me/telegram.me URL",
            state=SourceState.BLOCKED,
        )
    parts = [item for item in parsed.path.split("/") if item]
    if parts and parts[0] == "s":
        parts = parts[1:]
    if not parts or parts[0].startswith("+") or parts[0] == "joinchat":
        raise CandidateCollectionError(
            SourceReasonCode.AUTH_REQUIRED,
            "Telegram candidate is a private/invite link, not a public channel",
            state=SourceState.AUTH_REQUIRED,
        )
    message_id: int | None = None
    if len(parts) > 1:
        with suppress(ValueError):
            message_id = int(parts[1])
    try:
        return TelegramTarget(username=parts[0], message_id=message_id)
    except ValueError as error:
        raise CandidateCollectionError(
            SourceReasonCode.UNSUPPORTED_PAGE,
            str(error),
            state=SourceState.BLOCKED,
        ) from error


@dataclass(frozen=True, slots=True)
class TelegramRecord:
    channel_username: str
    message_id: int
    text: str
    published_at: datetime
    is_comment: bool = False
    parent_message_id: int | None = None

    def __post_init__(self) -> None:
        username = self.channel_username.strip().lstrip("@").lower()
        if not username:
            raise ValueError("channel_username must not be empty")
        object.__setattr__(self, "channel_username", username)
        if not isinstance(self.message_id, int) or self.message_id <= 0:
            raise ValueError("message_id must be positive")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Telegram record text must not be empty")
        object.__setattr__(self, "text", self.text.strip())
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise ValueError("Telegram published_at must include UTC offset")
        object.__setattr__(
            self,
            "published_at",
            self.published_at.astimezone(UTC),
        )
        if self.parent_message_id is not None and (
            not isinstance(self.parent_message_id, int)
            or self.parent_message_id <= 0
        ):
            raise ValueError("parent_message_id must be positive")
        if self.is_comment and self.parent_message_id is None:
            raise ValueError("Telegram comment requires parent_message_id")

    @property
    def url(self) -> str:
        if self.is_comment and self.parent_message_id is not None:
            return (
                f"https://t.me/{self.channel_username}/{self.parent_message_id}"
                f"?comment={self.message_id}"
            )
        return f"https://t.me/{self.channel_username}/{self.message_id}"


class TelegramGateway(Protocol):
    def collect(
        self,
        target: TelegramTarget,
        *,
        search_terms: tuple[str, ...],
        history_limit: int,
        comments_per_post: int,
    ) -> tuple[TelegramRecord, ...]: ...


class UnavailableTelegramGateway:
    def __init__(
        self,
        reason: str = "Telegram MTProto credentials are not configured",
    ) -> None:
        self.reason = reason

    def collect(
        self,
        target: TelegramTarget,
        *,
        search_terms: tuple[str, ...],
        history_limit: int,
        comments_per_post: int,
    ) -> tuple[TelegramRecord, ...]:
        del target, search_terms, history_limit, comments_per_post
        raise CandidateCollectionError(
            SourceReasonCode.API_CREDENTIALS_MISSING,
            self.reason,
            state=SourceState.CONFIGURATION_MISSING,
        )


def _telethon_error(error: BaseException) -> CandidateCollectionError:
    name = type(error).__name__
    if name == "FloodWaitError":
        seconds = getattr(error, "seconds", None)
        label = (
            f"Telegram MTProto flood wait ({seconds}s)"
            if isinstance(seconds, int)
            else "Telegram MTProto flood wait"
        )
        return CandidateCollectionError(
            SourceReasonCode.HTTP_429,
            label,
            state=SourceState.UNAVAILABLE,
            retryable=True,
            details={"wait_seconds": seconds},
        )
    if name in {
        "AuthKeyUnregisteredError",
        "AuthKeyInvalidError",
        "SessionPasswordNeededError",
        "UserDeactivatedBanError",
        "PhoneNumberBannedError",
        "ChannelPrivateError",
    }:
        return CandidateCollectionError(
            SourceReasonCode.AUTH_REQUIRED,
            f"Telegram MTProto authorization cannot access the source: {name}",
            state=SourceState.AUTH_REQUIRED,
        )
    if name in {
        "UsernameInvalidError",
        "UsernameNotOccupiedError",
        "PeerIdInvalidError",
    }:
        return CandidateCollectionError(
            SourceReasonCode.NO_RESULTS,
            f"Telegram public channel is unavailable or missing: {name}",
            state=SourceState.NO_RELEVANT_RESULTS,
        )
    return CandidateCollectionError(
        SourceReasonCode.PARSER_FAILED,
        f"Telegram MTProto request failed: {name}",
        state=SourceState.FAILED,
        retryable=name in {"RpcCallFailError", "ServerError", "TimedOutError"},
    )


def _message_record(
    message: Any,
    username: str,
    *,
    parent_id: int | None = None,
) -> TelegramRecord | None:
    text = (
        getattr(message, "raw_text", None)
        or getattr(message, "message", None)
        or ""
    )
    date = getattr(message, "date", None)
    message_id = getattr(message, "id", None)
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(date, datetime) or not isinstance(message_id, int):
        return None
    if date.tzinfo is None or date.utcoffset() is None:
        return None
    return TelegramRecord(
        channel_username=username,
        message_id=message_id,
        text=text,
        published_at=date,
        is_comment=parent_id is not None,
        parent_message_id=parent_id,
    )


@dataclass(frozen=True, slots=True)
class TelethonTelegramGateway:
    credentials: TelegramCredentials
    request_retries: int = 2
    connection_retries: int = 2
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not isinstance(self.credentials, TelegramCredentials):
            raise TypeError("credentials must be TelegramCredentials")
        for name in ("request_retries", "connection_retries"):
            value = getattr(self, name)
            if not isinstance(value, int) or not 0 <= value <= 5:
                raise ValueError(f"{name} must be in [0, 5]")
        if (
            not isinstance(self.timeout_seconds, int | float)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")

    def _client(self) -> Any:
        try:
            from telethon.sessions import StringSession
            from telethon.sync import TelegramClient
        except ImportError as error:
            raise CandidateCollectionError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                "Telethon runtime is not installed",
                state=SourceState.CONFIGURATION_MISSING,
            ) from error
        return TelegramClient(
            StringSession(self.credentials.session_string),
            self.credentials.api_id,
            self.credentials.api_hash,
            connection_retries=self.connection_retries,
            request_retries=self.request_retries,
            timeout=float(self.timeout_seconds),
            auto_reconnect=False,
        )

    @staticmethod
    def _terms(search_terms: tuple[str, ...]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for item in search_terms:
            cleaned = " ".join(item.split()).strip()
            key = cleaned.casefold()
            if len(cleaned) >= 3 and key not in seen:
                result.append(cleaned)
                seen.add(key)
        return tuple(result[:4])

    def collect(
        self,
        target: TelegramTarget,
        *,
        search_terms: tuple[str, ...],
        history_limit: int,
        comments_per_post: int,
    ) -> tuple[TelegramRecord, ...]:
        if not 1 <= history_limit <= 500:
            raise ValueError("history_limit must be in [1, 500]")
        if not 0 <= comments_per_post <= 200:
            raise ValueError("comments_per_post must be in [0, 200]")
        client = self._client()
        try:
            client.connect()
            if not client.is_user_authorized():
                raise CandidateCollectionError(
                    SourceReasonCode.AUTH_REQUIRED,
                    "Telegram service-account session is not authorized",
                    state=SourceState.AUTH_REQUIRED,
                )
            entity = client.get_entity(target.username)
            messages = self._messages(
                client,
                entity,
                target,
                search_terms=search_terms,
                history_limit=history_limit,
            )
            return self._records(
                client,
                entity,
                target.username,
                messages,
                comments_per_post=comments_per_post,
            )
        except CandidateCollectionError:
            raise
        except Exception as error:  # noqa: BLE001 - MTProto isolation boundary
            raise _telethon_error(error) from error
        finally:
            with suppress(Exception):  # noqa: BLE001 - cleanup only
                client.disconnect()

    def _messages(
        self,
        client: Any,
        entity: Any,
        target: TelegramTarget,
        *,
        search_terms: tuple[str, ...],
        history_limit: int,
    ) -> list[Any]:
        if target.message_id is not None:
            message = client.get_messages(entity, ids=target.message_id)
            return [message] if message is not None else []
        terms = self._terms(search_terms)
        if not terms:
            return list(client.iter_messages(entity, limit=history_limit))
        messages: list[Any] = []
        seen: set[int] = set()
        per_term = max(5, history_limit // len(terms))
        for term in terms:
            for message in client.iter_messages(
                entity,
                search=term,
                limit=per_term,
            ):
                message_id = getattr(message, "id", None)
                if isinstance(message_id, int) and message_id not in seen:
                    messages.append(message)
                    seen.add(message_id)
                if len(messages) >= history_limit:
                    return messages
        return messages

    @staticmethod
    def _records(
        client: Any,
        entity: Any,
        username: str,
        messages: list[Any],
        *,
        comments_per_post: int,
    ) -> tuple[TelegramRecord, ...]:
        records: list[TelegramRecord] = []
        seen: set[tuple[int, bool]] = set()
        for message in messages:
            record = _message_record(message, username)
            if record is None:
                continue
            key = (record.message_id, False)
            if key not in seen:
                records.append(record)
                seen.add(key)
            if comments_per_post <= 0:
                continue
            try:
                replies = client.iter_messages(
                    entity,
                    reply_to=record.message_id,
                    limit=comments_per_post,
                )
                for reply in replies:
                    comment = _message_record(
                        reply,
                        username,
                        parent_id=record.message_id,
                    )
                    if comment is None:
                        continue
                    comment_key = (comment.message_id, True)
                    if comment_key not in seen:
                        records.append(comment)
                        seen.add(comment_key)
            except Exception as error:  # noqa: BLE001 - comments are optional
                if type(error).__name__ not in _IGNORED_REPLY_ERRORS:
                    raise
        return tuple(records)


def telegram_search_terms(scope: GeoScope) -> tuple[str, ...]:
    terms: list[str] = []
    if scope.street and scope.house_number:
        terms.extend(
            [
                f"{scope.street} {scope.house_number}",
                f"{scope.city} {scope.street} {scope.house_number}",
            ]
        )
    if scope.street:
        terms.append(f"{scope.city} {scope.street}")
    terms.append(scope.raw_address)
    return tuple(
        dict.fromkeys(" ".join(item.split()) for item in terms if item.strip())
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _relevance(text: str, scope: GeoScope) -> str:
    normalized = _normalize(text)
    street = _normalize(scope.street or "")
    house = _normalize(scope.house_number or "")
    district = _normalize(scope.district or "")
    city = _normalize(scope.city)
    if street and house and street in normalized and house in normalized:
        return "house"
    if street and street in normalized:
        return "street"
    if district and district in normalized:
        return "district"
    if city and city in normalized:
        return "city"
    return "unresolved"


@dataclass(frozen=True, slots=True)
class TelegramCollector:
    gateway: TelegramGateway
    source_kind: SourceKind = SourceKind.TELEGRAM
    history_limit: int = 100
    comments_per_post: int = 100

    def __post_init__(self) -> None:
        if self.source_kind is not SourceKind.TELEGRAM:
            raise ValueError("TelegramCollector source_kind must be telegram")
        if not 1 <= self.history_limit <= 500:
            raise ValueError("history_limit must be in [1, 500]")
        if not 0 <= self.comments_per_post <= 200:
            raise ValueError("comments_per_post must be in [0, 200]")

    def collect(
        self,
        candidate: SourceCandidate,
        scope: GeoScope,
    ) -> CandidateCollectionResult:
        if candidate.kind is not SourceKind.TELEGRAM:
            raise ValueError("candidate must be Telegram")
        target = parse_telegram_target(candidate.url)
        terms = telegram_search_terms(scope)
        records = self.gateway.collect(
            target,
            search_terms=terms,
            history_limit=self.history_limit,
            comments_per_post=self.comments_per_post,
        )
        messages: list[SourceMessage] = []
        relevant = 0
        for record in records:
            relevance = _relevance(record.text, scope)
            relevant += int(relevance in _RELEVANT_HINTS)
            messages.append(
                SourceMessage(
                    source="telegram",
                    external_id=(
                        f"{record.channel_username}:{record.message_id}:"
                        f"{'comment' if record.is_comment else 'post'}"
                    ),
                    text=record.text,
                    published_at=record.published_at,
                    url=record.url,
                    author_id=None,
                    metadata={
                        "kind": (
                            "telegram_comment"
                            if record.is_comment
                            else "telegram_post"
                        ),
                        "channel_username": record.channel_username,
                        "parent_message_id": record.parent_message_id,
                        "geo_relevance_hint": relevance,
                        "final_geo_filter_required": True,
                    },
                )
            )
        if messages:
            state = SourceState.COLLECTED
            reason_code = SourceReasonCode.NONE
            reason = "Telegram public channel collected successfully"
        else:
            state = SourceState.NO_RELEVANT_RESULTS
            reason_code = SourceReasonCode.NO_RESULTS
            reason = (
                "Telegram public channel was accessible but returned no "
                "matching public messages"
            )
        return CandidateCollectionResult(
            messages=tuple(messages),
            outcome=SourceOutcome(
                source_id=candidate.candidate_id,
                kind=SourceKind.TELEGRAM,
                state=state,
                reason_code=reason_code,
                reason=reason,
                attempted_urls=(candidate.url,),
                messages_collected=len(messages),
                relevant_messages=relevant,
                details={
                    "channel_username": target.username,
                    "target_message_id": target.message_id,
                    "search_terms": list(terms),
                    "comments_enabled": self.comments_per_post > 0,
                    "final_geo_filter_required": True,
                },
            ),
        )


def build_telegram_collector_from_env() -> TelegramCollector:
    api_id_file = os.getenv("SOIKA_TELEGRAM_API_ID_FILE")
    api_hash_file = os.getenv("SOIKA_TELEGRAM_API_HASH_FILE")
    session_file = os.getenv("SOIKA_TELEGRAM_SESSION_FILE")
    if not api_id_file or not api_hash_file or not session_file:
        return TelegramCollector(UnavailableTelegramGateway())
    credentials = TelegramCredentials.from_secret_files(
        api_id_file=Path(api_id_file),
        api_hash_file=Path(api_hash_file),
        session_file=Path(session_file),
    )
    return TelegramCollector(TelethonTelegramGateway(credentials))


__all__ = [
    "TelegramCollector",
    "TelegramCredentials",
    "TelegramGateway",
    "TelegramRecord",
    "TelegramTarget",
    "TelethonTelegramGateway",
    "UnavailableTelegramGateway",
    "build_telegram_collector_from_env",
    "parse_telegram_target",
    "telegram_search_terms",
]
