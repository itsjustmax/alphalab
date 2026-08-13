"""AlphaLab's inline engine: real market data with zero setup.

These operations need no accounts, no keys, no local stack — daily bars
from Yahoo's public chart endpoint, with computed summaries. They make the
desk compelling the moment the harness is installed; the full engine
(broker quotes, options, dealer gamma) lights up when the bridged stack
is present. Every answer is a receipt: ok, summary, data, as_of, gaps.
"""

import datetime
import json
import urllib.parse
import urllib.request

import bridge
import forms
import gates
import plans
import web


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def receipt(summary, data=None, gaps=None, ok=True):
    return {"ok": ok, "summary": summary, "data": data or {},
            "as_of": _now(), "gaps": gaps or []}


def _chart_symbol(symbol):
    symbol = str(symbol).strip().upper()
    return {"SPX": "^GSPC", "NDX": "^NDX", "VIX": "^VIX", "DJI": "^DJI"}.get(symbol, symbol)


def _fetch_bars(symbol, days):
    span = "1y" if int(days) > 120 else ("6mo" if int(days) > 60 else "3mo")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(_chart_symbol(symbol))}?range={span}&interval=1d")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    bars = []
    for index, stamp in enumerate(stamps):
        close = quote["close"][index]
        if close is None:
            continue
        bars.append({
            "date": datetime.datetime.fromtimestamp(
                stamp, datetime.timezone.utc).date().isoformat(),
            "open": float(quote["open"][index] or close),
            "close": float(close),
            "high": float(quote["high"][index] or close),
            "low": float(quote["low"][index] or close),
            "volume": float(quote["volume"][index] or 0),
        })
    return bars[-int(days):]


def daily_bars(arguments):
    symbol = str(arguments.get("symbol", "")).strip()
    if not symbol:
        return receipt("daily_bars needs a symbol", ok=False,
                       gaps=["no symbol given"])
    days = min(int(arguments.get("days", 60) or 60), 500)
    try:
        bars = _fetch_bars(symbol, days)
    except Exception as error:
        return receipt(f"could not fetch {symbol} bars", ok=False,
                       gaps=[f"chart endpoint: {str(error)[:200]}"])
    if not bars:
        return receipt(f"no bars for {symbol}", ok=False,
                       gaps=[f"no data returned for {symbol}"])
    points = [[bar["date"], bar["close"]] for bar in bars]
    candles = [[bar["date"], bar["open"], bar["high"], bar["low"], bar["close"]]
               for bar in bars]
    return receipt(
        f"{symbol.upper()}: {len(bars)} daily closes through {bars[-1]['date']}"
        " (end-of-day public data, not real-time)",
        {"symbol": symbol.upper(), "points": points, "candles": candles,
         "last": bars[-1]},
        gaps=["end-of-day data; intraday needs the full engine"],
    )


def price_summary(arguments):
    symbol = str(arguments.get("symbol", "")).strip()
    if not symbol:
        return receipt("price_summary needs a symbol", ok=False,
                       gaps=["no symbol given"])
    try:
        bars = _fetch_bars(symbol, 30)
    except Exception as error:
        return receipt(f"could not fetch {symbol}", ok=False,
                       gaps=[f"chart endpoint: {str(error)[:200]}"])
    if len(bars) < 2:
        return receipt(f"not enough history for {symbol}", ok=False,
                       gaps=["fewer than 2 bars returned"])
    last, prior = bars[-1], bars[-2]
    month_ago = bars[0]
    change_1d = 100 * (last["close"] - prior["close"]) / prior["close"]
    change_1m = 100 * (last["close"] - month_ago["close"]) / month_ago["close"]
    high = max(bar["high"] for bar in bars)
    low = min(bar["low"] for bar in bars)
    return receipt(
        f"{symbol.upper()} closed {last['close']:.2f} on {last['date']} "
        f"({change_1d:+.1f}% on the day)",
        {"symbol": symbol.upper(), "close": last["close"],
         "close_label": f"{last['close']:.2f}",
         "date": last["date"],
         "change_1d_pct": round(change_1d, 2),
         "change_1m_pct": round(change_1m, 2),
         "range_30d": f"{low:.2f} – {high:.2f}"},
        gaps=["end-of-day close, not a live quote"],
    )


def capabilities(arguments):
    full = bridge.available()
    lanes = {
        "inline (daily bars, summaries)": True,
        "paper gates (case_check; fill_check needs the quote lane)": True,
        "full engine (broker quotes, options, dealer gamma)": full,
    }
    summary = ("full engine installed — its lanes answer when the broker "
               "stack is up; a failed receipt names itself" if full else
               "inline data only — light the full engine (broker quotes, "
               "live streams, options, dealer gamma) with: "
               "python3 tools/install_engine.py")
    return receipt(summary, {"lanes": lanes, "full_engine": full})


OPERATIONS = {
    "daily_bars": daily_bars,
    "price_summary": price_summary,
    "capabilities": capabilities,
    "trade_check": gates.trade_check,
    "case_check": gates.case_check,
    "fill_check": gates.fill_check,
    "fill_watch": gates.fill_watch,
    "live_quote": gates.live_quote,
    "web_fetch": web.web_fetch,
    "rss_fetch": web.rss_fetch,
    "live_quotes": gates.live_quotes,
    "form_check": forms.form_check,
    "plan_check": plans.plan_check,
    "plan_library": plans.plan_library,
}


def run(operation, arguments):
    handler = OPERATIONS.get(operation)
    if handler is None:
        return receipt(f"unknown inline operation: {operation}", ok=False,
                       gaps=[f"'{operation}' is not in the inline engine"])
    try:
        return handler(arguments)
    except Exception as error:
        return receipt(f"{operation} failed", ok=False, gaps=[str(error)[:300]])
