"""Turn an error message or trace id into a timeline from SLS: when, how often, where, and which code.

The lines are pulled through the sls-logs tool (sign in first with `shed sls-login`). They are grouped
into signatures (the matching line with ids and numbers masked), counted per hour and per container, and
scanned for stack frames so the investigation can go straight from the log to a file and line.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib.manila import parse_manila
from shed.registry import load_tool

Env = Literal["qat", "prod"]
# Same SLS project/logstore pairs as deployed-version; PMS, finance-ai and the autorunner all log here.
ENVS: dict[Env, tuple[str, str]] = {
    "qat": ("platform-qat-aliyun-logs", "platform-pms-qat"),
    "prod": ("platform-prod-aliyun-logs", "platform-pms-prod"),
}
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SIGNATURE_WIDTH = 160
SAMPLE_WIDTH = 400
WITHHELD = "(line withheld: it mentions credentials)"
CUT = " …(rest withheld: it mentions credentials)"
_SECRET_RE = re.compile(
    r"password|passwd|\botp\b|secret|authorization|ref\. ?no|(?:token|api_?key)[\"']?\s*[:=]", re.IGNORECASE
)
# A record joins container lines with NUL, each after the first led by the runtime's `<time> stdout F `.
_SPLIT_RE = re.compile(r"[\x00\n]+")
_CRI_RE = re.compile(r"^\S+ (?:stdout|stderr) [FP] ")
_STACK_RE = re.compile(r'(?:^|[\s|])at\s|File "')
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_RE = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\d+")
_JS_FRAME_RE = re.compile(r"((?:[\w@.-]*/)*[\w@.-]+\.(?:js|ts|mjs|cjs|jsx|tsx|py)):(\d+)(?::\d+)?")
_PY_FRAME_RE = re.compile(r'File "([^"]+\.py)", line (\d+)')
_SKIP_FRAME = ("node_modules/", "node:", "site-packages/")


class Input(BaseModel):
    match: list[str] = Field(
        description="Text the log line must contain: an error message, a code or a trace id; "
        "repeat to require all of them"
    )
    env: Env = Field("qat", description="Environment whose logstore to search")
    container: str | None = Field(
        None, description="Only this container, e.g. finance-ai, pms-auto-deposit, pms-session-manager-bdo"
    )
    hours: int = Field(24, ge=1, description="Look back this many hours from the end time")
    start: str | None = Field(
        None, description="Start time, Manila 'YYYY-MM-DD HH:MM[:SS]'; overrides --hours"
    )
    end: str | None = Field(None, description="End time, Manila 'YYYY-MM-DD HH:MM[:SS]' (default: now)")
    around: int = Field(
        0,
        ge=0,
        le=600,
        description="Also return every line of the same container within this many "
        "seconds of the latest occurrence",
    )
    samples: int = Field(3, ge=1, le=20, description="Signatures to list, most frequent first")
    limit: int = Field(2000, ge=1, description="Maximum matching log records to pull")


class Signature(BaseModel):
    signature: str
    """The matching line with ids and numbers masked, so repeats of one error group together."""
    count: int
    first_seen: str
    last_seen: str
    sample: str


class Bucket(BaseModel):
    hour: str
    count: int


class Named(BaseModel):
    name: str
    count: int


class ContextLine(BaseModel):
    time: str
    content: str


class Trace(BaseModel):
    env: Env
    query: str
    start: str
    end: str
    count: int
    """Matching log lines; one SLS record can hold several."""
    truncated: bool
    """True when the pull stopped at --limit; SLS is read oldest first, so the newest matches are missing."""
    first_seen: str | None
    last_seen: str | None
    per_hour: list[Bucket]
    containers: list[Named]
    signatures: list[Signature]
    other_signatures: int
    frames: list[Named]
    """Source files and lines named in stack traces, most frequent first; dependencies are left out."""
    context_container: str | None = None
    context: list[ContextLine] = []
    notes: list[str] = []


def _sls() -> Any:
    """The sls-logs tool; a seam so tests can hand back recorded pulls."""
    return load_tool("sls-logs")


def build_query(matches: list[str], container: str | None) -> str:
    """Every --match as a quoted phrase, AND-ed, inside the container when one is given."""
    terms = [m.strip() for m in matches if m.strip()]
    if not terms:
        raise ToolError("--match needs some text")
    parts = ['"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"' for term in terms]
    if container:
        parts.insert(0, f"__tag__:_container_name_: {container}")
    return " AND ".join(parts)


def message_of(content: str) -> str:
    """The readable part of a record: the `msg` (plus `stack`) of a JSON log line, else the text itself."""
    text = content.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return content
        if isinstance(data, dict):
            parts = [data[key] for key in ("msg", "message", "err", "error", "stack") if key in data]
            texts = [p if isinstance(p, str) else json.dumps(p, ensure_ascii=False) for p in parts]
            if texts:
                return "\n".join(texts)
    return content


def split_record(content: str) -> list[str]:
    """The log lines inside one SLS record, without the container runtime's prefix."""
    parts = (_CRI_RE.sub("", part.strip()) for part in _SPLIT_RE.split(message_of(content)))
    return [part for part in parts if part]


