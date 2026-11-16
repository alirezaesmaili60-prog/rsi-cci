"""Resumable, cancellable daily market collection and honest coverage reports.

The worker never calls Streamlit. One serial HTTP client keeps request volume
bounded; raw history is saved after each successful symbol, independently of
the report checkpoint. A network failure never silently uses an old cache.
"""

import copy
import json
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .data import DataError, HistoryCache, parse_history, prepare_prices, tehran_today, write_json_atomic
from .strategy import StrategyConfig, analyze
from .tsetmc import STOCK_TYPES, Instrument, TsetmcClient


@dataclass(frozen=True)
class ScanOptions:
    periods: tuple[int, ...] = (14, 20)
    entries: tuple[str, ...] = ("A1", "A2", "B")
    scope: str = "stocks"
    mode: str = "resume"  # resume / refresh / offline
    adjusted: bool = True
    close_basis: str = "final"
    include_today: bool = False
    lookback: int = 1
    cache_minutes: int = 60
    delay_seconds: float = 0.4
    failure_limit: int = 5

    def __post_init__(self):
        if not self.periods or len(set(self.periods)) != len(self.periods):
            raise ValueError("حداقل یک دوره CCI انتخاب کنید؛ دوره تکراری مجاز نیست.")
        for period in self.periods:
            StrategyConfig(period, self.entries)
        if self.scope not in ("stocks", "all") or self.mode not in ("resume", "refresh", "offline"):
            raise ValueError("نوع اسکن معتبر نیست.")
        if self.close_basis not in ("final", "close") or not 1 <= self.lookback <= 10:
            raise ValueError("مبنای قیمت یا بازه سیگنال معتبر نیست.")
        if not 1 <= self.cache_minutes <= 1440 or not 0 <= self.delay_seconds <= 10 or self.failure_limit < 1:
            raise ValueError("تنظیمات دریافت معتبر نیست.")


def summarize(result: pd.DataFrame, lookback: int) -> dict:
    """Keep last-bar state separate from recent threshold events."""
    last, recent = result.iloc[-1], result.tail(lookback)
    entry = recent.loc[recent["entry_conditions"].ne("")]
    buy = recent.loc[recent["action"].eq("BUY")]
    exits = recent.loc[recent["action"].eq("EXIT")]

    def event_date(events):
        return events.iloc[-1]["date"].date().isoformat() if not events.empty else ""

    return {"date": last["date"].date().isoformat(), "close": float(last["close"]),
            "rsi": float(last["rsi"]), "cci": float(last["cci"]),
            "entry_signal_date": event_date(entry),
            "entry_conditions": str(entry.iloc[-1]["entry_conditions"]) if not entry.empty else "",
            "buy_signal_date": event_date(buy), "exit_signal_date": event_date(exits),
            "exit_reason": str(exits.iloc[-1]["reason"]) if not exits.empty else "",
            "action": str(last["action"]), "holding": bool(last["holding"]),
            "reached_100": bool(last["reached_100"])}


def report_frame(snapshot: dict) -> pd.DataFrame:
    frame = pd.DataFrame(snapshot.get("rows", []))
    if frame.empty:
        return frame
    options = snapshot.get("options", {})
    for key in ("adjusted", "close_basis", "include_today", "lookback", "scope", "mode"):
        frame[key] = options.get(key)
    frame["entry_rules"] = ",".join(options.get("entries", []))
    frame["scan_started_at_utc"] = snapshot.get("started_at", "")
    frame["reference_date"] = snapshot.get("reference_date", "")
    frame["run_state"] = snapshot.get("state", "")
    return frame


def filter_results(snapshot: dict, kind: str = "entry", include_stale: bool = False) -> pd.DataFrame:
    frame = report_frame(snapshot)
    if frame.empty:
        return frame
    mask = frame["status"].eq("OK")
    if not include_stale:
        mask &= ~frame["stale"]
    if kind == "entry":
        mask &= frame["entry_conditions"].ne("")
    elif kind == "buy":
        mask &= frame["buy_signal_date"].ne("")
    elif kind == "exit":
        mask &= frame["exit_signal_date"].ne("")
    elif kind == "holding":
        mask &= frame["holding"]
    elif kind != "all":
        raise ValueError("Unknown result filter")
    return frame.loc[mask].sort_values(["symbol", "cci_period"]).reset_index(drop=True)


