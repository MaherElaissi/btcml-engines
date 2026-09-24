from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import INTERVAL_SECONDS, load_config


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="btcml")
    p.add_argument("--config", type=Path, help="path to config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", help="download historical klines from the Binance archive")
    d.add_argument("--interval", choices=["1s", "1m"], help="default: all in [history]")
    d.add_argument("--months", type=int, help="override months of history")

    f = sub.add_parser("fill-recent", help="fetch candles newer than the archive via REST")
    f.add_argument("--interval", choices=["1s", "1m"], required=True)

    c = sub.add_parser("check", help="data quality report")
    c.add_argument("--interval", choices=list(INTERVAL_SECONDS), default="1m")

    b = sub.add_parser("build-bars", help="build 5m/10m/1h candles from 1m")
    b.add_argument("--timeframe", choices=["5m", "10m", "1h"], help="default: all")

    n = sub.add_parser("news", help="run the RSS collector")
    n.add_argument("--once", action="store_true", help="poll every feed once and exit")

    sv = sub.add_parser("serve", help="run the read-only dashboard API")
    sv.add_argument("--host", help="default: [api] host in config.toml")
    sv.add_argument("--port", type=int, help="default: [api] port in config.toml")

    o = sub.add_parser("openapi", help="write the API's OpenAPI spec (for the dashboard client)")
    o.add_argument("-o", "--output", type=Path, help="default: openapi.json next to config.toml")

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_config(args.config)
    try:
        _run(args, cfg)
    except FileNotFoundError as exc:  # missing data: print the hint, not a traceback
        sys.exit(str(exc))


def _run(args: argparse.Namespace, cfg) -> None:
    if args.cmd == "download":
        from .data.download import download_interval

        for interval, months in cfg.history_months.items():
            if args.interval and args.interval != interval:
                continue
            stats = download_interval(cfg, interval, args.months or months)
            print(f"{interval}: {dict(stats)}")
    elif args.cmd == "fill-recent":
        from .data.download import fill_recent

        print(f"{args.interval}: wrote {fill_recent(cfg, args.interval):,} recent candles")
    elif args.cmd == "check":
        from .data.quality import check, format_report

        print(format_report(check(cfg, args.interval)))
    elif args.cmd == "build-bars":
        from .data.resample import build_bars

        for tf in [args.timeframe] if args.timeframe else ["5m", "10m", "1h"]:
            print(f"{tf}: {build_bars(cfg, tf):,} bars")
    elif args.cmd == "news":
        from .news.collector import run

        try:
            run(cfg, once=args.once)
        except KeyboardInterrupt:
            print("stopped")
    elif args.cmd == "serve":
        import uvicorn

        from .api import create_app
        from .api.app import TOKEN_ENV

        host = args.host or cfg.api_host
        if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get(TOKEN_ENV):
            sys.exit(f"Refusing to listen on {host} without {TOKEN_ENV} set.")
        uvicorn.run(create_app(cfg), host=host, port=args.port or cfg.api_port)
    elif args.cmd == "openapi":
        from .api import create_app

        out = args.output or cfg.root / "openapi.json"
        out.write_text(json.dumps(create_app(cfg).openapi(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