def occurrences(lines: list[str], matches: list[str]) -> list[int]:
    """Indexes of the lines that hold every match; failing that any match, failing that the first line."""
    terms = [m.strip().lower() for m in matches if m.strip()]
    lowered = [ln.lower() for ln in lines]
    every = [i for i, ln in enumerate(lowered) if all(t in ln for t in terms)]
    if every:
        return every
    some = [i for i, ln in enumerate(lowered) if any(t in ln for t in terms)]
    return some or ([0] if lines else [])


def safe(text: str, width: int) -> str:
    """A line fit to show: cut where it starts to mention credentials, and to ``width``."""
    found = _SECRET_RE.search(text)
    if found:
        if found.start() < 20:
            return WITHHELD
        text = text[: found.start()].rstrip()
        return (text if len(text) <= width else text[: width - 1] + "…") + CUT
    return text if len(text) <= width else text[: width - 1] + "…"


def signature_of(text: str) -> str:
    masked = _UUID_RE.sub("<id>", text)
    masked = _HEX_RE.sub("<id>", masked)
    masked = _NUM_RE.sub("N", masked)
    return safe(masked, SIGNATURE_WIDTH)


def stack_after(lines: list[str], index: int) -> str:
    """The line at ``index`` with the stack-trace lines that directly follow it."""
    block = [lines[index]]
    for following in lines[index + 1 : index + 40]:
        if not _STACK_RE.search(following):
            break
        block.append(following)
    return "\n".join(block)


def frames_of(message: str) -> list[str]:
    """`file:line` for each stack frame in the text that is the project's own code."""
    found: list[str] = []
    for regex in (_PY_FRAME_RE, _JS_FRAME_RE):
        for path, number in regex.findall(message):
            if any(skip in path for skip in _SKIP_FRAME):
                continue
            frame = f"{path}:{number}"
            if frame not in found:
                found.append(frame)
    return found


def _pull(env: Env, query: str, limit: int, ctx: ToolContext, **window: Any) -> Any:
    project, logstore = ENVS[env]
    sls = _sls()
    params = sls.input(project=project, logstore=logstore, query=query, limit=limit, **window)
    try:
        return sls.run(params, ctx)
    except ToolError as exc:
        raise ToolError(f"{env}: {exc}") from exc


