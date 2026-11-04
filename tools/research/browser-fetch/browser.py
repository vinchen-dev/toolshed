"""Persistent per-site Chromium profiles and the one function the other research tools call: extract().

The Playwright calls sit behind `driver`; tests replace it with a fake."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel

from shed import ToolError
from shed.lib.manila import MANILA

from .sites import SITES, Site

MIN_DELAY = 1.5
"""Seconds between page loads on the same site, so a run never looks like a flood."""
LOGIN_TIMEOUT = 600
_last_load: dict[str, float] = {}


def profiles_dir() -> Path:
    return Path(os.environ.get("SHED_BROWSER_DIR") or Path.home() / ".config" / "shed" / "browser")


def state_dir() -> Path:
    return (
        Path(os.environ.get("SHED_STATE_DIR") or Path.home() / ".local" / "state" / "shed") / "browser-fetch"
    )


def sleep(seconds: float) -> None:
    time.sleep(seconds)


class Session(Protocol):
    url: str
    title: str

    def goto(self, url: str) -> None: ...
    def cookies(self) -> list[dict[str, Any]]: ...
    def scroll(self) -> None: ...
    def evaluate(self, js: str) -> Any: ...
    def content(self) -> str: ...
    def closed(self) -> bool: ...
    def close(self) -> None: ...


class PlaywrightSession:
    def __init__(self, playwright: Any, context: Any, owns_browser: Any | None) -> None:
        self._pw, self._context, self._browser = playwright, context, owns_browser
        self._page = context.pages[0] if context.pages else context.new_page()
        self.url, self.title = "about:blank", ""

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        with contextlib.suppress(
            Exception
        ):  # app pages draw once the network settles; a busy one is read as is
            self._page.wait_for_load_state("networkidle", timeout=15_000)
        self._page.wait_for_timeout(1_500)
        self.url, self.title = self._page.url, self._page.title()

    def cookies(self) -> list[dict[str, Any]]:
        return list(self._context.cookies())

    def scroll(self) -> None:
        self._page.mouse.wheel(0, 2_000)
        self._page.wait_for_timeout(1_500)

    def evaluate(self, js: str) -> Any:
        return self._page.evaluate(js)

    def content(self) -> str:
        return str(self._page.content())

    def closed(self) -> bool:
        return bool(self._page.is_closed())

    def close(self) -> None:
        try:
            self._context.close()
            if self._browser is not None:
                self._browser.close()
        finally:
            self._pw.stop()


class PlaywrightDriver:
    """Opens a persistent context on a profile folder, or a throwaway context when there is none."""

    def open(self, profile_dir: Path | None, *, headed: bool) -> Session:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        if profile_dir is not None:
            context = pw.chromium.launch_persistent_context(
                str(profile_dir), headless=not headed, viewport={"width": 1280, "height": 900}
            )
            return PlaywrightSession(pw, context, None)
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-PH")
        return PlaywrightSession(pw, context, browser)


driver: Any = PlaywrightDriver()


class ProfileStatus(BaseModel):
    site: str
    last_login: datetime | None
    signed_in: bool | None
    """None when the site never needs a login."""


def site_for(name: str) -> Site:
    try:
        return SITES[name]
    except KeyError as exc:
        raise ToolError(f"unknown site '{name}'; known: {', '.join(sorted(SITES))}") from exc


def site_for_url(url: str) -> str:
    """The site whose home host matches `url`; "web" (a plain anonymous browser, no wall rules) otherwise."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for site in SITES.values():
        site_host = urlparse(site.home).netloc.lower().removeprefix("www.").removeprefix("html.")
        if host and site_host and (host == site_host or host.endswith("." + site_host)):
            return site.name
    return "web"


def login_hint(site: Site) -> str:
    return (
        f"run: shed browser-fetch --login {site.name}" if site.login_url else "this site has no login route"
    )


def _polite(site: str) -> None:
    waited = time.monotonic() - _last_load.get(site, 0.0)
    if waited < MIN_DELAY:
        sleep(MIN_DELAY - waited)
    _last_load[site] = time.monotonic()


def _has_cookie(session: Session, name: str | None) -> bool:
    return name is None or any(c.get("name") == name and c.get("value") for c in session.cookies())


