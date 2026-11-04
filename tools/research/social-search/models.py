"""Result models shared by the tool and its platform adapters."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Post(BaseModel):
    platform: str
    url: str
    author: str | None = None
    author_url: str | None = None
    text: str = ""
    created_at: datetime | None = None
    """Manila time when the platform shows one."""
    when: str | None = None
    """The platform's own relative label ('16h ago') when no exact time is shown."""
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    views: int | None = None
    tags: list[str] = []


class PlatformResult(BaseModel):
    platform: str
    ok: bool
    note: str | None = None
    posts: list[Post] = []


class SocialSearch(BaseModel):
    query: str | None
    url: str | None
    since: datetime | None
    fetched_at: datetime
    platforms: list[PlatformResult]
    total: int
