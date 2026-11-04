"""Search social platforms, news and the web for posts about a topic, or watch a page, channel or profile.

Anonymous platforms (youtube, threads, bluesky, hackernews, news, web, tiktok-trends) need no account and are
safe to schedule. facebook, instagram, tiktok, x and reddit need a one-time manual login through
`shed browser-fetch --login <site>` and the --use-login flag on each run; keep those out of cron.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from shed import Tool, ToolContext, ToolError
from shed.lib import manila

from . import cursor as cursor_file
from . import platforms
from .models import PlatformResult, Post, SocialSearch

Platform = Literal[
    "youtube",
    "threads",
    "bluesky",
    "hackernews",
    "news",
    "web",
    "tiktok-trends",
    "facebook",
    "instagram",
    "tiktok",
    "x",
    "reddit",
]
ANONYMOUS: tuple[str, ...] = ("youtube", "threads", "bluesky", "hackernews", "news", "web", "tiktok-trends")
LOGGED_IN: tuple[str, ...] = ("facebook", "instagram", "tiktok", "x", "reddit")
DEFAULT_PLATFORMS: list[Platform] = ["youtube", "threads", "bluesky", "hackernews", "news", "web"]
_DURATION = re.compile(r"^(\d+)([mhd])$")


def now_manila() -> datetime:
    """Aware Manila time without microseconds. Patched in tests."""
    return manila.now_manila().replace(microsecond=0)


def parse_since(text: str, now: datetime) -> datetime:
    """'24h', '90m', '7d' relative to now, or a Manila time 'YYYY-MM-DD HH:MM[:SS]'."""
    if m := _DURATION.match(text.strip()):
        amount, unit = int(m.group(1)), m.group(2)
        return now - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[unit]: amount})
    try:
        return manila.parse_manila(text)
    except ValueError as exc:
        raise ToolError(f"bad --since '{text}': use 24h, 90m, 7d or 'YYYY-MM-DD HH:MM'") from exc


class Transport:
    """Every live route the tool uses. Tests replace the module-level instance with a fake."""

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        import httpx

        response = httpx.get(url, params=params, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        import httpx

        response = httpx.get(url, params=params, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response.text

    def ytdlp(self, target: str, *, flat: bool = True) -> list[dict[str, Any]]:
        """Entries of a yt-dlp search ('ytsearchN:words') or a listing (channel URL)."""
        import yt_dlp

        options: Any = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": flat}
        with yt_dlp.YoutubeDL(options) as ydl:
            info: Any = ydl.extract_info(target, download=False) or {}
        return list(info.get("entries") or [info])

    def browse(
        self, site: str, url: str, extractor: str, *, profile: bool, headed: bool | None, scrolls: int
    ) -> Any:
        """Open the page through browser-fetch (same group) and return what the in-page extractor produced."""
        from shed.registry import load_tool_module

        browser = load_tool_module("browser-fetch")
        return browser.extract(site, url, extractor, profile=profile, headed=headed, scrolls=scrolls)


transport = Transport()


class Input(BaseModel):
    query: str | None = Field(None, description="Words to search for")
    url: str | None = Field(
        None,
        description="Instead of a query: a page, channel, account or profile URL to list recent posts from",
    )
    platform: list[Platform] = Field(
        default_factory=lambda: list(DEFAULT_PLATFORMS),
        description=(
            "Platforms to search; repeat for several. Anonymous: youtube threads bluesky hackernews news web "
            "tiktok-trends. Need --use-login and a saved session: facebook instagram tiktok x reddit"
        ),
    )
    site: str | None = Field(None, description="Limit the web platform to one domain, e.g. facebook.com")
    limit: int = Field(20, ge=1, le=100, description="Posts to keep per platform")
    since: str = Field("24h", description="Window: 24h, 90m, 7d or a Manila time 'YYYY-MM-DD HH:MM'")
    cursor: str | None = Field(
        None,
        description=(
            "State file for repeated checks: the first run applies --since and records what it saw, later "
            "runs return only posts not seen before"
        ),
    )
    use_login: bool = Field(
        False, description="Allow the logged-in platforms (uses your saved browser sessions; never on cron)"
    )
    strict: bool = Field(False, description="Fail the run when any platform is skipped or fails")
    headed: bool | None = Field(None, description="Force a visible (true) or headless (false) browser")

    @model_validator(mode="after")
    def _query_or_url(self) -> Input:
        if bool(self.query) == bool(self.url):
            raise ValueError("give exactly one of --query or --url")
        return self


def run(params: Input, ctx: ToolContext) -> SocialSearch:
    now = now_manila()
    since = parse_since(params.since, now)
    subject = params.query or params.url or ""
    cursor_path = (ctx.cwd / Path(params.cursor).expanduser()) if params.cursor else None
    state = cursor_file.load(cursor_path) if cursor_path else {}
    results: list[PlatformResult] = []
    first_run = True
    names = [platforms.platform_for(params.url)] if params.url else list(params.platform)
    for name in names:
        anonymous_watch = params.url and name == "facebook"
        if name in LOGGED_IN and not params.use_login and not anonymous_watch:
            note = f"skipped: needs --use-login and a saved session (shed browser-fetch --login {name})"
            if params.strict:
                raise ToolError(f"{name}: {note}")
            results.append(PlatformResult(platform=name, ok=False, note=note))
            continue
        ctx.log(f"{name}: {'listing ' + params.url if params.url else 'searching'}")
        adapter = platforms.WATCHERS[name] if params.url else platforms.ADAPTERS[name]
        try:
            posts = adapter(
                transport,
                params.url or params.query or "",
                limit=params.limit,
                since=since,
                site=params.site,
                headed=params.headed,
            )
        except Exception as exc:  # one platform failing must not take the others down
            if params.strict:
                raise ToolError(f"{name}: {exc}") from exc
            ctx.log(f"{name}: failed: {exc}")
            results.append(PlatformResult(platform=name, ok=False, note=str(exc)))
            continue
        posts = unique_by_link(posts)
        key = cursor_file.key(name, subject)
        if key in state:
            first_run = False
            posts = cursor_file.unseen(state, key, posts)
        else:
            posts = [p for p in posts if p.created_at is None or p.created_at >= since]
        if cursor_path:
            cursor_file.remember(state, key, posts)
        results.append(PlatformResult(platform=name, ok=True, posts=posts))
    if cursor_path:
        cursor_file.save(cursor_path, state)
    return SocialSearch(
        query=params.query,
        url=params.url,
        since=since if first_run else None,
        fetched_at=now,
        platforms=results,
        total=sum(len(r.posts) for r in results),
    )


def unique_by_link(posts: list[Post]) -> list[Post]:
    seen: set[str] = set()
    return [p for p in posts if not (p.url in seen or seen.add(p.url))]


def newest_first(posts: list[Post]) -> list[Post]:
    """Dated posts newest first, then the undated ones in the order the platform gave them."""
    dated = sorted((p for p in posts if p.created_at is not None), key=lambda p: p.created_at or now_manila())
    return list(reversed(dated)) + [p for p in posts if p.created_at is None]


def _count(value: int | None) -> str:
    return "-" if value is None else str(value)


def compact(value: int) -> str:
    """16_600_000 -> '16.6M', 18_400 -> '18.4K', 950 -> '950'."""
    for scale, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= scale:
            return f"{value / scale:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


def render(result: SocialSearch) -> str:
    lines: list[str] = []
    for block in result.platforms:
        note = f" ({block.note})" if block.note else ""
        if block.platform == "tiktok-trends":
            lines.append(f"== tiktok-trends: {len(block.posts)} hashtags{note}")
            for post in block.posts:
                views = f"{compact(post.views)} views" if post.views is not None else "- views"
                lines.append(f"  {post.text}  {views}  {post.url}")
            continue
        lines.append(f"== {block.platform}: {len(block.posts)} posts (likes/comments/shares){note}")
        for post in newest_first(block.posts):
            when = post.created_at.strftime("%Y-%m-%d %H:%M:%S") if post.created_at else (post.when or "-")
            engagement = f"{_count(post.likes)}/{_count(post.comments)}/{_count(post.shares)}"
            text = " ".join(post.text.split())[:70]
            lines.append(
                f"  {when:<19}  {(post.author or '-')[:28]:<28}  {engagement:>9}  {text:<70}  {post.url}"
            )
    return "\n".join(lines)


TOOL = Tool(
    name="social-search",
    description=(
        "Search YouTube, Threads, Bluesky, Hacker News, Google News and the web (plus Facebook, Instagram, "
        "TikTok, X and Reddit with a saved login) for posts about a topic, or list a page/channel/profile's "
        "recent posts. Use it to research what people are saying; add --cursor to report only what is new."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed social-search --query 'shopee voucher' --platform bluesky --platform news --since 7d",
        "shed social-search --query gcash --cursor ~/.local/state/shed/gcash.cursor --json",
        "shed social-search --url https://www.facebook.com/ShopeePH --platform facebook",
    ),
)
