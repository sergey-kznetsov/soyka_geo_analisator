from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from soika_uds.parsers import (
    AccessMethod,
    AuthorIdentifierMode,
    ComplianceContext,
    DataCategory,
    DataProtectionPolicy,
    ParserRequest,
    PermissionEvidence,
    PermissionEvidenceKind,
    PermissionStatus,
    RateLimitPolicy,
    RequirementDecision,
    RobotsDecision,
    RobotsRequirement,
    SecurityPolicy,
    SourcePolicy,
    SourceResearchRecord,
    TransportResponse,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
PURPOSE = "urban issue analysis"


def approved_connector_policy(
    source_id: str,
    access_method: AccessMethod,
    domains: tuple[str, ...],
) -> SourcePolicy:
    public_web = access_method is AccessMethod.PUBLIC_WEB
    return SourcePolicy(
        source_id=source_id,
        display_name=source_id,
        owner="Fixture owner",
        access_method=access_method,
        permission_status=PermissionStatus.APPROVED,
        jurisdictions=("RU",),
        legal_basis="controlled integration fixture",
        terms_url=f"https://{domains[0]}/terms",
        privacy_url=f"https://{domains[0]}/privacy",
        official_docs_url=(f"https://{domains[0]}/docs" if not public_web else None),
        robots_requirement=(
            RobotsRequirement.REQUIRED
            if public_web
            else RobotsRequirement.NOT_APPLICABLE
        ),
        research=SourceResearchRecord(
            collection_plan="Collect one allowlisted fixture page.",
            official_access_available=(
                RequirementDecision.NO if public_web else RequirementDecision.YES
            ),
            permission_required=RequirementDecision.YES,
            permission_contact="legal@example.test",
            copyright_constraints="Fixture only; no republication.",
            terms_constraints="Controlled integration tests only.",
            personal_data_notes="Drop author identifiers in fixture tests.",
            security_risks=("untrusted response",),
            deletion_or_correction_process="Delete by external identifier.",
            rate_limit_source="Controlled fixture transport.",
            reviewed_sources=(f"https://{domains[0]}/terms",),
            robots_url=(f"https://{domains[0]}/robots.txt" if public_web else None),
        ),
        permission=PermissionEvidence(
            kind=PermissionEvidenceKind.INTERNAL_LEGAL_MEMO,
            reference=f"test/{source_id}",
            reviewed_by="test@example.test",
            reviewed_at=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=30),
        ),
        data=DataProtectionPolicy(
            categories=(DataCategory.PUBLIC_TEXT,),
            allowed_fields=(
                "external_id",
                "text",
                "published_at",
                "url",
                "metadata.kind",
                "metadata.title",
                "metadata.owner_id",
                "metadata.post_id",
                "metadata.discussion_id",
                "metadata.discussion_type",
                "metadata.document_url",
                "metadata.parent_external_id",
            ),
            retention_days=30,
            author_identifier_mode=AuthorIdentifierMode.DROP,
            purpose=PURPOSE,
        ),
        security=SecurityPolicy(
            allowed_domains=domains,
            allowed_content_types=(
                "application/json",
                "text/html",
                "application/rss+xml",
                "application/xml",
            ),
            allow_subdomains=True,
            credential_reference=(f"secret://{source_id}" if not public_web else None),
        ),
        rate_limit=RateLimitPolicy(
            requests_per_minute=6000,
            burst=100,
            max_retries=0,
        ),
        allowed_purposes=(PURPOSE,),
        parser_version="1.0.0",
        reviewed_at=NOW - timedelta(days=1),
        review_due_at=NOW + timedelta(days=30),
        enabled=True,
    )


class FixtureTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, *, headers=None) -> TransportResponse:
        del headers
        self.urls.append(url)
        host = urlsplit(url).hostname
        if host == "api.vk.com" and "wall.getComments" in url:
            return self._json(
                url,
                {
                    "response": {
                        "items": [
                            {
                                "id": 20,
                                "from_id": 100,
                                "date": 1785931320,
                                "text": "Во дворе после дождя снова стоит вода",
                            }
                        ]
                    }
                },
            )
        if host == "api.vk.com":
            return self._json(
                url,
                {
                    "response": {
                        "items": [
                            {
                                "id": 10,
                                "from_id": -1,
                                "date": 1785931200,
                                "text": ("На улице Центральной ремонтируют освещение"),
                            }
                        ]
                    }
                },
            )
        if host == "api.ok.ru":
            return self._json(
                url,
                {
                    "comments": [
                        {
                            "id": "ok-1",
                            "author_ref": "user:1",
                            "date_ms": 1785931440000,
                            "message": "Остановка у школы требует ремонта",
                        }
                    ],
                    "anchor": None,
                    "has_more": False,
                },
            )
        if host == "media.example.ru":
            return TransportResponse(
                status_code=200,
                url=url,
                headers={"content-type": "application/rss+xml"},
                body=(
                    b"<?xml version='1.0' encoding='UTF-8'?><rss><channel>"
                    b"<item><title>Local media</title>"
                    b"<description>Road works started</description>"
                    b"<pubDate>Wed, 05 Aug 2026 12:00:00 GMT</pubDate>"
                    b"<link>https://media.example.ru/news/1</link>"
                    b"</item></channel></rss>"
                ),
            )
        if host == "municipal.example.ru" and url.endswith("sitemap.xml"):
            return TransportResponse(
                status_code=200,
                url=url,
                headers={"content-type": "application/xml"},
                body=(
                    b"<?xml version='1.0'?><urlset>"
                    b"<url><loc>https://municipal.example.ru/news/1</loc></url>"
                    b"</urlset>"
                ),
            )
        html_by_host = {
            "municipal.example.ru": ("Муниципалитет сообщил о ремонте тротуара"),
            "dzen.ru": "Жители обсудили благоустройство сквера",
            "pikabu.ru": "Пользователи сообщили о яме на дороге",
            "rutube.ru": "Видео о ремонте городской набережной",
        }
        if host in html_by_host:
            return self._html(url, host, html_by_host[host])
        raise AssertionError(f"unexpected fixture URL: {url}")

    @staticmethod
    def _json(url: str, payload: object) -> TransportResponse:
        return TransportResponse(
            status_code=200,
            url=url,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode(),
        )

    @staticmethod
    def _html(url: str, host: str, text: str) -> TransportResponse:
        return TransportResponse(
            status_code=200,
            url=url,
            headers={"content-type": "text/html; charset=utf-8"},
            body=f"""
                <html><head><link rel='canonical' href='{url}'></head><body>
                <h1>Fixture {host}</h1>
                <article>{text}</article>
                <time datetime='2026-08-05T12:05:00+00:00'></time>
                <div class='comment' data-comment-id='1'>
                  <span class='comment-text'>Комментарий: {text}</span>
                  <span class='comment-author'>fixture-user</span>
                  <time datetime='2026-08-05T12:06:00+00:00'></time>
                </div>
                </body></html>
            """.encode(),
        )


def connector_policies() -> dict[str, SourcePolicy]:
    return {
        "vk": approved_connector_policy(
            "vk", AccessMethod.OFFICIAL_API, ("api.vk.com", "vk.com")
        ),
        "ok": approved_connector_policy(
            "ok", AccessMethod.OFFICIAL_API, ("api.ok.ru", "ok.ru")
        ),
        "local-media": approved_connector_policy(
            "local-media", AccessMethod.PUBLIC_WEB, ("media.example.ru",)
        ),
        "municipal-public": approved_connector_policy(
            "municipal-public",
            AccessMethod.PUBLIC_WEB,
            ("municipal.example.ru",),
        ),
        "dzen": approved_connector_policy(
            "dzen", AccessMethod.PUBLIC_WEB, ("dzen.ru",)
        ),
        "pikabu": approved_connector_policy(
            "pikabu", AccessMethod.PUBLIC_WEB, ("pikabu.ru",)
        ),
        "rutube": approved_connector_policy(
            "rutube", AccessMethod.PUBLIC_WEB, ("rutube.ru",)
        ),
    }


