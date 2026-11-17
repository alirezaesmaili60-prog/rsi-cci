"""Optional command-line collection and analysis, including scheduled local use."""

import argparse
import os
from pathlib import Path

import pandas as pd

from .data import DataError, HistoryCache, parse_history
from .market import MarketScanner, ScanOptions, filter_results, report_frame
from .strategy import StrategyConfig, analyze
from .tsetmc import TsetmcClient
from .timeframes import Session, TIMEFRAMES
from .workflow import prepare_timeframe


def main() -> int:
    parser = argparse.ArgumentParser(description="Local TSETMC RSI/CCI collector")
    parser.add_argument("action", choices=["search", "fetch", "analyze", "scan"])
    parser.add_argument("value", nargs="?", help="Symbol for search, numeric insCode for fetch/analyze")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("RSI_CCI_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))))
    parser.add_argument("--cci", type=int, choices=[14, 20], default=14)
    parser.add_argument("--raw", action="store_true", help="Use unadjusted prices")
    parser.add_argument("--last-trade", action="store_true", help="Use last trade instead of closing price")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--both-cci", action="store_true", help="Scan CCI14 and CCI20 independently")
    parser.add_argument("--scope", choices=["stocks", "all"], default="stocks")
    parser.add_argument("--offline", action="store_true", help="Scan cached universe and histories only")
    parser.add_argument("--refresh", action="store_true", help="Force refresh of all histories in scan")
    parser.add_argument("--lookback", type=int, default=1, help="Scan signals in last N bars (1..10)")
    parser.add_argument("--previous-candles", type=int, choices=range(10), help="Include current bar and N previous bars (0..9)")
    parser.add_argument("--cache-minutes", type=int, default=60)
    parser.add_argument("--timeframe", choices=list(TIMEFRAMES), default="daily", help="Single-symbol or default scan timeframe")
    parser.add_argument("--timeframes", nargs="+", choices=list(TIMEFRAMES), help="Multiple scan timeframes")
    parser.add_argument("--intraday-days", type=int, default=30, help="Trading days of trades for 2h; 0 means all available days")
    parser.add_argument("--session-start", default="09:00")
    parser.add_argument("--session-end", default="12:30")
    parser.add_argument("--full-bars-only", action="store_true", help="Exclude the shortened final session candle")
    args = parser.parse_args()
    if args.action != "scan" and not args.value:
        parser.error("search/fetch/analyze require a symbol or instrument code")
    if args.offline and args.refresh:
        parser.error("--offline and --refresh cannot be combined")
    cache = HistoryCache(args.data_dir)
    try:
        session = Session(args.session_start, args.session_end, not args.full_bars_only)
        if args.intraday_days < 0:
            raise DataError("--intraday-days must be zero or positive")
        if args.action == "scan":
            options = ScanOptions(periods=(14, 20) if args.both_cci else (args.cci,), scope=args.scope,
                                  mode="offline" if args.offline else "refresh" if args.refresh else "resume",
                                  adjusted=not args.raw, close_basis="close" if args.last_trade else "final",
                                  lookback=args.lookback if args.previous_candles is None else args.previous_candles + 1,
                                  cache_minutes=args.cache_minutes,
                                  timeframes=tuple(args.timeframes or [args.timeframe]), intraday_days=args.intraday_days,
                                  session_start=session.start, session_end=session.end, include_short_bar=session.include_short)
            print("Collecting market universe and daily histories...", flush=True)

            def progress(snapshot):
                print(f"{snapshot['processed']}/{snapshot['total']} processed; "
                      f"downloaded={snapshot['downloaded']}, cached={snapshot['cached']}, "
                      f"failed={snapshot['failed']}", flush=True)

            snapshot = MarketScanner(cache).run(options, progress)
            frame = report_frame(snapshot)
            output = args.output or (args.data_dir / "market" / "latest_scan.csv")
            output.parent.mkdir(parents=True, exist_ok=True)
            if frame.empty:
                # Replace the previous run's CSV even if universe loading failed;
                # its older candidates must not look like results of this run.
                frame = pd.DataFrame([{"code": "", "symbol": "", "status": "SCAN_ERROR",
                                       "error": snapshot.get("error", ""), "run_state": snapshot["state"],
                                       "scan_started_at_utc": snapshot["started_at"]}])
                matches = frame.iloc[:0]
            else:
                matches = filter_results(snapshot)
            frame.to_csv(output, index=False, encoding="utf-8-sig")
            matches.to_csv(output.with_name(output.stem + "_matches.csv"), index=False, encoding="utf-8-sig")
            print(f"Report: {output}; matching symbols: {matches['code'].nunique()}")
            print(f"State: {snapshot['state']}; reference date: {snapshot.get('reference_date', '')}")
            if snapshot.get("error"):
                print("Error: " + snapshot["error"])
            complete = (snapshot["state"] == "done" and not snapshot["failed"] and not snapshot["save_warning"]
                        and (options.scope == "all" or not snapshot["unclassified"]))
            return 0 if complete else 1
        if args.action == "search":
            with TsetmcClient() as client:
                for match in client.search(args.value):
                    print(match.label)
            return 0
        if args.action == "fetch":
            with TsetmcClient() as client:
                document = cache.save(args.value, args.value, client.history(args.value))
                if args.timeframe == "2h":
                    prepare_timeframe(parse_history(document["payload"]), "2h", adjusted=not args.raw,
                                      close_basis="close" if args.last_trade else "final", cache=cache, code=args.value,
                                      client=client, mode="refresh" if args.refresh else "resume", session=session,
                                      intraday_days=args.intraday_days,
                                      progress=lambda day, source, number, total: print(f"Intraday {day}: {number}/{total} ({source})", flush=True))
            print(f"Saved {cache.path(args.value)} at {document['fetched_at']}")
        else:
            document = cache.load(args.value)
        prices = prepare_timeframe(parse_history(document["payload"]), args.timeframe, adjusted=not args.raw,
                                   close_basis="close" if args.last_trade else "final", cache=cache, code=args.value,
                                   session=session, intraday_days=args.intraday_days)
        if len(prices) < max(14, args.cci) + 2:
            raise DataError("Insufficient completed candles for the selected timeframe")
        result = analyze(prices, StrategyConfig(args.cci))
        result["source"] = "TSETMC"
        result["instrument_code"] = args.value
        result["fetched_at_utc"] = document["fetched_at"]
        result["adjusted"] = not args.raw
        result["close_basis"] = "close" if args.last_trade or args.timeframe == "2h" else "final"
        result["timeframe"] = args.timeframe
        result["timezone"] = "Asia/Tehran"
        result["intraday_days"] = args.intraday_days
        result["session_start"], result["session_end"] = session.start, session.end
        result["include_short_bar"] = session.include_short
        result["cci_period"] = args.cci
        result["entry_rules"] = "A1,A2,B"
        result["include_today"] = False
        suffix = "" if args.timeframe == "daily" else "_" + args.timeframe
        output = args.output or (args.data_dir / f"{document['code']}_cci{args.cci}{suffix}.csv")
        output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(output, index=False, encoding="utf-8-sig")
        print(result[["date", "rsi", "cci", "action", "reason", "holding"]].tail(1).to_string(index=False))
        print(f"Analysis saved: {output}")
        return 0
    except (DataError, OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
