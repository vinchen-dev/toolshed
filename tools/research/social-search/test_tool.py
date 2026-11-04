from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.lib.manila import MANILA
from shed.registry import load_tool, load_tool_module

module = load_tool_module("social-search")
TOOL = load_tool("social-search")
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=MANILA)


def fixture(name: str) -> Any:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


class FakeTransport:
    """Serves recorded responses; every live route the tool knows is a method here."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.news_extra: str = ""
        """An extra <item> appended to the news feed, to simulate a new article on a later run."""
        self.sessions: set[str] = set()
        """Sites with a saved login; a profile browse anywhere else fails like browser-fetch does."""

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append(("json", url))
        if "searchPosts" in url:
            return fixture("bluesky.json")
        if "hn.algolia.com" in url:
            return fixture("hackernews.json")
        if "getAuthorFeed" in url:
            return fixture("bluesky-author.json")
        raise AssertionError(f"unexpected JSON fetch {url}")

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        self.calls.append(("text", url))
        if "news.google.com" in url:
            return fixture("news.xml").replace("</channel>", self.news_extra + "</channel>")
        raise AssertionError(f"unexpected text fetch {url}")

    def ytdlp(self, target: str, *, flat: bool = True) -> list[dict[str, Any]]:
        self.calls.append(("ytdlp", target))
        if target.startswith("ytsearch"):
            return fixture("youtube-search.json")["entries"]
        raise AssertionError(f"unexpected yt-dlp target {target}")

    def browse(
        self, site: str, url: str, extractor: str, *, profile: bool, headed: bool | None, scrolls: int
    ) -> Any:
        self.calls.append(("browse", f"{site} {url}" + (" profile" if profile else "")))
        if site == "duckduckgo":
            return fixture("web.json")
        if site == "threads":
            return fixture("threads.json")
        if site == "tiktok-trends":
            return fixture("tiktok-trends.json")
        if site == "facebook" and not profile:
            return fixture("facebook-page.json")
        if profile and site not in self.sessions:
            raise ToolError(f"{site}: no saved session; run: shed browser-fetch --login {site}")
        if profile:
            return fixture(f"{site}-search.json")
        raise AssertionError(f"unexpected browse {site} {url}")


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    transport = FakeTransport()
    monkeypatch.setattr(module, "transport", transport)
    monkeypatch.setattr(module, "now_manila", lambda: NOW)
    return transport


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def test_bluesky_posts_normalize_from_the_public_search(tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(query="shopee", platform=["bluesky"]), ctx(tmp_path))
    block = result.platforms[0]
    assert (block.platform, block.ok, block.note, len(block.posts)) == ("bluesky", True, None, 2)
    post = block.posts[0]
    assert post.author == "achadinhoscupons.bsky.social"
    assert post.url == "https://bsky.app/profile/achadinhoscupons.bsky.social/post/3mzhgyo677e2u"
    assert post.created_at == datetime(2026, 11, 4, 11, 47, 31, tzinfo=MANILA)
    assert post.text.startswith("Saida de Praia Plus Size")
    assert (post.likes, post.comments, post.shares) == (0, 0, 0)


def test_hackernews_stories_link_to_the_discussion_and_keep_the_article_link(tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(query="shopee", platform=["hackernews"], since="300d"), ctx(tmp_path))
    post = result.platforms[0].posts[0]
    assert post.url == "https://news.ycombinator.com/item?id=47760530"
    assert post.author == "kenttuzuu" and post.likes == 4 and post.comments == 1
    assert post.created_at == datetime(2026, 4, 14, 10, 25, 27, tzinfo=MANILA)
    assert post.text == (
        "AI Agent Stores – Making Shopee Products Findable by ChatGPT and Perplexity"  # noqa: RUF001
        "\nhttps://www.bbiz.shop/blog"
    )


def test_news_items_carry_the_publisher_as_author_and_manila_time(tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(query="shopee", platform=["news"]), ctx(tmp_path))
    posts = result.platforms[0].posts
    assert [p.author for p in posts] == ["InsiderPH", "The Manila Times"]
    assert posts[0].text == "Shopee affiliate program drives sales growth for Filipino MSMEs - InsiderPH"
    assert posts[0].created_at == datetime(2026, 11, 4, 7, 11, 23, tzinfo=MANILA)
    assert posts[0].url.startswith("https://news.google.com/rss/articles/")


def test_youtube_search_uses_ytdlp_and_keeps_undated_videos(tmp_path: Path, fake: FakeTransport) -> None:
    result = TOOL.run(TOOL.input(query="shopee voucher", platform=["youtube"], limit=5), ctx(tmp_path))
    assert ("ytdlp", "ytsearch5:shopee voucher") in fake.calls
    post = result.platforms[0].posts[0]
    assert post.url == "https://www.youtube.com/watch?v=ABW7iT-K0NU"
    assert (post.author, post.views, post.created_at) == ("Archie Noche", 25193, None)
    assert (
        post.text
        == "Paano Gamitin Ang Voucher Sa Shopee 2026 - Paano Maka Discount Beginners Guide (Tutorial)"
    )


def test_since_window_drops_older_dated_posts(tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(query="shopee", platform=["hackernews", "news"], since="2026-11-04 05:00"), ctx(tmp_path)
    )
    by_name = {block.platform: block for block in result.platforms}
    assert by_name["hackernews"].posts == [] and by_name["hackernews"].ok
    assert [p.author for p in by_name["news"].posts] == ["InsiderPH"]
    assert result.since == datetime(2026, 11, 4, 5, 0, tzinfo=MANILA) and result.total == 1


def test_cursor_seeds_on_the_first_run_and_reports_only_unseen_posts_after(
    tmp_path: Path, fake: FakeTransport
) -> None:
    cursor = tmp_path / "state" / "shopee.cursor"
    first = TOOL.run(TOOL.input(query="shopee", platform=["news"], cursor=str(cursor)), ctx(tmp_path))
    assert first.total == 2 and first.since is not None
    state = json.loads(cursor.read_text())
    assert len(state["news|shopee"]["seen"]) == 2

    second = TOOL.run(TOOL.input(query="shopee", platform=["news"], cursor=str(cursor)), ctx(tmp_path))
    assert second.total == 0 and second.since is None and second.platforms[0].ok

    fake.news_extra = (
        "<item><title>New voucher rules - Rappler</title><link>https://news.google.com/rss/articles/NEW</link>"
        '<pubDate>Wed, 04 Nov 2026 03:00:00 GMT</pubDate><source url="https://www.rappler.com">Rappler'
        "</source></item>"
    )
    third = TOOL.run(TOOL.input(query="shopee", platform=["news"], cursor=str(cursor)), ctx(tmp_path))
    assert [p.text for p in third.platforms[0].posts] == ["New voucher rules - Rappler"]
    assert len(json.loads(cursor.read_text())["news|shopee"]["seen"]) == 3


def test_logged_in_platforms_are_skipped_with_a_note_unless_opted_in(tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(query="shopee", platform=["facebook", "bluesky"]), ctx(tmp_path))
    facebook, bluesky = result.platforms
    assert facebook.ok is False and facebook.posts == []
    assert (
        facebook.note
        == "skipped: needs --use-login and a saved session (shed browser-fetch --login facebook)"
    )
    assert bluesky.ok and len(bluesky.posts) == 2 and result.total == 2


def test_strict_mode_fails_the_run_on_a_skipped_platform(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="facebook: skipped"):
        TOOL.run(TOOL.input(query="shopee", platform=["facebook"], strict=True), ctx(tmp_path))


def test_web_search_runs_in_the_browser_with_a_site_filter_and_decodes_redirect_links(
    tmp_path: Path, fake: FakeTransport
) -> None:
    result = TOOL.run(
        TOOL.input(query="shopee voucher", platform=["web"], site="facebook.com"), ctx(tmp_path)
    )
    assert any(
        call[1].startswith("duckduckgo ") and "site%3Afacebook.com+shopee+voucher" in call[1]
        for call in fake.calls
    )
    posts = result.platforms[0].posts
    assert posts[0].url == "https://www.facebook.com/groups/574215833436962/"
    assert posts[0].text == (
        "SHOPEE SALE & DISCOUNT VOUCHERS - Facebook\n"
        "SHOPEE SALE & DISCOUNT VOUCHERS. 49,213 likes. Daily vouchers and flash deals."
    )
    assert posts[1].url == "https://www.facebook.com/ShopeePH/" and posts[1].created_at is None


def test_threads_search_posts_get_absolute_links_authors_and_times(
    tmp_path: Path, fake: FakeTransport
) -> None:
    result = TOOL.run(TOOL.input(query="shopee voucher", platform=["threads"]), ctx(tmp_path))
    assert ("browse", "threads https://www.threads.com/search?q=shopee+voucher&serp_type=default") in (
        fake.calls
    )
    post = result.platforms[0].posts[0]
    assert post.url == "https://www.threads.com/@ahliarizuan_/post/DfTXhfREkQ2"
    assert (post.author, post.author_url) == ("ahliarizuan_", "https://www.threads.com/@ahliarizuan_")
    assert post.created_at == datetime(2026, 11, 4, 6, 15, tzinfo=MANILA)
    assert (post.likes, post.comments, post.shares) == (12, 3, 1)


def test_tiktok_trends_list_philippine_hashtags_with_counts(tmp_path: Path, fake: FakeTransport) -> None:
    result = TOOL.run(TOOL.input(query="ignored", platform=["tiktok-trends"]), ctx(tmp_path))
    assert any(
        "tiktok-trends https://ads.tiktok.com/" in call[1] and "region=PH" in call[1] for call in fake.calls
    )
    top = result.platforms[0].posts[0]
    assert top.url == "https://www.tiktok.com/tag/aouboom" and top.tags == ["aouboom"]
    assert top.text == "#1 #aouboom · News & Entertainment · 18.4K posts" and top.views == 16_600_000


def test_watching_a_public_facebook_page_needs_no_login_and_keeps_the_relative_time(
    tmp_path: Path, fake: FakeTransport
) -> None:
    result = TOOL.run(TOOL.input(url="https://www.facebook.com/ShopeePH/"), ctx(tmp_path))
    assert ("browse", "facebook https://www.facebook.com/ShopeePH/") in fake.calls
    assert [b.platform for b in result.platforms] == ["facebook"]
    assert result.url == "https://www.facebook.com/ShopeePH/"
    post = result.platforms[0].posts[0]
    assert post.url == "https://www.facebook.com/ShopeePH/posts/pfbid02AbCdEf"
    assert (post.author, post.when, post.created_at) == ("Shopee", "16h", None)
    assert post.text.startswith("Lakas Maka-VIP")


def test_watching_a_bluesky_account_reads_its_public_feed(tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(url="https://bsky.app/profile/bsky.app", since="365d"), ctx(tmp_path))
    post = result.platforms[0].posts[0]  # a repost by the watched account keeps its original author
    assert result.platforms[0].platform == "bluesky" and post.author == "safety.bsky.app"
    assert post.url == "https://bsky.app/profile/safety.bsky.app/post/3myaysp7gmt2o"
    assert post.created_at == datetime(2026, 10, 20, 4, 52, 31, tzinfo=MANILA) and post.likes == 457


def test_query_and_url_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        TOOL.input(query="a", url="https://x.com/a")
    with pytest.raises(ValueError, match="exactly one"):
        TOOL.input()


def test_a_logged_in_platform_without_a_session_fails_only_that_platform(tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(query="shopee", platform=["facebook", "bluesky"], use_login=True), ctx(tmp_path)
    )
    facebook, bluesky = result.platforms
    assert facebook.ok is False and facebook.posts == []
    assert facebook.note == "facebook: no saved session; run: shed browser-fetch --login facebook"
    assert bluesky.ok and result.total == 2
    with pytest.raises(ToolError, match="shed browser-fetch --login facebook"):
        TOOL.run(
            TOOL.input(query="shopee", platform=["facebook"], use_login=True, strict=True), ctx(tmp_path)
        )


def test_logged_in_searches_normalize_counts_times_and_tracking_free_links(
    tmp_path: Path, fake: FakeTransport
) -> None:
    fake.sessions = {"facebook", "x", "reddit"}
    result = TOOL.run(
        TOOL.input(query="shopee voucher", platform=["facebook", "x", "reddit"], use_login=True),
        ctx(tmp_path),
    )
    facebook, x, reddit = (block.posts[0] for block in result.platforms)
    assert facebook.url == "https://www.facebook.com/groups/574215833436962/posts/1234/"
    assert (facebook.likes, facebook.comments, facebook.shares, facebook.when) == (128, 45, 12, "3h")
    assert x.url == "https://x.com/ShopeePH/status/1850000000000000000" and x.likes == 1200
    assert x.created_at == datetime(2026, 11, 4, 9, 30, tzinfo=MANILA)
    assert reddit.url == "https://www.reddit.com/r/phinvest/comments/1abcd2/shopee_voucher_stacking_guide/"
    assert (reddit.author, reddit.likes, reddit.comments) == ("juan_dc", 57, 23)
    assert reddit.text == "Shopee voucher stacking guide" and reddit.tags == ["r/phinvest"]
    assert any(
        call[1].startswith("x https://x.com/search?") and call[1].endswith(" profile") for call in fake.calls
    )


def test_render_lists_each_platform_newest_first_with_time_author_engagement_text_and_link(
    tmp_path: Path, fake: FakeTransport
) -> None:
    fake.news_extra = (
        "<item><title>Late breaking voucher story</title><link>https://news.google.com/rss/articles/LATE</link>"
        '<pubDate>Wed, 04 Nov 2026 03:30:00 GMT</pubDate><source url="https://x.ph">X News</source></item>'
    )
    text = module.render(TOOL.run(TOOL.input(query="shopee", platform=["bluesky", "news"]), ctx(tmp_path)))
    lines = text.splitlines()
    assert lines[0] == "== bluesky: 2 posts (likes/comments/shares)"
    first = lines[1]
    assert first.split()[:2] == ["2026-11-04", "11:47:31"] and "achadinhoscupons.bsky.social" in first
    assert " 0/0/0 " in first and first.rstrip().endswith("/post/3mzhgyo677e2u")
    news = lines[lines.index("== news: 3 posts (likes/comments/shares)") + 1 :]
    assert [line.split()[1] for line in news[:3]] == ["11:30:00", "07:11:23", "03:48:48"]  # newest first
    assert "Late breaking voucher story" in news[0] and "X News" in news[0]


def test_render_shows_trends_as_rank_hashtag_posts_and_views(tmp_path: Path) -> None:
    text = module.render(TOOL.run(TOOL.input(query="x", platform=["tiktok-trends"]), ctx(tmp_path)))
    assert "== tiktok-trends: 2 hashtags" in text
    assert "#1 #aouboom · News & Entertainment · 18.4K posts" in text and "16.6M views" in text


def test_posts_are_deduplicated_by_link_within_a_platform(tmp_path: Path, fake: FakeTransport) -> None:
    first_link = fixture("news.xml").split("<item>")[1].split("<link>")[1].split("</link>")[0]
    fake.news_extra = (
        f"<item><title>Same story again</title><link>{first_link}</link>"
        "<pubDate>Wed, 04 Nov 2026 03:00:00 GMT</pubDate></item>"
    )
    result = TOOL.run(TOOL.input(query="shopee", platform=["news"]), ctx(tmp_path))
    assert result.total == 2


def test_cursor_keeps_only_the_newest_links_when_full(
    tmp_path: Path, fake: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module.cursor_file, "MAX_SEEN", 3)
    cursor = tmp_path / "news.cursor"
    TOOL.run(TOOL.input(query="shopee", platform=["news"], cursor=str(cursor)), ctx(tmp_path))
    fake.news_extra = "".join(
        f"<item><title>Extra {n}</title><link>https://news.google.com/rss/articles/EXTRA{n}</link>"
        "<pubDate>Wed, 04 Nov 2026 03:00:00 GMT</pubDate></item>"
        for n in (1, 2)
    )
    TOOL.run(TOOL.input(query="shopee", platform=["news"], cursor=str(cursor)), ctx(tmp_path))
    seen = json.loads(cursor.read_text())["news|shopee"]["seen"]
    assert len(seen) == 3 and seen[-2:] == [
        "https://news.google.com/rss/articles/EXTRA1",
        "https://news.google.com/rss/articles/EXTRA2",
    ]


def test_instagram_uses_the_hashtag_page_only_for_hash_queries(tmp_path: Path, fake: FakeTransport) -> None:
    fake.sessions = {"instagram"}
    TOOL.run(TOOL.input(query="shopee", platform=["instagram"], use_login=True), ctx(tmp_path))
    TOOL.run(TOOL.input(query="#shopee", platform=["instagram"], use_login=True), ctx(tmp_path))
    urls = [call[1] for call in fake.calls if call[1].startswith("instagram ")]
    assert urls == [
        "instagram https://www.instagram.com/explore/search/keyword/?q=shopee profile",
        "instagram https://www.instagram.com/explore/tags/shopee/ profile",
    ]