def html_profile(source_id: str, kind: str, domain: str) -> dict[str, object]:
    return {
        "source_id": source_id,
        "display_name": source_id,
        "kind": kind,
        "base_url": f"https://{domain}/",
        "region": "Республика Татарстан",
        "municipalities": ["Казань"],
        "discovery_mode": "sitemap" if kind == "municipal" else "rss",
        "discovery_urls": [
            (
                f"https://{domain}/sitemap.xml"
                if kind == "municipal"
                else f"https://{domain}/rss.xml"
            )
        ],
        "selectors": {
            "title": "h1",
            "body": "article",
            "published_at": "time[datetime]",
            "author": ".author",
            "canonical_url": "link[rel='canonical']",
            "comment_item": ".comment",
            "comment_text": ".comment-text",
            "comment_id": "[data-comment-id]",
            "comment_author": ".comment-author",
            "comment_published_at": "time[datetime]",
            "comment_parent_id": "[data-parent-id]",
        },
        "robots_url": f"https://{domain}/robots.txt",
        "render_javascript": False,
        "rendering_justification": None,
        "enabled": False,
    }


def connector_requests() -> dict[str, ParserRequest]:
    return {
        "vk": ParserRequest(
            analysis_id="fixture-vk",
            source_id="vk",
            purpose=PURPOSE,
            territory={"city": "Казань"},
            options={"community_ids": ["1"], "include_comments": True},
            max_pages=10,
        ),
        "ok": ParserRequest(
            analysis_id="fixture-ok",
            source_id="ok",
            purpose=PURPOSE,
            territory={"city": "Казань"},
            options={
                "discussion_ids": ["1"],
                "discussion_types": ["GROUP_TOPIC"],
            },
        ),
        "local-media": ParserRequest(
            analysis_id="fixture-media",
            source_id="local-media",
            purpose=PURPOSE,
            territory={"city": "Казань"},
            options={
                "site_profile": html_profile(
                    "local-media.fixture",
                    "local_media",
                    "media.example.ru",
                )
            },
        ),
        "municipal-public": ParserRequest(
            analysis_id="fixture-municipal",
            source_id="municipal-public",
            purpose=PURPOSE,
            territory={"city": "Казань"},
            options={
                "site_profile": html_profile(
                    "municipal.fixture",
                    "municipal",
                    "municipal.example.ru",
                )
            },
            max_pages=4,
        ),
        "dzen": ParserRequest(
            analysis_id="fixture-dzen",
            source_id="dzen",
            purpose=PURPOSE,
            territory={"city": "Казань"},
            options={"channel_urls": ["https://dzen.ru/fixture"]},
        ),
        "pikabu": ParserRequest(
            analysis_id="fixture-pikabu",
            source_id="pikabu",
            purpose=PURPOSE,
            territory={"city": "Казань"},
            options={"community_urls": ["https://pikabu.ru/community/fixture"]},
        ),
        "rutube": ParserRequest(
            analysis_id="fixture-rutube",
            source_id="rutube",
            purpose=PURPOSE,
            territory={"city": "Казань"},
            options={"channel_urls": ["https://rutube.ru/u/fixture/videos/"]},
        ),
    }


def compliance_context(policy_value: SourcePolicy) -> ComplianceContext:
    public_web = policy_value.access_method is AccessMethod.PUBLIC_WEB
    return ComplianceContext(
        purpose=PURPOSE,
        robots_decision=(
            RobotsDecision.ALLOWED if public_web else RobotsDecision.NOT_APPLICABLE
        ),
        credential_available=not public_web,
        current_time=NOW,
    )
