"""Policy and robots gates for dynamically discovered public-web candidates."""

from __future__ import annotations

import json
import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from ..parsers import (
    AccessMethod,
    ComplianceContext,
    RobotsDecision,
    SourcePolicy,
)
from ..parsers.compliance import ComplianceGate
from ..parsers.security import Resolver, UnsafeOutboundRequestError, validate_outbound_url
from .collection import CandidateCollectionError
from .models import SourceCandidate, SourceReasonCode, SourceState

_MAX_ROBOTS_BYTES = 512_000


class SourcePolicyResolver(Protocol):
    def resolve(self, candidate: SourceCandidate) -> SourcePolicy | None: ...


@dataclass(frozen=True, slots=True)
class DirectorySourcePolicyResolver:
    """Load approved/reviewed policies from a deployment-controlled directory."""

    directory: Path

    def _policies(self) -> tuple[SourcePolicy, ...]:
        directory = Path(self.directory)
        if not directory.exists():
            return ()
        policies: list[SourcePolicy] = []
        for path in sorted(directory.glob("*.json")):
            if path.name == "source-policy-template.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            try:
                policy = SourcePolicy.from_dict(payload)
            except (TypeError, ValueError):
                continue
            policies.append(policy)
        return tuple(policies)

    def resolve(self, candidate: SourceCandidate) -> SourcePolicy | None:
        host = candidate.domain.lower().rstrip(".")
        matches: list[SourcePolicy] = []
        for policy in self._policies():
            if policy.access_method is not AccessMethod.PUBLIC_WEB:
                continue
            for allowed in policy.security.allowed_domains:
                if host == allowed or (
                    policy.security.allow_subdomains and host.endswith(f".{allowed}")
                ):
                    matches.append(policy)
                    break
        if not matches:
            return None
        matches.sort(key=lambda item: item.source_id)
        return matches[0]


@dataclass(frozen=True, slots=True)
class StaticSourcePolicyResolver:
    policies: tuple[SourcePolicy, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policies", tuple(self.policies))

    def resolve(self, candidate: SourceCandidate) -> SourcePolicy | None:
        host = candidate.domain.lower().rstrip(".")
        for policy in self.policies:
            if policy.access_method is not AccessMethod.PUBLIC_WEB:
                continue
            for allowed in policy.security.allowed_domains:
                if host == allowed or (
                    policy.security.allow_subdomains and host.endswith(f".{allowed}")
                ):
                    return policy
        return None


class RobotsEvaluator(Protocol):
    def evaluate(self, policy: SourcePolicy, target_url: str) -> RobotsDecision: ...


class _ValidatedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, policy: SourcePolicy, resolver: Resolver | None) -> None:
        super().__init__()
        self.policy = policy
        self.resolver = resolver
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del fp, code, msg, headers
        self.redirects += 1
        if self.redirects > self.policy.security.max_redirects:
            raise UnsafeOutboundRequestError("redirect limit exceeded")
        absolute = urljoin(req.full_url, newurl)
        kwargs = {"resolver": self.resolver} if self.resolver is not None else {}
        validated = validate_outbound_url(absolute, self.policy.security, **kwargs)
        return Request(
            validated,
            headers=dict(req.header_items()),
            method="GET",
        )


@dataclass(frozen=True, slots=True)
class NetworkRobotsEvaluator:
    """Fetch and evaluate robots.txt before public-web content collection."""

    resolver: Resolver | None = None
    timeout_seconds: float = 10.0

    def evaluate(self, policy: SourcePolicy, target_url: str) -> RobotsDecision:
        robots_url = policy.research.robots_url
        if not robots_url:
            return RobotsDecision.UNAVAILABLE
        kwargs = {"resolver": self.resolver} if self.resolver is not None else {}
        try:
            target = validate_outbound_url(target_url, policy.security, **kwargs)
            robots = validate_outbound_url(robots_url, policy.security, **kwargs)
        except UnsafeOutboundRequestError:
            return RobotsDecision.DISALLOWED
        handler = _ValidatedRedirectHandler(policy, self.resolver)
        opener = build_opener(handler, HTTPSHandler(context=ssl.create_default_context()))
        request = Request(
            robots,
            headers={"User-Agent": policy.security.user_agent},
            method="GET",
        )
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    return RobotsDecision.UNAVAILABLE
                body = response.read(_MAX_ROBOTS_BYTES + 1)
        except HTTPError as error:
            if error.code in {401, 403}:
                return RobotsDecision.DISALLOWED
            return RobotsDecision.UNAVAILABLE
        except (URLError, TimeoutError, OSError, UnsafeOutboundRequestError):
            return RobotsDecision.UNAVAILABLE
        if len(body) > _MAX_ROBOTS_BYTES:
            return RobotsDecision.UNAVAILABLE
        text = body.decode("utf-8", errors="replace")
        parser = RobotFileParser()
        parser.set_url(robots)
        parser.parse(text.splitlines())
        return (
            RobotsDecision.ALLOWED
            if parser.can_fetch(policy.security.user_agent, target)
            else RobotsDecision.DISALLOWED
        )


@dataclass(frozen=True, slots=True)
class SourceAccessAuthorizer:
    resolver: SourcePolicyResolver
    robots: RobotsEvaluator
    purpose: str = "urban issue analysis"
    credential_sources: frozenset[str] = frozenset()

    def authorize(self, candidate: SourceCandidate) -> SourcePolicy:
        policy = self.resolver.resolve(candidate)
        if policy is None:
            raise CandidateCollectionError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                (
                    f"no reviewed public-web source policy is configured for "
                    f"{candidate.domain}"
                ),
                state=SourceState.CONFIGURATION_MISSING,
            )
        robots_decision = self.robots.evaluate(policy, candidate.url)
        decision = ComplianceGate().evaluate(
            policy,
            ComplianceContext(
                purpose=self.purpose,
                robots_decision=robots_decision,
                credential_available=policy.source_id in self.credential_sources,
            ),
        )
        if decision.allowed:
            return policy

        reasons = tuple(decision.reasons)
        if "ROBOTS_DISALLOWED" in reasons:
            code = SourceReasonCode.ROBOTS_DENIED
            state = SourceState.BLOCKED
        elif "ROBOTS_UNAVAILABLE" in reasons or "ROBOTS_NOT_CHECKED" in reasons:
            code = SourceReasonCode.ROBOTS_DENIED
            state = SourceState.BLOCKED
        elif "CREDENTIAL_UNAVAILABLE" in reasons:
            code = SourceReasonCode.API_CREDENTIALS_MISSING
            state = SourceState.CONFIGURATION_MISSING
        else:
            code = SourceReasonCode.SOURCE_CONFIGURATION_MISSING
            state = SourceState.BLOCKED
        raise CandidateCollectionError(
            code,
            f"source policy blocked collection: {', '.join(reasons)}",
            state=state,
            details={
                "policy_source_id": policy.source_id,
                "robots_decision": robots_decision.value,
                "compliance_reasons": list(reasons),
            },
        )


__all__ = [
    "DirectorySourcePolicyResolver",
    "NetworkRobotsEvaluator",
    "RobotsEvaluator",
    "SourceAccessAuthorizer",
    "SourcePolicyResolver",
    "StaticSourcePolicyResolver",
]
