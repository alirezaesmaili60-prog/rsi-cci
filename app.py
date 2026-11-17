"""Persian local browser UI. Start with: python -m streamlit run app.py"""

import os
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from rsi_cci.data import DataError, HistoryCache, parse_history, prepare_prices, tehran_today
from rsi_cci.demo import demo_history, demo_two_hour
from rsi_cci.market_ui import render_market
from rsi_cci.strategy import StrategyConfig, analyze
from rsi_cci.tsetmc import TsetmcClient
from rsi_cci.timeframes import Session, TIMEFRAMES, label_timestamp
from rsi_cci.workflow import prepare_timeframe


ROOT = Path(__file__).resolve().parent
CACHE = HistoryCache(Path(os.environ.get("RSI_CCI_DATA_DIR", str(ROOT / "data"))))
ACTION = {"BUY": "سیگنال ورود", "EXIT": "سیگنال خروج", "": "بدون سیگنال تازه"}
REASONS = {"CCI_DOWN_100": "عبور نزولی CCI از ۱۰۰",
           "CCI_STOP_MINUS_100": "خروج زیان: عبور نزولی از منفی ۱۰۰ پیش از رسیدن به بالای ۱۰۰"}
HEADERS = {"date": "تاریخ میلادی", "open": "بازگشایی", "high": "بیشترین", "low": "کمترین",
           "close": "قیمت مبنای تحلیل", "final": "پایانی", "last_trade": "آخرین معامله",
           "volume": "حجم", "rsi": "RSI14", "cci": "CCI", "action": "سیگنال",
           "reason": "علت", "holding": "در معامله فرضی", "reached_100": "عبور از ۱۰۰ در معامله"}


def chart(result: pd.DataFrame, days: int, cci_period: int) -> go.Figure:
    frame = result.tail(days).copy()
    if frame["date"].dt.tz is not None:
        frame["date"] = frame["date"].dt.tz_convert("Asia/Tehran").dt.tz_localize(None)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.035,
                        row_heights=[0.52, 0.24, 0.24])
    fig.add_trace(go.Scatter(x=frame["date"], y=frame["close"], name="قیمت مبنای تحلیل",
                             line={"color": "#1c5678", "width": 2}), row=1, col=1)
    for action, symbol, color, label in [("BUY", "triangle-up", "#008b71", "ورود"),
                                          ("EXIT", "triangle-down", "#ce4b48", "خروج")]:
        events = frame.loc[frame["action"].eq(action)]
        fig.add_trace(go.Scatter(x=events["date"], y=events["close"], mode="markers", name=label,
                                 marker={"symbol": symbol, "size": 12, "color": color}), row=1, col=1)
    fig.add_trace(go.Scatter(x=frame["date"], y=frame["rsi"], name="RSI14",
                             line={"color": "#946a18"}), row=2, col=1)
    fig.add_hline(y=50, line_dash="dot", line_color="#a9afb9", row=2, col=1)
    fig.update_yaxes(range=[0, 100], row=2, col=1)
    fig.add_trace(go.Scatter(x=frame["date"], y=frame["cci"], name=f"CCI{cci_period}",
                             line={"color": "#087f75"}), row=3, col=1)
    for threshold in (-100, 0, 100):
        fig.add_hline(y=threshold, line_dash="dot", line_color="#a9afb9", row=3, col=1)
    fig.update_layout(height=670, margin={"l": 12, "r": 12, "t": 25, "b": 12},
                      paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                      font={"family": "Tahoma, Arial", "size": 12},
                      hovermode="x unified", legend={"orientation": "h", "y": 1.09})
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#edf0f5")
    return fig