def _check_wall(site: Site, session: Session, *, profile: bool) -> None:
    if site.wall(session.url, session.title):
        why = "session expired or rejected" if profile else "blocked for logged-out visitors"
        raise ToolError(f"{site.name}: {why} at {session.url}; {login_hint(site)}")
    if profile and not _has_cookie(session, site.login_cookie):
        raise ToolError(f"{site.name}: session expired (no {site.login_cookie} cookie); {login_hint(site)}")


def _save_page(site: Site, session: Session) -> Path:
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{site.name}-{datetime.now(MANILA).strftime('%Y%m%d-%H%M%S')}.html"
    path.write_text(session.content(), encoding="utf-8")
    return path


def extract(site_name: str, url: str, js: str, *, profile: bool, headed: bool | None, scrolls: int) -> Any:
    """Open `url` on `site` (with its saved login when `profile`) and return what the JS function produced."""
    site = site_for(site_name)
    folder = profiles_dir() / site.name if profile else None
    if folder is not None and not (folder / "profile.json").exists():
        raise ToolError(f"{site.name}: no saved session; {login_hint(site)}")
    visible = headed if headed is not None else (site.headed_default if profile else False)
    _polite(site.name)
    session = driver.open(folder, headed=visible)
    try:
        session.goto(url)
        _check_wall(site, session, profile=profile)
        for _ in range(scrolls):
            session.scroll()
        try:
            result = session.evaluate(js)
        except Exception as exc:
            saved = _save_page(site, session)
            raise ToolError(f"{site.name}: extractor failed ({exc}); page saved to {saved}") from exc
        if isinstance(result, list) and not result:  # an empty page usually means the markup moved
            saved = _save_page(site, session)
            raise ToolError(f"{site.name}: extractor found nothing at {url}; page saved to {saved}")
        return result
    finally:
        session.close()


def login(site_name: str, *, timeout: float = LOGIN_TIMEOUT) -> ProfileStatus:
    """Open a visible window at the sign-in page and wait for the human; the session stays in the profile."""
    site = site_for(site_name)
    if not site.login_url or not site.login_cookie:
        raise ToolError(f"{site.name} needs no login")
    folder = profiles_dir() / site.name
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    session = driver.open(folder, headed=True)
    signed_in = False
    try:
        session.goto(site.login_url)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not session.closed():
            if _has_cookie(session, site.login_cookie):
                signed_in = True
                break
            sleep(2)
    finally:
        session.close()
    stamp = datetime.now(MANILA).replace(microsecond=0)
    (folder / "profile.json").write_text(
        json.dumps({"site": site.name, "last_login": stamp.isoformat(), "signed_in": signed_in}),
        encoding="utf-8",
    )
    return ProfileStatus(site=site.name, last_login=stamp, signed_in=signed_in)


def status() -> list[ProfileStatus]:
    """Every saved profile, with a cheap headless probe of its home page to see if the session still holds."""
    out: list[ProfileStatus] = []
    root = profiles_dir()
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if (p / "profile.json").is_file()):
        site = SITES.get(folder.name)
        meta = json.loads((folder / "profile.json").read_text(encoding="utf-8"))
        last = datetime.fromisoformat(meta["last_login"]) if meta.get("last_login") else None
        signed_in: bool | None = None
        if site is not None and site.login_cookie:
            _polite(site.name)
            session = driver.open(folder, headed=False)
            try:
                session.goto(site.home)
                signed_in = _has_cookie(session, site.login_cookie) and not site.wall(
                    session.url, session.title
                )
            finally:
                session.close()
        out.append(ProfileStatus(site=folder.name, last_login=last, signed_in=signed_in))
    return out


_COUNT = re.compile(r"^\s*([\d][\d.,]*)\s*([KMB]?)(?![A-Za-z])", re.I)


def parse_count(value: Any) -> int | None:
    """'1.2K', '34,000 sold' or a number as an int; anything else is None. Shared by the research tools."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    m = _COUNT.match(str(value).strip())
    if not m:
        return None
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2).upper()]
    return int(float(m.group(1).replace(",", "")) * scale)


def read_extractor(folder: Path, name: str) -> str:
    """The in-page JavaScript `<folder>/<name>.js`: a function returning records a tool then normalizes."""
    return (folder / f"{name}.js").read_text(encoding="utf-8")


def forget(site_name: str) -> bool:
    folder = profiles_dir() / site_name
    if not re.fullmatch(r"[a-z0-9-]+", site_name) or not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True
