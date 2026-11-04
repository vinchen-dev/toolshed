"""One adapter per platform: takes the transport, returns normalized posts. Nothing here parses HTML in
Python; browser extractors run in the page and hand back records."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from shed import ToolError
from shed.lib.manila import MANILA
from shed.registry import load_tool_module

from .models import Post

EXTRACTORS = Path(__file__).parent / "extractors"


def _browser() -> Any:
    """The browser-fetch tool module: site registry and the helpers shared across the research group."""
    return load_tool_module("browser-fetch")


def extractor(name: str) -> str:
    return _browser().read_extractor(EXTRACTORS, name)


def parse_count(value: Any) -> int | None:
    return _browser().parse_count(value)


def site_home(site: str) -> str:
    return str(_browser().SITES[site].home).rstrip("/")


def to_manila(iso: str) -> datetime:
    """An ISO-8601 timestamp (Z or offset) as aware Manila time without microseconds."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MANILA).replace(microsecond=0)


def bluesky(transport: Any, query: str, *, limit: int, since: datetime, **_: Any) -> list[Post]:
    data = transport.get_json(
        "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        {"q": query, "limit": min(limit, 100), "sort": "latest", "since": since.astimezone(UTC).isoformat()},
    )
    return [bluesky_post(item) for item in data.get("posts", [])][:limit]


def bluesky_post(item: dict[str, Any]) -> Post:
    handle = item["author"]["handle"]
    rkey = item["uri"].rsplit("/", 1)[-1]
    return Post(
        platform="bluesky",
        url=f"https://bsky.app/profile/{handle}/post/{rkey}",
        author=handle,
        author_url=f"https://bsky.app/profile/{handle}",
        text=item["record"].get("text", ""),
        created_at=to_manila(item["record"]["createdAt"]),
        likes=item.get("likeCount"),
        comments=item.get("replyCount"),
        shares=item.get("repostCount"),
    )


def bluesky_feed(transport: Any, url: str, *, limit: int, **_: Any) -> list[Post]:
    handle = (
        urlparse(url).path.strip("/").split("/")[1] if "/profile/" in url else urlparse(url).path.strip("/")
    )
    data = transport.get_json(
        "https://api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
        {"actor": handle, "limit": min(limit, 100), "filter": "posts_no_replies"},
    )
    return [bluesky_post(item["post"]) for item in data.get("feed", []) if item.get("post")][:limit]


def hackernews(transport: Any, query: str, *, limit: int, since: datetime, **_: Any) -> list[Post]:
    data = transport.get_json(
        "https://hn.algolia.com/api/v1/search",
        {
            "query": query,
            "tags": "story",
            "hitsPerPage": min(limit, 100),
            "numericFilters": f"created_at_i>{int(since.timestamp())}",
        },
    )
    posts: list[Post] = []
    for hit in data.get("hits", []):
        text = hit.get("title") or ""
        if hit.get("url"):
            text += "\n" + hit["url"]
        elif hit.get("story_text"):
            text += "\n" + hit["story_text"]
        posts.append(
            Post(
                platform="hackernews",
                url=f"https://news.ycombinator.com/item?id={hit['objectID']}",
                author=hit.get("author"),
                author_url=f"https://news.ycombinator.com/user?id={hit['author']}"
                if hit.get("author")
                else None,
                text=text,
                created_at=to_manila(hit["created_at"]),
                likes=hit.get("points"),
                comments=hit.get("num_comments"),
            )
        )
    return posts[:limit]


def news(transport: Any, query: str, *, limit: int, since: datetime, **_: Any) -> list[Post]:
    text = transport.get_text(
        "https://news.google.com/rss/search", {"q": query, "hl": "en-PH", "gl": "PH", "ceid": "PH:en"}
    )
    posts: list[Post] = []
    for item in ET.fromstring(text).iter("item"):
        source = item.find("source")
        published = item.findtext("pubDate")
        posts.append(
            Post(
                platform="news",
                url=item.findtext("link") or "",
                author=source.text if source is not None else None,
                author_url=source.get("url") if source is not None else None,
                text=item.findtext("title") or "",
                created_at=parsedate_to_datetime(published).astimezone(MANILA) if published else None,
            )
        )
    return posts[:limit]


def video_post(entry: dict[str, Any]) -> Post:
    stamp = entry.get("timestamp")
    return Post(
        platform="youtube",
        url=entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
        author=entry.get("channel") or entry.get("uploader"),
        author_url=entry.get("channel_url"),
        text=entry.get("title") or "",
        created_at=datetime.fromtimestamp(stamp, MANILA) if stamp else None,
        views=entry.get("view_count"),
    )


def youtube(transport: Any, query: str, *, limit: int, since: datetime, **_: Any) -> list[Post]:
    entries = transport.ytdlp(f"ytsearch{limit}:{query}")
    return [video_post(e) for e in entries if e.get("id")][:limit]


