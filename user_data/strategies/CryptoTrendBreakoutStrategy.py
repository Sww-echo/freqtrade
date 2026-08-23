"""Crypto-native trend and breakout strategy for Freqtrade.

This is an original, lightweight implementation inspired by broadly known
trend-following, volume confirmation and Donchian-channel ideas.  It does not
depend on the supplied stock-analysis application's code or external data APIs.
"""

import talib.abstract as ta
from pandas import DataFrame
from technical import qtpylib

from freqtrade.enums import TradingMode
from freqtrade.exceptions import OperationalException
from freqtrade.strategy import IStrategy, informative, merge_informative_pair


class CryptoTrendBreakoutStrategy(IStrategy):
    """Trade liquid USDT crypto pairs only when the pair and BTC are both bullish.

    The strategy is deliberately conservative:
    - 1h EMA20/EMA60 define the pair and market regime.
    - A 5m Donchian breakout plus relative volume and OBV confirm the entry.
    - A 5m channel failure or loss of the 1h trend exits the trade.

    It is a starting point for backtesting and dry-run validation, not a claim
    of profitability and not suitable for live trading without validation.
    """

    INTERFACE_VERSION = 3

    can_short: bool = False
    timeframe = "5m"
    startup_candle_count: int = 800
    process_only_new_candles = True

    # The original stock tool's fixed 5% stop is too tight for many 5m crypto
    # pairs.  The channel exit is the primary exit; this is the hard fail-safe.
    stoploss = -0.10
    minimal_roi = {
        "0": 0.08,
        "240": 0.035,
        "720": 0.015,
        "1440": 0.0,
    }
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.04
    trailing_only_offset_is_reached = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    plot_config = {
        "main_plot": {
            "ema20": {"color": "#f59e0b"},
            "ema60": {"color": "#2563eb"},
            "donchian_high_48": {"color": "#16a34a"},
            "donchian_low_24": {"color": "#dc2626"},
        },
        "subplots": {
            "Volume ratio": {"volume_ratio": {"color": "#7c3aed"}},
            "Trend strength": {"adx": {"color": "#0f766e"}},
        },
    }

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Higher-timeframe trend filter for the currently traded pair."""
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema60"] = ta.EMA(dataframe, timeperiod=60)
        dataframe["ema20_slope"] = dataframe["ema20"].pct_change(5)
        return dataframe

    def informative_pairs(self):
        """Cache BTC 1h candles using the correct spot/futures pair notation."""
        stake = self.config["stake_currency"]
        if self.config.get("trading_mode", TradingMode.SPOT) == TradingMode.FUTURES:
            return [(f"BTC/{stake}:{stake}", "1h", "futures")]
        return [(f"BTC/{stake}", "1h", "spot")]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Create only indicators used by the entry and exit rules."""
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema60"] = ta.EMA(dataframe, timeperiod=60)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]

        # Shifted channels intentionally exclude the current candle.  This
        # prevents using the current high to define its own breakout threshold.
        dataframe["donchian_high_48"] = dataframe["high"].rolling(48).max().shift(1)
        dataframe["donchian_low_24"] = dataframe["low"].rolling(24).min().shift(1)
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(20).mean().shift(1)
        dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume_mean_20"]

        dataframe["obv"] = ta.OBV(dataframe)
        dataframe["obv_ema"] = ta.EMA(dataframe["obv"], timeperiod=12)

        stake = self.config["stake_currency"]
        is_futures = self.config.get("trading_mode", TradingMode.SPOT) == TradingMode.FUTURES
        btc_pair = f"BTC/{stake}:{stake}" if is_futures else f"BTC/{stake}"
        btc_candle_type = "futures" if is_futures else "spot"
        btc_dataframe = self.dp.get_pair_dataframe(btc_pair, "1h", btc_candle_type)
        if btc_dataframe.empty:
            raise OperationalException(f"No 1h BTC informative data available for {btc_pair}.")
        btc_dataframe["btc_close"] = btc_dataframe["close"]
        btc_dataframe["btc_ema20"] = ta.EMA(btc_dataframe, timeperiod=20)
        btc_dataframe["btc_ema60"] = ta.EMA(btc_dataframe, timeperiod=60)
        dataframe = merge_informative_pair(
            dataframe,
            btc_dataframe[["date", "btc_close", "btc_ema20", "btc_ema60"]],
            self.timeframe,
            "1h",
            ffill=True,
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Enter only on a confirmed breakout in a healthy pair and BTC regime."""
        pair_trend = (
            (dataframe["close_1h"] > dataframe["ema20_1h"])
            & (dataframe["ema20_1h"] > dataframe["ema60_1h"])
            & (dataframe["ema20_slope_1h"] > 0)
        )
        btc_trend = (dataframe["btc_close_1h"] > dataframe["btc_ema20_1h"]) & (
            dataframe["btc_ema20_1h"] > dataframe["btc_ema60_1h"]
        )
        breakout = dataframe["close"] > dataframe["donchian_high_48"]
        volume_confirmation = dataframe["volume_ratio"] >= 1.20
        trend_confirmation = (
            (dataframe["ema20"] > dataframe["ema60"])
            & (dataframe["obv"] > dataframe["obv_ema"])
            & (dataframe["adx"] >= 18)
        )
        risk_guard = (
            (dataframe["rsi"] < 72)
            & (dataframe["atr_pct"] >= 0.002)
            & (dataframe["atr_pct"] <= 0.10)
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[
            pair_trend
            & btc_trend
            & breakout
            & volume_confirmation
            & trend_confirmation
            & risk_guard,
            ["enter_long", "enter_tag"],
        ] = (1, "trend_breakout")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Leave when the short-term breakout fails or the higher trend breaks."""
        channel_failure = dataframe["close"] < dataframe["donchian_low_24"]
        local_trend_failure = qtpylib.crossed_below(dataframe["ema20"], dataframe["ema60"])
        higher_trend_failure = (dataframe["close_1h"] < dataframe["ema20_1h"]) & (
            dataframe["rsi"] < 45
        )

        dataframe.loc[
            (channel_failure | local_trend_failure | higher_trend_failure)
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "trend_failure")
        return dataframe