def run(params: Input, ctx: ToolContext) -> Trace:
    query = build_query(params.match, params.container)
    window: dict[str, Any] = {"minutes": params.hours * 60}
    if params.start:
        window["start"] = params.start
    if params.end:
        window["end"] = params.end
    ctx.log(f"# {params.env}: {query}")
    pulled = _pull(params.env, query, params.limit, ctx, **window)
    lines = list(pulled.lines)

    hours: Counter[str] = Counter()
    containers: Counter[str] = Counter()
    frames: Counter[str] = Counter()
    groups: dict[str, list[tuple[str, str]]] = {}
    total = 0
    for line in lines:
        parts = split_record(line.content)
        for index in occurrences(parts, params.match):
            total += 1
            hours[line.time[:13] + ":00"] += 1
            containers[line.container] += 1
            frames.update(frames_of(stack_after(parts, index)))
            groups.setdefault(signature_of(parts[index]), []).append((line.time, parts[index]))

    ranked = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    signatures = [
        Signature(
            signature=signature,
            count=len(hits),
            first_seen=min(time for time, _ in hits),
            last_seen=max(time for time, _ in hits),
            sample=safe(max(hits)[1], SAMPLE_WIDTH),
        )
        for signature, hits in ranked[: params.samples]
    ]
    notes: list[str] = []
    if pulled.truncated:
        notes.append(
            f"the pull stopped at --limit {params.limit}, oldest first: later matches are missing, "
            "so narrow the window, add --container or raise --limit before trusting the count and last seen"
        )
    if not lines:
        notes.append("no matches: check the wording, the --env and the --container, or widen --hours")

    context: list[ContextLine] = []
    context_container = None
    if params.around and lines:
        latest = max(lines, key=lambda item: item.time)
        context_container = latest.container
        moment = parse_manila(latest.time)
        span = timedelta(seconds=params.around)
        nearby = _pull(
            params.env,
            f"__tag__:_container_name_: {latest.container}",
            500,
            ctx,
            start=(moment - span).strftime(TIME_FORMAT),
            end=(moment + span + timedelta(seconds=1)).strftime(TIME_FORMAT),
        )
        context = [
            ContextLine(time=item.time, content=safe(part, SAMPLE_WIDTH))
            for item in nearby.lines
            for part in split_record(item.content)
        ]
        if nearby.truncated:
            notes.append("the context stopped at 500 lines: lower --around")

    return Trace(
        env=params.env,
        query=query,
        start=pulled.start,
        end=pulled.end,
        count=total,
        truncated=bool(pulled.truncated),
        first_seen=min((line.time for line in lines), default=None),
        last_seen=max((line.time for line in lines), default=None),
        per_hour=[Bucket(hour=hour, count=count) for hour, count in sorted(hours.items())],
        containers=[Named(name=name, count=count) for name, count in containers.most_common()],
        signatures=signatures,
        other_signatures=max(0, len(ranked) - len(signatures)),
        frames=[Named(name=name, count=count) for name, count in frames.most_common(10)],
        context_container=context_container,
        context=context,
        notes=notes,
    )


def render(result: Trace) -> str:
    more = "+" if result.truncated else ""
    out = [f"# {result.env.upper()} {result.query}", f"window {result.start} to {result.end} (Manila)"]
    if not result.count:
        out.append("0 matches")
    else:
        out.append(f"{result.count}{more} matches, first {result.first_seen}, last {result.last_seen}")
        out.append("containers: " + ", ".join(f"{c.name} x{c.count}" for c in result.containers))
        out.append("per hour:")
        out.extend(f"  {b.hour}  {b.count}" for b in result.per_hour)
        out.append("signatures:")
        for sig in result.signatures:
            out.append(f"  x{sig.count}  {sig.first_seen} to {sig.last_seen}  {sig.signature}")
            out.append(f"      latest: {sig.sample}")
        if result.other_signatures:
            out.append(f"  (+{result.other_signatures} more signatures; raise --samples)")
        if result.frames:
            out.append("code frames:")
            out.extend(f"  x{f.count}  {f.name}" for f in result.frames)
        else:
            out.append("code frames: none in these lines (search the code for the message text instead)")
    if result.context:
        out.append(f"around the latest occurrence ({result.context_container}):")
        out.extend(f"  {c.time}  {c.content}" for c in result.context)
    out.extend(f"note: {note}" for note in result.notes)
    return "\n".join(out) + "\n"


TOOL = Tool(
    name="error-trace",
    description=(
        "Trace one error through the SLS logs of PMS, finance-ai or the autorunner: give the error text or "
        "a trace id and get how often it happened, first and last seen, the count per hour and per "
        "container, the distinct messages, the source files and lines named in stack traces, and "
        "optionally every log line around the latest occurrence. Use it as the first log step of an "
        "investigation, before reading raw lines with sls-logs. Read-only."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        'shed error-trace --match "Cannot read properties of undefined" --env prod --hours 48',
        "shed error-trace --match 2955310f --env prod --container finance-ai --around 30",
        'shed error-trace --match "[BDO]" --match Timeout --container pms-auto-deposit --samples 5 --json',
    ),
    version="0.1.0",
)
