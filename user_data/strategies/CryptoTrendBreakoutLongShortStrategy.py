"""Long/short crypto trend-breakout strategy.

This strategy extends the long-only implementation with independently
confirmed short breakdown signals for futures dry-run/backtesting.
"""

from pandas import DataFrame

from technical import qtpylib

from CryptoTrendBreakoutStrategy import CryptoTrendBreakoutStrategy


class CryptoTrendBreakoutLongShortStrategy(CryptoTrendBreakoutStrategy):
    """Trade liquid USDT futures in both directions with regime confirmation."""

    can_short: bool = True

    plot_config = {
        "main_plot": {
            "ema20": {"color": "#f59e0b"},
            "ema60": {"color": "#2563eb"},
            "donchian_high_48": {"color": "#16a34a"},
            "donchian_low_48": {"color": "#dc2626"},
            "donchian_high_24": {"color": "#65a30d"},
            "donchian_low_24": {"color": "#b91c1c"},
        },
        "subplots": {
            "Volume ratio": {"volume_ratio": {"color": "#7c3aed"}},
            "Trend strength": {"adx": {"color": "#0f766e"}},
        },
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        # Short entries use the same lookback as long entries; exits use a
        # shorter opposing channel to protect profits during reversals.
        dataframe["donchian_low_48"] = dataframe["low"].rolling(48).min().shift(1)
        dataframe["donchian_high_24"] = dataframe["high"].rolling(24).max().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)

        pair_downtrend = (
            (dataframe["close_1h"] < dataframe["ema20_1h"])
            & (dataframe["ema20_1h"] < dataframe["ema60_1h"])
            & (dataframe["ema20_slope_1h"] < 0)
        )
        btc_downtrend = (
            (dataframe["btc_close_1h"] < dataframe["btc_ema20_1h"])
            & (dataframe["btc_ema20_1h"] < dataframe["btc_ema60_1h"])
        )
        breakdown = dataframe["close"] < dataframe["donchian_low_48"]
        volume_confirmation = dataframe["volume_ratio"] >= 1.20
        trend_confirmation = (
            (dataframe["ema20"] < dataframe["ema60"])
            & (dataframe["obv"] < dataframe["obv_ema"])
            & (dataframe["adx"] >= 18)
        )
        risk_guard = (
            (dataframe["rsi"] > 28)
            & (dataframe["atr_pct"] >= 0.002)
            & (dataframe["atr_pct"] <= 0.10)
            & (dataframe["volume"] > 0)
        )

        if not self.config.get("short_enabled", True):
            return dataframe

        dataframe.loc[
            pair_downtrend
            & btc_downtrend
            & breakdown
            & volume_confirmation
            & trend_confirmation
            & risk_guard,
            ["enter_short", "enter_tag"],
        ] = (1, "trend_breakdown")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_exit_trend(dataframe, metadata)

        channel_recovery = dataframe["close"] > dataframe["donchian_high_24"]
        local_trend_recovery = qtpylib.crossed_above(
            dataframe["ema20"], dataframe["ema60"]
        )
        higher_trend_recovery = (
            (dataframe["close_1h"] > dataframe["ema20_1h"])
            & (dataframe["rsi"] > 55)
        )
        dataframe.loc[
            (channel_recovery | local_trend_recovery | higher_trend_recovery)
            & (dataframe["volume"] > 0),
            ["exit_short", "exit_tag"],
        ] = (1, "trend_recovery")
        return dataframe
