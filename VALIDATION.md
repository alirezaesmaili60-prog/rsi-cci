# Validation

- Runtime used: Linux, Python 3.12.14.
- Installed all pinned dependencies from `requirements.txt` with TLS verification enabled.
- `python -m unittest discover -s tests -v`: 36 tests passed.
- First-launch regression: with Streamlit credentials reported missing, non-headless mode enabled, and no stdin response supplied, the real server reached HTTP health readiness without an email prompt. The project config and Windows launcher disable `server.showEmailPrompt` while retaining automatic browser opening.
- Coverage includes Wilder's published RSI reference values, CCI mean absolute deviation, neutral flat prices, warm-up, A1/A2/B entries, equality boundaries, duplicate entries, separate per-position +100 state, both exits, adjustment around a split, unconfirmed/future-day filtering, cache integrity, and actionable network errors.
- Streamlit AppTest exercises demo mode, switching CCI periods, disabling all entries, mocked symbol search and fetch, cache reload, and failed refresh clearing the current analysis.
- CLI tests exercise mocked collection, cached analysis without network access, CSV settings, and a failing exit status for missing data.
- Started the Streamlit server on loopback; `/_stcore/health` returned `ok`. A headless system Chromium browser rendered the Persian demo, metrics, Plotly chart, and synthetic-data warning; changing to CCI20 worked. The browser downloaded the CSV, and its 260 rows, demo provenance, CCI period, and BUY/EXIT events were verified.
- Dependency compatibility check: all 36 installed packages compatible.
- A real loopback HTTP request exercises the client, parsing, local storage, indicators, and both CCI variants with synthetic data. This is not a TSETMC live test.
- Live read attempted: `https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/46348559193224090/0`. The managed cloud proxy rejected CONNECT with HTTP 403; TSETMC application data was not received. The configured cloud allowlist excludes this destination. No TLS or proxy checks were bypassed.
- Windows launchers are supplied, but have not been run on Windows. User-machine installation and live connectivity remain to be verified there.

This software emits hypothetical strategy signals. It has no brokerage integration and makes no claims about fills or returns.
