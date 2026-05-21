"""Cryptocurrency trading bot driven by three Supertrend indicators."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd
import schedule

import config

LOG = logging.getLogger("supertrend_bot")
STATE_FILE = Path("positions.json")
LOG_FILE = Path("bot.log")


# --- Logging --------------------------------------------------------------

_ANSI = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "cyan": "\x1b[36m",
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _color(text: str, color: str) -> str:
    return f"{_ANSI[color]}{text}{_ANSI['reset']}"


class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG": _ANSI["dim"],
        "INFO": _ANSI["cyan"],
        "WARNING": _ANSI["yellow"],
        "ERROR": _ANSI["red"],
        "CRITICAL": _ANSI["red"],
    }

    def format(self, record: logging.LogRecord) -> str:
        original = record.levelname
        color = self.LEVEL_COLORS.get(original)
        if color:
            record.levelname = f"{color}{original}{_ANSI['reset']}"
        try:
            return super().format(record)
        finally:
            record.levelname = original


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _ANSI_RE.sub("", super().format(record))


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    datefmt = "%Y-%m-%d %H:%M:%S"
    msg_fmt = "%(asctime)s [%(levelname)s] %(message)s"

    console = logging.StreamHandler()
    console.setFormatter(ColorFormatter(msg_fmt, datefmt=datefmt))
    root.addHandler(console)

    file_h = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=7, encoding="utf-8"
    )
    file_h.setFormatter(PlainFormatter(msg_fmt, datefmt=datefmt))
    root.addHandler(file_h)


# --- Exchange -------------------------------------------------------------

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


# --- Indicator ------------------------------------------------------------

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


# --- State ----------------------------------------------------------------

def load_positions() -> dict[str, dict]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning("Could not read state file (%s); starting empty", exc)
        return {}
    positions: dict[str, dict] = {}
    for symbol, value in data.items():
        # Migrate legacy {symbol: bool} schema.
        if isinstance(value, bool):
            if value:
                positions[symbol] = {"entry_price": None, "qty": None}
        elif isinstance(value, dict):
            positions[symbol] = {
                "entry_price": value.get("entry_price"),
                "qty": value.get("qty"),
            }
    return positions


def save_positions(state: dict[str, dict]) -> None:
    held = {
        s: {"entry_price": info.get("entry_price"), "qty": info.get("qty")}
        for s, info in state.items()
        if info["in_position"]
    }
    try:
        STATE_FILE.write_text(json.dumps(held, indent=2, sort_keys=True))
    except OSError as exc:
        LOG.error("Could not save state: %s", exc)


# --- Market data ----------------------------------------------------------

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
    exchange: ccxt.Exchange, symbols: list[str], positions: dict[str, dict]
) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for symbol in symbols:
        df = fetch_ohlcv_df(exchange, symbol)
        if df is None or df.empty:
            continue
        held = positions.get(symbol)
        state[symbol] = {
            "info": df,
            "in_position": held is not None,
            "entry_price": held["entry_price"] if held else None,
            "qty": held["qty"] if held else None,
            "action": None,
        }
    return state


def fetch_all_balances(exchange: ccxt.Exchange) -> dict[str, float]:
    try:
        bal = exchange.fetch_balance()
    except ccxt.BaseError as exc:
        LOG.error("Balance fetch failed: %s", exc)
        return {}
    free = bal.get("free") or {}
    out: dict[str, float] = {}
    for asset, amount in free.items():
        try:
            out[asset] = float(amount)
        except (TypeError, ValueError):
            continue
    return out


def fetch_prices(exchange: ccxt.Exchange, symbols: list[str]) -> dict[str, float]:
    try:
        tickers = exchange.fetch_tickers(symbols)
    except ccxt.BaseError as exc:
        LOG.warning("Batch ticker fetch failed (%s); falling back per symbol", exc)
        tickers = {}
        for s in symbols:
            try:
                tickers[s] = exchange.fetch_ticker(s)
            except ccxt.BaseError as e2:
                LOG.error("Ticker fetch failed for %s: %s", s, e2)
    prices: dict[str, float] = {}
    for s, t in tickers.items():
        last = (t or {}).get("last")
        if last is None:
            continue
        try:
            prices[s] = float(last)
        except (TypeError, ValueError):
            continue
    return prices


# --- Trading --------------------------------------------------------------

def market_buy(
    exchange: ccxt.Exchange, symbol: str, amount: float, price: float
) -> Optional[float]:
    try:
        qty = float(exchange.amount_to_precision(symbol, amount))
    except (ccxt.BaseError, ValueError) as exc:
        LOG.error("Precision rounding failed for %s: %s", symbol, exc)
        return None
    if qty <= 0:
        LOG.warning("Computed buy qty <= 0 for %s; skipping", symbol)
        return None
    try:
        exchange.create_market_buy_order(symbol, qty)
    except ccxt.BaseError as exc:
        LOG.error("Buy failed for %s: %s", symbol, exc)
        return None
    LOG.info("%s %s qty=%s @ %.6f", _color("BUY", "green"), symbol, qty, price)
    return qty


def market_sell(exchange: ccxt.Exchange, symbol: str, qty: float) -> bool:
    if qty is None or qty <= 0:
        LOG.warning("No %s qty to sell", symbol)
        return False
    try:
        qty_p = float(exchange.amount_to_precision(symbol, qty))
    except (ccxt.BaseError, ValueError) as exc:
        LOG.error("Precision rounding failed for %s: %s", symbol, exc)
        return False
    if qty_p <= 0:
        return False
    try:
        exchange.create_market_sell_order(symbol, qty_p)
    except ccxt.BaseError as exc:
        LOG.error("Sell failed for %s: %s", symbol, exc)
        return False
    LOG.info("%s %s qty=%s", _color("SELL", "red"), symbol, qty_p)
    return True


def _last_two(df: pd.DataFrame) -> Optional[tuple[int, int]]:
    if len(df) < 2:
        return None
    return len(df) - 1, len(df) - 2


def _close_position(info: dict, action: str) -> None:
    info["in_position"] = False
    info["entry_price"] = None
    info["qty"] = None
    info["action"] = action


def _open_position(info: dict, price: float, qty: float, action: str) -> None:
    info["in_position"] = True
    info["entry_price"] = price
    info["qty"] = qty
    info["action"] = action


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
            if market_sell(exchange, symbol, info["qty"]):
                _close_position(info, "SELL (downtrend)")


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
            if market_sell(exchange, symbol, info["qty"]):
                _close_position(info, "SELL (flat band)")


def enter_on_reversal(
    exchange: ccxt.Exchange,
    state: dict[str, dict],
    allocation: float,
    prices: dict[str, float],
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
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            qty = market_buy(exchange, symbol, allocation / price, price)
            if qty:
                _open_position(info, price, qty, "BUY (reversal)")


def enter_on_rising_band(
    exchange: ccxt.Exchange,
    state: dict[str, dict],
    allocation: float,
    prices: dict[str, float],
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
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            qty = market_buy(exchange, symbol, allocation / price, price)
            if qty:
                _open_position(info, price, qty, "BUY (rising band)")


# --- Reporting ------------------------------------------------------------

def _fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:,.4f}"


def render_portfolio(
    cash: float, holdings: float, label: str
) -> None:
    total = cash + holdings
    LOG.info(
        "Portfolio (%s): %s cash + %s holdings = %s",
        label,
        _color(f"{cash:,.2f} USDT", "cyan"),
        _color(f"{holdings:,.2f} USDT", "cyan"),
        _color(f"{total:,.2f} USDT", "bold"),
    )


def render_cycle_summary(
    state: dict[str, dict], prices: dict[str, float]
) -> None:
    widths = (12, 6, 14, 6, 20, 14, 9)
    headers = ("Symbol", "Pos", "Price", "Trend", "Action", "Entry", "P&L")
    header_line = " ".join(
        f"{h:<{w}}" if i in (0, 1, 3, 4) else f"{h:>{w}}"
        for i, (h, w) in enumerate(zip(headers, widths))
    )
    rows = [header_line, "-" * len(header_line)]

    for symbol in sorted(state):
        info = state[symbol]
        df = info["info"]
        price = prices.get(symbol)
        in_pos = info["in_position"]
        trend_up = bool(df["uptrend"].iat[-1])
        trend = "UP" if trend_up else "DOWN"
        pos = "HOLD" if in_pos else "-"
        action = info.get("action") or "-"
        entry = info.get("entry_price")

        pnl_pct: Optional[float] = None
        if in_pos and entry and price:
            pnl_pct = (price - entry) / entry * 100
        pnl_str = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "-"

        # Pre-pad, then colorize (ANSI escapes don't affect visible width).
        sym_c = f"{symbol:<{widths[0]}}"
        pos_c = f"{pos:<{widths[1]}}"
        if in_pos:
            pos_c = _color(pos_c, "green")
        price_c = f"{_fmt_price(price):>{widths[2]}}"
        trend_c = f"{trend:<{widths[3]}}"
        trend_c = _color(trend_c, "green" if trend_up else "red")
        action_c = f"{action:<{widths[4]}}"
        if action.startswith("BUY"):
            action_c = _color(action_c, "green")
        elif action.startswith("SELL"):
            action_c = _color(action_c, "red")
        entry_c = f"{_fmt_price(entry):>{widths[5]}}"
        pnl_c = f"{pnl_str:>{widths[6]}}"
        if pnl_pct is not None:
            pnl_c = _color(pnl_c, "green" if pnl_pct >= 0 else "red")

        rows.append(" ".join([sym_c, pos_c, price_c, trend_c, action_c, entry_c, pnl_c]))

    LOG.info("Cycle summary:\n%s", "\n".join(rows))


# --- Orchestration --------------------------------------------------------

def run_bot(exchange: ccxt.Exchange, symbols: list[str]) -> None:
    LOG.info(_color("=" * 70, "dim"))
    LOG.info(_color("Cycle start", "bold"))
    try:
        positions = load_positions()
        state = build_market_state(exchange, symbols, positions)
        if not state:
            LOG.warning("No market data fetched; skipping cycle")
            return

        balances = fetch_all_balances(exchange)
        prices = fetch_prices(exchange, list(state.keys()))

        # Sync stored qty with actual on-exchange balance for held positions.
        # Drop positions whose balance vanished (manual sell, dust, etc.).
        for symbol, info in state.items():
            if not info["in_position"]:
                continue
            base = symbol.split("/")[0]
            bal = balances.get(base, 0.0)
            if bal > 0:
                info["qty"] = bal
            else:
                LOG.warning("%s marked closed (no on-exchange balance)", symbol)
                _close_position(info, "CLOSED (no balance)")

        cash = balances.get("USDT", 0.0)
        holdings_open = sum(
            (info["qty"] or 0.0) * (prices.get(s) or 0.0)
            for s, info in state.items()
            if info["in_position"]
        )
        render_portfolio(cash, holdings_open, "before")

        allocation = (cash + holdings_open) / max(len(state), 1)
        LOG.info("Allocation per slot: %s",
                 _color(f"{allocation:,.4f} USDT", "cyan"))

        exit_on_downtrend(exchange, state)
        exit_on_flat_band(exchange, state)
        enter_on_reversal(exchange, state, allocation, prices)
        enter_on_rising_band(exchange, state, allocation, prices)

        render_cycle_summary(state, prices)
        save_positions(state)

        any_trade = any(info.get("action") for info in state.values())
        if any_trade:
            balances_after = fetch_all_balances(exchange)
            cash_after = balances_after.get("USDT", cash)
            holdings_after = sum(
                balances_after.get(s.split("/")[0], 0.0) * (prices.get(s) or 0.0)
                for s, info in state.items()
                if info["in_position"]
            )
            render_portfolio(cash_after, holdings_after, "after")

        actions = [info["action"] for info in state.values() if info.get("action")]
        buys = sum(1 for a in actions if a.startswith("BUY"))
        sells = sum(1 for a in actions if a.startswith("SELL"))
        LOG.info(
            "Cycle done: %s buys, %s sells, %d no-op",
            _color(str(buys), "green"),
            _color(str(sells), "red"),
            len(state) - len(actions),
        )
    except Exception:
        LOG.exception("Unhandled error during bot cycle")
    finally:
        LOG.info(_color("=" * 70, "dim"))


def main() -> None:
    configure_logging()
    exchange = build_exchange()
    symbols = ["BTC/USDT", "ETH/USDT"] if config.IS_TESTNET else config.SYMBOLS
    run_bot(exchange, symbols)
    schedule.every().hour.at(":00").do(run_bot, exchange, symbols)
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
