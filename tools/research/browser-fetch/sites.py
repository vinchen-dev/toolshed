"""What browser-fetch knows about each site: where to sign in, how to tell a live session from a wall."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Site:
    name: str
    home: str
    login_url: str | None = None
    """Where `--login` opens a window; None for sites used anonymously only."""
    login_cookie: str | None = None
    """Cookie that exists only while signed in; None when a login is never needed."""
    headed_default: bool = False
    """Open a visible window by default when using the saved profile (sites that dislike headless)."""
    walls: tuple[str, ...] = field(default_factory=tuple)
    """Regexes over the landed URL or title that mean a login wall or a bot check."""

    def wall(self, url: str, title: str) -> bool:
        return any(re.search(p, url) or re.search(p, title, re.I) for p in self.walls)


SITES: dict[str, Site] = {
    s.name: s
    for s in (
        Site("web", "https://"),
        Site("lazada", "https://www.lazada.com.ph/", walls=(r"/captcha", r"punish", r"slide to verify")),
        Site("threads", "https://www.threads.com/", walls=(r"/login", r"login\?")),
        Site("duckduckgo", "https://html.duckduckgo.com/", walls=(r"anomaly", r"/bots")),
        Site("tiktok-trends", "https://ads.tiktok.com/", walls=(r"/login",)),
        Site(
            "shopee",
            "https://shopee.ph/",
            login_url="https://shopee.ph/buyer/login",
            login_cookie="SPC_ST",
            headed_default=True,
            walls=(r"/verify/traffic", r"/buyer/login"),
        ),
        Site(
            "facebook",
            "https://www.facebook.com/",
            login_url="https://www.facebook.com/login/",
            login_cookie="c_user",
            headed_default=True,
            walls=(r"/login", r"login\.php", r"/checkpoint"),
        ),
        Site(
            "instagram",
            "https://www.instagram.com/",
            login_url="https://www.instagram.com/accounts/login/",
            login_cookie="sessionid",
            headed_default=True,
            walls=(r"/accounts/login", r"/challenge"),
        ),
        Site(
            "tiktok",
            "https://www.tiktok.com/",
            login_url="https://www.tiktok.com/login",
            login_cookie="sessionid",
            headed_default=True,
            walls=(r"/login", r"verify"),
        ),
        Site(
            "x",
            "https://x.com/",
            login_url="https://x.com/i/flow/login",
            login_cookie="auth_token",
            headed_default=True,
            walls=(r"/i/flow/login", r"/i/jf/", r"/login"),
        ),
        Site(
            "reddit",
            "https://www.reddit.com/",
            login_url="https://www.reddit.com/login/",
            login_cookie="reddit_session",
            headed_default=True,
            walls=(r"js_challenge", r"/login", r"prove your humanity", r"blocked"),
        ),
    )
}