def youtube_channel(transport: Any, url: str, *, limit: int, **_: Any) -> list[Post]:
    """A channel listing comes back flat, without dates or views; each kept video gets its own pass."""
    posts: list[Post] = []
    for entry in transport.ytdlp(url, flat=True):
        if not entry.get("id"):
            continue
        detailed = transport.ytdlp(
            entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}", flat=False
        )
        posts.append(video_post(detailed[0] if detailed else entry))
        if len(posts) >= limit:
            break
    return posts


def ddg_link(href: str) -> str:
    """DuckDuckGo wraps results in a redirect; the target sits in its uddg parameter."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def web(
    transport: Any, query: str, *, limit: int, site: str | None = None, headed: bool | None = None, **_: Any
) -> list[Post]:
    words = f"site:{site} {query}" if site else query
    records = transport.browse(
        "duckduckgo",
        f"https://html.duckduckgo.com/html/?q={quote_plus(words)}",
        extractor("ddg"),
        profile=False,
        headed=headed,
        scrolls=0,
    )
    posts = [
        Post(platform="web", url=ddg_link(r["href"]), text=f"{r['title']}\n{r['snippet']}".strip())
        for r in records
        if r.get("href")
    ]
    return posts[:limit]


def threads(transport: Any, query: str, *, limit: int, headed: bool | None = None, **_: Any) -> list[Post]:
    url = f"https://www.threads.com/search?q={quote_plus(query)}&serp_type=default"
    return threads_records(transport, url, limit=limit, headed=headed)


def threads_records(transport: Any, url: str, *, limit: int, headed: bool | None) -> list[Post]:
    records = transport.browse("threads", url, extractor("threads"), profile=False, headed=headed, scrolls=1)
    posts: list[Post] = []
    for r in records:
        author = r.get("author")
        posts.append(
            Post(
                platform="threads",
                url="https://www.threads.com" + r["url"],
                author=author,
                author_url=f"https://www.threads.com/@{author}" if author else None,
                text=r.get("text") or "",
                created_at=to_manila(r["time"]) if r.get("time") else None,
                likes=parse_count(r.get("likes")),
                comments=parse_count(r.get("comments")),
                shares=parse_count(r.get("reposts")),
            )
        )
    return posts[:limit]


def tiktok_trends(
    transport: Any, query: str, *, limit: int, headed: bool | None = None, **_: Any
) -> list[Post]:
    records = transport.browse(
        "tiktok-trends",
        "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en?region=PH",
        extractor("tiktok-trends"),
        profile=False,
        headed=headed,
        scrolls=1,
    )
    posts: list[Post] = []
    for r in records:
        tag = str(r.get("hashtag") or "").lstrip("#")
        if not tag:
            continue
        posts.append(
            Post(
                platform="tiktok-trends",
                url=f"https://www.tiktok.com/tag/{tag}",
                text=f"#{r.get('rank')} #{tag} · {r.get('category') or '-'} · {r.get('posts') or '?'} posts",
                views=parse_count(r.get("views")),
                tags=[tag],
            )
        )
    return posts[:limit]


_FB_TRACKING = ("__cft__", "__tn__", "ref", "notif_id", "notif_t", "mibextid")


def facebook_link(href: str) -> str:
    parsed = urlparse(href if href.startswith("http") else "https://www.facebook.com" + href)
    keep = [(k, v[0]) for k, v in parse_qs(parsed.query).items() if not k.startswith(_FB_TRACKING)]
    query = "&".join(f"{k}={v}" for k, v in keep)
    return parsed._replace(query=query, fragment="").geturl()


def facebook_page(
    transport: Any, url: str, *, limit: int, headed: bool | None = None, **_: Any
) -> list[Post]:
    records = transport.browse(
        "facebook", url, extractor("facebook-page"), profile=False, headed=headed, scrolls=1
    )
    posts = [
        Post(
            platform="facebook",
            url=facebook_link(r["url"]),
            author=r.get("author"),
            author_url=url,
            text=r.get("text") or "",
            when=r.get("when"),
        )
        for r in records
        if r.get("url")
    ]
    return posts[:limit]


def threads_profile(
    transport: Any, url: str, *, limit: int, headed: bool | None = None, **_: Any
) -> list[Post]:
    return threads_records(transport, url, limit=limit, headed=headed)


def platform_for(url: str) -> str:
    """Which platform a page, channel, account or profile URL belongs to."""
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    for domain, name in HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return name
    raise ToolError(
        f"no watcher for {host}: use a Facebook Page, YouTube channel, Bluesky, Threads, "
        "Instagram, TikTok, X or Reddit URL"
    )


HOSTS: dict[str, str] = {
    "facebook.com": "facebook",
    "youtube.com": "youtube",
    "bsky.app": "bluesky",
    "threads.com": "threads",
    "threads.net": "threads",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "x.com": "x",
    "twitter.com": "x",
    "reddit.com": "reddit",
}


def reddit_post(r: dict[str, Any]) -> Post | None:
    if not r.get("permalink"):
        return None
    author = r.get("author")
    return Post(
        platform="reddit",
        url="https://www.reddit.com" + r["permalink"],
        author=author,
        author_url=f"https://www.reddit.com/user/{author}" if author else None,
        text=r.get("title") or "",
        created_at=to_manila(r["created"]) if r.get("created") else None,
        likes=parse_count(r.get("score")),
        comments=parse_count(r.get("comments")),
        tags=[r["subreddit"]] if r.get("subreddit") else [],
    )


def site_post(site: str, r: dict[str, Any]) -> Post | None:
    """A facebook, instagram, tiktok or x record as a post; links become absolute and lose tracking."""
    if not r.get("url"):
        return None
    base = site_home(site)
    link = r["url"] if r["url"].startswith("http") else base + r["url"]
    if site == "facebook":
        link = facebook_link(link)
    author = r.get("author")
    handle = f"@{author}" if site == "tiktok" else author
    return Post(
        platform=site,
        url=link,
        author=author,
        author_url=f"{base}/{handle}" if author else None,
        text=r.get("text") or "",
        created_at=to_manila(r["time"]) if r.get("time") else None,
        when=r.get("when"),
        likes=parse_count(r.get("likes")),
        comments=parse_count(r.get("comments")),
        shares=parse_count(r.get("shares")),
        views=parse_count(r.get("views")),
    )


def logged_in_posts(
    transport: Any, site: str, url: str, extractor_js: str, *, limit: int, headed: bool | None
) -> list[Post]:
    records = transport.browse(site, url, extractor_js, profile=True, headed=headed, scrolls=2)
    mapper = reddit_post if site == "reddit" else lambda r: site_post(site, r)
    posts = [post for post in (mapper(r) for r in records) if post is not None]
    return posts[:limit]


def facebook(transport: Any, query: str, *, limit: int, headed: bool | None = None, **_: Any) -> list[Post]:
    url = f"https://www.facebook.com/search/posts/?q={quote_plus(query)}"
    return logged_in_posts(
        transport, "facebook", url, extractor("facebook-search"), limit=limit, headed=headed
    )


def instagram(transport: Any, query: str, *, limit: int, headed: bool | None = None, **_: Any) -> list[Post]:
    words = query.strip()
    url = (
        f"https://www.instagram.com/explore/tags/{quote_plus(words[1:])}/"
        if words.startswith("#")
        else f"https://www.instagram.com/explore/search/keyword/?q={quote_plus(words)}"
    )
    return logged_in_posts(transport, "instagram", url, extractor("instagram"), limit=limit, headed=headed)


def tiktok(transport: Any, query: str, *, limit: int, headed: bool | None = None, **_: Any) -> list[Post]:
    url = f"https://www.tiktok.com/search?q={quote_plus(query)}"
    return logged_in_posts(transport, "tiktok", url, extractor("tiktok"), limit=limit, headed=headed)


def x(transport: Any, query: str, *, limit: int, headed: bool | None = None, **_: Any) -> list[Post]:
    url = f"https://x.com/search?q={quote_plus(query)}&src=typed_query&f=live"
    return logged_in_posts(transport, "x", url, extractor("x"), limit=limit, headed=headed)


def reddit(transport: Any, query: str, *, limit: int, headed: bool | None = None, **_: Any) -> list[Post]:
    url = f"https://www.reddit.com/search/?q={quote_plus(query)}&sort=new"
    return logged_in_posts(transport, "reddit", url, extractor("reddit"), limit=limit, headed=headed)


def _profile_watcher(site: str, extractor: str) -> Adapter:
    def watch(transport: Any, url: str, *, limit: int, headed: bool | None = None, **_: Any) -> list[Post]:
        return logged_in_posts(transport, site, url, extractor, limit=limit, headed=headed)

    return watch


Adapter = Callable[..., list[Post]]
WATCHERS: dict[str, Adapter] = {
    "facebook": facebook_page,
    "youtube": youtube_channel,
    "bluesky": bluesky_feed,
    "threads": threads_profile,
    "instagram": _profile_watcher("instagram", extractor("instagram")),
    "tiktok": _profile_watcher("tiktok", extractor("tiktok")),
    "x": _profile_watcher("x", extractor("x")),
    "reddit": _profile_watcher("reddit", extractor("reddit")),
}
ADAPTERS: dict[str, Adapter] = {
    "bluesky": bluesky,
    "hackernews": hackernews,
    "news": news,
    "youtube": youtube,
    "web": web,
    "threads": threads,
    "tiktok-trends": tiktok_trends,
    "facebook": facebook,
    "instagram": instagram,
    "tiktok": tiktok,
    "x": x,
    "reddit": reddit,
}
