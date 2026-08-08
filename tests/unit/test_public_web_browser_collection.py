from __future__ import annotations

from soika_uds.discovery import (
    CandidateCollectionError,
    GeoScope,
    SourceCandidate,
    SourceKind,
    SourceReasonCode,
    SourceState,
)
from soika_uds.discovery.access import (
    SourceAccessAuthorizer,
    StaticSourcePolicyResolver,
)
from soika_uds.discovery.browser import (
    BrowserRenderError,
    RenderedComment,
    RenderedPage,
    classify_browser_block,
)
from soika_uds.discovery.public_web import PublicWebCollector, geo_relevance_hint
from soika_uds.parsers import AccessMethod, RobotsDecision
from tests.unit.connector_fixture_support import approved_connector_policy


SCOPE = GeoScope(
    raw_address="Ижевск Пушкинская 277",
    city="Ижевск",
    region="Удмуртия",
    district="Октябрьский район",
    street="Пушкинская улица",
    house_number="277",
    longitude=53.2072056,
    latitude=56.8665403,
    precision="house",
    confidence=0.81,
    candidate_id="house-277",
    label="277, Пушкинская улица, Ижевск",
)

CANDIDATE = SourceCandidate(
    candidate_id="web:media",
    kind=SourceKind.LOCAL_MEDIA,
    url="https://media.example.ru/news/277",
    domain="media.example.ru",
    title="Новости Ижевска",
    discovered_by="fixture-yandex",
    query='"Пушкинская 277" Ижевск',
    geo_evidence=("city_text_match",),
)


class AllowRobots:
    def evaluate(self, policy, target_url):
        assert policy.source_id == "local-media"
        assert target_url == CANDIDATE.url
        return RobotsDecision.ALLOWED


class FixedFetcher:
    def __init__(self, page):
        self.page = page
        self.calls = 0

    def fetch(self, url, security):
        self.calls += 1
        assert url == CANDIDATE.url
        assert "media.example.ru" in security.allowed_domains
        return self.page


class FixedBrowser:
    def __init__(self, page):
        self.page = page
        self.calls = 0

    def render(self, url, security):
        self.calls += 1
        assert url == CANDIDATE.url
        assert security.https_only is True
        return self.page


def _policy():
    return approved_connector_policy(
        "local-media",
        AccessMethod.PUBLIC_WEB,
        ("media.example.ru",),
    )


def _authorizer(*policies):
    return SourceAccessAuthorizer(
        StaticSourcePolicyResolver(tuple(policies)),
        AllowRobots(),
    )


def test_missing_reviewed_policy_is_explicit_configuration_failure() -> None:
    collector = PublicWebCollector(
        SourceKind.LOCAL_MEDIA,
        _authorizer(),
        FixedFetcher(
            RenderedPage(
                requested_url=CANDIDATE.url,
                final_url=CANDIDATE.url,
                status_code=200,
                title="ignored",
                body_text="ignored",
            )
        ),
    )

    try:
        collector.collect(CANDIDATE, SCOPE)
    except CandidateCollectionError as error:
        assert error.code is SourceReasonCode.SOURCE_CONFIGURATION_MISSING
        assert error.state is SourceState.CONFIGURATION_MISSING
        assert "media.example.ru" in str(error)
    else:
        raise AssertionError("unreviewed source must fail closed")


def test_static_page_emits_real_source_message_without_browser() -> None:
    page = RenderedPage(
        requested_url=CANDIDATE.url,
        final_url=CANDIDATE.url,
        status_code=200,
        title="Ремонт на Пушкинской",
        body_text=(
            "Ижевск. На Пушкинской улице у дома 277 начался ремонт тротуара. "
            * 8
        ),
        canonical_url=CANDIDATE.url,
        published_at="2026-08-08T10:00:00+04:00",
    )
    fetcher = FixedFetcher(page)
    browser = FixedBrowser(page)
    collector = PublicWebCollector(
        SourceKind.LOCAL_MEDIA,
        _authorizer(_policy()),
        fetcher,
        browser,
    )

    result = collector.collect(CANDIDATE, SCOPE)

    assert fetcher.calls == 1
    assert browser.calls == 0
    assert len(result.messages) == 1
    assert result.messages[0].url == CANDIDATE.url
    assert result.messages[0].metadata["kind"] == "news_article"
    assert result.outcome.state is SourceState.COLLECTED
    assert result.outcome.relevant_messages == 1
    assert result.outcome.details["browser_used"] is False


def test_short_static_shell_falls_back_to_browser_and_collects_comments() -> None:
    static = RenderedPage(
        requested_url=CANDIDATE.url,
        final_url=CANDIDATE.url,
        status_code=200,
        title="Новости",
        body_text="JavaScript required",
    )
    rendered = RenderedPage(
        requested_url=CANDIDATE.url,
        final_url=CANDIDATE.url,
        status_code=200,
        title="Пушкинская 277",
        body_text=("Материал о Пушкинской улице 277 в Ижевске. " * 10),
        published_at="2026-08-08T10:00:00+04:00",
        comments=(
            RenderedComment(
                external_id="comment-1",
                text="У дома 277 тротуар действительно ремонтируют",
                published_at="2026-08-08T10:05:00+04:00",
            ),
        ),
        blocked_subrequests=3,
    )
    fetcher = FixedFetcher(static)
    browser = FixedBrowser(rendered)
    collector = PublicWebCollector(
        SourceKind.LOCAL_MEDIA,
        _authorizer(_policy()),
        fetcher,
        browser,
    )

    result = collector.collect(CANDIDATE, SCOPE)

    assert fetcher.calls == 1
    assert browser.calls == 1
    assert len(result.messages) == 2
    assert result.outcome.details["browser_used"] is True
    assert result.outcome.details["blocked_subrequests"] == 3
    assert result.outcome.details["comments_seen"] == 1


def test_access_wall_is_not_treated_as_empty_success() -> None:
    error = classify_browser_block(
        final_url="https://media.example.ru/login",
        status_code=200,
        title="Вход",
        body_text="Авторизация",
    )

    assert isinstance(error, BrowserRenderError)
    assert error.code is SourceReasonCode.AUTH_REQUIRED
    assert error.state is SourceState.AUTH_REQUIRED


def test_captcha_is_explicitly_blocked() -> None:
    error = classify_browser_block(
        final_url=CANDIDATE.url,
        status_code=200,
        title="Проверка",
        body_text="Verify you are human — CAPTCHA",
    )

    assert isinstance(error, BrowserRenderError)
    assert error.code is SourceReasonCode.CAPTCHA
    assert error.state is SourceState.BLOCKED


def test_geo_hint_does_not_promote_city_only_text_to_house() -> None:
    assert geo_relevance_hint("Новости города Ижевска", SCOPE) == "city"
    assert (
        geo_relevance_hint("Пушкинская улица, дом 277: ремонт", SCOPE)
        == "house"
    )
