from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.lib.manila import MANILA
from shed.registry import load_tool, load_tool_module

module = load_tool_module("product-search")
TOOL = load_tool("product-search")
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=MANILA)
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.sessions: set[str] = set()
        self.pages: dict[str, list[dict[str, Any]]] = {}
        """Records per URL when a test wants to control pagination; otherwise the site's fixture."""

    def browse(
        self, site: str, url: str, extractor: str, *, profile: bool, headed: bool | None, scrolls: int
    ) -> Any:
        self.calls.append((site, url, profile))
        if profile and site not in self.sessions:
            raise ToolError(f"{site}: no saved session; run: shed browser-fetch --login {site}")
        if url in self.pages:
            return self.pages[url]
        return fixture(f"{site}.json")


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    transport = FakeTransport()
    monkeypatch.setattr(module, "transport", transport)
    monkeypatch.setattr(module, "now_manila", lambda: NOW)
    return transport


def test_lazada_listings_normalize_prices_counts_and_links_and_drop_duplicates(fake: FakeTransport) -> None:
    result = TOOL.run(TOOL.input(query="earbuds", source=["lazada"]), CTX)
    block = result.sources[0]
    assert (block.source, block.ok, block.note) == ("lazada", True, None)
    assert fake.calls == [("lazada", "https://www.lazada.com.ph/catalog/?q=earbuds", False)]
    assert [item.price for item in block.items] == [
        109.94,
        1299.0,
    ]  # duplicate link and priceless card dropped
    first = block.items[0]
    assert first.url == "https://www.lazada.com.ph/products/m10-tws-i123456.html"
    assert (first.shop, first.rating, first.sold, first.location) == (None, 4.8, 1200, "Metro Manila")
    assert first.currency == "PHP" and first.image == "https://img.lazcdn.com/m10.jpg"
    assert result.total == 2 and result.fetched_at == NOW


def test_shopee_is_skipped_with_a_note_unless_opted_in_and_strict_fails(fake: FakeTransport) -> None:
    result = TOOL.run(TOOL.input(query="earbuds"), CTX)
    lazada, shopee = result.sources
    assert lazada.ok and shopee.ok is False and shopee.items == []
    assert shopee.note == "skipped: needs --use-login and a saved session (shed browser-fetch --login shopee)"
    assert fake.calls[0][0] == "lazada" and len(fake.calls) == 1
    with pytest.raises(ToolError, match="shopee: skipped"):
        TOOL.run(TOOL.input(query="earbuds", strict=True), CTX)


def test_shopee_without_a_session_fails_only_that_source(fake: FakeTransport) -> None:
    result = TOOL.run(TOOL.input(query="earbuds", use_login=True), CTX)
    shopee = result.sources[1]
    assert (
        shopee.ok is False
        and shopee.note == "shopee: no saved session; run: shed browser-fetch --login shopee"
    )
    assert result.sources[0].ok and result.total == 2
    assert ("shopee", "https://shopee.ph/search?keyword=earbuds", True) in fake.calls


def test_price_filters_and_sorts_apply_per_source(fake: FakeTransport) -> None:
    cheap = TOOL.run(TOOL.input(query="earbuds", source=["lazada"], max_price=500), CTX)
    assert [i.price for i in cheap.sources[0].items] == [109.94]
    dear = TOOL.run(TOOL.input(query="earbuds", source=["lazada"], min_price=500, sort="price-desc"), CTX)
    assert [i.title[:5] for i in dear.sources[0].items] == ["Anker"]
    by_sold = TOOL.run(TOOL.input(query="earbuds", source=["lazada"], sort="sold"), CTX)
    assert [i.sold for i in by_sold.sources[0].items] == [1200, 856]
    desc = TOOL.run(TOOL.input(query="earbuds", source=["lazada"], sort="price-desc"), CTX)
    assert [i.price for i in desc.sources[0].items] == [1299.0, 109.94]


def test_a_limit_beyond_one_page_fetches_the_next_page_and_stops_when_it_is_empty(
    fake: FakeTransport,
) -> None:
    page_one = [
        {"title": f"Item {n}", "price": f"₱{n}.00", "url": f"//www.lazada.com.ph/products/item-i{n}.html"}
        for n in range(1, 41)
    ]
    fake.pages["https://www.lazada.com.ph/catalog/?q=earbuds"] = page_one
    fake.pages["https://www.lazada.com.ph/catalog/?q=earbuds&page=2"] = [
        {"title": "Item 41", "price": "₱41.00", "url": "//www.lazada.com.ph/products/item-i41.html"}
    ]
    fake.pages["https://www.lazada.com.ph/catalog/?q=earbuds&page=3"] = []
    result = TOOL.run(TOOL.input(query="earbuds", source=["lazada"], limit=100), CTX)
    assert [call[1][-7:] for call in fake.calls] == ["earbuds", "&page=2", "&page=3"]
    assert len(result.sources[0].items) == 41

    fake.calls.clear()
    TOOL.run(TOOL.input(query="earbuds", source=["lazada"], limit=10), CTX)
    assert len(fake.calls) == 1


def test_render_lists_each_source_with_price_rating_sold_and_link() -> None:
    text = module.render(TOOL.run(TOOL.input(query="earbuds", source=["lazada"], sort="price-asc"), CTX))
    assert text.startswith("earbuds: 2 listings (2026-11-04 12:00)")
    assert "== lazada: 2" in text
    lines = [line for line in text.splitlines() if "₱" in line]
    assert lines[0].split()[:4] == ["₱109.94", "4.8", "1200", "sold"]
    assert lines[0].rstrip().endswith("https://www.lazada.com.ph/products/m10-tws-i123456.html")
