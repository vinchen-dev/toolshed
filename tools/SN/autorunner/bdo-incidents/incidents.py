"""Turn BDO session-manager and auto-deposit log text into events, incidents and tables.

Pure text processing: nothing here talks to SLS, so tests can feed saved pulls.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel

TS = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_RECORD = re.compile(r"(?=\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \| (?:INFO|WARNING|ERROR|SUCCESS|CRITICAL))")
_SAVED_LINE = re.compile(TS + r" \| INFO \| BDO \|")
_SAVED_TIMES = re.compile(r"created_at (\S+ \S+?)\.\d+\+00:00 → (\S+ \S+?)\.\d+\+00:00")
_VISIT_EXPIRES = re.compile(r"expires_at \S+ \S+ → (\S+ \S+?)\.\d+\+00:00")
_GET_PAGE = re.compile(r"GET page (\d+) error")
_MILLIS = re.compile(r"after (\d+) milliseconds")
_FAIL_REASON = re.compile(r"unsuccessful: (.{0,100})")
_OD_SECS = re.compile(r"done in ([\d.]+s)")
_TIMER_AGE = re.compile(r"age ([\d.]+) min")
_STALE_DELTA = re.compile(r"\(\+([\d.]+)\)")
UTC_TO_MANILA = timedelta(hours=8)  # payload timestamps (created_at, expires_at) are UTC
BIG_OUTAGE = 600  # seconds; outages this long are starred in the tables
NEAR_2H = (6900, 7800)  # session age window flagged as "near the 2h mark"

Kind = Literal["FLAG", "RETRY_OK", "FAIL", "SAVED", "LOC", "VISIT", "OD", "TIMER", "STALE", "SCRAPER_STOP"]
Format = Literal["box", "side", "plain", "md"]
Sort = Literal["time", "outage"]
Retry = Literal["No", "Yes", "Yes, then failed again"]
State = Literal["up", "down", "unknown"]
UP_KINDS = frozenset({"SAVED", "RETRY_OK", "VISIT", "OD", "TIMER"})
DOWN_KINDS = frozenset({"FLAG", "SCRAPER_STOP"})

SHORT = {
    "account selector 25s": "sel25",
    "30s wait": "wait30",
    "Sign In 5s": "signin5",
    "login page 40s": "page40",
    "login form 30s": "form30",
    "page crash": "crash",
    "get_attribute 30s": "attr30",
    "FC9220 goto timeout": "goto",
}
LEGEND = "Failed steps: " + ", ".join(f"{short} = {long}" for long, short in SHORT.items())
BOX_HEADERS = [
    "Flag",
    "Life before flag",
    "Hang location",
    "Retry recovered?",
    "Relogin attempts / failed (where)",
    "Saved",
    "Outage",
]
MD_HEADER = "| " + " | ".join(BOX_HEADERS) + " |\n|" + "---|" * len(BOX_HEADERS)
WRAP = 72  # widest attempts cell before it wraps onto extra lines
BOX_FOOTNOTE = "* = outage of 10 minutes or more. Outage runs from the first flag to the saved session."
SIDE_FOOTNOTE = (
    "Att/Fail = relogin attempts / failed (the saving attempt counts). "
    "Yes+again = retry worked once, then failed again. "
    "PROD-QAT = PROD flag time minus QAT flag time. Use --format plain for the failed-step breakdown."
)


class Event(BaseModel):
    time: datetime
    kind: Kind
    detail: str | None = None
    """FAIL: failed step. LOC: hang location. OD: duration. TIMER: cookie age. STALE: balance delta."""
    expires_at: datetime | None = None
    """VISIT: the session's new expires_at, Manila time."""
    created_at: datetime | None = None
    """SAVED: created_at of the session that was replaced, Manila time."""
    new_created_at: datetime | None = None
    """SAVED: created_at of the session that was saved, Manila time."""


