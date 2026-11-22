"""Assemble signal-fusion inputs from stored candles and live context (read-only).

Everything comes from the database (closed candles, strategy validation records, model
registry) or Redis (risk state, live order book, news, derivatives). Missing inputs stay
missing and become blockers or reasons; nothing is estimated.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pandas as pd
from nexus_common import events
from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_db import models, repo
from nexus_decision.fusion import Candidate, Context, ModelView, fuse
from nexus_decision.selector import Assessment, assess
from nexus_strategies.frames import TF_SECONDS, to_frame
from nexus_strategies.regime import classify
from nexus_strategies.strategies import ALL_STRATEGIES
from sqlalchemy import select
from sqlalchemy.orm import Session

BARS = 600
MODEL_NAME = "xgb_triple_barrier_15m"


def _frames(s: Session, exchange: str, symbol: str, tfs: set[str]) -> dict[str, pd.DataFrame]:
    pid = repo.pair_id(s, exchange, symbol)
    return {tf: to_frame(repo.load_candles(s, pid, tf, limit=BARS)) for tf in tfs}


def _model_view(s: Session) -> ModelView:
    active = s.scalar(select(models.AiModel).where(models.AiModel.status == "active"))
    if active is None:
        return ModelView(False, reason="no validated model is active")
    # Inference for an active model would run here; none has passed validation, so no
    # model can be active (activation requires a validated model and the owner).
    return ModelView(False, reason=f"model {active.name} v{active.version} is active but "
                                   "live inference is not enabled in this version",
                     version=active.version)


def compute(s: Session, redis_values: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """``redis_values``: pre-read Redis values (risk state, books, news, derivatives)."""
    now = utcnow()
    exchange = settings.signal_exchange
    rows = {st.code: st for st in s.scalars(select(models.Strategy))}
    strategies = {code: cls for code, cls in ALL_STRATEGIES.items() if code in rows}
    tfs = {tf for cls in strategies.values() for tf in (cls.base_tf, cls.confirm_tf)}
    tfs |= {"5m", "15m", "1h", "4h"}
    model = _model_view(s)
    risk = redis_values.get("risk_state") or {}
    news = redis_values.get("news") or None
    out = []
    frames_by_symbol = {}
    for symbol in settings.trading_pairs:
        try:
            frames_by_symbol[symbol] = _frames(s, exchange, symbol, tfs)
        except LookupError:
            frames_by_symbol[symbol] = {}
    btc_trend = None
    btc = frames_by_symbol.get("BTC/USDT") or {}
    if btc.get("1h") is not None and len(btc["1h"]) >= 60:
        from nexus_analysis.mtf import tf_state

        btc_trend = float(tf_state(btc["1h"])["trend"].iloc[-1])
    for symbol in settings.trading_pairs:
        frames = frames_by_symbol.get(symbol) or {}
        if not frames or any(f.empty for f in frames.values()):
            out.append({"symbol": symbol, "decision": "NO_TRADE",
                        "blockers": ["no stored candles for this pair yet"]})
            continue
        assessments: list[Assessment] = []
        candidates: list[Candidate] = []
        regimes: dict[str, str | None] = {}
        for code, cls in strategies.items():
            base = frames[cls.base_tf]
            if cls.base_tf not in regimes:
                r = classify(base)
                regimes[cls.base_tf] = None if r.empty or pd.isna(r.iloc[-1]) else str(r.iloc[-1])
            validation = (rows[code].parameters or {}).get("validation") or {}
            assessments.append(assess(code, validation, regimes[cls.base_tf]))
            try:
                sig = cls().latest({tf: frames[tf] for tf in cls().timeframes}, symbol)
            except (KeyError, ValueError):
                sig = None
            if sig is not None:
                candidates.append(Candidate(code, cls.base_tf, sig.bar_time, sig.entry_price,
                                            sig.stop, sig.target))
        m5 = frames["5m"]
        newest_close = m5.index[-1] + pd.Timedelta(seconds=TF_SECONDS["5m"])
        fresh = (now - newest_close.to_pydatetime()) <= timedelta(minutes=15)
        book = (redis_values.get("books") or {}).get(symbol)
        spread = None
        if book:
            bid, ask = float(book["bid"]), float(book["ask"])
            spread = (ask - bid) / ((ask + bid) / 2) * 10_000
        from nexus_analysis.mtf import mtf_snapshot

        mtf = mtf_snapshot({tf: frames[tf] for tf in ("15m", "1h", "4h")}, "15m")["scores"]
        deriv = (redis_values.get("derivatives") or {}).get(symbol.replace("/", "")) or {}
        news_block = None
        if news is None:
            news_block = "news risk unknown"
        elif (news.get("score") or 0) > settings.news_block_threshold:
            news_block = f"high-impact news window ({news['score']:.2f})"
        ctx = Context(
            regime=regimes.get("1h"), risk_state=risk.get("state"),
            entries_allowed=bool(risk.get("entries_allowed")), data_fresh=fresh,
            spread_bps=spread, news_block=news_block, mtf_conflict=bool(mtf.get("conflict")),
            htf_trend=mtf.get("htf_trend"),
            btc_trend=btc_trend if symbol != "BTC/USDT" else None,
            funding_24h=deriv.get("funding_rate"),
            futures_enabled=settings.futures_enabled)
        fused = fuse(symbol, now=now, candidates=candidates, assessments=assessments,
                     model=model, ctx=ctx)
        d = fused.as_dict()
        d["regimes_by_timeframe"] = regimes
        d["mtf_scores"] = mtf
        out.append(d)
    return {"generated_at": now.isoformat(), "signals": out,
            "principle": "Only strategies or models with validated out-of-sample edges can "
                         "create a trade. Everything else is context."}


async def redis_inputs(redis: Any, settings: Settings) -> dict[str, Any]:
    from nexus_market.derivatives import KEY as DERIV_KEY
    from nexus_news.service import KEY as NEWS_KEY

    async def js(key: str) -> Any:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None

    books = {}
    derivs = {}
    for symbol in settings.trading_pairs:
        books[symbol] = await js(events.book_key(settings.signal_exchange, symbol))
        native = symbol.replace("/", "")
        derivs[native] = await js(DERIV_KEY.format(symbol=native))
    return {"risk_state": await js("nexus:risk:state:paper"), "news": await js(NEWS_KEY),
            "books": books, "derivatives": derivs}
