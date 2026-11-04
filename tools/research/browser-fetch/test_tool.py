from __future__ import annotations

import importlib
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("browser-fetch")
browser = importlib.import_module(f"{module.__package__}.browser")
TOOL = load_tool("browser-fetch")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)


class FakeSession:
    def __init__(self, driver: FakeDriver, profile_dir: Path | None, headed: bool) -> None:
        self.driver, self.profile_dir, self.headed = driver, profile_dir, headed
        self.url, self.title = "about:blank", ""
        self.scrolled = 0
        self.is_closed = False

    def goto(self, url: str) -> None:
        self.driver.visited.append((url, self.profile_dir.name if self.profile_dir else None, self.headed))
        self.url = self.driver.redirects.get(url, url)
        self.title = self.driver.titles.get(self.url, "Some page")

    def cookies(self) -> list[dict[str, Any]]:
        return [{"name": name, "value": "x"} for name in self.driver.cookies]

    def scroll(self) -> None:
        self.scrolled += 1

    def evaluate(self, js: str) -> Any:
        if self.driver.evaluate_error:
            raise RuntimeError(self.driver.evaluate_error)
        if self.driver.evaluate_result is not None:
            return self.driver.evaluate_result
        return {
            "js": js[:20],
            "url": self.url,
            "title": self.title,
            "text": "visible text",
            "scrolled": self.scrolled,
        }

    def content(self) -> str:
        return f"<html><title>{self.title}</title></html>"

    def closed(self) -> bool:
        return self.is_closed

    def close(self) -> None:
        self.is_closed = True


class FakeDriver:
    def __init__(self) -> None:
        self.visited: list[tuple[str, str | None, bool]] = []
        self.redirects: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.cookies: set[str] = set()
        self.evaluate_error: str | None = None
        self.evaluate_result: Any = None
        self.sessions: list[FakeSession] = []

    def open(self, profile_dir: Path | None, *, headed: bool) -> FakeSession:
        session = FakeSession(self, profile_dir, headed)
        self.sessions.append(session)
        return session


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeDriver:
    driver = FakeDriver()
    monkeypatch.setattr(browser, "driver", driver)
    monkeypatch.setattr(browser, "sleep", lambda _: None)
    monkeypatch.setenv("SHED_BROWSER_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("SHED_STATE_DIR", str(tmp_path / "state"))
    return driver


def test_anonymous_extraction_uses_a_throwaway_headless_browser_and_scrolls(fake: FakeDriver) -> None:
    result = browser.extract(
        "lazada", "https://www.lazada.com.ph/catalog/?q=x", "() => 1", profile=False, headed=None, scrolls=2
    )
    assert (result["js"], result["url"], result["scrolled"]) == (
        "() => 1",
        "https://www.lazada.com.ph/catalog/?q=x",
        2,
    )
    assert fake.visited == [("https://www.lazada.com.ph/catalog/?q=x", None, False)]
    assert fake.sessions[0].is_closed


def test_a_profile_browse_without_a_saved_login_names_the_login_command() -> None:
    with pytest.raises(ToolError, match=r"shopee: no saved session; run: shed browser-fetch --login shopee"):
        browser.extract(
            "shopee", "https://shopee.ph/search?keyword=x", "() => 1", profile=True, headed=None, scrolls=0
        )


def test_a_login_wall_or_bot_check_fails_with_the_login_command(fake: FakeDriver) -> None:
    fake.redirects["https://shopee.ph/search?keyword=x"] = (
        "https://shopee.ph/verify/traffic/error?is_logged_in=false"
    )
    with pytest.raises(ToolError, match=r"shopee: blocked for logged-out visitors.*--login shopee"):
        browser.extract(
            "shopee", "https://shopee.ph/search?keyword=x", "() => 1", profile=False, headed=None, scrolls=0
        )


def test_login_creates_a_private_profile_and_records_the_time(fake: FakeDriver, tmp_path: Path) -> None:
    fake.cookies.add("SPC_ST")
    result = TOOL.run(TOOL.input(login="shopee"), CTX)
    assert result.site == "shopee" and result.signed_in is True
    profile = tmp_path / "profiles" / "shopee"
    assert stat.S_IMODE(profile.stat().st_mode) == 0o700
    assert json.loads((profile / "profile.json").read_text())["site"] == "shopee"
    assert fake.visited[0][2] is True  # headed window for the human


def test_an_expired_session_fails_with_the_login_command(fake: FakeDriver, tmp_path: Path) -> None:
    fake.cookies.add("c_user")
    TOOL.run(TOOL.input(login="facebook"), CTX)
    fake.cookies.clear()
    with pytest.raises(ToolError, match=r"facebook: session expired.*--login facebook"):
        browser.extract(
            "facebook",
            "https://www.facebook.com/search/posts/?q=x",
            "() => 1",
            profile=True,
            headed=None,
            scrolls=0,
        )


def test_status_lists_profiles_and_forget_removes_one(fake: FakeDriver, tmp_path: Path) -> None:
    fake.cookies.add("c_user")
    TOOL.run(TOOL.input(login="facebook"), CTX)
    fake.cookies.clear()
    status = TOOL.run(TOOL.input(status=True), CTX)
    assert [(p.site, p.signed_in) for p in status.profiles] == [("facebook", False)]
    gone = TOOL.run(TOOL.input(forget="facebook"), CTX)
    assert gone.removed is True and not (tmp_path / "profiles" / "facebook").exists()
    assert TOOL.run(TOOL.input(status=True), CTX).profiles == []


def test_an_extractor_error_saves_the_page_and_names_it(fake: FakeDriver, tmp_path: Path) -> None:
    fake.evaluate_error = "x is not defined"
    with pytest.raises(
        ToolError, match=r"lazada: extractor failed \(x is not defined\); page saved to .*lazada-.*\.html"
    ):
        browser.extract(
            "lazada",
            "https://www.lazada.com.ph/catalog/?q=x",
            "() => x",
            profile=False,
            headed=None,
            scrolls=0,
        )
    saved = list((tmp_path / "state" / "browser-fetch").glob("lazada-*.html"))
    assert len(saved) == 1 and "<title>Some page</title>" in saved[0].read_text()


def test_fetching_a_url_returns_its_visible_text(fake: FakeDriver) -> None:
    result = TOOL.run(TOOL.input(url="https://www.lazada.com.ph/", text=True), CTX)
    assert (result.url, result.title, result.body) == (
        "https://www.lazada.com.ph/",
        "Some page",
        "visible text",
    )


def test_site_for_url_maps_known_hosts_and_falls_back_to_a_plain_browser() -> None:
    assert browser.site_for_url("https://www.lazada.com.ph/catalog/?q=x") == "lazada"
    assert browser.site_for_url("https://shopee.ph/search?keyword=x") == "shopee"
    assert browser.site_for_url("https://html.duckduckgo.com/html/?q=x") == "duckduckgo"
    assert browser.site_for_url("https://example.com/blog") == "web"


def test_an_extractor_that_finds_nothing_saves_the_page_and_fails(fake: FakeDriver, tmp_path: Path) -> None:
    fake.evaluate_result = []
    with pytest.raises(
        ToolError, match=r"lazada: extractor found nothing at .*; page saved to .*lazada-.*\.html"
    ):
        browser.extract(
            "lazada",
            "https://www.lazada.com.ph/catalog/?q=x",
            "() => []",
            profile=False,
            headed=None,
            scrolls=0,
        )
    assert len(list((tmp_path / "state" / "browser-fetch").glob("lazada-*.html"))) == 1