def show_analysis(document: dict, config: StrategyConfig, adjusted: bool,
                  basis: str, include_today: bool, days: int, *, timeframe="daily", intraday_days=30, session=None):
    session = session or Session()
    is_demo = document.get("demo", False)
    if is_demo:
        st.warning("حالت آزمایشی — تمام قیمت‌های زیر ساختگی‌اند و از TSETMC دریافت نشده‌اند.")
    else:
        st.caption(f"منبع: TSETMC | شناسه: {document['code']} | زمان دریافت: {document['fetched_at']} (UTC)")
        st.info("این نسخه داده ذخیره‌شده را تحلیل می‌کند. برای بررسی تغییرات بازار، «دریافت و ذخیره» را دوباره بزنید.")
    raw = parse_history(document["payload"])
    if is_demo and timeframe == "2h":
        daily = prepare_prices(raw, adjusted=adjusted, close_basis=basis, include_today=include_today)
        prices = demo_two_hour(daily, session, intraday_days)
    else:
        prices = prepare_timeframe(raw, timeframe, adjusted=adjusted, close_basis=basis, include_today=include_today,
                                   cache=CACHE, code=document["code"], intraday_days=intraday_days, session=session)
    if len(prices) < max(14, config.cci_period) + 2:
        st.error("تعداد کندل‌های کامل برای این تایم‌فریم کافی نیست؛ حداقل ۲۲ کندل توصیه می‌شود.")
        return
    result = analyze(prices, config)
    latest = result.iloc[-1]
    label = str(document.get("label", "نماد"))
    st.subheader(label)
    st.caption(f"آخرین کندل: {label_timestamp(latest['date'], timeframe)} | {TIMEFRAMES[timeframe]} | CCI{config.cci_period} | "
               + ("قیمت تعدیل‌شده" if adjusted else "قیمت خام")
               + " | مبنا: " + ("قیمت پایانی" if basis == "final" and timeframe != "2h" else "آخرین معامله"))
    stale_days = 12 if timeframe == "weekly" else 5
    if not is_demo and latest["date"].date() < tehran_today() - timedelta(days=stale_days):
        st.warning(f"آخرین کندل بیش از {stale_days} روز تقویمی فاصله دارد؛ توقف نماد، تعطیلات یا قدیمی‌بودن داده را بررسی کنید.")
    columns = st.columns(4)
    columns[0].metric("سیگنال آخرین روز" if timeframe == "daily" else "سیگنال آخرین کندل", {"BUY": "ورود", "EXIT": "خروج", "": "ندارد"}[latest["action"]])
    columns[1].metric("RSI 14", f"{latest['rsi']:.2f}")
    columns[2].metric(f"CCI {config.cci_period}", f"{latest['cci']:.2f}")
    columns[3].metric("وضعیت فرضی راهبرد", "داخل معامله" if latest["holding"] else "خارج معامله")
    st.plotly_chart(chart(result, days, config.cci_period), width="stretch")
    st.caption("محاسبات روی سابقه انتخابی انجام می‌شود؛ بازه نمودار فقط نمایش را عوض می‌کند. قیمت‌ها ریال و ساعت‌ها به وقت تهران‌اند.")
    st.write("وضعیت معامله حاصل بازپخش سیگنال‌ها از ابتدای سابقه است؛ وضعیت واقعی سبد شما محسوب نمی‌شود.")
    events = result.loc[result["action"].ne(""), ["date", "close", "rsi", "cci", "action", "reason"]].copy()
    st.markdown("#### تاریخچه سیگنال‌ها")
    if events.empty:
        st.info("در این سابقه سیگنالی با تنظیمات انتخاب‌شده تشکیل نشده است.")
    else:
        events["action"] = events["action"].map(ACTION)
        events["reason"] = events["reason"].map(lambda x: REASONS.get(x, x))
        st.dataframe(events.iloc[::-1].rename(columns=HEADERS), hide_index=True, width="stretch")
    export = result.copy()
    export["source"] = "SYNTHETIC_DEMO" if is_demo else "TSETMC"
    export["instrument_code"] = document.get("code", "DEMO")
    export["cci_period"] = config.cci_period
    export["entry_rules"] = ",".join(config.entries)
    export["adjusted"] = adjusted
    export["close_basis"] = basis
    export["timeframe"] = timeframe
    export["timezone"] = "Asia/Tehran"
    if timeframe == "2h":
        export["close_basis"] = "close"
        export["intraday_days"] = intraday_days
        export["session_start"], export["session_end"] = session.start, session.end
        export["include_short_bar"] = session.include_short
    export["include_today"] = include_today
    export["fetched_at_utc"] = document.get("fetched_at", "")
    st.download_button("ذخیره جدول تحلیل برای Excel (CSV)", export.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"rsi_cci_{document.get('code', 'demo')}_{config.cci_period}_{timeframe}.csv", mime="text/csv", on_click="ignore")
    with st.expander("داده روزانه و اندیکاتورها"):
        st.dataframe(result.tail(500).iloc[::-1].rename(columns=HEADERS), hide_index=True, width="stretch")


