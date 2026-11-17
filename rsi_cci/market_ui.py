"""Streamlit controls for a background market scan; worker has no UI calls."""

from pathlib import Path

import streamlit as st

from .data import DataError, HistoryCache
from .market import MarketScanner, ScanOptions, filter_results, report_frame
from .timeframes import TIMEFRAMES, Session


@st.cache_resource
def market_scanner(directory: str) -> MarketScanner:
    return MarketScanner(HistoryCache(Path(directory)))


STATE_LABELS = {"listing": "دریافت فهرست بازار", "running": "در حال دریافت و تحلیل",
                "done": "پایان پردازش", "cancelled": "توقف به درخواست شما",
                "failed": "توقف با خطا", "interrupted": "اجرای قبلی ناتمام است"}
STATUS_LABELS = {"OK": "موفق", "PENDING": "بررسی‌نشده", "FETCH_ERROR": "خطای دریافت",
                 "ANALYSIS_ERROR": "خطای تحلیل", "INTRADAY_ERROR": "خطای داده درون‌روزی", "INSUFFICIENT": "سابقه ناکافی"}
HEADERS = {"symbol": "نماد", "code": "شناسه", "name": "نام", "market": "بازار",
           "cci_period": "دوره CCI", "timeframe": "تایم‌فریم", "date": "آخرین کندل", "close": "قیمت مبنا",
           "rsi": "RSI آخرین کندل", "cci": "CCI آخرین کندل", "entry_conditions": "شرط ورود",
           "entry_signal_date": "تاریخ شرط ورود", "buy_signal_date": "تاریخ ورود مجاز راهبرد",
           "exit_signal_date": "تاریخ خروج", "exit_reason": "علت خروج",
           "holding": "داخل معامله فرضی", "stale": "تاریخ قدیمی‌تر از بازار",
           "status": "وضعیت", "error": "توضیح خطا", "source": "منبع دریافت",
           "fetched_at": "زمان دریافت UTC", "bar_minutes": "مدت کندل (دقیقه)", "short_session_bar": "کندل کوتاه پایان جلسه",
           "signal_candle": "کندل سیگنال", "signal_bars_ago": "فاصله سیگنال (کندل)", "signal_date": "زمان سیگنال",
           "matched_conditions": "شرط برقرارشده", "signal_rsi": "RSI هنگام سیگنال", "signal_cci": "CCI هنگام سیگنال"}


