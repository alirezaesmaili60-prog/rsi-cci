"""Optional command-line collection and analysis, including scheduled local use."""

import argparse
import os
from pathlib import Path

import pandas as pd

from .data import DataError, HistoryCache, parse_history, prepare_prices
from .market import MarketScanner, ScanOptions, filter_results, report_frame
from .strategy import StrategyConfig, analyze
from .tsetmc import TsetmcClient


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
    parser.add_argument("--cache-minutes", type=int, default=60)
    args = parser.parse_args()
    if args.action != "scan" and not args.value:
        parser.error("search/fetch/analyze require a symbol or instrument code")
    if args.offline and args.refresh:
        parser.error("--offline and --refresh cannot be combined")
    cache = HistoryCache(args.data_dir)
    try:
        if args.action == "scan":
            options = ScanOptions(periods=(14, 20) if args.both_cci else (args.cci,), scope=args.scope,
                                  mode="offline" if args.offline else "refresh" if args.refresh else "resume",
                                  adjusted=not args.raw, close_basis="close" if args.last_trade else "final",
                                  lookback=args.lookback, cache_minutes=args.cache_minutes)
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
            print(f"Saved {cache.path(args.value)} at {document['fetched_at']}")
        else:
            document = cache.load(args.value)
        prices = prepare_prices(parse_history(document["payload"]), adjusted=not args.raw,
                                close_basis="close" if args.last_trade else "final")
        if len(prices) < max(14, args.cci) + 2:
            raise DataError("Insufficient completed trading days")
        result = analyze(prices, StrategyConfig(args.cci))
        result["source"] = "TSETMC"
        result["instrument_code"] = args.value
        result["fetched_at_utc"] = document["fetched_at"]
        result["adjusted"] = not args.raw
        result["close_basis"] = "close" if args.last_trade else "final"
        result["cci_period"] = args.cci
        result["entry_rules"] = "A1,A2,B"
        result["include_today"] = False
        output = args.output or (args.data_dir / f"{document['code']}_cci{args.cci}.csv")
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
