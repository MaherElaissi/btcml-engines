"""Response models. The OpenAPI spec generated from these is what the dashboard's client is built from."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Interval(str, Enum):
    s1 = "1s"
    m1 = "1m"
    m5 = "5m"
    m10 = "10m"
    h1 = "1h"


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    time: datetime


class RoadmapItem(BaseModel):
    text: str
    done: bool
    group: str | None = Field(None, description="bold sub-heading inside the section, if any")


class RoadmapSection(BaseModel):
    title: str
    status: Literal["done", "in_progress", "not_started"]
    done: int
    total: int
    items: list[RoadmapItem]


class Status(BaseModel):
    done: int
    total: int
    sections: list[RoadmapSection]


class Coverage(BaseModel):
    interval: Interval
    available: bool
    files: int
    first: datetime | None = None
    last: datetime | None = Field(None, description="open time of the newest stored candle")
    stored_rows: int | None = Field(None, description="rows on disk, before deduplication")
    size_bytes: int | None = None
    lag_seconds: int | None = Field(None, description="seconds since the newest candle closed")


class Gap(BaseModel):
    gap_start: datetime
    missing: int


class Quality(BaseModel):
    interval: Interval
    rows: int
    first: datetime
    last: datetime
    duplicates_removed: int | None = Field(None, description="raw intervals only")
    expected_rows: int
    missing_rows: int
    missing_pct: float
    gap_count: int
    largest_gaps: list[Gap]
    bad_ohlc_rows: int
    negative_volume_rows: int
    zero_volume_pct: float
    incomplete_bars: int | None = Field(
        None, description="built bars only: buckets made from fewer 1m candles than expected"
    )


class Candles(BaseModel):
    """Column arrays; `t` is the bucket open time in Unix seconds (lightweight-charts format)."""

    interval: Interval
    bucket_seconds: int
    start: datetime
    end: datetime
    t: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


class FeedStatus(BaseModel):
    source: str
    configured: bool
    articles: int
    live_articles: int = Field(description="articles with a trustworthy first_seen_at (backfill=0)")
    last_article_at: datetime | None
    last_poll_at: datetime | None
    last_ok_at: datetime | None
    last_new: int | None
    last_error: str | None
    healthy: bool = Field(description="last successful poll within 3 poll intervals")


class Article(BaseModel):
    source: str
    title: str
    link: str | None
    published_at: datetime | None
    first_seen_at: datetime
    backfill: bool


class NewsVolume(BaseModel):
    hour: datetime
    articles: int
