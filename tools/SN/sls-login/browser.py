"""The browser side of sls-login: a Chromium that carries the single sign-on's session between runs.

The Playwright calls sit in `Browser`; tests replace the tool's `browser` object with a fake."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from shed import ToolError

SAML_POST = re.compile(r"/saml-role/sso(\?|$)", re.IGNORECASE)
DONE_PAGE = "<!doctype html><title>Signed in</title><p>Signed in. This window closes by itself.</p>"


@dataclass(frozen=True, slots=True)
class Capture:
    saml_response: str
    posted_from: str
    """Address of the page that posted it: the app's own sign-on page, which a renewal opens directly."""
    user_agent: str
    """What the browser called itself, so a windowless renewal can present the same."""


def _first_line(exc: Exception) -> str:
    """Playwright appends a call log to its messages; the first line says what happened."""
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


class Browser:
    """A Chromium that starts from the saved sign-in state and saves it again when it is done."""

    def capture(
        self, url: str, state: Path, timeout: float, *, headed: bool, user_agent: str | None = None
    ) -> Capture | None:
        """Open `url` and return the SAML response the sign-in posts to Alibaba Cloud; None if none comes.

        The single sign-on keeps its session in a cookie a browser drops when it closes, so the cookies are
        loaded from `state` first and written back after, which is what lets a later run stay signed in."""
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        posted: list[Capture] = []

        def intercept(route: Any) -> None:
            # Answered here instead of passed on, so the response is spent on STS, not on a console session.
            request = route.request
            response = parse_qs(request.post_data or "").get("SAMLResponse")
            if not response:
                route.continue_()
                return
            posted.append(Capture(response[0], request.frame.url, request.headers.get("user-agent", "")))
            route.fulfill(status=200, content_type="text/html", body=DONE_PAGE)

        with sync_playwright() as pw:
            try:
                # The full Chromium in both modes, so a windowless renewal behaves like the window did.
                chromium = pw.chromium.launch(headless=not headed, channel="chromium")
            except PlaywrightError as exc:
                raise ToolError(f"Could not open the sign-in browser: {_first_line(exc)}") from exc
            try:
                context = chromium.new_context(
                    storage_state=str(state) if state.is_file() else None,
                    user_agent=user_agent or None,
                    viewport={"width": 1280, "height": 900},
                )
                context.route(SAML_POST, intercept)
                context.new_page().goto(url, wait_until="domcontentloaded", timeout=60_000)
                deadline = time.monotonic() + timeout
                while not posted and time.monotonic() < deadline:
                    pages = [p for p in context.pages if not p.is_closed()]
                    if not pages:
                        break
                    try:
                        pages[0].wait_for_timeout(500)  # also lets Playwright deliver the intercepted post
                    except PlaywrightError:
                        time.sleep(0.1)  # that tab closed mid-wait; the next pass picks another or stops
                if chromium.is_connected():  # not when the person quit the browser: nothing left to save
                    context.storage_state(path=str(state))
                    os.chmod(state, 0o600)
            except PlaywrightError as exc:
                raise ToolError(f"The sign-in browser failed: {_first_line(exc)}") from exc
            finally:
                chromium.close()
        return posted[0] if posted else None
