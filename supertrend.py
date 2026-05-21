"""Cryptocurrency trading bot driven by three Supertrend indicators."""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd
import schedule

import config

LOG = logging.getLogger("supertrend_bot")
STATE_FILE = Path("positions.json")


def build_exchange() -> ccxt.Exchange:
    if config.IS_TESTNET:
        LOG.info("Using Binance testnet (spot). CCXT %s", ccxt.__version__)
        exchange = ccxt.binance({
            "apiKey": config.BINANCE_API_KEY_TEST,
            "secret": config.BINANCE_SECRET_KEY_TEST,
            "enableRateLimit": True,
        })
        exchange.set_sandbox_mode(True)
    else:
        LOG.info("Using Binance production (spot). CCXT %s", ccxt.__version__)
        exchange = ccxt.binance({
            "apiKey": config.BINANCE_API_KEY_PROD,
            "secret": config.BINANCE_SECRET_KEY_PROD,
            "enableRateLimit": True,
        })
    exchange.load_markets()
    return exchange


def true_range(data: pd.DataFrame) -> pd.Series:
    prev_close = data["close"].shift(1)
    h_l = (data["high"] - data["low"]).abs()
    h_pc = (data["high"] - prev_close).abs()
    l_pc = (data["low"] - prev_close).abs()
    return pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)


def average_true_range(data: pd.DataFrame, period: int = 14) -> pd.Series:
    # Wilder's smoothing: EMA with alpha = 1/period.
    return true_range(data).ewm(alpha=1 / period, adjust=False).mean()


def _apply_supertrend_band(
    df: pd.DataFrame, period: int, multiplier: float, suffix: str
) -> None:
    hl2 = (df["high"] + df["low"]) / 2
    atr_series = average_true_range(df, period)
    upper = (hl2 + multiplier * atr_series).to_numpy().copy()
    lower = (hl2 - multiplier * atr_series).to_numpy().copy()
    close = df["close"].to_numpy()

    in_uptrend = [True] * len(df)
    for i in range(1, len(df)):
        if close[i] > upper[i - 1]:
            in_uptrend[i] = True
        elif close[i] < lower[i - 1]:
            in_uptrend[i] = False
        else:
            in_uptrend[i] = in_uptrend[i - 1]
            if in_uptrend[i] and lower[i] < lower[i - 1]:
                lower[i] = lower[i - 1]
            if not in_uptrend[i] and upper[i] > upper[i - 1]:
                upper[i] = upper[i - 1]

    df[f"upperband{suffix}"] = upper
    df[f"lowerband{suffix}"] = lower
    df[f"in_uptrend{suffix}"] = in_uptrend


def supertrend(
    df: pd.DataFrame,
    period: int = 12, atr_multiplier: float = 3,
    period2: int = 10, atr_multiplier2: float = 1,
    period3: int = 11, atr_multiplier3: float = 2,
) -> pd.DataFrame:
    _apply_supertrend_band(df, period, atr_multiplier, "")
    _apply_supertrend_band(df, period2, atr_multiplier2, "2")
    _apply_supertrend_band(df, period3, atr_multiplier3, "3")
    df["ewma"] = df["close"].ewm(span=200, adjust=True).mean()
    df["uptrend"] = df["in_uptrend"] & df["in_uptrend2"] & df["in_uptrend3"]
    return df


def load_positions() -> dict[str, bool]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning("Could not read state file (%s); starting empty", exc)
        return {}


def save_positions(positions: dict[str, bool]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(positions, indent=2, sort_keys=True))
        LOG.info("Saved state. See you in an hour.")
    except OSError as exc:
        LOG.error("Could not save state: %s", exc)


