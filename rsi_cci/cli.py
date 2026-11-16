"""Optional command-line collection and analysis, including scheduled local use."""

import argparse
import os
from pathlib import Path

from .data import DataError, HistoryCache, parse_history, prepare_prices
from .strategy import StrategyConfig, analyze
from .tsetmc import TsetmcClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Local TSETMC RSI/CCI collector")
    parser.add_argument("action", choices=["search", "fetch", "analyze"])
    parser.add_argument("value", help="Symbol for search, numeric insCode otherwise")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("RSI_CCI_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))))
    parser.add_argument("--cci", type=int, choices=[14, 20], default=14)
    parser.add_argument("--raw", action="store_true", help="Use unadjusted prices")
    parser.add_argument("--last-trade", action="store_true", help="Use last trade instead of closing price")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cache = HistoryCache(args.data_dir)
    try:
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
