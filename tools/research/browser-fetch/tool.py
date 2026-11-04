"""Manage the per-site browser sessions the research tools use, and fetch a page through them for debugging.

`--login <site>` opens a visible window once; you sign in by hand and close it. Later runs of product-search
and social-search reuse that session until the site ends it. Profiles live under ~/.config/shed/browser
(owner-only).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from shed import Tool, ToolContext, ToolError

from . import browser
from .browser import extract, parse_count, read_extractor
from .sites import SITES

Site = Literal[
    "shopee",
    "lazada",
    "facebook",
    "instagram",
    "tiktok",
    "x",
    "reddit",
    "threads",
    "duckduckgo",
    "tiktok-trends",
]
LOGIN_SITES = tuple(name for name, site in SITES.items() if site.login_url)
__all__ = [
    "SITES",
    "TOOL",
    "extract",
    "parse_count",
    "read_extractor",
]  # shared with the other research tools


class Input(BaseModel):
    login: Site | None = Field(
        None,
        description="Open a window to sign in to this site once (shopee facebook instagram tiktok x reddit)",
    )
    status: bool = Field(False, description="List saved sessions and whether each still looks signed in")
    forget: Site | None = Field(None, description="Delete this site's saved session")
    url: str | None = Field(None, description="Fetch this page (debugging): visible text, or run --js on it")
    site: Site | None = Field(
        None, description="With --url: use this site's saved session instead of a throwaway browser"
    )
    js: str | None = Field(
        None, description="With --url: a JavaScript function '() => ...' whose result is returned"
    )
    text: bool = Field(False, description="With --url: return the page's visible text instead of its HTML")
    headed: bool | None = Field(None, description="Force a visible (true) or headless (false) browser")
    scrolls: int = Field(0, ge=0, le=10, description="With --url: scroll down this many times before reading")

    @model_validator(mode="after")
    def _one_action(self) -> Input:
        actions = [bool(self.login), self.status, bool(self.forget), bool(self.url)]
        if sum(actions) != 1:
            raise ValueError("give exactly one of --login, --status, --forget or --url")
        if self.login and self.login not in LOGIN_SITES:
            raise ValueError(f"{self.login} needs no login; sites with one: {' '.join(LOGIN_SITES)}")
        return self


class StatusReport(BaseModel):
    folder: str
    profiles: list[browser.ProfileStatus]


class Forgotten(BaseModel):
    site: str
    removed: bool


class Page(BaseModel):
    url: str
    title: str
    fetched_at: datetime
    body: str | None = None
    """Visible text (--text) or HTML, truncated to 20k characters."""
    result: object = None
    """What --js returned."""


Output = browser.ProfileStatus | StatusReport | Forgotten | Page
PAGE_JS = (
    "() => ({url: location.href, title: document.title, text: document.body.innerText, "
    "html: document.documentElement.outerHTML})"
)


def run(params: Input, ctx: ToolContext) -> Output:
    if params.login:
        ctx.log(f"opening {params.login}: sign in, then close the window")
        return browser.login(params.login)
    if params.status:
        return StatusReport(folder=str(browser.profiles_dir()), profiles=browser.status())
    if params.forget:
        return Forgotten(site=params.forget, removed=browser.forget(params.forget))
    assert params.url
    site = params.site or browser.site_for_url(params.url)
    js = params.js or PAGE_JS
    raw = browser.extract(
        site, params.url, js, profile=bool(params.site), headed=params.headed, scrolls=params.scrolls
    )
    if params.js:
        return Page(url=params.url, title="", fetched_at=datetime.now(), result=raw)
    if not isinstance(raw, dict):
        raise ToolError("page read failed")
    body = str(raw.get("text" if params.text else "html") or "")
    return Page(
        url=str(raw.get("url")),
        title=str(raw.get("title") or ""),
        fetched_at=datetime.now(),
        body=body[:20_000],
    )


def render(result: Output) -> str:
    if isinstance(result, browser.ProfileStatus):
        state = "signed in" if result.signed_in else "not signed in"
        return f"{result.site}: {state} (profile saved {result.last_login:%Y-%m-%d %H:%M})"
    if isinstance(result, StatusReport):
        if not result.profiles:
            return f"no saved sessions under {result.folder}"
        rows = []
        for p in result.profiles:
            state = {True: "signed in", False: "signed out", None: "no login needed"}[p.signed_in]
            rows.append(
                f"{p.site:<10} {state:<16} last login {p.last_login:%Y-%m-%d %H:%M}"
                if p.last_login
                else f"{p.site:<10} {state}"
            )
        return "\n".join(rows)
    if isinstance(result, Forgotten):
        return f"{result.site}: {'removed' if result.removed else 'no saved session'}"
    if result.result is not None:
        return json.dumps(result.result, indent=2, ensure_ascii=False)
    return f"{result.title}\n{result.url}\n\n{result.body or ''}"


TOOL = Tool(
    name="browser-fetch",
    description=(
        "Browser sessions for the research tools: sign in to Shopee, Facebook, Instagram, TikTok, X or "
        "Reddit once (--login), check or remove saved sessions (--status, --forget), or fetch a page's text "
        "through a browser (--url) to debug an extractor. product-search and social-search reuse the "
        "sessions with --use-login."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed browser-fetch --login shopee",
        "shed browser-fetch --status",
        "shed browser-fetch --url https://www.lazada.com.ph/catalog/?q=earbuds --text",
    ),
)
