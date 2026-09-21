from __future__ import annotations

import argparse
import logging
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

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_config(args.config)

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


if __name__ == "__main__":
    main()
