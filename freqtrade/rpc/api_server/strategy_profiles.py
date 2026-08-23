"""Approved strategy profiles and guarded runtime configuration changes."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import HTTPException

from freqtrade.configuration import validate_config_consistency
from freqtrade.constants import Config
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.persistence import Order, Trade
from freqtrade.resolvers.strategy_resolver import StrategyResolver
from freqtrade.rpc import RPC
from freqtrade.rpc.api_server.api_schemas import (
    RuntimeSettings,
    StrategyProfile,
    StrategyProfilePayload,
    StrategyProfilePreview,
)


logger = logging.getLogger(__name__)


SUPPORTED_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h"]
RUNTIME_TIMEFRAMES = ["5m"]
_RUNTIME_APPLY_LOCK = RLock()
_RUNTIME_RELOAD_STATUS_LOCK = RLock()


@dataclass
class _RuntimeReload:
    path: Path
    previous: bytes | None
    status: str = "pending"
    error: str | None = None


_runtime_reload: _RuntimeReload | None = None


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix="active-profile-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temp_name).replace(path)
    except Exception:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass
        raise


def _restore_runtime_profile(path: Path, previous: bytes | None) -> None:
    if previous is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        _atomic_write_bytes(path, previous)


def _begin_runtime_reload(path: Path, previous: bytes | None) -> None:
    global _runtime_reload
    with _RUNTIME_RELOAD_STATUS_LOCK:
        _runtime_reload = _RuntimeReload(path=path, previous=previous)


def runtime_reload_status() -> tuple[str, str | None]:
    with _RUNTIME_RELOAD_STATUS_LOCK:
        if _runtime_reload is None:
            return "idle", None
        return _runtime_reload.status, _runtime_reload.error


def mark_runtime_reload_success() -> None:
    with _RUNTIME_RELOAD_STATUS_LOCK:
        if _runtime_reload and _runtime_reload.status == "pending":
            _runtime_reload.status = "succeeded"
            _runtime_reload.error = None


def rollback_runtime_reload(error: Exception | str) -> bool:
    """Restore the previous runtime profile after asynchronous worker reload failure."""
    with _RUNTIME_RELOAD_STATUS_LOCK:
        transaction = _runtime_reload
        if transaction is None or transaction.status != "pending":
            return False
        try:
            _restore_runtime_profile(transaction.path, transaction.previous)
        except Exception as restore_error:
            transaction.status = "failed"
            transaction.error = f"{error}; 恢复旧配置失败: {restore_error}"
            logger.exception("Failed to restore runtime profile")
            return False
        transaction.status = "failed"
        transaction.error = str(error)
        return True


PROFILE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "sample-spot",
        "strategy": "SampleStrategy",
        "display_name": "SampleStrategy · 现货",
        "trading_mode": TradingMode.SPOT,
        "margin_mode": MarginMode.NONE,
        "supports_short": False,
        "leverage_allowed": False,
        "runtime_timeframes": RUNTIME_TIMEFRAMES,
    },
    {
        "id": "crypto-trend-long",
        "strategy": "CryptoTrendBreakoutStrategy",
        "display_name": "趋势突破做多 · 现货",
        "trading_mode": TradingMode.SPOT,
        "margin_mode": MarginMode.NONE,
        "supports_short": False,
        "leverage_allowed": False,
        "runtime_timeframes": RUNTIME_TIMEFRAMES,
    },
    {
        "id": "crypto-trend-long-short",
        "strategy": "CryptoTrendBreakoutLongShortStrategy",
        "display_name": "趋势突破多空 · 期货逐仓",
        "trading_mode": TradingMode.FUTURES,
        "margin_mode": MarginMode.ISOLATED,
        "supports_short": True,
        "leverage_allowed": True,
        "runtime_timeframes": RUNTIME_TIMEFRAMES,
    },
)


def _as_trading_mode(value: Any) -> TradingMode:
    return value if isinstance(value, TradingMode) else TradingMode(str(value))


def _as_margin_mode(value: Any) -> MarginMode:
    return value if isinstance(value, MarginMode) else MarginMode(str(value or ""))


def _available_strategy_names(config: Config) -> set[str]:
    objects = StrategyResolver.search_all_objects(
        config, False, config.get("recursive_strategy_search", False)
    )
    return {obj["name"] for obj in objects}


def _profile_for_definition(
    definition: dict[str, Any], config: Config, available_strategies: set[str]
) -> StrategyProfile:
    current_mode = _as_trading_mode(config.get("trading_mode", TradingMode.SPOT))
    current_margin = _as_margin_mode(config.get("margin_mode", MarginMode.NONE))
    available = definition["strategy"] in available_strategies
    compatible = available
    reason: str | None = None
    if not available:
        reason = "策略文件未挂载到当前机器人"
    elif definition["trading_mode"] != current_mode:
        compatible = False
        reason = f"当前机器人是 {current_mode.value}, 该配置需要 {definition['trading_mode'].value}"
    elif definition["margin_mode"] != current_margin:
        compatible = False
        reason = (
            f"当前保证金模式是 {current_margin.value}, 该配置需要 {definition['margin_mode'].value}"
        )
    return StrategyProfile(
        **{key: value for key, value in definition.items() if key != "runtime_timeframes"},
        timeframes=SUPPORTED_TIMEFRAMES,
        runtime_timeframes=definition["runtime_timeframes"],
        default_timeframe="5m",
        compatible=compatible,
        compatibility_reason=reason,
    )


def list_strategy_profiles(config: Config) -> list[StrategyProfile]:
    available_strategies = _available_strategy_names(config)
    return [
        _profile_for_definition(item, config, available_strategies) for item in PROFILE_DEFINITIONS
    ]


def _find_profile(profile_id: str, config: Config) -> StrategyProfile:
    for profile in list_strategy_profiles(config):
        if profile.id == profile_id:
            return profile
    raise HTTPException(status_code=404, detail="策略配置不存在")


def _runtime_profile_path(config: Config) -> Path:
    configured = os.environ.get("FT_RUNTIME_PROFILE_PATH")
    if configured:
        return Path(configured)
    for filename in reversed(config.get("config_files", [])):
        path = Path(filename)
        if path.name == "active-profile.json":
            return path
    return Path(config["user_data_dir"]) / "runtime" / "active-profile.json"


def _active_profile_id(config: Config) -> str | None:
    strategy = config.get("strategy")
    mode = _as_trading_mode(config.get("trading_mode", TradingMode.SPOT))
    margin = _as_margin_mode(config.get("margin_mode", MarginMode.NONE))
    for profile in PROFILE_DEFINITIONS:
        if (
            profile["strategy"] == strategy
            and profile["trading_mode"] == mode
            and profile["margin_mode"] == margin
        ):
            return profile["id"]
    return None


def current_runtime_settings(config: Config, rpc: RPC) -> RuntimeSettings:
    strategy = rpc._freqtrade.strategy.get_strategy_name()
    timeframe = str(getattr(rpc._freqtrade.strategy, "timeframe", config.get("timeframe", "5m")))
    open_trades = len(Trade.get_open_trades())
    open_orders = len(Order.get_open_orders())
    reload_status, reload_error = runtime_reload_status()
    return RuntimeSettings(
        profile_id=_active_profile_id(config),
        strategy=strategy,
        timeframe=timeframe,
        trading_mode=_as_trading_mode(config.get("trading_mode", TradingMode.SPOT)),
        margin_mode=_as_margin_mode(config.get("margin_mode", MarginMode.NONE)),
        leverage=float(config["leverage"]) if config.get("leverage") is not None else None,
        short_enabled=bool(config.get("short_enabled", True)),
        open_trades=open_trades,
        open_orders=open_orders,
        can_apply=open_trades == 0 and open_orders == 0,
        warning=("存在持仓或未完成订单时禁止切换策略" if open_trades or open_orders else None),
        reload_status=reload_status,
        reload_error=reload_error,
    )


def _validated_payload(
    payload: StrategyProfilePayload, config: Config, rpc: RPC
) -> tuple[StrategyProfile, dict[str, Any], RuntimeSettings]:
    profile = _find_profile(payload.profile_id, config)
    if not profile.compatible:
        raise HTTPException(status_code=409, detail=profile.compatibility_reason)
    timeframe = payload.timeframe or profile.default_timeframe
    if timeframe not in profile.runtime_timeframes:
        raise HTTPException(
            status_code=422,
            detail=(f"该策略当前仅允许交易周期: {', '.join(profile.runtime_timeframes)}"),
        )
    if payload.short_enabled and not profile.supports_short:
        raise HTTPException(status_code=422, detail="该策略不支持做空")
    if payload.leverage and not profile.leverage_allowed:
        raise HTTPException(status_code=422, detail="该策略配置不支持杠杆")
    if (
        payload.leverage
        and _as_trading_mode(config.get("trading_mode", TradingMode.SPOT)) != TradingMode.FUTURES
    ):
        raise HTTPException(status_code=422, detail="杠杆只能用于期货模式")
    max_leverage = float(
        config.get("exchange", {}).get("max_leverage", config.get("max_leverage", 125))
    )
    if payload.leverage and payload.leverage > max_leverage:
        raise HTTPException(status_code=422, detail=f"杠杆不能超过 {max_leverage:g}x")
    current = current_runtime_settings(config, rpc)
    if current.open_trades or current.open_orders:
        raise HTTPException(status_code=409, detail="存在持仓或未完成订单, 请先处理后再切换")
    candidate = {
        "strategy": profile.strategy,
        "timeframe": timeframe,
        "short_enabled": (
            payload.short_enabled if payload.short_enabled is not None else profile.supports_short
        ),
    }
    if payload.leverage is not None:
        candidate["leverage"] = payload.leverage
    elif profile.leverage_allowed:
        candidate["leverage"] = current.leverage or 1.0
    candidate_config = deepcopy(config)
    candidate_config.update(candidate)
    try:
        validate_config_consistency(candidate_config)
        StrategyResolver.load_strategy(candidate_config)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"策略配置校验失败: {exc}") from exc
    return profile, candidate, current


def preview_runtime_settings(
    payload: StrategyProfilePayload, config: Config, rpc: RPC
) -> StrategyProfilePreview:
    profile, candidate, current = _validated_payload(payload, config, rpc)
    return StrategyProfilePreview(
        profile=profile,
        profile_id=profile.id,
        strategy=profile.strategy,
        timeframe=candidate["timeframe"],
        trading_mode=profile.trading_mode,
        margin_mode=profile.margin_mode,
        leverage=candidate.get("leverage"),
        short_enabled=candidate["short_enabled"],
        open_trades=current.open_trades,
        open_orders=current.open_orders,
        can_apply=True,
        warning="应用后机器人会重载配置, 已有持仓时接口会拒绝操作",
    )


def apply_runtime_settings(
    payload: StrategyProfilePayload, config: Config, rpc: RPC
) -> RuntimeSettings:
    """Atomically persist and reload a profile while preventing new entries."""
    from freqtrade.enums import State

    with _RUNTIME_APPLY_LOCK:
        # Synchronize with the worker's entry critical section.  Once this lock
        # is acquired, no in-flight process() call can still reach enter_positions().
        with rpc._freqtrade._entry_lock:
            previous_state = rpc._freqtrade.state
            if previous_state == State.RELOAD_CONFIG:
                raise HTTPException(status_code=409, detail="机器人正在重载配置, 请稍后再试")
            was_running = previous_state == State.RUNNING
            was_paused = previous_state == State.PAUSED
            if was_running or was_paused:
                rpc._rpc_stop()

            def restore_state() -> None:
                if was_running:
                    rpc._rpc_start()
                elif was_paused:
                    rpc._rpc_pause()

            try:
                _, candidate, current = _validated_payload(payload, config, rpc)
                # Preserve the user's running/paused/stopped state across worker reconfigure.
                candidate["initial_state"] = previous_state.name.lower()
                path = _runtime_profile_path(config)
                path.parent.mkdir(parents=True, exist_ok=True)
                previous = path.read_bytes() if path.exists() else None
                _atomic_write_bytes(
                    path,
                    (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode(),
                )
                _begin_runtime_reload(path, previous)
                try:
                    rpc._rpc_reload_config()
                except Exception as exc:
                    rollback_runtime_reload(exc)
                    raise
                return RuntimeSettings(
                    profile_id=payload.profile_id,
                    strategy=candidate["strategy"],
                    timeframe=candidate["timeframe"],
                    trading_mode=current.trading_mode,
                    margin_mode=current.margin_mode,
                    leverage=candidate.get("leverage"),
                    short_enabled=candidate["short_enabled"],
                    open_trades=0,
                    open_orders=0,
                    can_apply=True,
                    warning="配置已写入, 机器人正在重载",
                    reload_status="pending",
                )
            except Exception:
                # A rejected preflight must not leave a previously running bot stopped.
                if rpc._freqtrade.state != State.RELOAD_CONFIG:
                    restore_state()
                raise