def render_market(cache: HistoryCache, entries: tuple[str, ...], adjusted: bool,
                  basis: str, include_today: bool, *, intraday_days=30, session=None):
    session = session or Session()
    scanner = market_scanner(str(cache.directory.resolve()))
    st.subheader("دریافت و اسکن کل بازار")
    st.write("با یک شروع، فهرست نمادها و تمام سابقه روزانه موجود هر نماد دریافت، ذخیره و بررسی می‌شود.")
    st.caption("محدوده پیش‌فرض: سهام بورس، فرابورس و پایه در فهرست دیده‌بان TSETMC؛ "
               "روزانه و هفتگی از سابقه روزانه، و ۲ساعته از ریزمعاملات ساخته می‌شوند. نمادهای حذف‌شده از فهرست پوشش داده نمی‌شوند.")
    scopes = {"تمام سهام بورس، فرابورس و پایه": "stocks", "تمام اوراق موجود در فهرست (شامل صندوق و حق تقدم)": "all"}
    modes = {"دریافت و ادامه با استفاده از فایل تازه": "resume", "به‌روزرسانی اجباری همه نمادها": "refresh",
             "تحلیل آفلاین فایل‌های قبلی": "offline"}
    with st.form("market_scan_form"):
        left, right = st.columns(2)
        scope = left.selectbox("محدوده بازار", list(scopes))
        mode = right.selectbox("روش جمع‌آوری بازار", list(modes))
        periods = left.multiselect("دوره‌های CCI برای اسکن بازار", [14, 20], default=[14, 20])
        timeframes = right.multiselect("تایم‌فریم‌های اسکن بازار", list(TIMEFRAMES), default=["daily"], format_func=TIMEFRAMES.get)
        previous_candles = right.number_input("حداکثر کندل قبل در اسکن (۰: فقط کندل فعلی)", 0, 9, 3)
        cache_minutes = left.number_input("حداکثر عمر فایل برای ادامه دریافت (دقیقه)", 1, 1440, 60)
        st.caption("روش‌های ورود، مبنای قیمت، تعدیل و تأیید پایان جلسه از نوار کناری گرفته می‌شوند. "
                   "تنظیمات هر اسکن تا پایان همان اجرا ثابت می‌مانند.")
        st.caption(f"۲ساعته: {intraday_days or 'همه'} روز معاملاتی؛ جلسه {session.start} تا {session.end}. "
                   "دریافت اول درون‌روزی کل بازار ممکن است بسیار طولانی باشد؛ فایل هر روز برای ادامه حفظ می‌شود. "
                   "مبنای ۲ساعته همیشه آخرین معامله است.")
        submitted = st.form_submit_button("شروع دریافت و اسکن بازار", type="primary")
    if submitted:
        try:
            options = ScanOptions(periods=tuple(periods), entries=entries, scope=scopes[scope], mode=modes[mode],
                                  adjusted=adjusted, close_basis=basis, include_today=include_today,
                                  lookback=int(previous_candles) + 1, cache_minutes=int(cache_minutes), timeframes=tuple(timeframes),
                                  intraday_days=intraday_days, session_start=session.start, session_end=session.end,
                                  include_short_bar=session.include_short)
            scanner.start(options)
        except (DataError, ValueError) as exc:
            st.error(str(exc))
    # Poll while downloading, then redraw once without a timer. Fixed result
    # tables and download controls should not keep refreshing every second.
    polling = scanner.running
    st.fragment(run_every=1 if polling else None)(scan_progress)(scanner, polling)


