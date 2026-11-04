"""Search Philippine marketplaces for a product and return comparable listings: title, price, shop, rating,
sold count and link. Lazada needs no account. Shopee needs a one-time `shed browser-fetch --login shopee`
and --use-login on the run; keep it out of cron. Scheduled runs form a price history on the dashboard."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib import manila

Source = Literal["lazada", "shopee"]
Sort = Literal["relevance", "price-asc", "price-desc", "sold"]
LOGGED_IN: tuple[str, ...] = ("shopee",)
PAGE_SIZE = 40
MAX_PAGES = 3
EXTRACTORS = Path(__file__).parent / "extractors"
_PRICE = re.compile(r"[\d][\d,]*(?:\.\d+)?")


def now_manila() -> datetime:
    return manila.now_manila().replace(microsecond=0)


class Transport:
    """The one live route: a page opened through browser-fetch (same group) and its in-page extractor."""

    def browse(
        self, site: str, url: str, extractor: str, *, profile: bool, headed: bool | None, scrolls: int
    ) -> Any:
        from shed.registry import load_tool_module

        browser = load_tool_module("browser-fetch")
        return browser.extract(site, url, extractor, profile=profile, headed=headed, scrolls=scrolls)


transport = Transport()


class Input(BaseModel):
    query: str = Field(description="What to search for")
    source: list[Source] = Field(
        default_factory=lambda: ["lazada", "shopee"],
        description="Marketplaces to search; repeat for several. shopee needs --use-login",
    )
    limit: int = Field(
        20, ge=1, le=120, description="Listings to keep per source (pages are fetched as needed)"
    )
    min_price: float | None = Field(None, ge=0, description="Drop listings below this price")
    max_price: float | None = Field(None, ge=0, description="Drop listings above this price")
    sort: Sort = Field(
        "relevance", description="Order: relevance (as the site ranks), price-asc, price-desc, sold"
    )
    use_login: bool = Field(
        False, description="Allow sources that need your saved browser session (never on cron)"
    )
    strict: bool = Field(False, description="Fail the run when any source is skipped or fails")
    headed: bool | None = Field(None, description="Force a visible (true) or headless (false) browser")


class Listing(BaseModel):
    source: str
    title: str
    price: float
    currency: str = "PHP"
    url: str
    shop: str | None = None
    rating: float | None = None
    sold: int | None = None
    location: str | None = None
    image: str | None = None


class SourceResult(BaseModel):
    source: str
    ok: bool
    note: str | None = None
    items: list[Listing] = []


class ProductSearch(BaseModel):
    query: str
    fetched_at: datetime
    sources: list[SourceResult]
    total: int


def _browser() -> Any:
    """The browser-fetch tool module: the helpers shared across the research group."""
    from shed.registry import load_tool_module

    return load_tool_module("browser-fetch")


def extractor(name: str) -> str:
    return _browser().read_extractor(EXTRACTORS, name)


def parse_count(value: Any) -> int | None:
    return _browser().parse_count(value)


def parse_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    m = _PRICE.search(str(value))
    return float(m.group(0).replace(",", "")) if m else None


def parse_rating(value: Any) -> float | None:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if 0 <= rating <= 5 else None


def clean_link(href: str, base: str) -> str:
    """Absolute product link without the site's tracking query."""
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = base + href
    parsed = urlparse(href)
    return parsed._replace(query="", fragment="").geturl()


def listings(source: str, records: list[dict[str, Any]], base: str) -> list[Listing]:
    out: list[Listing] = []
    seen: set[str] = set()
    for r in records:
        price = parse_price(r.get("price"))
        if price is None or not r.get("url"):
            continue
        url = clean_link(str(r["url"]), base)
        if url in seen:
            continue
        seen.add(url)
        out.append(
            Listing(
                source=source,
                title=str(r.get("title") or "").strip(),
                price=price,
                url=url,
                shop=r.get("shop") or None,
                rating=parse_rating(r.get("rating")),
                sold=parse_count(r.get("sold")),
                location=r.get("location") or None,
                image=r.get("image") or None,
            )
        )
    return out


