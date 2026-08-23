import json
from threading import RLock
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from freqtrade.enums import MarginMode, State, TradingMode
from freqtrade.rpc.api_server import strategy_profiles as profiles
from freqtrade.rpc.api_server.api_schemas import (
    RuntimeSettings,
    StrategyProfile,
    StrategyProfilePayload,
)


def _profile(
    *,
    profile_id: str = "crypto-trend-long",
    trading_mode: TradingMode = TradingMode.SPOT,
    margin_mode: MarginMode = MarginMode.NONE,
    supports_short: bool = False,
    leverage_allowed: bool = False,
    compatible: bool = True,
) -> StrategyProfile:
    return StrategyProfile(
        id=profile_id,
        strategy="CryptoTrendBreakoutStrategy",
        display_name="Trend",
        trading_mode=trading_mode,
        margin_mode=margin_mode,
        timeframes=profiles.SUPPORTED_TIMEFRAMES,
        runtime_timeframes=["5m"],
        default_timeframe="5m",
        supports_short=supports_short,
        leverage_allowed=leverage_allowed,
        compatible=compatible,
        compatibility_reason=None if compatible else "incompatible",
    )


def _settings(
    *,
    trading_mode: TradingMode = TradingMode.SPOT,
    margin_mode: MarginMode = MarginMode.NONE,
    leverage: float | None = None,
    open_trades: int = 0,
    open_orders: int = 0,
) -> RuntimeSettings:
    return RuntimeSettings(
        profile_id="crypto-trend-long",
        strategy="CryptoTrendBreakoutStrategy",
        timeframe="5m",
        trading_mode=trading_mode,
        margin_mode=margin_mode,
        leverage=leverage,
        short_enabled=False,
        open_trades=open_trades,
        open_orders=open_orders,
        can_apply=open_trades == 0 and open_orders == 0,
    )


def _patch_validators(mocker, profile: StrategyProfile, current: RuntimeSettings) -> None:
    mocker.patch.object(profiles, "_find_profile", return_value=profile)
    mocker.patch.object(profiles, "current_runtime_settings", return_value=current)
    mocker.patch.object(profiles, "validate_config_consistency")
    mocker.patch.object(profiles.StrategyResolver, "load_strategy")


@pytest.mark.parametrize(
    ("payload", "config", "message"),
    [
        (
            StrategyProfilePayload(profile_id="crypto-trend-long", timeframe="15m"),
            {"trading_mode": "spot"},
            "仅允许交易周期",
        ),
        (
            StrategyProfilePayload(profile_id="crypto-trend-long", short_enabled=True),
            {"trading_mode": "spot"},
            "不支持做空",
        ),
        (
            StrategyProfilePayload(profile_id="crypto-trend-long", leverage=2),
            {"trading_mode": "spot"},
            "不支持杠杆",
        ),
    ],
)
def test_validated_payload_rejects_unsafe_spot_changes(mocker, payload, config, message):
    profile = _profile()
    _patch_validators(mocker, profile, _settings())

    with pytest.raises(HTTPException, match=message) as exc:
        profiles._validated_payload(payload, config, MagicMock())

    assert exc.value.status_code == 422


def test_validated_payload_rejects_excessive_leverage(mocker):
    profile = _profile(
        profile_id="crypto-trend-long-short",
        trading_mode=TradingMode.FUTURES,
        margin_mode=MarginMode.ISOLATED,
        supports_short=True,
        leverage_allowed=True,
    )
    current = _settings(
        trading_mode=TradingMode.FUTURES,
        margin_mode=MarginMode.ISOLATED,
        leverage=2,
    )
    _patch_validators(mocker, profile, current)
    config = {
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "exchange": {"max_leverage": 5},
    }

    with pytest.raises(HTTPException, match="杠杆不能超过 5x") as exc:
        profiles._validated_payload(
            StrategyProfilePayload(
                profile_id="crypto-trend-long-short",
                leverage=10,
                short_enabled=True,
            ),
            config,
            MagicMock(),
        )

    assert exc.value.status_code == 422


@pytest.mark.parametrize(("open_trades", "open_orders"), [(1, 0), (0, 1)])
def test_validated_payload_blocks_switch_with_live_activity(mocker, open_trades, open_orders):
    profile = _profile()
    _patch_validators(
        mocker,
        profile,
        _settings(open_trades=open_trades, open_orders=open_orders),
    )

    with pytest.raises(HTTPException, match="存在持仓或未完成订单") as exc:
        profiles._validated_payload(
            StrategyProfilePayload(profile_id="crypto-trend-long"),
            {"trading_mode": "spot"},
            MagicMock(),
        )

    assert exc.value.status_code == 409


def test_validated_payload_builds_futures_candidate(mocker):
    profile = _profile(
        profile_id="crypto-trend-long-short",
        trading_mode=TradingMode.FUTURES,
        margin_mode=MarginMode.ISOLATED,
        supports_short=True,
        leverage_allowed=True,
    )
    current = _settings(
        trading_mode=TradingMode.FUTURES,
        margin_mode=MarginMode.ISOLATED,
        leverage=3,
    )
    _patch_validators(mocker, profile, current)
    config = {"trading_mode": "futures", "margin_mode": "isolated"}

    returned_profile, candidate, returned_current = profiles._validated_payload(
        StrategyProfilePayload(
            profile_id="crypto-trend-long-short",
            short_enabled=True,
        ),
        config,
        MagicMock(),
    )

    assert returned_profile is profile
    assert returned_current is current
    assert candidate == {
        "strategy": "CryptoTrendBreakoutStrategy",
        "timeframe": "5m",
        "short_enabled": True,
        "leverage": 3,
    }
    profiles.validate_config_consistency.assert_called_once()
    profiles.StrategyResolver.load_strategy.assert_called_once()


def test_apply_runtime_settings_writes_atomically_and_requests_reload(mocker, tmp_path):
    runtime_path = tmp_path / "runtime" / "active-profile.json"
    mocker.patch.dict("os.environ", {"FT_RUNTIME_PROFILE_PATH": str(runtime_path)})
    profile = _profile()
    current = _settings()
    candidate = {
        "strategy": profile.strategy,
        "timeframe": "5m",
        "short_enabled": False,
    }
    mocker.patch.object(
        profiles,
        "_validated_payload",
        return_value=(profile, candidate, current),
    )
    ftbot = MagicMock()
    ftbot.state = State.RUNNING
    ftbot._entry_lock = RLock()
    rpc = MagicMock()
    rpc._freqtrade = ftbot

    result = profiles.apply_runtime_settings(
        StrategyProfilePayload(profile_id=profile.id), {}, rpc
    )

    written = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert written == {
        "strategy": profile.strategy,
        "timeframe": "5m",
        "short_enabled": False,
        "initial_state": "running",
    }
    rpc._rpc_stop.assert_called_once_with()
    rpc._rpc_reload_config.assert_called_once_with()
    assert result.reload_status == "pending"


def test_rollback_runtime_reload_restores_previous_file(tmp_path):
    runtime_path = tmp_path / "active-profile.json"
    previous = b'{"strategy": "OldStrategy"}\n'
    runtime_path.write_bytes(b'{"strategy": "NewStrategy"}\n')
    profiles._begin_runtime_reload(runtime_path, previous)

    assert profiles.rollback_runtime_reload("reload failed") is True
    assert runtime_path.read_bytes() == previous
    assert profiles.runtime_reload_status() == ("failed", "reload failed")
    assert profiles.rollback_runtime_reload("again") is False
