"""Concrete research provider adapters for Ideas Research.

Each adapter implements the :class:`ResearchProvider` protocol from
:mod:`ttvturbo.ideas_research.providers`.  The service never performs
network calls directly — it delegates to whichever provider is wired
in ``app_factory.py``.

Available adapters:

* :class:`RedditResearchProvider` — fetches hot posts from configurable
  subreddits via Reddit's public JSON API.  **No API key needed.**
* :class:`YouTubeResearchProvider` — uses the YouTube Data API v3 to
  search for recent gaming videos.  Needs ``YOUTUBE_API_KEY``.
* :class:`RssResearchProvider` — fetches and parses gaming news RSS
  feeds.  **No API key needed.**
* :class:`TwitterXResearchProvider` — searches recent posts on X
  (formerly Twitter) for gaming topics.  Needs ``X_BEARER_TOKEN``.
* :class:`GoogleTrendsResearchProvider` — fetches related queries and
  interest scores from Google Trends.  **No API key needed** (uses the
  unofficial JSON endpoint).
* :class:`TikTokResearchProvider` — searches viral videos via the
  TikTok Research API.  Needs ``TIKTOK_CLIENT_KEY`` +
  ``TIKTOK_CLIENT_SECRET``.
* :class:`AggregatingResearchProvider` — combines all configured
  providers, merges their results and deduplicates by URL.

All adapters use :mod:`httpx` (already in requirements.txt) for HTTP.
Every adapter gracefully reports ``available() == False`` when its
prerequisites (API key, network) are missing, so the
:class:`AggregatingResearchProvider` can skip it and use the others.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import re
import time
import xml.etree.ElementTree as _ET
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx

from .providers import RawSource, ResearchProvider
from .schemas import Reliability

logger = logging.getLogger("ttvturbo.ideas_research.research_adapters")

# Default gaming subreddits for Reddit.
_DEFAULT_SUBREDDITS = (
    "gaming", "Games", "LivestreamFail", "Twitch",
)

# Default gaming RSS feeds.
_DEFAULT_RSS_FEEDS = (
    "https://feeds.feedburner.com/ign/games-all",
    "https://www.pcgamer.com/rss/",
    "https://www.eurogamer.net/feed",
    "https://kotaku.com/rss",
)

# HTTP timeout for all adapter calls.
_HTTP_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _iso_from_epoch(epoch: float | int | None) -> str:
    if epoch is None or epoch <= 0:
        return ""
    try:
        return (
            _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except (ValueError, OSError, OverflowError):
        return ""


def _iso_from_rfc3339(value: str) -> str:
    """Normalise an RFC-3339 / ISO-8601 string to a clean ISO string."""
    if not value:
        return ""
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            microsecond=0
        ).isoformat()
    except (ValueError, TypeError):
        return value


def _truncate(text: str, limit: int = 300) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def _normalize_growth_signal(raw: float, cap: float = 1_000_000.0) -> float:
    """Log-saturating normalisation of a raw engagement count to 0..1."""
    if raw <= 0 or cap <= 0:
        return 0.0
    return min(1.0, math.log10(1 + raw) / math.log10(1 + cap))


def _time_range_to_iso(time_range: str) -> str:
    """Convert a time range code like '7d' to an ISO-8601 cutoff string."""
    from .scoring import parse_time_range_seconds
    seconds = parse_time_range_seconds(time_range)
    if seconds is None:
        seconds = 7 * 86400.0
    cutoff = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return cutoff.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Reddit Research Provider
# ---------------------------------------------------------------------------


class RedditResearchProvider:
    """Research provider that fetches hot posts from gaming subreddits.

    Reddit now requires OAuth2 for all API access (the old unauthenticated
    ``.json`` endpoint returns 403).  This adapter supports the OAuth2
    "script" application flow: provide ``client_id`` and ``client_secret``
    (register at https://www.reddit.com/prefs/apps).  Without credentials,
    ``available()`` returns False.

    ``subreddits`` defaults to a gaming-focused set but can be
    overridden.  When a requested topic matches a subreddit name, that
    subreddit is queried directly; otherwise the default set is used
    and the topic is used as a search term.
    """

    _TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    _API_BASE = "https://oauth.reddit.com"

    def __init__(
        self,
        *,
        client_id: str = "",
        client_secret: str = "",
        subreddits: tuple[str, ...] = _DEFAULT_SUBREDDITS,
        http_client: Optional[httpx.Client] = None,
        timeout: float = _HTTP_TIMEOUT,
        max_posts_per_sub: int = 10,
        user_agent: str = "TTVturbo/1.0 (Ideas Research Backend)",
    ) -> None:
        self._client_id = client_id or ""
        self._client_secret = client_secret or ""
        self._subreddits = subreddits
        self._client = http_client
        self._timeout = timeout
        self._max_posts = max(1, max_posts_per_sub)
        self._user_agent = user_agent
        self._token: str = ""
        self._token_expiry: float = 0.0

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _ensure_token(self) -> str:
        """Fetch or reuse the OAuth2 access token."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        if not self._client_id or not self._client_secret:
            raise IdeasResearchUnavailableError("Reddit OAuth2 credentials not configured")
        resp = self._get_client().post(
            self._TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            headers={"User-Agent": self._user_agent},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = str(data.get("access_token") or "")
        expires_in = int(data.get("expires_in") or 3600)
        self._token_expiry = time.time() + expires_in
        return self._token

    def available(self) -> bool:
        if not self._client_id or not self._client_secret:
            return False
        try:
            self._ensure_token()
            return bool(self._token)
        except Exception:
            return False

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        from .schemas import IdeasResearchUnavailableError

        if not self._client_id or not self._client_secret:
            raise IdeasResearchUnavailableError(
                "Reddit OAuth2 credentials not configured"
            )
        token = self._ensure_token()
        results: list[RawSource] = []
        for topic in topics[:max_topics]:
            subs = self._pick_subreddits(topic)
            for sub in subs:
                try:
                    results.extend(self._fetch_sub_hot(sub, topic, token))
                except Exception as exc:
                    logger.debug("Reddit fetch failed for r/%s: %s", sub, exc)
        return results

    def _pick_subreddits(self, topic: str) -> list[str]:
        clean = topic.strip().lower().replace("r/", "").replace("/", "")
        if clean and " " not in clean and clean in {s.lower() for s in self._subreddits}:
            return [clean]
        return list(self._subreddits)

    def _fetch_sub_hot(self, subreddit: str, topic: str, token: str) -> list[RawSource]:
        url = f"{self._API_BASE}/r/{subreddit}/hot"
        resp = self._get_client().get(
            url,
            params={"limit": self._max_posts, "raw_json": 1},
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self._user_agent,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        out: list[RawSource] = []
        children = (data.get("data") or {}).get("children") or []
        for child in children:
            post = (child.get("data") or {})
            score = int(post.get("score") or 0)
            comments = int(post.get("num_comments") or 0)
            permalink = str(post.get("permalink") or "")
            full_url = f"https://www.reddit.com{permalink}" if permalink else ""
            created = float(post.get("created_utc") or 0)
            title = str(post.get("title") or "")
            if topic and " " in topic.strip():
                if topic.strip().lower() not in title.lower():
                    continue
            engagement = {"upvotes": score, "comments": comments}
            out.append(RawSource(
                url=full_url,
                title=title.strip(),
                publisher=f"r/{subreddit}",
                published_at=_iso_from_epoch(created),
                summary=_truncate(
                    str(post.get("selftext") or "") or title, 300
                ),
                growth_signal=_normalize_growth_signal(score, 10_000),
                reliability_hint=Reliability.LOW.value,
                engagement_metrics=engagement,
            ))
        return out


# ---------------------------------------------------------------------------
# YouTube Data API v3 Research Provider
# ---------------------------------------------------------------------------


class YouTubeResearchProvider:
    """Research provider using the YouTube Data API v3.

    Searches for recent videos matching the requested topics and
    fetches statistics (views, likes, comments).  Requires a
    ``YOUTUBE_API_KEY``.  Without a key, ``available()`` returns False
    and ``search()`` raises :class:`IdeasResearchUnavailableError`.
    """

    _BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(
        self,
        api_key: str,
        *,
        client: Optional[httpx.Client] = None,
        timeout: float = _HTTP_TIMEOUT,
        max_results_per_topic: int = 10,
    ) -> None:
        self._api_key = api_key or ""
        self._client = client
        self._timeout = timeout
        self._max_results = max(1, max_results_per_topic)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def available(self) -> bool:
        if not self._api_key:
            return False
        try:
            resp = self._get_client().get(
                f"{self._BASE}/videos",
                params={"part": "id", "chart": "mostPopular", "maxResults": 1, "key": self._api_key},
                timeout=5.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        from .schemas import IdeasResearchUnavailableError

        if not self._api_key:
            raise IdeasResearchUnavailableError(
                "YouTube API key not configured"
            )
        published_after = _time_range_to_iso(time_range)
        results: list[RawSource] = []
        for topic in topics[:max_topics]:
            try:
                results.extend(self._search_topic(topic, language, published_after))
            except Exception as exc:
                logger.debug("YouTube search failed for %r: %s", topic, exc)
        return results

    def _search_topic(self, topic: str, language: str, published_after: str) -> list[RawSource]:
        resp = self._get_client().get(
            f"{self._BASE}/search",
            params={
                "part": "snippet",
                "q": topic,
                "type": "video",
                "maxResults": self._max_results,
                "order": "viewCount",
                "publishedAfter": published_after,
                "relevanceLanguage": language,
                "key": self._api_key,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") or []
        if not items:
            return []
        video_ids = [str(item.get("id", {}).get("videoId", "")) for item in items if item.get("id", {}).get("videoId")]
        if not video_ids:
            return []
        stats = self._fetch_stats(video_ids)
        out: list[RawSource] = []
        for item in items:
            vid = str(item.get("id", {}).get("videoId", ""))
            snippet = item.get("snippet") or {}
            stat = stats.get(vid, {})
            views = int(stat.get("viewCount") or 0)
            likes = int(stat.get("likeCount") or 0)
            comments = int(stat.get("commentCount") or 0)
            out.append(RawSource(
                url=f"https://www.youtube.com/watch?v={vid}" if vid else "",
                title=str(snippet.get("title") or "").strip(),
                publisher=str(snippet.get("channelTitle") or "YouTube"),
                published_at=_iso_from_rfc3339(str(snippet.get("publishedAt") or "")),
                summary=_truncate(str(snippet.get("description") or ""), 300),
                growth_signal=_normalize_growth_signal(views, 500_000),
                reliability_hint=Reliability.MEDIUM.value,
                engagement_metrics={"views": views, "likes": likes, "comments": comments},
            ))
        return out

    def _fetch_stats(self, video_ids: list[str]) -> dict[str, dict]:
        if not video_ids:
            return {}
        resp = self._get_client().get(
            f"{self._BASE}/videos",
            params={
                "part": "statistics",
                "id": ",".join(video_ids),
                "key": self._api_key,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        out: dict[str, dict] = {}
        for item in data.get("items") or []:
            vid = str(item.get("id") or "")
            if vid:
                out[vid] = item.get("statistics") or {}
        return out


# ---------------------------------------------------------------------------
# RSS Feed Research Provider
# ---------------------------------------------------------------------------


class RssResearchProvider:
    """Research provider that fetches and parses gaming news RSS feeds.

    No API key needed.  Uses a configurable list of feed URLs.  Items
    are filtered by topic (title/summary match).

    ``available()`` is True when at least one feed is reachable.
    """

    def __init__(
        self,
        *,
        feeds: tuple[str, ...] = _DEFAULT_RSS_FEEDS,
        client: Optional[httpx.Client] = None,
        timeout: float = _HTTP_TIMEOUT,
        max_items_per_feed: int = 10,
    ) -> None:
        self._feeds = feeds
        self._client = client
        self._timeout = timeout
        self._max_items = max(1, max_items_per_feed)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def available(self) -> bool:
        for url in self._feeds:
            try:
                resp = self._get_client().get(
                    url, timeout=5.0, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code == 200:
                    return True
            except Exception:
                continue
        return False

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        results: list[RawSource] = []
        topic_terms = [t.strip().lower() for t in topics if t.strip()]
        for feed_url in self._feeds:
            try:
                results.extend(self._fetch_feed(feed_url, topic_terms))
            except Exception as exc:
                logger.debug("RSS fetch failed for %s: %s", feed_url, exc)
        return results

    def _fetch_feed(self, feed_url: str, topic_terms: list[str]) -> list[RawSource]:
        resp = self._get_client().get(
            feed_url, timeout=self._timeout, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        root = _ET.fromstring(resp.content)
        out: list[RawSource] = []
        if root.tag == "rss":
            items = root.findall(".//item")[: self._max_items]
        else:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")[: self._max_items]
        for item in items:
            title = _text(item, "title")
            link = _text(item, "link")
            if not link:
                link = _attr(item, "link", "href")
            pub = _text(item, "pubDate") or _text(item, "{http://www.w3.org/2005/Atom}published")
            summary = _text(item, "description") or _text(item, "{http://www.w3.org/2005/Atom}summary")
            combined = f"{title} {summary}".lower()
            if topic_terms and not any(term in combined for term in topic_terms):
                continue
            out.append(RawSource(
                url=link,
                title=title.strip(),
                publisher=_extract_publisher(root, feed_url),
                published_at=_parse_rss_date(pub),
                summary=_truncate(summary, 300),
                growth_signal=0.0,
                reliability_hint=Reliability.MEDIUM.value,
                engagement_metrics={},
            ))
        return out


# ---------------------------------------------------------------------------
# RSS helper functions
# ---------------------------------------------------------------------------


def _text(parent: _ET.Element, tag: str) -> str:
    elem = parent.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    local = tag.split("}")[-1] if "}" in tag else tag
    for child in parent:
        if child.tag.split("}")[-1] == local:
            if child.text:
                return child.text.strip()
    return ""


def _attr(parent: _ET.Element, tag: str, attr: str) -> str:
    elem = parent.find(tag)
    if elem is not None:
        return elem.get(attr, "")
    local = tag.split("}")[-1] if "}" in tag else tag
    for child in parent:
        if child.tag.split("}")[-1] == local:
            val = child.get(attr)
            if val:
                return val
    return ""


def _extract_publisher(root: _ET.Element, feed_url: str) -> str:
    for elem in root.iter():
        if elem.tag.split("}")[-1] == "title" and elem.text:
            return elem.text.strip()
    from urllib.parse import urlsplit
    parts = urlsplit(feed_url)
    return parts.netloc or feed_url


def _parse_rss_date(value: str) -> str:
    if not value:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return _dt.datetime.strptime(value.strip(), fmt).replace(
                microsecond=0
            ).isoformat()
        except ValueError:
            continue
    return _iso_from_rfc3339(value)


# ---------------------------------------------------------------------------
# Twitter / X Research Provider
# ---------------------------------------------------------------------------


class TwitterXResearchProvider:
    """Research provider that searches recent posts on X (formerly Twitter).

    Uses the X API v2 ``GET /2/tweets/search/recent`` endpoint.
    Requires a Bearer Token (``X_BEARER_TOKEN``).  Without it,
    ``available()`` returns False.

    Returns tweets with engagement metrics (retweet_count, reply_count,
    like_count, quote_count) and the author as publisher.  Good for:
    gaming announcements, drama, viral moments, developer posts.
    """

    _BASE = "https://api.twitter.com/2"

    def __init__(
        self,
        bearer_token: str,
        *,
        client: Optional[httpx.Client] = None,
        timeout: float = _HTTP_TIMEOUT,
        max_results_per_topic: int = 10,
    ) -> None:
        self._bearer_token = bearer_token or ""
        self._client = client
        self._timeout = timeout
        self._max_results = max(10, max_results_per_topic)  # API min is 10

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def available(self) -> bool:
        if not self._bearer_token:
            return False
        try:
            resp = self._get_client().get(
                f"{self._BASE}/tweets/search/recent",
                params={"query": "gaming", "max_results": 10},
                headers={"Authorization": f"Bearer {self._bearer_token}"},
                timeout=5.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        from .schemas import IdeasResearchUnavailableError

        if not self._bearer_token:
            raise IdeasResearchUnavailableError(
                "X Bearer Token not configured"
            )
        start_time = _time_range_to_iso(time_range)
        results: list[RawSource] = []
        for topic in topics[:max_topics]:
            try:
                results.extend(self._search_topic(topic, language, start_time))
            except Exception as exc:
                logger.debug("X search failed for %r: %s", topic, exc)
        return results

    def _search_topic(self, topic: str, language: str, start_time: str) -> list[RawSource]:
        # Build query: topic + language filter + exclude retweets.
        query = f"{topic} lang:{language} -is:retweet"
        resp = self._get_client().get(
            f"{self._BASE}/tweets/search/recent",
            params={
                "query": query,
                "max_results": self._max_results,
                "start_time": start_time,
                "tweet.fields": "public_metrics,created_at,author_id,text",
                "expansions": "author_id",
                "user.fields": "username,name",
            },
            headers={"Authorization": f"Bearer {self._bearer_token}"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        users = {u["id"]: u for u in (data.get("includes", {}).get("users") or [])}
        out: list[RawSource] = []
        for tweet in data.get("data") or []:
            tid = str(tweet.get("id") or "")
            author_id = str(tweet.get("author_id") or "")
            user = users.get(author_id, {})
            username = str(user.get("username") or "unknown")
            metrics = tweet.get("public_metrics") or {}
            rts = int(metrics.get("retweet_count") or 0)
            replies = int(metrics.get("reply_count") or 0)
            likes = int(metrics.get("like_count") or 0)
            quotes = int(metrics.get("quote_count") or 0)
            text = str(tweet.get("text") or "")
            total_eng = likes + rts * 3 + replies * 2 + quotes * 2
            out.append(RawSource(
                url=f"https://x.com/{username}/status/{tid}" if tid else "",
                title=_truncate(text, 120),
                publisher=f"@{username}",
                published_at=_iso_from_rfc3339(str(tweet.get("created_at") or "")),
                summary=_truncate(text, 300),
                growth_signal=_normalize_growth_signal(total_eng, 50_000),
                reliability_hint=Reliability.LOW.value,
                engagement_metrics={
                    "likes": likes, "retweets": rts,
                    "comments": replies, "quotes": quotes,
                },
            ))
        return out


# ---------------------------------------------------------------------------
# Google Trends Research Provider
# ---------------------------------------------------------------------------


class GoogleTrendsResearchProvider:
    """Research provider using Google Trends' unofficial JSON endpoint.

    Fetches related queries for the requested topics and converts the
    top rising queries into sources.  **No API key needed** — uses the
    same JSON endpoint that the Google Trends website uses internally.

    The Trends API requires a two-step flow:
    1. ``GET /trends/api/explore`` — returns a list of widgets, each
       with a ``token`` and ``request`` payload.
    2. ``GET /trends/api/widgetdata/related_searches`` — uses the token
       from the ``RELATED_QUERIES`` widget to fetch the actual data.

    Each rising query becomes a :class:`RawSource` with:
    * ``title`` — the related search query
    * ``summary`` — interest score + growth percentage
    * ``growth_signal`` — normalised from the growth percentage
    * ``engagement_metrics`` — ``{"interest": score}`` where score is
      Google's 0-100 interest value.

    ``available()`` is True when the Google Trends explore endpoint is
    reachable and returns a valid widgets response.
    """

    _BASE = "https://trends.google.com/trends/api"

    def __init__(
        self,
        *,
        client: Optional[httpx.Client] = None,
        timeout: float = _HTTP_TIMEOUT,
        max_queries_per_topic: int = 10,
        geo: str = "",  # empty = worldwide
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._max_queries = max(1, max_queries_per_topic)
        self._geo = geo

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _strip_xss_prefix(self, text: str) -> str:
        """Strip the )]}' XSS protection prefix from Trends API responses."""
        if text.startswith(")]}'"):
            return text[4:]
        return text

    def available(self) -> bool:
        try:
            req_payload = json.dumps({
                "comparisonItem": [{"keyword": "test", "geo": "", "time": "now 7-d"}],
                "category": 0,
                "property": "",
            })
            resp = self._get_client().get(
                f"{self._BASE}/explore",
                params={"hl": "en", "tz": "-120", "req": req_payload},
                timeout=8.0,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return False
            text = self._strip_xss_prefix(resp.text)
            data = json.loads(text)
            return bool(data.get("widgets"))
        except Exception:
            return False

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        results: list[RawSource] = []
        for topic in topics[:max_topics]:
            try:
                results.extend(self._fetch_related_queries(topic, time_range))
            except Exception as exc:
                logger.debug("Google Trends failed for %r: %s", topic, exc)
        return results

    def _fetch_related_queries(self, topic: str, time_range: str) -> list[RawSource]:
        timeframe = self._time_range_to_trends_format(time_range)
        # Step 1: get widgets from the explore endpoint.
        req_payload = json.dumps({
            "comparisonItem": [{"keyword": topic, "geo": self._geo, "time": timeframe}],
            "category": 8,  # category 8 = Games
            "property": "",
        })
        resp = self._get_client().get(
            f"{self._BASE}/explore",
            params={"hl": "en", "tz": "-120", "req": req_payload},
            timeout=self._timeout,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        text = self._strip_xss_prefix(resp.text)
        try:
            data = json.loads(text)
        except Exception:
            return []
        widgets = data.get("widgets") or []
        # Find the RELATED_QUERIES widget.
        rq_widget = next(
            (w for w in widgets if w.get("id") == "RELATED_QUERIES"), None
        )
        if not rq_widget or not rq_widget.get("token"):
            return []
        # Step 2: fetch the actual related queries data using the token.
        resp2 = self._get_client().get(
            f"{self._BASE}/widgetdata/related_searches",
            params={
                "hl": "en", "tz": "-120",
                "req": json.dumps(rq_widget["request"]),
                "token": rq_widget["token"],
            },
            timeout=self._timeout,
            follow_redirects=True,
        )
        if resp2.status_code != 200:
            return []
        text2 = self._strip_xss_prefix(resp2.text)
        try:
            data2 = json.loads(text2)
        except Exception:
            return []
        out: list[RawSource] = []
        for group in (data2.get("default", {}).get("rankedList") or []):
            entries = (group.get("rankedKeyword") or [])[: self._max_queries]
            for entry in entries:
                query = str(entry.get("query") or "").strip()
                if not query:
                    continue
                value_str = str(entry.get("value") or "0")
                growth_pct = 0.0
                interest = 0
                if value_str.endswith("%"):
                    try:
                        growth_pct = float(value_str.rstrip("%").replace("+", ""))
                    except ValueError:
                        pass
                else:
                    try:
                        interest = int(float(value_str))
                    except ValueError:
                        pass
                growth = _normalize_growth_signal(max(growth_pct, interest), 500)
                out.append(RawSource(
                    url=f"https://trends.google.com/trends/explore?q={quote_plus(query)}",
                    title=query,
                    publisher="Google Trends",
                    published_at=_dt.datetime.now(tz=_dt.timezone.utc).replace(
                        microsecond=0
                    ).isoformat(),
                    summary=f"Related query for '{topic}': interest={interest}, growth={growth_pct}%",
                    growth_signal=growth,
                    reliability_hint=Reliability.MEDIUM.value,
                    engagement_metrics={"interest": interest},
                ))
        return out

    def _time_range_to_trends_format(self, time_range: str) -> str:
        """Convert a time range code to Google Trends' timeframe format."""
        from .scoring import parse_time_range_seconds
        seconds = parse_time_range_seconds(time_range)
        if seconds is None:
            seconds = 7 * 86400.0
        days = int(seconds / 86400)
        if days <= 1:
            return "now 1-d"
        if days <= 7:
            return "now 7-d"
        if days <= 30:
            return "today 1-m"
        if days <= 90:
            return "today 3-m"
        return "today 12-m"


# ---------------------------------------------------------------------------
# TikTok Research API Provider
# ---------------------------------------------------------------------------


class TikTokResearchProvider:
    """Research provider using the TikTok Research API.

    Searches for videos matching the requested topics and returns them
    with engagement metrics (views, likes, comments, shares).  Requires
    ``TIKTOK_CLIENT_KEY`` and ``TIKTOK_CLIENT_SECRET``.  Without them,
    ``available()`` returns False.

    The TikTok Research API uses OAuth2 client credentials flow: the
    adapter fetches an access token first, then queries the
    ``/v2/research/video/query/`` endpoint.
    """

    _AUTH_URL = "https://open.tiktokapis.com/v2/oauth2/client_token"
    _QUERY_URL = "https://open.tiktokapis.com/v2/research/video/query/"

    def __init__(
        self,
        client_key: str,
        client_secret: str,
        *,
        http_client: Optional[httpx.Client] = None,
        timeout: float = _HTTP_TIMEOUT,
        max_results_per_topic: int = 10,
    ) -> None:
        self._client_key = client_key or ""
        self._client_secret = client_secret or ""
        self._client = http_client
        self._timeout = timeout
        self._max_results = max(1, max_results_per_topic)
        self._token: str = ""
        self._token_expiry: float = 0.0

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _ensure_token(self) -> str:
        """Fetch or reuse the OAuth2 client token."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        resp = self._get_client().post(
            self._AUTH_URL,
            json={
                "client_key": self._client_key,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/json"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = str(data.get("access_token") or "")
        expires_in = int(data.get("expires_in") or 7200)
        self._token_expiry = time.time() + expires_in
        return self._token

    def available(self) -> bool:
        if not self._client_key or not self._client_secret:
            return False
        try:
            self._ensure_token()
            return bool(self._token)
        except Exception:
            return False

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        from .schemas import IdeasResearchUnavailableError

        if not self._client_key or not self._client_secret:
            raise IdeasResearchUnavailableError(
                "TikTok client key/secret not configured"
            )
        token = self._ensure_token()
        start_date, end_date = self._time_range_to_dates(time_range)
        results: list[RawSource] = []
        for topic in topics[:max_topics]:
            try:
                results.extend(self._search_topic(topic, token, start_date, end_date))
            except Exception as exc:
                logger.debug("TikTok search failed for %r: %s", topic, exc)
        return results

    def _time_range_to_dates(self, time_range: str) -> tuple[str, str]:
        """Convert a time range code to (start_date, end_date) in YYYYMMDD."""
        from .scoring import parse_time_range_seconds
        seconds = parse_time_range_seconds(time_range)
        if seconds is None:
            seconds = 7 * 86400.0
        end = _dt.datetime.now(tz=_dt.timezone.utc)
        start = end - _dt.timedelta(seconds=seconds)
        return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    def _search_topic(
        self, topic: str, token: str, start_date: str, end_date: str,
    ) -> list[RawSource]:
        resp = self._get_client().post(
            self._QUERY_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "query": {
                    "and": [
                        {"operation": "IN", "field_name": "keyword", "field_values": [topic]},
                    ],
                },
                "start_date": start_date,
                "end_date": end_date,
                "max_count": self._max_results,
                "fields": ["id", "video_description", "create_time", "share_url",
                           "view_count", "like_count", "comment_count", "share_count",
                           "region_code"],
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        out: list[RawSource] = []
        for video in (data.get("data", {}) or {}).get("videos") or []:
            vid = str(video.get("id") or "")
            views = int(video.get("view_count") or 0)
            likes = int(video.get("like_count") or 0)
            comments = int(video.get("comment_count") or 0)
            shares = int(video.get("share_count") or 0)
            desc = str(video.get("video_description") or "")
            create_time = int(video.get("create_time") or 0)
            out.append(RawSource(
                url=str(video.get("share_url") or f"https://www.tiktok.com/@unknown/video/{vid}"),
                title=_truncate(desc, 120),
                publisher="TikTok",
                published_at=_iso_from_epoch(create_time),
                summary=_truncate(desc, 300),
                growth_signal=_normalize_growth_signal(views, 1_000_000),
                reliability_hint=Reliability.LOW.value,
                engagement_metrics={
                    "views": views, "likes": likes,
                    "comments": comments, "shares": shares,
                },
            ))
        return out


# ---------------------------------------------------------------------------
# Aggregating Research Provider
# ---------------------------------------------------------------------------


class AggregatingResearchProvider:
    """Combines multiple research providers into one.

    Calls ``search()`` on every provider that reports
    ``available() == True`` and merges the results.  Providers that
    are unavailable or raise are silently skipped (logged at DEBUG).

    If no provider is available, ``available()`` returns False and
    ``search()`` raises :class:`IdeasResearchUnavailableError`.
    """

    def __init__(self, providers: list[ResearchProvider]) -> None:
        self._providers = list(providers)

    def available(self) -> bool:
        return any(p.available() for p in self._providers)

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        from .schemas import IdeasResearchUnavailableError

        if not self.available():
            raise IdeasResearchUnavailableError(
                "no research provider available"
            )
        results: list[RawSource] = []
        for provider in self._providers:
            if not provider.available():
                continue
            try:
                results.extend(
                    provider.search(
                        topics, language=language, time_range=time_range, max_topics=max_topics,
                    )
                )
            except Exception as exc:
                logger.debug("Provider %s failed: %s", type(provider).__name__, exc)
        results = _dedup_raw(results)
        return results


def _dedup_raw(sources: list[RawSource]) -> list[RawSource]:
    """Deduplicate raw sources by URL, keeping first occurrence."""
    seen: dict[str, RawSource] = {}
    out: list[RawSource] = []
    for src in sources:
        if not src.url:
            out.append(src)
            continue
        if src.url in seen:
            continue
        seen[src.url] = src
        out.append(src)
    return out