def scan_progress(scanner: MarketScanner, polling: bool = False):
    snapshot = scanner.snapshot()
    if polling and not snapshot["running"]:
        st.rerun(scope="app")
    if not snapshot.get("state"):
        st.info("برای بررسی همه نمادها، «شروع دریافت و اسکن بازار» را بزنید. دریافت اول ممکن است زمان‌بر باشد.")
        return
    options = snapshot["options"]
    if snapshot.get("restored"):
        st.warning("این گزارش از اجرای قبلی بارگذاری شده است و نتیجه زنده نیست. برای ادامه یا به‌روزرسانی، اسکن را شروع کنید.")
    if options["mode"] == "offline":
        st.warning("اسکن آفلاین: هیچ داده تازه‌ای از TSETMC دریافت نمی‌شود.")
    st.markdown(f"**وضعیت: {STATE_LABELS.get(snapshot['state'], snapshot['state'])}**")
    st.caption(f"شروع (UTC): {snapshot['started_at']} | دوره‌ها: {options['periods']} | "
               f"روش‌های ورود: {', '.join(options['entries'])} | بازه اسکن: کندل فعلی + {options['lookback'] - 1} کندل قبل | "
               + ("تعدیل‌شده" if options["adjusted"] else "خام")
               + (" | پایانی" if options["close_basis"] == "final" else " | آخرین معامله")
               + (" | روز جاری: تأییدشده" if options["include_today"] else " | روز جاری: حذف‌شده"))
    total, processed = snapshot["total"], snapshot["processed"]
    st.progress(processed / total if total else 0,
                text=f"{processed} از {total} نماد پردازش شده | {snapshot.get('current', '')}")
    if snapshot["running"] and st.button("توقف اسکن بازار", key="cancel_market_scan"):
        scanner.cancel()
        st.info("درخواست توقف ثبت شد؛ درخواست در حال انجام تمام می‌شود و فایل‌های موفق باقی می‌مانند.")
    if snapshot.get("error"):
        st.error(snapshot["error"])
    if snapshot.get("save_warning"):
        st.warning(snapshot["save_warning"])
    if snapshot.get("intraday_warning"):
        st.warning(snapshot["intraday_warning"])
    st.caption("تایم‌فریم‌های این اسکن: " + "، ".join(TIMEFRAMES.get(tf, tf) for tf in options.get("timeframes", ["daily"]))
               + f" | روز درون‌روزی جدید: {snapshot.get('intraday_downloaded', 0)} | فایل روز درون‌روزی: {snapshot.get('intraday_cached', 0)}")
    if "2h" in options.get("timeframes", []):
        st.caption(f"تنظیمات ثبت‌شده این گزارش ۲ساعته: {options.get('intraday_days', 30) or 'همه'} روز؛ "
                   f"جلسه {options.get('session_start', '09:00')}–{options.get('session_end', '12:30')}؛ "
                   + ("با کندل کوتاه پایان جلسه" if options.get("include_short_bar", True) else "فقط کندل‌های کامل ۱۲۰دقیقه‌ای"))
    st.caption(f"فهرست منبع: {snapshot['universe_total']} نماد | انتخاب‌شده: {total} | "
               f"خارج از محدوده: {snapshot['excluded']} | دانلود جدید: {snapshot['downloaded']} | "
               f"استفاده از فایل: {snapshot['cached']}")
    if snapshot["unclassified"] and options["scope"] == "stocks":
        st.warning(f"نوع اوراق {snapshot['unclassified']} نماد مشخص نیست؛ در اسکن سهام وارد نشده‌اند. "
                   "این محدودیت در فایل فهرست بازار ثبت شده است.")
    if snapshot["failed"] or processed < total:
        st.warning(f"پوشش هنوز کامل نیست: {snapshot['failed']} نماد دارای خطا/سابقه ناکافی و "
                   f"{total - processed} نماد بررسی‌نشده است. نتایج زیر فقط مربوط به تحلیل‌های موفق‌اند.")
    frame = report_frame(snapshot)
    if frame.empty:
        return
    references = snapshot.get("reference_dates", {"daily": snapshot.get("reference_date", "")})
    st.caption("تاریخ مرجع مستقل هر تایم‌فریم: " + " | ".join(TIMEFRAMES.get(tf, tf) + ": " + (stamp or "نامشخص") for tf, stamp in references.items())
               + ". سیگنال‌های قدیمی‌تر به‌صورت پیش‌فرض از نتایج کنار گذاشته می‌شوند.")
    stale_count = frame.loc[frame["status"].eq("OK") & frame["stale"], "code"].nunique()
    if stale_count:
        st.warning(f"آخرین کندل {stale_count} نماد از تاریخ مرجع قدیمی‌تر است؛ توقف نماد یا تأخیر داده را بررسی کنید.")
    kinds = {"شرط ورود تازه برقرار است": "entry", "ورود مجاز طبق سابقه راهبرد": "buy",
             "خروج تازه": "exit", "داخل معامله فرضی": "holding", "تمام تحلیل‌های موفق": "all"}
    st.markdown("#### شناسایی سهم‌ها در تایم‌فریم و بازه انتخابی")
    kind = st.selectbox("نوع نتیجه بازار", list(kinds), key="market_result_kind")
    frame_choices = ["all"] + list(options.get("timeframes", ["daily"]))
    selected_frame = st.selectbox("نمایش تایم‌فریم نتیجه", frame_choices,
                                  format_func=lambda tf: "همه تایم‌فریم‌ها" if tf == "all" else TIMEFRAMES[tf], key="market_result_timeframe")
    successful = frame.loc[frame["status"].eq("OK")]
    legacy = not successful.empty and not successful["window_history_available"].all()
    if st.session_state.get("market_result_scan_id") != snapshot["started_at"]:
        st.session_state["market_result_scan_id"] = snapshot["started_at"]
        st.session_state["market_result_previous_candles"] = options["lookback"] - 1
    previous = int(st.number_input("نمایش شرط از کندل فعلی تا چند کندل قبل؟", 0, 9,
                                   key="market_result_previous_candles", disabled=legacy))
    if legacy:
        st.warning("این گزارش قدیمی سابقه فاصله سیگنال‌ها را ندارد؛ نتایج قبلی حفظ شده‌اند. "
                   "برای تغییر بازه، یک بار اسکن آفلاین یا ادامه را با نسخه جدید اجرا کنید.")
    frame_label = "همه تایم‌فریم‌ها" if selected_frame == "all" else TIMEFRAMES[selected_frame]
    st.caption(f"نمایش {frame_label}: کندل صفر (آخرین کندل کامل‌شده) تا {previous} کندل قبل، شامل هر دو سر بازه. "
               "تغییر این بازه و فیلتر تایم‌فریم، دانلود دوباره لازم ندارد. کندل در حال تشکیل سیگنال قطعی محسوب نمی‌شود.")
    include_stale = st.checkbox("نتایج با تاریخ قدیمی‌تر از بازار هم نمایش داده شوند", key="market_include_stale")
    matches = filter_results(snapshot, kinds[kind], include_stale, None if selected_frame == "all" else selected_frame,
                              None if legacy else previous)
    matches["signal_candle"] = matches["signal_bars_ago"].map(
        lambda n: "نامشخص" if n is None or n != n else "کندل فعلی" if n == 0 else f"{int(n)} کندل قبل")
    cols = st.columns(3)
    cols[0].metric("نماد پردازش‌شده", str(processed))
    cols[1].metric("نماد دارای خطا یا سابقه ناکافی", str(snapshot["failed"]))
    cols[2].metric("نماد در نتیجه انتخابی", str(matches["code"].nunique()))
    st.caption("«شرط ورود» عبور اندیکاتورها را مستقل از وضعیت قبلی نشان می‌دهد؛ "
               "«ورود مجاز» فقط ورودهای مجاز موتور راهبرد است. دو دوره CCI مستقل‌اند و هرکدام یک ردیف دارند.")
    identifiers = ["symbol", "code", "market", "timeframe", "cci_period"]
    signal_columns = ["signal_candle", "signal_date", "matched_conditions", "signal_rsi", "signal_cci", "holding", "exit_signal_date"]
    current_columns = ["date", "rsi", "cci", "entry_conditions", "buy_signal_date", "exit_signal_date", "exit_reason", "holding", "stale"]
    columns = identifiers + (current_columns if kinds[kind] in ("all", "holding") else signal_columns)
    if matches.empty:
        st.info("در داده‌های موفق فعلی، نمادی با این فیلتر پیدا نشد.")
    else:
        display = matches[[c for c in columns if c in matches]].copy()
        display["timeframe"] = display["timeframe"].map(TIMEFRAMES)
        st.dataframe(display.round({"rsi": 2, "cci": 2, "signal_rsi": 2, "signal_cci": 2}).rename(columns=HEADERS), width="stretch", hide_index=True)
        with st.expander("مقادیر فعلی و جزئیات نمادهای انتخابی"):
            details = matches[[c for c in identifiers + current_columns + ["bar_minutes", "short_session_bar"] if c in matches]].copy()
            details["timeframe"] = details["timeframe"].map(TIMEFRAMES)
            st.dataframe(details.round({"rsi": 2, "cci": 2}).rename(columns=HEADERS), width="stretch", hide_index=True)
    st.download_button("دانلود نتایج انتخابی بازار (CSV)", matches.to_csv(index=False).encode("utf-8-sig"),
                       "market_matches.csv", "text/csv", key="market_matches_download", on_click="ignore")
    st.download_button("دانلود گزارش کامل پوشش و خطاها (CSV)", frame.to_csv(index=False).encode("utf-8-sig"),
                       "market_coverage.csv", "text/csv", key="market_coverage_download", on_click="ignore")
    with st.expander("پوشش تمام نمادها و جزئیات خطاها"):
        coverage = frame[["symbol", "code", "timeframe", "cci_period", "status", "date", "source", "fetched_at", "error"]].copy()
        coverage["status"] = coverage["status"].map(STATUS_LABELS)
        st.dataframe(coverage.rename(columns=HEADERS), width="stretch", hide_index=True)
    st.caption("اطلاعات خام هر نماد پس از دریافت روی دیسک ذخیره می‌شود؛ "
               "گزارش هر ۱۰ نماد و هنگام پایان ذخیره می‌شود. پس از توقف، دوباره حالت ادامه را اجرا کنید.")
