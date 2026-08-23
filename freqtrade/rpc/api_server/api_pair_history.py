import logging
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException

from freqtrade.configuration import validate_config_consistency
from freqtrade.rpc.api_server.api_pairlists import handleExchangePayload
from freqtrade.rpc.api_server.api_schemas import PairHistory, PairHistoryRequest
from freqtrade.rpc.api_server.deps import get_config, get_exchange, verify_strategy
from freqtrade.rpc.api_server.strategy_profiles import SUPPORTED_TIMEFRAMES, list_strategy_profiles
from freqtrade.rpc.rpc import RPC


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/pair_history", response_model=PairHistory, tags=["Candle data"])
def pair_history(
    pair: str,
    timeframe: str,
    timerange: str,
    strategy: str,
    freqaimodel: str | None = None,
    config=Depends(get_config),
    exchange=Depends(get_exchange),
):
    verify_strategy(strategy)
    # The initial call to this endpoint can be slow, as it may need to initialize
    # the exchange class.
    config_loc = deepcopy(config)
    config_loc.update(
        {
            "timeframe": timeframe,
            "strategy": strategy,
            "timerange": timerange,
            "freqaimodel": freqaimodel if freqaimodel else config_loc.get("freqaimodel"),
        }
    )
    validate_config_consistency(config_loc)
    try:
        return RPC._rpc_analysed_history_full(config_loc, pair, timeframe, exchange, None, False)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/pair_history", response_model=PairHistory, tags=["Candle data"])
def pair_history_filtered(payload: PairHistoryRequest, config=Depends(get_config)):
    verify_strategy(payload.strategy)
    # The initial call to this endpoint can be slow, as it may need to initialize
    # the exchange class.
    config_loc = deepcopy(config)
    config_loc.update(
        {
            "timeframe": payload.timeframe,
            "strategy": payload.strategy,
            "timerange": payload.timerange,
            "freqaimodel": (
                payload.freqaimodel if payload.freqaimodel else config_loc.get("freqaimodel")
            ),
        }
    )
    handleExchangePayload(payload, config_loc)
    exchange = get_exchange(config_loc)

    validate_config_consistency(config_loc)

    try:
        return RPC._rpc_analysed_history_full(
            config_loc,
            payload.pair,
            payload.timeframe,
            exchange,
            payload.columns,
            payload.live_mode,
        )
    except Exception as e:
        logger.exception("Error in pair_history_filtered")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/chart_history", response_model=PairHistory, tags=["Candle data"])
def chart_history(payload: PairHistoryRequest, config=Depends(get_config)):
    """Analyze historical candles for an approved strategy in Webserver mode."""
    if payload.timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"不支持的图表周期: {payload.timeframe}")
    strategy_name = payload.strategy or config.get("strategy")
    verify_strategy(strategy_name)
    profile = next(
        (
            profile
            for profile in list_strategy_profiles(config)
            if profile.strategy == strategy_name
        ),
        None,
    )
    if profile is None or not profile.compatible:
        raise HTTPException(status_code=409, detail="策略未在当前机器人模式中启用")
    config_loc = deepcopy(config)
    handleExchangePayload(payload, config_loc)
    config_loc.update(
        {
            "timeframe": payload.timeframe,
            "strategy": strategy_name,
            "timerange": payload.timerange,
        }
    )
    validate_config_consistency(config_loc)
    exchange = get_exchange(config_loc)
    try:
        return RPC._rpc_analysed_history_full(
            config_loc,
            payload.pair,
            payload.timeframe,
            exchange,
            payload.columns,
            payload.live_mode,
        )
    except Exception as exc:
        logger.exception("Error in chart_history")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
