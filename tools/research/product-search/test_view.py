from __future__ import annotations

import importlib
from datetime import datetime, timedelta

from shed.cron import RunRecord
from shed.lib.manila import MANILA
from shed.registry import load_tool_module

module = load_tool_module("product-search")
view = importlib.import_module(f"{module.__package__}.view")
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=MANILA)


def record(started: datetime, prices: dict[str, float], *, ok: bool = True) -> RunRecord:
    items = [
        {
            "source": "lazada",
            "title": f"Item {key}",
            "price": price,
            "currency": "PHP",
            "url": f"https://l/{key}",
        }
        for key, price in prices.items()
    ]
    result = {
        "query": "earbuds",
        "fetched_at": started.isoformat(),
        "sources": [{"source": "lazada", "ok": True, "items": items}],
        "total": len(items),
    }
    return RunRecord(
        job="earbuds-watch",
        tool="product-search",
        args={},
        started=started,
        finished=started + timedelta(seconds=9),
        duration_ms=9000,
        ok=ok,
        error=None if ok else "lazada: blocked",
        summary="x",
        result=result if ok else None,
    )


def test_history_tracks_first_lowest_highest_and_latest_price_per_product() -> None:
    runs = [
        record(T0, {"a": 100.0, "b": 50.0}),
        record(T0 + timedelta(days=1), {"a": 80.0, "b": 55.0, "c": 10.0}),
        record(T0 + timedelta(days=2), {"a": 120.0}, ok=False),  # failed run is ignored
        record(T0 + timedelta(days=3), {"a": 90.0, "b": 40.0}),
    ]
    rows = {r.url: r for r in view.history(view.searches(runs))}
    a, b, c = rows["https://l/a"], rows["https://l/b"], rows["https://l/c"]
    assert (a.first_seen, a.last_seen, a.lowest, a.highest, a.latest, a.runs) == (
        T0,
        T0 + timedelta(days=3),
        80.0,
        100.0,
        90.0,
        3,
    )
    assert (b.lowest, b.highest, b.latest, b.runs) == (40.0, 55.0, 40.0, 3)
    assert (c.latest, c.runs) == (10.0, 1)
    assert [r.url[-1] for r in view.history(view.searches(runs))] == ["b", "a", "c"]  # biggest drop first


def test_page_lists_products_with_prices_and_handles_no_runs() -> None:
    runs = [record(T0, {"a": 100.0}), record(T0 + timedelta(days=1), {"a": 75.0})]
    html = view.view("earbuds-watch", runs, T0 + timedelta(days=2), {})
    assert 'href="/jobs/earbuds-watch"' in html and "earbuds: 1 products across 2 runs" in html
    assert '<table class="data">' in html
    assert "₱75.00" in html and "₱100.00" in html and "25%" in html and 'href="https://l/a"' in html
    assert "No successful run yet" in view.view("earbuds-watch", [], T0, {})
