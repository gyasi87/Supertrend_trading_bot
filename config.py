"""Configuration for the supertrend trading bot.

Secrets are read from environment variables so they aren't checked into git.
Set BINANCE_API_KEY_PROD / BINANCE_SECRET_KEY_PROD for live trading and
BINANCE_API_KEY_TEST / BINANCE_SECRET_KEY_TEST for the sandbox.
BOT_IS_TESTNET defaults to "true" so accidental runs hit the testnet.
"""

import os

BINANCE_API_KEY_PROD = os.environ.get("BINANCE_API_KEY_PROD", "")
BINANCE_SECRET_KEY_PROD = os.environ.get("BINANCE_SECRET_KEY_PROD", "")

BINANCE_API_KEY_TEST = os.environ.get("BINANCE_API_KEY_TEST", "")
BINANCE_SECRET_KEY_TEST = os.environ.get("BINANCE_SECRET_KEY_TEST", "")

IS_TESTNET = os.environ.get("BOT_IS_TESTNET", "true").lower() == "true"

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "NEO/USDT", "LTC/USDT", "QTUM/USDT",
    "ADA/USDT", "XRP/USDT", "EOS/USDT", "LINK/USDT", "VET/USDT", "MATIC/USDT",
    "DOGE/USDT", "DOT/USDT", "RSR/USDT", "SHIB/USDT", "ZIL/USDT", "ZRX/USDT",
    "ETC/USDT", "BAKE/USDT", "SOL/USDT", "THETA/USDT", "ENJ/USDT", "DASH/USDT",
    "KSM/USDT", "SUPER/USDT", "SUSHI/USDT", "XLM/USDT", "BADGER/USDT",
    "CKB/USDT", "ICP/USDT", "IOTA/USDT", "ALGO/USDT", "MKR/USDT", "BCH/USDT",
    "SAND/USDT", "CAKE/USDT", "AAVE/USDT", "KAVA/USDT", "TFUEL/USDT", "ONE/USDT",
    "FIL/USDT", "UNI/USDT", "XMR/USDT", "BAT/USDT", "CTXC/USDT",
]