class MarketScanner:
    def __init__(self, cache: HistoryCache, client_factory=TsetmcClient):
        self.cache = cache
        self.directory = cache.directory / "market"
        self.client_factory = client_factory
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._state: dict = {}
        try:
            previous = json.loads((self.directory / "latest_scan.json").read_text(encoding="utf-8"))
            if previous.get("version") == 1 and isinstance(previous.get("rows"), list):
                self._state = previous
                self._state["restored"] = True
                if previous.get("state") in ("listing", "running"):
                    self._state["state"] = "interrupted"
        except (OSError, ValueError, AttributeError):
            pass

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def snapshot(self) -> dict:
        with self._lock:
            result = copy.deepcopy(self._state)
            result["running"] = self._running
        dates = [row["date"] for row in result.get("rows", []) if row["status"] == "OK"]
        today = result.get("today", "")
        include_today = result.get("options", {}).get("include_today", False)
        dates += [row["catalog_last_date"] for row in result.get("rows", [])
                  if row.get("catalog_last_date") and (row["catalog_last_date"] < today or
                                                       include_today and row["catalog_last_date"] == today)]
        reference = max(dates, default="")
        result["reference_date"] = reference
        for row in result.get("rows", []):
            row["stale"] = bool(row["status"] == "OK" and row["date"] < reference)
        return result

    def _begin(self, options: ScanOptions):
        with self._lock:
            if self._running:
                raise DataError("اسکن دیگری در حال اجراست؛ ابتدا آن را متوقف کنید یا منتظر پایان بمانید.")
            self._running = True
            self._cancel.clear()
            self._state = {"version": 1, "state": "listing", "restored": False,
                           "started_at": datetime.now(timezone.utc).isoformat(), "ended_at": "",
                           "today": tehran_today().isoformat(), "options": asdict(options),
                           "universe_total": 0, "total": 0, "excluded": 0, "unclassified": 0,
                           "processed": 0, "downloaded": 0, "cached": 0, "failed": 0,
                           "current": "", "error": "", "save_warning": "", "rows": []}

    def start(self, options: ScanOptions):
        self._begin(options)
        self._thread = threading.Thread(target=self._work, args=(options,), daemon=True,
                                        name="tsetmc-market-scan")
        self._thread.start()

    def run(self, options: ScanOptions, progress=None) -> dict:
        self._begin(options)
        self._work(options, progress)
        return self.snapshot()

    def cancel(self):
        self._cancel.set()

    def wait(self, timeout: float = 30):
        if self._thread:
            self._thread.join(timeout)

    def _persist(self):
        snapshot = self.snapshot()
        try:
            # Raw histories are already committed one by one; report checkpoint
            # is additional, so a report-write error must not discard them.
            write_json_atomic(self.directory / "latest_scan.json", snapshot)
        except (OSError, ValueError) as exc:
            with self._lock:
                self._state["save_warning"] = f"ذخیره گزارش ممکن نشد: {exc}"

    def _universe(self, client: TsetmcClient, options: ScanOptions) -> list[Instrument]:
        path = self.directory / "universe.json"
        if options.mode == "offline":
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                items = [Instrument(**row) for row in stored["instruments"]]
                if stored["version"] != 1 or not items:
                    raise ValueError("Invalid saved universe")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise DataError("فهرست بازار ذخیره نشده است؛ ابتدا یک دریافت اینترنتی انجام دهید.") from exc
            fetched_at = stored["fetched_at"]
        else:
            items = client.market_universe()
            fetched_at = datetime.now(timezone.utc).isoformat()
            write_json_atomic(path, {"version": 1, "fetched_at": fetched_at,
                                     "instruments": [asdict(item) for item in items]})
        chosen = items if options.scope == "all" else [item for item in items if item.security_type in STOCK_TYPES]
        with self._lock:
            self._state.update(universe_total=len(items), total=len(chosen),
                               excluded=len(items) - len(chosen),
                               unclassified=sum(not item.security_type for item in items),
                               universe_fetched_at=fetched_at)
        if not chosen:
            raise DataError("هیچ سهمی در محدوده انتخابی پیدا نشد؛ نوع اوراق و ساختار پاسخ TSETMC را بررسی کنید.")
        return chosen

    def _document(self, client, item: Instrument, options: ScanOptions) -> tuple[dict, str]:
        if options.mode == "offline":
            return self.cache.load(item.code), "offline"
        if options.mode == "resume":
            try:
                document = self.cache.load(item.code)
                fetched_at = datetime.fromisoformat(document["fetched_at"])
                age = datetime.now(timezone.utc) - fetched_at
                raw = parse_history(document["payload"])
                last_date = int(raw.iloc[-1]["date"].strftime("%Y%m%d"))
                today = int(self._state["today"].replace("-", ""))
                required_date = item.last_date if options.include_today or item.last_date < today else 0
                if timedelta(0) <= age <= timedelta(minutes=options.cache_minutes) and last_date >= required_date:
                    return document, "cache"
            except (DataError, TypeError):
                pass
        if self._cancel.wait(options.delay_seconds):
            raise InterruptedError()
        payload = client.history(item.code)
        return self.cache.save(item.code, item.symbol, payload), "download"

    @staticmethod
    def _empty_row(item: Instrument, period: int) -> dict:
        catalog_date = datetime.strptime(str(item.last_date), "%Y%m%d").date().isoformat() if item.last_date else ""
        return {"code": item.code, "symbol": item.symbol, "name": item.name, "market": item.market,
                "security_type": item.security_type, "classification_source": item.classification_source,
                "cci_period": period, "status": "PENDING", "error": "",
                "date": "", "catalog_last_date": catalog_date, "close": None, "rsi": None, "cci": None, "entry_conditions": "",
                "entry_signal_date": "", "buy_signal_date": "", "exit_signal_date": "", "exit_reason": "",
                "action": "", "holding": False, "reached_100": False, "source": "", "fetched_at": ""}

    def _work(self, options: ScanOptions, progress=None):
        try:
            with self.client_factory() as client:
                items = self._universe(client, options)
                with self._lock:
                    self._state["state"] = "running"
                    self._state["rows"] = [self._empty_row(item, period) for item in items for period in options.periods]
                    today = date.fromisoformat(self._state["today"])
                self._persist()
                if progress:
                    progress(self.snapshot())
                consecutive_failures = 0
                for index, item in enumerate(items):
                    if self._cancel.is_set():
                        break
                    with self._lock:
                        self._state["current"] = item.symbol
                    rows = [self._empty_row(item, period) for period in options.periods]
                    fetch_failed = False
                    try:
                        document, source = self._document(client, item, options)
                        with self._lock:
                            self._state["downloaded" if source == "download" else "cached"] += 1
                        consecutive_failures = 0
                    except InterruptedError:
                        break
                    except (DataError, OSError) as exc:
                        fetch_failed = True
                        consecutive_failures += 1
                        for row in rows:
                            row.update(status="FETCH_ERROR", error=str(exc))
                    if not fetch_failed:
                        for row in rows:
                            row.update(source=source, fetched_at=document["fetched_at"])
                        try:
                            prices = prepare_prices(parse_history(document["payload"]), adjusted=options.adjusted,
                                                    close_basis=options.close_basis, include_today=options.include_today,
                                                    today=today)
                            for row in rows:
                                if len(prices) < max(14, row["cci_period"]) + 2:
                                    row.update(status="INSUFFICIENT", error="سابقه برای این دوره کافی نیست.")
                                    continue
                                result = analyze(prices, StrategyConfig(row["cci_period"], options.entries))
                                row.update(summarize(result, options.lookback), status="OK")
                        except (DataError, ValueError, KeyError) as exc:
                            for row in rows:
                                row.update(status="ANALYSIS_ERROR", error=str(exc))
                    with self._lock:
                        start = index * len(options.periods)
                        self._state["rows"][start:start + len(rows)] = rows
                        self._state["processed"] += 1
                        self._state["failed"] += int(any(row["status"] != "OK" for row in rows))
                    if (index + 1) % 10 == 0:
                        self._persist()
                    if progress:
                        progress(self.snapshot())
                    if options.mode != "offline" and consecutive_failures >= options.failure_limit:
                        raise DataError(f"پس از {consecutive_failures} خطای متوالی دریافت، اسکن متوقف شد. "
                                        "اتصال را بررسی و دوباره ادامه دهید؛ داده‌های موفق ذخیره شده‌اند.")
                with self._lock:
                    self._state["state"] = "cancelled" if self._cancel.is_set() else "done"
        except KeyboardInterrupt:
            self._cancel.set()
            with self._lock:
                self._state["state"] = "cancelled"
        except Exception as exc:
            with self._lock:
                self._state["state"] = "failed"
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                self._state["ended_at"] = datetime.now(timezone.utc).isoformat()
                self._state["current"] = ""
                self._running = False
            self._persist()
