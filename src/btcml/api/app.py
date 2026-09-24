"""Read-only HTTP API for the dashboard (`btcml serve`). All routes live under /api/v1."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import Config
from . import services
from .progress import roadmap
from .schemas import (
    Article,
    Candles,
    Coverage,
    FeedStatus,
    Health,
    Interval,
    NewsVolume,
    Quality,
    Status,
)

TOKEN_ENV = "BTCML_API_TOKEN"

try:
    VERSION = version("btcml")
except PackageNotFoundError:  # running from a source tree without install
    VERSION = "0.0.0"


def require_token(request: Request) -> None:
    """If BTCML_API_TOKEN is set, require it as a Bearer header or ?token= (for WebSockets)."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return
    header = request.headers.get("authorization", "")
    given = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
    if not secrets.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "missing or invalid token")


def _cfg(request: Request) -> Config:
    return request.app.state.cfg


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


public = APIRouter(prefix="/api/v1")
api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


@public.get("/health", response_model=Health, tags=["meta"])
def health() -> Health:
    return Health(version=VERSION, time=datetime.now(timezone.utc))


@api.get("/status", response_model=Status, tags=["meta"])
def status(cfg: Config = Depends(_cfg)) -> dict:
    """Roadmap progress parsed from TODO.md."""
    sections = roadmap(cfg.root)
    return {
        "done": sum(s["done"] for s in sections),
        "total": sum(s["total"] for s in sections),
        "sections": sections,
    }


@api.get("/data/coverage", response_model=list[Coverage], tags=["data"])
def data_coverage(cfg: Config = Depends(_cfg)) -> list[dict]:
    """What is on disk for each interval (cheap: metadata only)."""
    return services.coverage(cfg)


@api.get("/data/{interval}/quality", response_model=Quality, tags=["data"])
def data_quality(interval: Interval, cfg: Config = Depends(_cfg)) -> dict:
    """The `btcml check` report. Slow the first time for 1s; cached until files change."""
    return services.quality(cfg, interval.value)


@api.get("/data/{interval}/candles", response_model=Candles, tags=["data"])
def data_candles(
    interval: Interval,
    start: datetime | None = Query(None, description="inclusive, UTC if no offset given"),
    end: datetime | None = Query(None, description="exclusive; default: after the newest candle"),
    max_points: int = Query(1000, ge=10, le=5000),
    cfg: Config = Depends(_cfg),
) -> dict:
    """OHLCV, downsampled into larger buckets when the range holds more than max_points candles."""
    start, end = _utc(start), _utc(end)
    if start and end and start >= end:
        raise HTTPException(422, "start must be before end")
    return services.candles(cfg, interval.value, start, end, max_points)


@api.get("/news/feeds", response_model=list[FeedStatus], tags=["news"])
def news_feeds(cfg: Config = Depends(_cfg)) -> list[dict]:
    """Collector health and article counts per feed."""
    return services.news_feeds(cfg)


@api.get("/news/latest", response_model=list[Article], tags=["news"])
def news_latest(
    limit: int = Query(50, ge=1, le=500),
    source: str | None = None,
    cfg: Config = Depends(_cfg),
) -> list[dict]:
    return services.news_latest(cfg, limit, source)


@api.get("/news/volume", response_model=list[NewsVolume], tags=["news"])
def news_volume(
    hours: int = Query(48, ge=1, le=24 * 90), cfg: Config = Depends(_cfg)
) -> list[dict]:
    """Live (non-backfill) articles per UTC hour."""
    return services.news_volume(cfg, hours)


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(
        title="btcml API",
        version=VERSION,
        description="Read-only API for the btcml dashboard.",
        generate_unique_id_function=lambda route: route.name,  # clean client method names
    )
    app.state.cfg = cfg
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.api_cors_origins),
        allow_methods=["GET"],
        allow_headers=["Authorization"],
    )

    @app.exception_handler(FileNotFoundError)
    def _not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    app.include_router(public)
    app.include_router(api)
    return app
