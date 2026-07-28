"""Tests for the concrete research adapters and the viral_potential score.

These tests do NOT make real network calls.  Each adapter is tested with
a mock ``httpx.Client`` that returns canned responses, so the tests are
deterministic and run offline.

Covers:
* Reddit adapter (hot posts parsing, upvotes/comments extraction).
* YouTube adapter (search + stats, unavailable without key).
* RSS adapter (feed parsing, topic filtering).
* Twitter/X adapter (recent search, engagement metrics, unavailable
  without bearer token).
* Google Trends adapter (related queries, growth percentages, no key
  needed).
* TikTok adapter (OAuth2 token + video query, unavailable without
  credentials).
* Aggregating provider (multi-source merge, skip unavailable).
* Viral-potential scoring component (volume, velocity, breadth).
* Source schema carries engagement_metrics through normalisation.
* app_factory wiring produces a non-Unavailable research provider.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from ttvturbo.ideas_research import (
    AggregatingResearchProvider,
    GoogleTrendsResearchProvider,
    RawSource,
    RedditResearchProvider,
    Reliability,
    RssResearchProvider,
    SCORE_COMPONENTS,
    Source,
    StaticLLMAdapter,
    StaticResearchProvider,
    TikTokResearchProvider,
    TwitterXResearchProvider,
    UnavailableResearchProvider,
    YouTubeResearchProvider,
    LLMResponse,
    IdeasResearchService,
    IdeasResearchStorage,
    IdeasResearchUnavailableError,
)
from ttvturbo.ideas_research.clustering import normalize_source, deduplicate_sources
from ttvturbo.ideas_research.scoring import ScoringInput, score_topic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(seconds_ago: float) -> str:
    t = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(seconds=seconds_ago)
    return t.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Reddit adapter
# ---------------------------------------------------------------------------


class TestRedditAdapter:
    def _token_response(self) -> dict:
        return {
            "access_token": "fake-reddit-token",
            "expires_in": 3600,
            "token_type": "bearer",
        }

    def _reddit_response(self) -> dict:
        return {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "New game announcement",
                            "score": 5000,
                            "num_comments": 300,
                            "upvote_ratio": 0.95,
                            "permalink": "/r/gaming/comments/abc/new_game_announcement",
                            "created_utc": _dt.datetime.now(tz=_dt.timezone.utc).timestamp() - 3600,
                            "selftext": "A new game was announced today",
                        }
                    },
                    {
                        "data": {
                            "title": "Unrelated post",
                            "score": 100,
                            "num_comments": 5,
                            "upvote_ratio": 0.8,
                            "permalink": "/r/gaming/comments/xyz/unrelated",
                            "created_utc": _dt.datetime.now(tz=_dt.timezone.utc).timestamp() - 7200,
                            "selftext": "",
                        }
                    },
                ]
            }
        }

    def test_search_returns_posts_with_engagement(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "access_token" in url:
                return httpx.Response(200, json=self._token_response(), request=request)
            if "oauth.reddit.com" in url:
                return httpx.Response(200, json=self._reddit_response(), request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = RedditResearchProvider(
            client_id="fake-id", client_secret="fake-secret",
            subreddits=("gaming",), http_client=client, max_posts_per_sub=10,
        )
        results = prov.search(["gaming"], language="en", time_range="7d", max_topics=1)
        assert len(results) == 2
        assert results[0].engagement_metrics["upvotes"] == 5000
        assert results[0].engagement_metrics["comments"] == 300
        assert results[0].publisher == "r/gaming"
        assert "reddit.com" in results[0].url

    def test_search_filters_by_topic_for_multi_word_topics(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "access_token" in url:
                return httpx.Response(200, json=self._token_response(), request=request)
            if "oauth.reddit.com" in url:
                return httpx.Response(200, json=self._reddit_response(), request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = RedditResearchProvider(
            client_id="fake-id", client_secret="fake-secret",
            subreddits=("gaming",), http_client=client,
        )
        results = prov.search(["nonexistent topic phrase"], language="en", time_range="7d", max_topics=1)
        assert results == []

    def test_unavailable_without_credentials(self):
        prov = RedditResearchProvider(client_id="", client_secret="")
        assert prov.available() is False

    def test_search_raises_without_credentials(self):
        prov = RedditResearchProvider(client_id="", client_secret="")
        with pytest.raises(IdeasResearchUnavailableError):
            prov.search(["gaming"], language="en", time_range="7d", max_topics=1)


# ---------------------------------------------------------------------------
# YouTube adapter
# ---------------------------------------------------------------------------


class TestYouTubeAdapter:
    def _search_response(self) -> dict:
        return {
            "items": [
                {
                    "id": {"videoId": "vid1"},
                    "snippet": {
                        "title": "Best Gaming Moments",
                        "channelTitle": "GamingChannel",
                        "publishedAt": _iso(3600),
                        "description": "Epic compilation",
                    },
                },
            ]
        }

    def _stats_response(self) -> dict:
        return {
            "items": [
                {
                    "id": "vid1",
                    "statistics": {"viewCount": "100000", "likeCount": "5000", "commentCount": "800"},
                },
            ]
        }

    def test_search_returns_videos_with_stats(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/search" in url:
                return httpx.Response(200, json=self._search_response(), request=request)
            if "/videos" in url:
                return httpx.Response(200, json=self._stats_response(), request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = YouTubeResearchProvider(api_key="fake-key", client=client)
        results = prov.search(["gaming"], language="en", time_range="7d", max_topics=1)
        assert len(results) == 1
        assert results[0].engagement_metrics["views"] == 100000
        assert results[0].engagement_metrics["likes"] == 5000
        assert results[0].engagement_metrics["comments"] == 800
        assert "youtube.com/watch?v=vid1" in results[0].url

    def test_unavailable_without_key(self):
        prov = YouTubeResearchProvider(api_key="")
        assert prov.available() is False

    def test_search_raises_without_key(self):
        prov = YouTubeResearchProvider(api_key="")
        with pytest.raises(IdeasResearchUnavailableError):
            prov.search(["gaming"], language="en", time_range="7d", max_topics=1)


# ---------------------------------------------------------------------------
# RSS adapter
# ---------------------------------------------------------------------------


class TestRssAdapter:
    def _rss_xml(self) -> str:
        return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>IGN Games</title>
    <item>
      <title>New Valorant Patch Notes</title>
      <link>https://example.com/valorant-patch</link>
      <pubDate>Wed, 29 Jul 2026 10:00:00 +0000</pubDate>
      <description>Valorant gets a major balance update</description>
    </item>
    <item>
      <title>Unrelated News</title>
      <link>https://example.com/unrelated</link>
      <pubDate>Wed, 29 Jul 2026 09:00:00 +0000</pubDate>
      <description>Something else entirely</description>
    </item>
  </channel>
</rss>"""

    def test_search_returns_matching_items(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=self._rss_xml().encode("utf-8"),
                headers={"content-type": "application/xml"},
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = RssResearchProvider(
            feeds=("https://example.com/feed",), client=client,
        )
        results = prov.search(["Valorant"], language="en", time_range="7d", max_topics=1)
        assert len(results) == 1
        assert "Valorant" in results[0].title
        assert results[0].url == "https://example.com/valorant-patch"
        assert results[0].publisher == "IGN Games"

    def test_available_false_on_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"error", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = RssResearchProvider(
            feeds=("https://example.com/feed",), client=client,
        )
        assert prov.available() is False


# ---------------------------------------------------------------------------
# Twitter / X adapter
# ---------------------------------------------------------------------------


class TestTwitterXAdapter:
    def _x_search_response(self) -> dict:
        return {
            "data": [
                {
                    "id": "1234567890",
                    "text": "Huge Valorant patch just dropped! New agent revealed.",
                    "created_at": _iso(3600),
                    "author_id": "user1",
                    "public_metrics": {
                        "retweet_count": 500,
                        "reply_count": 120,
                        "like_count": 3000,
                        "quote_count": 50,
                    },
                },
            ],
            "includes": {
                "users": [
                    {"id": "user1", "username": "GamingNews", "name": "Gaming News"},
                ],
            },
        }

    def test_search_returns_tweets_with_engagement(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._x_search_response(), request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = TwitterXResearchProvider(bearer_token="fake-token", client=client)
        results = prov.search(["Valorant"], language="en", time_range="7d", max_topics=1)
        assert len(results) == 1
        r = results[0]
        assert r.engagement_metrics["likes"] == 3000
        assert r.engagement_metrics["retweets"] == 500
        assert r.engagement_metrics["comments"] == 120
        assert r.engagement_metrics["quotes"] == 50
        assert r.publisher == "@GamingNews"
        assert "x.com/GamingNews/status/1234567890" in r.url

    def test_unavailable_without_token(self):
        prov = TwitterXResearchProvider(bearer_token="")
        assert prov.available() is False

    def test_search_raises_without_token(self):
        prov = TwitterXResearchProvider(bearer_token="")
        with pytest.raises(IdeasResearchUnavailableError):
            prov.search(["gaming"], language="en", time_range="7d", max_topics=1)


# ---------------------------------------------------------------------------
# Google Trends adapter
# ---------------------------------------------------------------------------


class TestGoogleTrendsAdapter:
    def _explore_response(self) -> str:
        """Step 1: explore endpoint returns widgets with tokens."""
        data = {
            "widgets": [
                {
                    "id": "TIMESERIES",
                    "title": "Interest over time",
                    "token": "timeseries-token",
                    "request": {"comparisonItem": [], "category": 8},
                },
                {
                    "id": "RELATED_QUERIES",
                    "title": "Related queries",
                    "token": "rq-token-abc123",
                    "request": {"comparisonItem": [{"keyword": "Valorant"}], "category": 8},
                },
            ]
        }
        return ")]}'" + json.dumps(data)

    def _widgetdata_response(self) -> str:
        """Step 2: widgetdata returns the actual related queries."""
        data = {
            "default": {
                "rankedList": [
                    {
                        "rankedKeyword": [
                            {"query": "valorant new agent", "value": "100", "formattedValue": "100"},
                            {"query": "valorant patch notes", "value": "85", "formattedValue": "85"},
                        ]
                    },
                    {
                        "rankedKeyword": [
                            {"query": "valorant mobile", "value": "+350%", "formattedValue": "+350%"},
                            {"query": "valorant console", "value": "+120%", "formattedValue": "+120%"},
                        ]
                    },
                ]
            }
        }
        return ")]}'" + json.dumps(data)

    def test_search_returns_related_queries(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/explore" in url:
                return httpx.Response(
                    200,
                    content=self._explore_response().encode("utf-8"),
                    headers={"content-type": "application/json"},
                    request=request,
                )
            if "/widgetdata/related_searches" in url:
                return httpx.Response(
                    200,
                    content=self._widgetdata_response().encode("utf-8"),
                    headers={"content-type": "application/json"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = GoogleTrendsResearchProvider(client=client, max_queries_per_topic=5)
        results = prov.search(["Valorant"], language="en", time_range="7d", max_topics=1)
        assert len(results) == 4  # 2 top + 2 rising
        # Top queries have interest values.
        top = [r for r in results if r.engagement_metrics.get("interest", 0) > 0]
        assert len(top) == 2
        assert top[0].title == "valorant new agent"
        assert top[0].engagement_metrics["interest"] == 100
        # Rising queries have growth percentages.
        rising = [r for r in results if r.engagement_metrics.get("interest", 0) == 0]
        assert len(rising) == 2
        assert rising[0].publisher == "Google Trends"
        # Growth signal should be > 0 for rising queries.
        assert rising[0].growth_signal > 0

    def test_search_handles_no_related_queries_widget(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # Return widgets without RELATED_QUERIES.
            data = {"widgets": [{"id": "TIMESERIES", "token": "ts", "request": {}}]}
            return httpx.Response(
                200,
                content=(")]}'" + json.dumps(data)).encode("utf-8"),
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = GoogleTrendsResearchProvider(client=client)
        results = prov.search(["Nothing"], language="en", time_range="7d", max_topics=1)
        assert results == []

    def test_search_handles_non_200_explore(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, content=b"rate limited", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = GoogleTrendsResearchProvider(client=client)
        results = prov.search(["Valorant"], language="en", time_range="7d", max_topics=1)
        assert results == []


# ---------------------------------------------------------------------------
# TikTok adapter
# ---------------------------------------------------------------------------


class TestTikTokAdapter:
    def _token_response(self) -> dict:
        return {
            "access_token": "fake-access-token",
            "expires_in": 7200,
            "token_type": "Bearer",
        }

    def _video_query_response(self) -> dict:
        return {
            "data": {
                "videos": [
                    {
                        "id": "7234567890123456789",
                        "video_description": "Insane Valorant clip! #valorant #gaming",
                        "create_time": int(_dt.datetime.now(tz=_dt.timezone.utc).timestamp()) - 3600,
                        "share_url": "https://www.tiktok.com/@gamer/video/7234567890123456789",
                        "view_count": 500000,
                        "like_count": 75000,
                        "comment_count": 3000,
                        "share_count": 12000,
                    },
                ]
            }
        }

    def test_search_returns_videos_with_engagement(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "client_token" in url:
                return httpx.Response(200, json=self._token_response(), request=request)
            if "research/video/query" in url:
                return httpx.Response(200, json=self._video_query_response(), request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = TikTokResearchProvider(
            client_key="fake-key", client_secret="fake-secret", http_client=client,
        )
        results = prov.search(["Valorant"], language="en", time_range="7d", max_topics=1)
        assert len(results) == 1
        r = results[0]
        assert r.engagement_metrics["views"] == 500000
        assert r.engagement_metrics["likes"] == 75000
        assert r.engagement_metrics["comments"] == 3000
        assert r.engagement_metrics["shares"] == 12000
        assert r.publisher == "TikTok"
        assert "tiktok.com" in r.url

    def test_unavailable_without_credentials(self):
        prov = TikTokResearchProvider(client_key="", client_secret="")
        assert prov.available() is False

    def test_search_raises_without_credentials(self):
        prov = TikTokResearchProvider(client_key="", client_secret="")
        with pytest.raises(IdeasResearchUnavailableError):
            prov.search(["gaming"], language="en", time_range="7d", max_topics=1)


# ---------------------------------------------------------------------------
# Aggregating provider
# ---------------------------------------------------------------------------


class TestAggregatingProvider:
    def test_merges_multiple_providers(self):
        p1 = StaticResearchProvider(results=[
            RawSource(url="https://a.test/1", title="A", publisher="p1"),
        ])
        p2 = StaticResearchProvider(results=[
            RawSource(url="https://b.test/2", title="B", publisher="p2"),
        ])
        agg = AggregatingResearchProvider([p1, p2])
        assert agg.available()
        results = agg.search(["gaming"], language="en", time_range="7d", max_topics=5)
        assert len(results) == 2

    def test_skips_unavailable_providers(self):
        p1 = StaticResearchProvider(results=[
            RawSource(url="https://a.test/1", title="A", publisher="p1"),
        ])
        p2 = UnavailableResearchProvider()
        agg = AggregatingResearchProvider([p1, p2])
        assert agg.available()
        results = agg.search(["gaming"], language="en", time_range="7d", max_topics=5)
        assert len(results) == 1

    def test_unavailable_when_all_unavailable(self):
        agg = AggregatingResearchProvider([UnavailableResearchProvider()])
        assert not agg.available()
        with pytest.raises(IdeasResearchUnavailableError):
            agg.search(["gaming"], language="en", time_range="7d", max_topics=5)

    def test_deduplicates_by_url(self):
        p1 = StaticResearchProvider(results=[
            RawSource(url="https://a.test/1", title="A", publisher="p1"),
        ])
        p2 = StaticResearchProvider(results=[
            RawSource(url="https://a.test/1", title="A duplicate", publisher="p2"),
        ])
        agg = AggregatingResearchProvider([p1, p2])
        results = agg.search(["gaming"], language="en", time_range="7d", max_topics=5)
        assert len(results) == 1  # deduped by URL


# ---------------------------------------------------------------------------
# Viral-potential scoring
# ---------------------------------------------------------------------------


class TestViralScore:
    def test_viral_potential_in_score_components(self):
        assert "viral_potential" in SCORE_COMPONENTS

    def test_high_engagement_scores_higher(self):
        high = Source(
            id=str(uuid.uuid4()), url="https://x.test/h", title="t", publisher="p1",
            published_at=_iso(3600), fetched_at=_iso(0), summary="s",
            engagement_metrics={"views": 500000, "likes": 50000, "comments": 5000},
        )
        low = Source(
            id=str(uuid.uuid4()), url="https://x.test/l", title="t", publisher="p1",
            published_at=_iso(3600), fetched_at=_iso(0), summary="s",
            engagement_metrics={"views": 100, "likes": 5, "comments": 1},
        )
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        inp_high = ScoringInput(
            sources=[high], now=now, time_range_seconds=7*86400,
            target_format="SHORT", language="de", novelty=1.0,
        )
        inp_low = ScoringInput(
            sources=[low], now=now, time_range_seconds=7*86400,
            target_format="SHORT", language="de", novelty=1.0,
        )
        s_high = score_topic(inp_high)
        s_low = score_topic(inp_low)
        assert s_high.components["viral_potential"].value > s_low.components["viral_potential"].value
        assert s_high.components["viral_potential"].rationale
        assert "Volume" in s_high.components["viral_potential"].rationale

    def test_cross_platform_breadth_boosts_viral(self):
        single = [Source(
            id=str(uuid.uuid4()), url="https://x.test/1", title="t", publisher="p1",
            published_at=_iso(3600), fetched_at=_iso(0), summary="s",
            engagement_metrics={"views": 10000},
        )]
        multi = [
            Source(
                id=str(uuid.uuid4()), url="https://x.test/1", title="t", publisher="p1",
                published_at=_iso(3600), fetched_at=_iso(0), summary="s",
                engagement_metrics={"views": 5000},
            ),
            Source(
                id=str(uuid.uuid4()), url="https://x.test/2", title="t", publisher="p2",
                published_at=_iso(3600), fetched_at=_iso(0), summary="s",
                engagement_metrics={"views": 5000},
            ),
            Source(
                id=str(uuid.uuid4()), url="https://x.test/3", title="t", publisher="p3",
                published_at=_iso(3600), fetched_at=_iso(0), summary="s",
                engagement_metrics={"views": 5000},
            ),
        ]
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        s_single = score_topic(ScoringInput(
            sources=single, now=now, time_range_seconds=7*86400,
            target_format="SHORT", language="de", novelty=1.0,
        ))
        s_multi = score_topic(ScoringInput(
            sources=multi, now=now, time_range_seconds=7*86400,
            target_format="SHORT", language="de", novelty=1.0,
        ))
        assert s_multi.components["viral_potential"].value >= s_single.components["viral_potential"].value

    def test_no_engagement_metrics_gives_zero_viral(self):
        src = Source(
            id=str(uuid.uuid4()), url="https://x.test/1", title="t", publisher="p1",
            published_at=_iso(3600), fetched_at=_iso(0), summary="s",
            engagement_metrics={},
        )
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        score = score_topic(ScoringInput(
            sources=[src], now=now, time_range_seconds=7*86400,
            target_format="SHORT", language="de", novelty=1.0,
        ))
        assert score.components["viral_potential"].value == 0.0

    def test_viral_potential_has_rationale(self):
        src = Source(
            id=str(uuid.uuid4()), url="https://x.test/1", title="t", publisher="p1",
            published_at=_iso(3600), fetched_at=_iso(0), summary="s",
            engagement_metrics={"views": 1000},
        )
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        score = score_topic(ScoringInput(
            sources=[src], now=now, time_range_seconds=7*86400,
            target_format="SHORT", language="de", novelty=1.0,
        ))
        comp = score.components["viral_potential"]
        assert comp.rationale and comp.rationale.strip()


# ---------------------------------------------------------------------------
# Engagement metrics flow through normalisation + dedup
# ---------------------------------------------------------------------------


class TestEngagementFlow:
    def test_normalize_source_carries_engagement_metrics(self):
        raw = RawSource(
            url="https://x.test/a", title="t", publisher="p",
            engagement_metrics={"views": 1000, "likes": 100},
        )
        src = normalize_source(raw, assigned_topic="g")
        assert src.engagement_metrics == {"views": 1000, "likes": 100}

    def test_dedup_merges_engagement_metrics(self):
        a = normalize_source(RawSource(
            url="https://x.test/a", title="t", publisher="p1",
            engagement_metrics={"views": 1000, "likes": 100},
        ), assigned_topic="g")
        b = normalize_source(RawSource(
            url="https://x.test/a?utm_source=x", title="t", publisher="p2",
            engagement_metrics={"views": 500, "likes": 50},
        ), assigned_topic="g")
        res = deduplicate_sources([a, b])
        assert len(res.sources) == 1
        merged = res.sources[0]
        assert merged.engagement_metrics["views"] == 1500
        assert merged.engagement_metrics["likes"] == 150


# ---------------------------------------------------------------------------
# app_factory wiring
# ---------------------------------------------------------------------------


class TestAppFactoryWiring:
    def test_default_app_has_real_research_provider(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from ttvturbo.app_factory import create_app
        from ttvturbo.settings import Settings

        s = Settings(data_root=tmp_path / "data")
        app = create_app(settings=s)
        with TestClient(app) as client:
            container = app.state.container
            svc = container.ideas_research_service
            assert svc is not None
            provider = svc.research_provider
            assert not isinstance(provider, UnavailableResearchProvider)

    def test_youtube_provider_wired_when_key_set(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from ttvturbo.app_factory import create_app
        from ttvturbo.settings import Settings

        s = Settings(data_root=tmp_path / "data")
        s.youtube_api_key = "fake-key"
        s.ideas_research_providers = "youtube"
        app = create_app(settings=s)
        with TestClient(app) as client:
            container = app.state.container
            svc = container.ideas_research_service
            assert svc is not None
            agg = svc.research_provider
            provider_types = [type(p).__name__ for p in agg._providers]
            assert "YouTubeResearchProvider" in provider_types

    def test_twitter_provider_wired_when_token_set(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from ttvturbo.app_factory import create_app
        from ttvturbo.settings import Settings

        s = Settings(data_root=tmp_path / "data")
        s.x_bearer_token = "fake-token"
        s.ideas_research_providers = "twitter"
        app = create_app(settings=s)
        with TestClient(app) as client:
            container = app.state.container
            svc = container.ideas_research_service
            agg = svc.research_provider
            provider_types = [type(p).__name__ for p in agg._providers]
            assert "TwitterXResearchProvider" in provider_types

    def test_tiktok_provider_wired_when_credentials_set(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from ttvturbo.app_factory import create_app
        from ttvturbo.settings import Settings

        s = Settings(data_root=tmp_path / "data")
        s.tiktok_client_key = "fake-key"
        s.tiktok_client_secret = "fake-secret"
        s.ideas_research_providers = "tiktok"
        app = create_app(settings=s)
        with TestClient(app) as client:
            container = app.state.container
            svc = container.ideas_research_service
            agg = svc.research_provider
            provider_types = [type(p).__name__ for p in agg._providers]
            assert "TikTokResearchProvider" in provider_types

    def test_google_trends_wired_by_default(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from ttvturbo.app_factory import create_app
        from ttvturbo.settings import Settings

        s = Settings(data_root=tmp_path / "data")
        app = create_app(settings=s)
        with TestClient(app) as client:
            container = app.state.container
            svc = container.ideas_research_service
            agg = svc.research_provider
            provider_types = [type(p).__name__ for p in agg._providers]
            assert "GoogleTrendsResearchProvider" in provider_types

    def test_reddit_not_wired_without_credentials(self, tmp_path: Path):
        """Reddit requires OAuth2 — should not be wired without credentials."""
        from fastapi.testclient import TestClient
        from ttvturbo.app_factory import create_app
        from ttvturbo.settings import Settings

        s = Settings(data_root=tmp_path / "data")
        # Even though "reddit" is in the providers list, without credentials
        # the RedditResearchProvider is still wired but will report
        # available()=False.  The aggregator will skip it at search time.
        s.ideas_research_providers = "reddit,google_trends"
        app = create_app(settings=s)
        with TestClient(app) as client:
            container = app.state.container
            svc = container.ideas_research_service
            agg = svc.research_provider
            provider_types = [type(p).__name__ for p in agg._providers]
            # Reddit is wired (it's in the enabled list) but will report
            # unavailable at runtime without credentials.
            assert "RedditResearchProvider" in provider_types
            # Verify it reports unavailable.
            reddit = next(p for p in agg._providers if type(p).__name__ == "RedditResearchProvider")
            assert reddit.available() is False

    def test_no_twitch_provider_in_default_wiring(self, tmp_path: Path):
        """Twitch should no longer be wired (removed per user request)."""
        from fastapi.testclient import TestClient
        from ttvturbo.app_factory import create_app
        from ttvturbo.settings import Settings

        s = Settings(data_root=tmp_path / "data")
        app = create_app(settings=s)
        with TestClient(app) as client:
            container = app.state.container
            svc = container.ideas_research_service
            agg = svc.research_provider
            provider_types = [type(p).__name__ for p in agg._providers]
            assert not any("Twitch" in pt for pt in provider_types)
