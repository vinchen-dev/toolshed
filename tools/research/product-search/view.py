"""Dashboard page for jobs that run product-search: every product seen across the job's kept runs with its
first-seen, lowest, highest and latest price, biggest drops first. Built from stored run records only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ValidationError

from shed.cron import RunRecord
from shed.ui import esc, when

from .tool import ProductSearch

TITLE = "Price watch"
MAX_ROWS = 300


class PriceRow(BaseModel):
    url: str
    source: str
    title: str
    first_seen: datetime
    last_seen: datetime
    lowest: float
    highest: float
    latest: float
    runs: int

    @property
    def drop(self) -> float:
        """How far the latest price sits below the highest seen, as a fraction."""
        return (self.highest - self.latest) / self.highest if self.highest else 0.0


def searches(runs: Sequence[RunRecord]) -> list[tuple[datetime, ProductSearch]]:
    """(started, result) of the successful runs, oldest first; unreadable results are skipped."""
    out: list[tuple[datetime, ProductSearch]] = []
    for record in runs:
        if record.ok and record.result is not None:
            try:
                out.append((record.started, ProductSearch.model_validate(record.result)))
            except ValidationError:
                continue
    out.sort(key=lambda pair: pair[0])
    return out


def history(found: Sequence[tuple[datetime, ProductSearch]]) -> list[PriceRow]:
    """One row per product link across every run, biggest drop from its highest price first."""
    rows: dict[str, PriceRow] = {}
    for started, result in found:
        for block in result.sources:
            for item in block.items:
                row = rows.get(item.url)
                if row is None:
                    rows[item.url] = PriceRow(
                        url=item.url,
                        source=item.source,
                        title=item.title,
                        first_seen=started,
                        last_seen=started,
                        lowest=item.price,
                        highest=item.price,
                        latest=item.price,
                        runs=1,
                    )
                    continue
                row.title = item.title
                row.last_seen = started
                row.lowest = min(row.lowest, item.price)
                row.highest = max(row.highest, item.price)
                row.latest = item.price
                row.runs += 1
    return sorted(rows.values(), key=lambda r: (-r.drop, r.latest))[:MAX_ROWS]


def peso(value: float) -> str:
    return f"₱{value:,.2f}"


def view(job: str, runs: Sequence[RunRecord], now: datetime, query: Mapping[str, str]) -> str:
    found = searches(runs)
    title = f'<h1>{esc(TITLE)} <small><a href="/jobs/{esc(job)}">{esc(job)}</a></small></h1>'
    if not found:
        return title + (
            '<div class="empty"><p>No successful run yet. '
            "Run the job once and this page fills itself.</p></div>"
        )
    latest_started, latest = found[-1]
    rows = history(found)
    head = (
        title + f'<p class="summary">{esc(latest.query)}: {len(rows)} products across {len(found)} runs, '
        f"latest {esc(when(latest_started))}</p>"
    )
    body = "".join(
        "<tr>"
        f"<td>{esc(r.source)}</td>"
        f'<td><a href="{esc(r.url)}" target="_blank" rel="noopener">{esc(r.title[:80])}</a></td>'
        f'<td class="num">{esc(peso(r.latest))}</td>'
        f'<td class="num">{esc(peso(r.lowest))}</td>'
        f'<td class="num">{esc(peso(r.highest))}</td>'
        f'<td class="num">{r.drop * 100:.0f}%</td>'
        f"<td>{esc(when(r.first_seen))}</td>"
        f'<td class="num">{r.runs}</td>'
        "</tr>"
        for r in rows
    )
    table = (
        '<div class="scroll"><table class="data"><tr><th>source</th><th>product</th><th>latest</th>'
        "<th>lowest</th><th>highest</th><th>drop</th><th>first seen</th><th>runs</th></tr>"
        f"{body}</table></div>"
    )
    return head + table