def paged(
    source: str,
    page_url: Callable[[int], str],
    *,
    base: str,
    page_size: int,
    scrolls: int,
    limit: int,
    headed: bool | None,
) -> list[Listing]:
    """Fetch result pages until `limit` listings are in hand, a page is empty, or MAX_PAGES is reached."""
    found: list[Listing] = []
    for page in range(1, min(MAX_PAGES, -(-limit // page_size)) + 1):
        records = transport.browse(
            source,
            page_url(page),
            extractor(source),
            profile=source in LOGGED_IN,
            headed=headed,
            scrolls=scrolls,
        )
        batch = listings(source, records, base)
        found.extend(batch)
        if not batch or len(found) >= limit:
            break
    return found


def lazada(query: str, *, limit: int, headed: bool | None) -> list[Listing]:
    def url(page: int) -> str:
        return f"https://www.lazada.com.ph/catalog/?q={quote_plus(query)}" + (
            f"&page={page}" if page > 1 else ""
        )

    return paged(
        "lazada",
        url,
        base="https://www.lazada.com.ph",
        page_size=PAGE_SIZE,
        scrolls=2,
        limit=limit,
        headed=headed,
    )


def shopee(query: str, *, limit: int, headed: bool | None) -> list[Listing]:
    def url(page: int) -> str:
        return f"https://shopee.ph/search?keyword={quote_plus(query)}" + (
            f"&page={page - 1}" if page > 1 else ""
        )

    return paged("shopee", url, base="https://shopee.ph", page_size=60, scrolls=3, limit=limit, headed=headed)


SEARCHERS = {"lazada": lazada, "shopee": shopee}


def arrange(items: list[Listing], params: Input) -> list[Listing]:
    kept = [
        i
        for i in items
        if (params.min_price is None or i.price >= params.min_price)
        and (params.max_price is None or i.price <= params.max_price)
    ]
    if params.sort == "price-asc":
        kept.sort(key=lambda i: i.price)
    elif params.sort == "price-desc":
        kept.sort(key=lambda i: -i.price)
    elif params.sort == "sold":
        kept.sort(key=lambda i: -(i.sold or 0))
    seen: set[str] = set()
    unique = [i for i in kept if not (i.url in seen or seen.add(i.url))]
    return unique[: params.limit]


def run(params: Input, ctx: ToolContext) -> ProductSearch:
    results: list[SourceResult] = []
    for name in params.source:
        if name in LOGGED_IN and not params.use_login:
            note = f"skipped: needs --use-login and a saved session (shed browser-fetch --login {name})"
            if params.strict:
                raise ToolError(f"{name}: {note}")
            results.append(SourceResult(source=name, ok=False, note=note))
            continue
        ctx.log(f"{name}: searching")
        try:
            items = SEARCHERS[name](params.query, limit=params.limit, headed=params.headed)
        except Exception as exc:  # one marketplace failing must not take the other down
            if params.strict:
                raise ToolError(f"{name}: {exc}") from exc
            ctx.log(f"{name}: failed: {exc}")
            results.append(SourceResult(source=name, ok=False, note=str(exc)))
            continue
        results.append(SourceResult(source=name, ok=True, items=arrange(items, params)))
    return ProductSearch(
        query=params.query, fetched_at=now_manila(), sources=results, total=sum(len(r.items) for r in results)
    )


def render(result: ProductSearch) -> str:
    lines = [f"{result.query}: {result.total} listings ({result.fetched_at:%Y-%m-%d %H:%M})"]
    for block in result.sources:
        note = f" ({block.note})" if block.note else ""
        lines.append(f"\n== {block.source}: {len(block.items)}{note}")
        for item in block.items:
            sold = f"{item.sold} sold" if item.sold is not None else "-"
            rating = f"{item.rating:.1f}" if item.rating is not None else "-"
            price = "₱" + format(item.price, ",.2f")
            lines.append(f"  {price:>12}  {rating:>4}  {sold:>12}  {item.title[:60]:<60}  {item.url}")
    return "\n".join(lines)


TOOL = Tool(
    name="product-search",
    description=(
        "Search Lazada (and Shopee with a saved login) for a product and get comparable listings with price, "
        "shop, rating, sold count and link. Use it to compare offers or, on a schedule, to watch prices."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed product-search --query 'anker soundcore r60i' --source lazada --sort price-asc",
        "shed product-search --query earbuds --max-price 500 --limit 10 --json",
        "shed product-search --query earbuds --use-login   # Shopee too (after browser-fetch --login shopee)",
    ),
)
