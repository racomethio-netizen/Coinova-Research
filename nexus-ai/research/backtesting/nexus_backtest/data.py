"""Load real candles and market specs from the database for research."""

from __future__ import annotations

import pandas as pd
from nexus_db import models, repo, transaction
from nexus_strategies.frames import to_frame
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from nexus_backtest.engine import MarketSpec


def load_frames(factory: sessionmaker[Session], exchange: str, symbols: list[str],
                timeframes: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    out: dict[str, dict[str, pd.DataFrame]] = {}
    with transaction(factory) as s:
        for symbol in symbols:
            pid = repo.pair_id(s, exchange, symbol)
            out[symbol] = {tf: to_frame(repo.load_candles(s, pid, tf)) for tf in timeframes}
    return out


def load_specs(factory: sessionmaker[Session], exchange: str,
               symbols: list[str]) -> dict[str, MarketSpec]:
    specs: dict[str, MarketSpec] = {}
    with transaction(factory) as s:
        for symbol in symbols:
            pair = s.scalar(select(models.TradingPair).where(
                models.TradingPair.id == repo.pair_id(s, exchange, symbol)))
            assert pair is not None and pair.amount_step is not None
            specs[symbol] = MarketSpec(symbol, pair.base_asset, pair.amount_step,
                                       pair.min_amount, pair.min_notional)
    return specs