class Incident(BaseModel):
    flag: datetime
    life_before_flag: str
    hang_location: str
    retry_recovered: Retry
    attempts: str
    failed_steps: dict[str, int]
    failed_count: int
    saved: str
    outage_seconds: int
    outage: str
    open: bool = False


class EventLine(BaseModel):
    time: datetime
    kind: Kind
    text: str


class Timed(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def time(self) -> datetime: ...


def session_state(events: Iterable[Timed]) -> tuple[State, datetime | None]:
    """Whether the scraper has a working session after the last event that settles it, and since when.

    A saved login, a retry recovery or a page visit means up; an invalidation or a scraper stop means
    down. A failed relogin counts only while nothing says the session is alive, because the session
    manager keeps retrying in the background after the scraper's own retry reactivated the session.
    """
    state: State = "unknown"
    since: datetime | None = None
    for e in events:
        if e.kind in UP_KINDS:
            new: State = "up"
        elif e.kind in DOWN_KINDS or (e.kind == "FAIL" and state != "up"):
            new = "down"
        else:
            continue
        if new != state:
            state, since = new, e.time
    return state, since


def session_created(events: Sequence[Event]) -> datetime | None:
    """created_at of the last saved login, Manila time: the session in use while the state is up."""
    return next((e.new_created_at for e in reversed(events) if e.kind == "SAVED" and e.new_created_at), None)


def ts(text: str) -> datetime:
    return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")


def dur(seconds: float) -> str:
    sec = int(seconds)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def where(reason: str) -> str:
    """Label a 'Re-login unsuccessful' reason by the step or wait that failed."""
    if "crash" in reason:
        return "page crash"
    if "FC9220" in reason:
        return "FC9220 goto timeout"
    if "Sign In" in reason:
        return "Sign In 5s"
    if "login page" in reason:
        return "login page 40s"
    if "form incomplete" in reason:
        return "login form 30s"
    if "get_attribute" in reason:
        return "get_attribute 30s"
    if "25000ms" in reason:
        return "account selector 25s"
    if "30000ms" in reason:
        return "30s wait"
    timeout = re.search(r"Timeout (\d+)ms", reason)
    if timeout:
        return f"{int(timeout.group(1)) // 1000}s wait"
    return reason[:25]


def _classify(record: str) -> Event | None:
    head = re.match(TS, record)
    if not head:
        return None
    time = ts(head.group(1))
    if "Invalidated session for BDO" in record:
        return Event(time=time, kind="FLAG")
    if "BDO retry worked" in record:
        return Event(time=time, kind="RETRY_OK")
    if "Re-login unsuccessful" in record:
        reason = _FAIL_REASON.search(record)
        return Event(time=time, kind="FAIL", detail=where(reason.group(1) if reason else ""))
    if "REPLACED (login saved)" in record and _SAVED_LINE.match(record):
        times = _SAVED_TIMES.search(record)
        created = ts(times.group(1)) + UTC_TO_MANILA if times else None
        new = ts(times.group(2)) + UTC_TO_MANILA if times else None
        return Event(time=time, kind="SAVED", created_at=created, new_created_at=new)
    page = _GET_PAGE.search(record)
    if page:
        millis = _MILLIS.search(record)
        secs = round(int(millis.group(1)) / 1000) if millis else 30
        return Event(time=time, kind="LOC", detail=f"GET page {page.group(1)}, {secs}s")
    if "balance fetch network error" in record:
        return Event(time=time, kind="LOC", detail="balance fetch")
    if "Token refresh failed" in record:
        return Event(time=time, kind="LOC", detail="token refresh (session expired)")
    if "BDO token CHANGED (page visit)" in record:
        expires = _VISIT_EXPIRES.search(record)
        return Event(
            time=time, kind="VISIT", expires_at=ts(expires.group(1)) + UTC_TO_MANILA if expires else None
        )
    if "on-demand page revisit done" in record:
        secs = _OD_SECS.search(record)
        return Event(time=time, kind="OD", detail=secs.group(1) if secs else "?")
    if "cookie refresh successful" in record:
        age = _TIMER_AGE.search(record)
        return Event(time=time, kind="TIMER", detail=age.group(1) if age else "?")
    if "Stale-grid signal" in record:
        delta = _STALE_DELTA.search(record)
        return Event(time=time, kind="STALE", detail=delta.group(1) if delta else "?")
    if "Session wait timed out" in record:
        return Event(time=time, kind="SCRAPER_STOP")
    return None


def parse(text: str) -> list[Event]:
    """Return time-sorted, de-duplicated events. Joined records are split on their inner timestamps."""
    text = re.sub(r"\s+", " ", _ANSI.sub("", text))
    events: list[Event] = []
    seen: set[str] = set()
    for record in _RECORD.split(text):
        event = _classify(record)
        if event is None:
            continue
        key = event.model_dump_json()
        if key not in seen:
            seen.add(key)
            events.append(event)
    events.sort(key=lambda e: e.time)
    return events


@dataclass
class _Draft:
    """An incident still being assembled: from its first flag until a save or a retry recovery."""

    flag: datetime
    loc: str | None
    prev_new: datetime | None
    fails: Counter[str] = field(default_factory=Counter)
    retry_ok: datetime | None = None
    born: datetime | None = None
    relogin: bool = False
    last: datetime = field(init=False)
    trouble: datetime = field(init=False)
    """Time of the latest flag or failed relogin; a retry only ends the incident when it came after."""

    def __post_init__(self) -> None:
        self.last = self.trouble = self.flag


def _life(draft: _Draft) -> str:
    born = draft.born or draft.prev_new
    return dur((draft.flag - born).total_seconds()) if born else "?"


def _close(draft: _Draft, end: datetime, how: Literal["save", "retry"]) -> Incident:
    failed = sum(draft.fails.values())
    steps = ", ".join(f"{n}x {w}" for w, n in draft.fails.most_common())
    if how == "save":
        attempts = f"{failed + 1} / {failed}" + (f" ({steps})" if failed else "")
        saved = end.strftime("%H:%M:%S")
        retry: Retry = "Yes, then failed again" if draft.retry_ok else "No"
    else:
        attempts = f"{failed} / {failed} ({steps})" if failed else "0 / 0"
        saved = "n/a (retry fixed it)"
        retry = "Yes, then failed again" if failed else "Yes"
    outage = int((end - draft.flag).total_seconds())
    return Incident(
        flag=draft.flag,
        life_before_flag=_life(draft),
        hang_location=draft.loc or "?",
        retry_recovered=retry,
        attempts=attempts,
        failed_steps=dict(draft.fails),
        failed_count=failed,
        saved=saved,
        outage_seconds=outage,
        outage=dur(outage),
    )


def _still_open(draft: _Draft, now: datetime) -> Incident:
    outage = max(0, int((now - draft.flag).total_seconds()))
    return Incident(
        flag=draft.flag,
        life_before_flag=_life(draft),
        hang_location=draft.loc or "?",
        retry_recovered="Yes, then failed again" if draft.retry_ok else "No",
        attempts="open",
        failed_steps=dict(draft.fails),
        failed_count=sum(draft.fails.values()),
        saved="open",
        outage_seconds=outage,
        outage=dur(outage),
        open=True,
    )


def build_incidents(events: Sequence[Event], now: datetime) -> list[Incident]:
    """Group flags into incidents, each closed by the next saved session or by a retry-only recovery.

    The last incident stays open while a flag or a failed relogin came after its latest retry recovery.
    """
    rows: list[Incident] = []
    draft: _Draft | None = None
    last_loc: Event | None = None
    last_new: datetime | None = None
    for e in events:
        if e.kind == "LOC":
            last_loc = e
        if e.kind == "FLAG":
            if draft and draft.retry_ok and not draft.relogin:
                quiet = (e.time - max(draft.retry_ok, draft.last)).total_seconds()
                if quiet > 120:
                    rows.append(_close(draft, draft.retry_ok, "retry"))
                    draft = None
            if draft is None:
                recent = last_loc is not None and (e.time - last_loc.time).total_seconds() <= 60
                draft = _Draft(
                    flag=e.time, loc=last_loc.detail if last_loc and recent else None, prev_new=last_new
                )
            else:
                draft.last = draft.trouble = e.time
        elif draft is not None:
            if e.kind == "RETRY_OK":
                draft.retry_ok = draft.last = e.time
            elif e.kind == "FAIL":
                draft.fails[e.detail or "?"] += 1
                draft.relogin = True
                draft.trouble = e.time
            elif e.kind == "SAVED":
                draft.born = e.created_at
                rows.append(_close(draft, e.time, "save"))
                draft = None
        if e.kind == "SAVED":
            last_new = e.new_created_at
    if draft is not None:
        retry_ok = draft.retry_ok
        if retry_ok is not None and retry_ok > draft.trouble:
            rows.append(_close(draft, retry_ok, "retry"))
        else:
            rows.append(_still_open(draft, now))
    return rows


def _near(time: datetime, others: Sequence[datetime], seconds: float = 3) -> bool:
    return any(abs((time - t).total_seconds()) <= seconds for t in others)


def event_lines(events: Sequence[Event], since: datetime) -> list[EventLine]:
    """Cron-style list of what happened after `since`: visits with expires_at, flags, retries, relogins."""
    on_demand = [e.time for e in events if e.kind == "OD"]
    timers = [e.time for e in events if e.kind == "TIMER"]
    lines: list[EventLine] = []
    born: datetime | None = None
    for e in events:
        if e.time < since:
            if e.kind == "SAVED" and e.new_created_at:
                born = e.new_created_at
            continue
        text: str | None = None
        if e.kind == "VISIT":
            label = (
                "on-demand" if _near(e.time, on_demand) else ("timer" if _near(e.time, timers) else "visit")
            )
            ahead = dur((e.expires_at - e.time).total_seconds()) if e.expires_at else "?"
            new = e.expires_at.strftime("%m-%d %H:%M:%S") if e.expires_at else "?"
            text = f"{label} visit, new expires_at {new} (+{ahead})"
        elif e.kind == "FLAG":
            if born is None:
                note = "age unknown"
            else:
                age = (e.time - born).total_seconds()
                note = f"session age {dur(age)}" + (
                    " <- near the 2h mark" if NEAR_2H[0] <= age <= NEAR_2H[1] else ""
                )
            text = f"session invalidated ({note})"
        elif e.kind == "RETRY_OK":
            text = "BDO retry worked (session reactivated)"
        elif e.kind == "FAIL":
            text = f"relogin FAILED: {e.detail}"
        elif e.kind == "SAVED":
            if e.new_created_at:
                born = e.new_created_at
            old = e.created_at.strftime("%H:%M:%S") if e.created_at else "?"
            new = e.new_created_at.strftime("%H:%M:%S") if e.new_created_at else "?"
            text = f"relogin SAVED (created_at {old} -> {new})"
        elif e.kind == "LOC":
            text = f"first failure: {e.detail}"
        elif e.kind == "STALE":
            text = f"stale-grid signal +{e.detail}"
        elif e.kind == "SCRAPER_STOP":
            text = "scraper stopped (20 min session wait)"
        if text:
            lines.append(EventLine(time=e.time, kind=e.kind, text=text))
    return lines


def sort_incidents(rows: Sequence[Incident], sort: Sort, top: int) -> list[Incident]:
    ordered = sorted(
        rows, key=(lambda r: (-r.outage_seconds, r.flag)) if sort == "outage" else (lambda r: r.flag)
    )
    return ordered[:top] if top else ordered


def _short_steps(r: Incident) -> str:
    steps = sorted(r.failed_steps.items(), key=lambda kv: -kv[1])
    return " ".join(f"{n}x{SHORT.get(w, w)}" for w, n in steps) or "-"


def _compact(r: Incident) -> tuple[str, str, str, str]:
    """(hang location, retry, attempts/failed, saved) in the short forms used by the compact tables."""
    nf = r.failed_count
    att = "open" if r.open else (f"{nf + 1}/{nf}" if r.saved[0].isdigit() else f"{nf}/{nf}")
    retry = {"Yes, then failed again": "Yes+again"}.get(r.retry_recovered, r.retry_recovered)
    loc = r.hang_location.replace(", 30s", "").replace("token refresh (session expired)", "token refresh")
    saved = r.saved if r.saved[0].isdigit() else ("retry" if r.saved.startswith("n/a") else r.saved)
    return loc, retry, att, saved


def _table(hdr: Sequence[str], body: Sequence[Sequence[str]], right: set[int]) -> str:
    widths = [max(len(h), *(len(b[i]) for b in body)) if body else len(h) for i, h in enumerate(hdr)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            (c.rjust(widths[i]) if i in right else c.ljust(widths[i])) for i, c in enumerate(cells)
        ).rstrip()

    out = ["```", line(hdr), "  ".join("-" * w for w in widths), *(line(b) for b in body), "```"]
    return "\n".join(out)


def render_plain(rows: Sequence[Incident]) -> str:
    """Compact fixed-width table with short step labels; LEGEND explains them."""
    hdr = ["Flag", "Life", "Hang at", "Retry", "Att/Fail", "Failed steps", "Saved", "Outage"]
    body = []
    for r in rows:
        loc, retry, att, saved = _compact(r)
        body.append(
            [
                r.flag.strftime("%m-%d %H:%M:%S"),
                r.life_before_flag,
                loc,
                retry,
                att,
                _short_steps(r),
                saved,
                r.outage,
            ]
        )
    return _table(hdr, body, right={1, 4, 7})


def render_md(rows: Sequence[Incident]) -> str:
    lines = [MD_HEADER]
    for r in rows:
        outage = f"**{r.outage}**" if r.outage_seconds >= BIG_OUTAGE else r.outage
        cells = [
            r.flag.strftime("%m-%d %H:%M:%S"),
            r.life_before_flag,
            r.hang_location,
            r.retry_recovered,
            r.attempts,
            r.saved,
            outage,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def wrap_cell(text: str, width: int) -> list[str]:
    """Wrap on ', ' boundaries so a cell stays within width."""
    if len(text) <= width:
        return [text]
    lines: list[str] = []
    cur = ""
    for piece in re.split(r"(?<=, )", text):
        if cur and len(cur) + len(piece.rstrip()) > width:
            lines.append(cur.rstrip())
            cur = "    " + piece
        else:
            cur += piece
    lines.append(cur.rstrip())
    return lines


def _box_rows(rows: Sequence[Incident]) -> list[list[list[str]]]:
    out: list[list[list[str]]] = []
    for r in rows:
        outage = r.outage + ("*" if r.outage_seconds >= BIG_OUTAGE else "")
        out.append(
            [
                [r.flag.strftime("%m-%d %H:%M:%S")],
                [r.life_before_flag],
                [r.hang_location],
                [r.retry_recovered],
                wrap_cell(r.attempts, WRAP),
                [r.saved],
                [outage],
            ]
        )
    return out


def render_boxes(groups: Sequence[tuple[str, Sequence[Incident]]]) -> str:
    """One boxed table per (title, rows) group; every table shares the same column widths."""
    prepared = [(title, _box_rows(rows)) for title, rows in groups]
    widths = [len(h) for h in BOX_HEADERS]
    for _, body in prepared:
        for row in body:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], *(len(x) for x in cell))

    def hline(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def vline(cells: Sequence[str], center: bool = False) -> str:
        inner = (
            " " + (c.center(widths[i]) if center else c.ljust(widths[i])) + " " for i, c in enumerate(cells)
        )
        return "│" + "│".join(inner) + "│"

    out: list[str] = []
    for title, body in prepared:
        out += [title, "```", hline("┌", "┬", "┐"), vline(BOX_HEADERS, center=True)]
        for row in body:
            out.append(hline("├", "┼", "┤"))
            for k in range(max(len(c) for c in row)):
                out.append(vline([c[k] if k < len(c) else "" for c in row]))
        out += [hline("└", "┴", "┘"), "```"]
    out.append(BOX_FOOTNOTE)
    return "\n".join(out)


def _side_cells(r: Incident | None) -> list[str]:
    if r is None:
        return ["-"] * 6
    loc, retry, att, _ = _compact(r)
    return [r.flag.strftime("%H:%M:%S"), r.life_before_flag, loc, retry, att, r.outage]


def pair_incidents(
    qat: Sequence[Incident], prod: Sequence[Incident], sort: Sort, top: int, tolerance: float = 150
) -> list[tuple[Incident | None, Incident | None]]:
    """Pair QAT and PROD incidents whose flags fall within `tolerance` seconds, then sort and trim."""
    pairs: list[tuple[Incident | None, Incident | None]] = []
    used: set[int] = set()
    for q in qat:
        candidates = [
            (abs((p.flag - q.flag).total_seconds()), i) for i, p in enumerate(prod) if i not in used
        ]
        best = min(candidates, default=None)
        if best is not None and best[0] <= tolerance:
            used.add(best[1])
            pairs.append((q, prod[best[1]]))
        else:
            pairs.append((q, None))
    pairs += [(None, p) for i, p in enumerate(prod) if i not in used]

    def flag_of(pair: tuple[Incident | None, Incident | None]) -> datetime:
        return min(x.flag for x in pair if x)

    def worst_of(pair: tuple[Incident | None, Incident | None]) -> int:
        return -max(x.outage_seconds for x in pair if x)

    pairs.sort(key=(lambda pr: (worst_of(pr), flag_of(pr))) if sort == "outage" else flag_of)
    return pairs[:top] if top else pairs


def render_side(pairs: Sequence[tuple[Incident | None, Incident | None]]) -> str:
    """QAT and PROD side by side, one row per pair, with the PROD minus QAT flag offset."""
    sub = ["Flag", "Life", "Hang at", "Retry", "Att/Fail", "Outage"]
    names = ["Date", *sub, "PROD-QAT", *sub]
    body: list[list[str]] = []
    for q, p in pairs:
        offset = f"{int((p.flag - q.flag).total_seconds()):+d}s" if q and p else "-"
        date = min(x.flag for x in (q, p) if x).strftime("%m-%d")
        body.append([date, *_side_cells(q), offset, *_side_cells(p)])
    widths = [max(len(n), *(len(r[i]) for r in body)) if body else len(n) for i, n in enumerate(names)]
    right = {2, 5, 6, 9, 12, 13}
    seps = {0: "  ", 6: " | ", 7: " | "}

    def join(cells: Sequence[str], fill: str = " ") -> str:
        out = ""
        for i, c in enumerate(cells):
            out += c
            if i < len(cells) - 1:
                sep = seps.get(i, "  ")
                out += sep if fill == " " else sep.replace(" ", fill).replace("|", "+")
        return out

    def row(vals: Sequence[str]) -> str:
        return join([v.rjust(widths[i]) if i in right else v.ljust(widths[i]) for i, v in enumerate(vals)])

    left_w = sum(widths[1:7]) + 2 * 5
    right_w = sum(widths[8:14]) + 2 * 5
    group = (
        " " * (widths[0] + 2)
        + "QAT".center(left_w)
        + " | "
        + " " * widths[7]
        + " | "
        + "PROD".center(right_w)
    ).rstrip()
    out = [
        "```",
        group,
        row(names),
        join(["-" * w for w in widths], fill="-"),
        *(row(b) for b in body),
        "```",
    ]
    return "\n".join(out)
