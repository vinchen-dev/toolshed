"""Scrub secrets from log text before it leaves the tool: OTP digits, reference numbers, credential lines."""

from __future__ import annotations

import re

SECRET_LINE = re.compile(r"password|credentials:", re.IGNORECASE)
PREFIX = re.compile(
    r"^(?:\[\w+\] \[\w+\] )?\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:[.,]\d+)?(?: \| [^|]*\| [^|]*\| \S+ -)?"
)
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\bvalue=)\d{4,8}\b"), r"\1***"),
    (re.compile(r"('otp':\s*')\d+(')"), r"\1***\2"),
    (re.compile(r"(\(OTP\) is )\d+"), r"\1***"),
    (re.compile(r"(\botp\b[^0-9\n]{0,12})\d{4,8}\b", re.IGNORECASE), r"\1***"),
    (re.compile(r"(\bRef\.?\s*No\.?\s*:?\s*)\S+", re.IGNORECASE), r"\1***"),
)
REDACTED = " [redacted: credentials]"


def redact(text: str) -> str:
    """Return the text with OTP-like digits masked and credential lines reduced to their prefix."""
    out: list[str] = []
    for line in text.split("\n"):
        if SECRET_LINE.search(line):
            head = PREFIX.match(line)
            out.append((head.group(0) if head else "") + REDACTED)
            continue
        for pattern, replacement in PATTERNS:
            line = pattern.sub(replacement, line)
        out.append(line)
    return "\n".join(out)