def fetch_ohlcv_df(exchange: ccxt.Exchange, symbol: str) -> Optional[pd.DataFrame]:
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=100)
    except ccxt.BaseError as exc:
        LOG.error("OHLCV fetch failed for %s: %s", symbol, exc)
        return None
    if not bars or len(bars) < 2:
        LOG.warning("Not enough OHLCV data for %s", symbol)
        return None
    df = pd.DataFrame(
        bars[:-1],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    supertrend(df)
    return df


def build_market_state(
    exchange: ccxt.Exchange, symbols: list[str], positions: dict[str, bool]
) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for symbol in symbols:
        df = fetch_ohlcv_df(exchange, symbol)
        if df is None or df.empty:
            continue
        state[symbol] = {
            "info": df,
            "in_position": bool(positions.get(symbol, False)),
        }
    return state


def safe_balance(exchange: ccxt.Exchange, asset: str) -> float:
    try:
        return float(exchange.fetch_balance()["free"].get(asset, 0.0))
    except ccxt.BaseError as exc:
        LOG.error("Balance fetch failed for %s: %s", asset, exc)
        return 0.0


def safe_price(exchange: ccxt.Exchange, symbol: str) -> Optional[float]:
    try:
        ticker = exchange.fetch_ticker(symbol)
    except ccxt.BaseError as exc:
        LOG.error("Ticker fetch failed for %s: %s", symbol, exc)
        return None
    last = ticker.get("last")
    if last is None:
        return None
    try:
        return float(last)
    except (TypeError, ValueError):
        return None


def market_buy(exchange: ccxt.Exchange, symbol: str, amount: float) -> bool:
    try:
        qty = float(exchange.amount_to_precision(symbol, amount))
    except (ccxt.BaseError, ValueError) as exc:
        LOG.error("Precision rounding failed for %s: %s", symbol, exc)
        return False
    if qty <= 0:
        LOG.warning("Computed buy qty <= 0 for %s; skipping", symbol)
        return False
    try:
        exchange.create_market_buy_order(symbol, qty)
    except ccxt.BaseError as exc:
        LOG.error("Buy failed for %s: %s", symbol, exc)
        return False
    LOG.info("Bought %s qty=%s", symbol, qty)
    return True


def market_sell_all(exchange: ccxt.Exchange, symbol: str) -> bool:
    base = symbol.split("/")[0]
    free = safe_balance(exchange, base)
    if free <= 0:
        LOG.warning("No %s balance available to sell", base)
        return False
    try:
        qty = float(exchange.amount_to_precision(symbol, free))
    except (ccxt.BaseError, ValueError) as exc:
        LOG.error("Precision rounding failed for %s: %s", symbol, exc)
        return False
    if qty <= 0:
        return False
    try:
        exchange.create_market_sell_order(symbol, qty)
    except ccxt.BaseError as exc:
        LOG.error("Sell failed for %s: %s", symbol, exc)
        return False
    LOG.info("Sold %s qty=%s", symbol, qty)
    return True


def compute_allocation(exchange: ccxt.Exchange, symbols: list[str]) -> float:
    cash = safe_balance(exchange, "USDT")
    for symbol in symbols:
        price = safe_price(exchange, symbol)
        if price is None:
            continue
        cash += price * safe_balance(exchange, symbol.split("/")[0])
    return cash / max(len(symbols), 1)


def _last_two(df: pd.DataFrame) -> Optional[tuple[int, int]]:
    if len(df) < 2:
        return None
    return len(df) - 1, len(df) - 2


def exit_on_downtrend(exchange: ccxt.Exchange, state: dict[str, dict]) -> None:
    for symbol, info in state.items():
        if not info["in_position"]:
            continue
        idx = _last_two(info["info"])
        if idx is None:
            continue
        last, prev = idx
        df = info["info"]
        if df["uptrend"].iat[prev] and not df["uptrend"].iat[last]:
            if market_sell_all(exchange, symbol):
                info["in_position"] = False
                LOG.info("%s exited on downtrend", symbol)


def exit_on_flat_band(
    exchange: ccxt.Exchange, state: dict[str, dict], rel_tol: float = 1e-9
) -> None:
    for symbol, info in state.items():
        if not info["in_position"]:
            continue
        idx = _last_two(info["info"])
        if idx is None:
            continue
        last, prev = idx
        df = info["info"]
        if math.isclose(
            df["lowerband2"].iat[last], df["lowerband2"].iat[prev], rel_tol=rel_tol
        ):
            if market_sell_all(exchange, symbol):
                info["in_position"] = False
                LOG.info("%s exited on flat lowerband2", symbol)


def enter_on_reversal(
    exchange: ccxt.Exchange, state: dict[str, dict], allocation: float
) -> None:
    for symbol, info in state.items():
        if info["in_position"]:
            continue
        idx = _last_two(info["info"])
        if idx is None:
            continue
        last, prev = idx
        df = info["info"]
        if (
            not df["uptrend"].iat[prev]
            and df["uptrend"].iat[last]
            and df["close"].iat[last] > df["ewma"].iat[last]
        ):
            price = safe_price(exchange, symbol)
            if price is None or price <= 0:
                continue
            if market_buy(exchange, symbol, allocation / price):
                info["in_position"] = True


def enter_on_rising_band(
    exchange: ccxt.Exchange, state: dict[str, dict], allocation: float
) -> None:
    for symbol, info in state.items():
        if info["in_position"]:
            continue
        idx = _last_two(info["info"])
        if idx is None:
            continue
        last, prev = idx
        df = info["info"]
        if (
            df["lowerband2"].iat[last] > df["lowerband2"].iat[prev]
            and df["uptrend"].iat[last]
            and df["uptrend"].iat[prev]
            and df["close"].iat[last] > df["ewma"].iat[last]
        ):
            price = safe_price(exchange, symbol)
            if price is None or price <= 0:
                continue
            if market_buy(exchange, symbol, allocation / price):
                info["in_position"] = True


def report_open_positions(state: dict[str, dict]) -> None:
    open_syms = [s for s, info in state.items() if info["in_position"]]
    if not open_syms:
        LOG.info("Not currently in any position.")
    else:
        LOG.info("Currently holding: %s", ", ".join(open_syms))


def run_bot(exchange: ccxt.Exchange, symbols: list[str]) -> None:
    LOG.info("Running bot cycle...")
    try:
        positions = load_positions()
        state = build_market_state(exchange, symbols, positions)
        if not state:
            LOG.warning("No market data fetched; skipping cycle")
            return
        allocation = compute_allocation(exchange, symbols)
        LOG.info("Allocation per slot: %.4f USDT", allocation)
        exit_on_downtrend(exchange, state)
        exit_on_flat_band(exchange, state)
        enter_on_reversal(exchange, state, allocation)
        enter_on_rising_band(exchange, state, allocation)
        save_positions({s: info["in_position"] for s, info in state.items()})
        report_open_positions(state)
    except Exception:
        LOG.exception("Unhandled error during bot cycle")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    exchange = build_exchange()
    symbols = ["BTC/USDT", "ETH/USDT"] if config.IS_TESTNET else config.SYMBOLS
    run_bot(exchange, symbols)
    schedule.every().hour.at(":00").do(run_bot, exchange, symbols)
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