def main():
    st.set_page_config(page_title="دیده‌بان RSI / CCI", page_icon="📈", layout="wide")
    st.markdown("""<style>
        .stApp { font-family: Tahoma, Arial, sans-serif; }
        [data-testid="stMain"], [data-testid="stSidebar"] { direction: rtl; text-align: right; }
        [data-testid="stMetric"] { background: white; border: 1px solid #e0e7ee;
            padding: 18px; border-radius: 12px; }
        [data-testid="stMetricValue"] { font-size: 1.7rem; }
        [data-testid="stMetricValue"] > div { direction: ltr; text-align: right; white-space: normal; }
        h1, h2, h3 { font-family: Tahoma, Arial, sans-serif !important; }
        [data-testid="stCode"], .js-plotly-plot { direction: ltr; }
        </style>""", unsafe_allow_html=True)
    st.title("دیده‌بان RSI / CCI")
    st.write("دریافت محلی داده بورس تهران و بررسی راهبرد در روزانه، هفتگی و ۲ساعته")
    with st.sidebar:
        st.header("تنظیمات راهبرد")
        timeframe = st.selectbox("تایم‌فریم نمودار تک‌سهم", list(TIMEFRAMES), format_func=TIMEFRAMES.get)
        period = st.radio("دوره CCI", [14, 20], horizontal=True)
        entries = st.multiselect("روش‌های ورود فعال", ["A1", "A2", "B"], default=["A1", "A2", "B"])
        adjusted = st.checkbox("تعدیل قیمت با مرجع روز بعد", value=True)
        basis_label = st.selectbox("مبنای RSI و قیمت بسته‌شدن CCI", ["قیمت پایانی", "آخرین معامله"])
        basis = "final" if basis_label == "قیمت پایانی" else "close"
        if timeframe == "2h":
            st.caption("۲ساعته همیشه از آخرین معامله استفاده می‌کند؛ انتخاب پایانی برای روزانه و هفتگی است.")
        include_today = st.checkbox("پایان جلسه امروز را تأیید می‌کنم؛ امروز هم تحلیل شود", value=False)
        st.caption("پیش‌فرض: حذف روز جاری به وقت تهران. تأیید دستی فقط پس از پایان جلسه نماد.")
        with st.expander("تنظیمات ۲ساعته و ساعت جلسه"):
            intraday_days = int(st.number_input("تعداد روز معاملاتی درون‌روزی (صفر: همه سابقه)", 0, 10000, 30))
            session_start = st.text_input("شروع جلسه (HH:MM)", value="09:00")
            session_end = st.text_input("پایان جلسه (HH:MM)", value="12:30")
            include_short = st.checkbox("کندل کوتاه انتهای جلسه هم لحاظ شود", value=True)
            st.caption("با ساعت پیش‌فرض، کندل‌ها ۹–۱۱ و ۱۱–۱۲:۳۰ هستند؛ دومی ۹۰دقیقه‌ای است. "
                       "۲ساعته از ریزمعاملات واقعی و با قیمت آخرین معامله ساخته می‌شود.")
        try:
            session = Session(session_start, session_end, include_short)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        st.caption("در هفتگی پایان هفته چهارشنبه است؛ هفته ناقص کنار گذاشته می‌شود. RSI۱۴ و CCI برحسب تعداد کندل تایم‌فریم انتخابی‌اند.")
        days = st.slider("تعداد کندل نمودار", min_value=60, max_value=750, value=250, step=10)
        st.divider()
        st.caption("CCI۱۴ و CCI۲۰ دو نسخه مستقل راهبرد هستند؛ با تغییر دوره، سابقه فرضی دوباره محاسبه می‌شود.")

    tab_market, tab_live, tab_cache, tab_demo = st.tabs(["اسکن کل بازار", "دریافت از TSETMC", "داده ذخیره‌شده", "آزمایش بدون اینترنت"])
    with tab_market:
        render_market(CACHE, tuple(entries), adjusted, basis, include_today, intraday_days=intraday_days, session=session)
    with tab_live:
        with st.form("search_form"):
            query = st.text_input("نام نماد", placeholder="مثلاً فولاد")
            submitted = st.form_submit_button("جست‌وجوی نماد")
        if submitted:
            st.session_state.pop("search_results", None)
            try:
                with st.spinner("در حال جست‌وجو…"), TsetmcClient() as client:
                    st.session_state.search_results = client.search(query)
                if not st.session_state.search_results:
                    st.info("نمادی با این نام پیدا نشد.")
            except DataError as exc:
                st.error(str(exc))
        candidates = st.session_state.get("search_results", [])
        if candidates:
            selected = st.selectbox("نماد و بازار درست را انتخاب کنید", candidates, format_func=lambda x: x.label)
            st.caption("سوابق شناسه‌های مختلف به هم متصل نمی‌شوند؛ آخرین تاریخ و نام نماد را بررسی کنید.")
            if st.button("دریافت و ذخیره سابقه کامل", type="primary"):
                fetch(selected.code, selected.symbol, timeframe=timeframe, intraday_days=intraday_days, session=session,
                      adjusted=adjusted, basis=basis, include_today=include_today)
        with st.expander("دریافت مستقیم با شناسه TSETMC"):
            st.caption("عدد insCode را از نشانی صفحه نماد در TSETMC بردارید.")
            direct_code = st.text_input("شناسه عددی نماد", placeholder="46348559193224090")
            if st.button("دریافت با شناسه"):
                fetch(direct_code, f"شناسه {direct_code}", timeframe=timeframe, intraday_days=intraday_days, session=session,
                      adjusted=adjusted, basis=basis, include_today=include_today)
    with tab_cache:
        files = sorted(CACHE.directory.glob("[0-9]*.json")) if CACHE.directory.exists() else []
        if files:
            cached_code = st.selectbox("فایل محلی", [p.stem for p in files])
            if st.button("تحلیل داده ذخیره‌شده"):
                try:
                    doc = CACHE.load(cached_code)
                    doc["offline"] = True
                    st.session_state.document = doc
                except DataError as exc:
                    st.error(str(exc))
        else:
            st.info("پس از اولین دریافت، داده‌ها برای استفاده آفلاین در اینجا قابل انتخاب‌اند.")
        st.caption(f"پوشه داده‌ها روی همین کامپیوتر: {CACHE.directory}")
    with tab_demo:
        st.write("برای بررسی رابط و سیگنال‌ها، یک سابقه ساختگی بارگذاری کنید.")
        if st.button("نمایش داده آزمایشی"):
            st.session_state.document = {"demo": True, "code": "DEMO", "label": "نمونه ساختگی",
                                         "payload": demo_history()}
    st.divider()
    if not entries:
        st.info("حداقل یکی از روش‌های ورود را انتخاب کنید.")
    elif "document" in st.session_state:
        doc = st.session_state.document
        if doc.get("offline"):
            st.warning("حالت آفلاین: داده فعلی از فایل محلی است و به‌روزرسانی نشده است.")
        if timeframe == "2h" and not doc.get("demo"):
            if st.button("دریافت یا تکمیل داده ۲ساعته این نماد"):
                fetch(doc["code"], doc["label"], timeframe=timeframe, intraday_days=intraday_days, session=session,
                      adjusted=adjusted, basis=basis, include_today=include_today)
                doc = st.session_state.get("document", doc)
        try:
            show_analysis(doc, StrategyConfig(period, tuple(entries)), adjusted, basis, include_today, days,
                          timeframe=timeframe, intraday_days=intraday_days, session=session)
        except (DataError, ValueError) as exc:
            st.error(str(exc))
    else:
        st.info("برای نمودار و تاریخچه یک سهم، از تب دریافت تک‌سهم یا داده ذخیره‌شده استفاده کنید؛ شناسه نماد در نتایج بازار هم موجود است.")
    with st.expander("قواعد دقیق و محدودیت‌ها"):
        st.markdown("""
        - A1: RSI14 بالای ۵۰ و عبور صعودی CCI از صفر.
        - A2: RSI14 بالای ۵۰ و عبور صعودی CCI از ۱۰۰.
        - B: CCI بالای صفر و عبور صعودی RSI14 از ۵۰.
        - عبور صعودی: مقدار قبلی کمتر یا مساوی سطح و مقدار فعلی بیشتر از سطح؛ عبور نزولی برعکس آن است.
        - اگر CCI از زمان ورود بالای ۱۰۰ رفته باشد، عبور نزولی از ۱۰۰ خروج می‌دهد.
        - اگر هنوز بالای ۱۰۰ نرفته باشد، عبور نزولی از منفی ۱۰۰ خروج زیان می‌دهد.
        - تا خروج، خرید مجدد انجام نمی‌شود؛ خروج و ورود مجدد در یک کندل انجام نمی‌شود.
        - سیگنال پس از بسته‌شدن کندل معلوم می‌شود. تاریخ سیگنال، تاریخ اجرای سفارش نیست.
        - حد ضرر قیمتی به راهبرد اصلی اضافه نشده؛ خروج CCI زیان را به درصد ثابتی محدود نمی‌کند.
        - صف، توقف، هزینه معامله و امکان اجرای سفارش مدل نشده‌اند؛ این برنامه بک‌تست بازده یا سفارش‌گذار نیست.
        - تعدیل از نسبت priceYesterday روز بعد به pClosing روز قبل محاسبه می‌شود؛ صحت آن به داده منبع وابسته است.
        """)


def fetch(code: str, label: str, *, timeframe="daily", intraday_days=30, session=None,
          adjusted=True, basis="final", include_today=False):
    # A failed refresh must never leave an old/demo document posing as new data.
    st.session_state.pop("document", None)
    try:
        with st.spinner("دریافت و بررسی سابقه روزانه…"), TsetmcClient() as client:
            payload = client.history(code)
            st.session_state.document = CACHE.save(code, label, payload)
            if timeframe == "2h":
                progress = st.empty()
                prepare_timeframe(parse_history(payload), "2h", adjusted=adjusted, close_basis=basis,
                                  include_today=include_today, cache=CACHE, code=code, client=client, mode="resume",
                                  intraday_days=intraday_days, session=session,
                                  progress=lambda day, source, number, total: progress.caption(f"درون‌روزی {day}: {number}/{total} روز ({source})"))
        st.success("داده معتبر دریافت شد و روی کامپیوتر ذخیره شد.")
    except (DataError, OSError) as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
