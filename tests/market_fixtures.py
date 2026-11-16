"""Artificial daily data and API clients, never recordings of a live market."""

import copy
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from rsi_cci.data import DataError
from rsi_cci.demo import demo_history
from rsi_cci.tsetmc import Instrument


def signal_history(shift_days=0, length=None):
    payload = copy.deepcopy(demo_history())
    newest = payload["closingPriceDaily"][0]
    newest.update(pClosing=30000, pDrCotVal=30000, priceMax=31000)
    if shift_days:
        for row in payload["closingPriceDaily"]:
            day = datetime.strptime(str(row["dEven"]), "%Y%m%d") - timedelta(days=shift_days)
            row["dEven"] = int(day.strftime("%Y%m%d"))
    if length:
        payload["closingPriceDaily"] = payload["closingPriceDaily"][:length]
    return payload


def market_item(code, symbol=None, security_type="300", history=None):
    history = history or signal_history()
    return Instrument(str(code), symbol or f"سهم{code}", f"شرکت {code}",
                      history["closingPriceDaily"][0]["dEven"], security_type, "بورس")


def fake_client(items, histories=None):
    client = MagicMock()
    client.__enter__.return_value = client
    client.market_universe.return_value = items

    def fetch(code):
        value = histories.get(code) if histories is not None else signal_history()
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise DataError("Synthetic fetch failure")
        return value

    client.history.side_effect = fetch
    return client
