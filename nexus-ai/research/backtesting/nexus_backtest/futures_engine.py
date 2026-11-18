"""USDT-M futures backtester: isolated margin, long and short, funding, liquidation.

Timing (no look-ahead):
* A signal is computed from bars closed at time t and executes at the OPEN of bar t+1.
* Stops and liquidation are checked against MARK prices (what Binance's stop orders use);
  take-profit against the last-price high/low. Within one bar, liquidation is checked
  first, then stop, then target. A gap through a level fills at the bar's open.
* Funding is charged at each 8-hour settlement (00, 08, 16 UTC) on the position's notional.
* Position size comes from nexus_risk.futures_sizing (risk budget, stop distance, fees,
  slippage); leverage only sets the margin used. Refused sizes are recorded.

Not modelled (stated, not hidden): partial fills (entries and exits fill fully at the
bar), order-book depth, and exchange-specific tiered maintenance margin (a single rate is
used). These make the result optimistic in the stated direction for thin markets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

import pandas as pd
from nexus_risk.futures_sizing import FuturesSizingInput, size_futures_position

Side = Literal["long", "short"]
ZERO = Decimal(0)


@dataclass(frozen=True)
class FuturesSignal:
    time: datetime              # bar on which the signal is known (its close)
    side: Side
    stop: Decimal
    target: Decimal | None


@dataclass
class FuturesConfig:
    initial_equity: Decimal = Decimal(10_000)
    leverage: int = 2
    risk_fraction: Decimal = Decimal("0.01")
    fee_rate: Decimal = Decimal("0.0005")          # taker, per side
    slippage_rate: Decimal = Decimal("0.0002")
    maintenance_margin_rate: Decimal = Decimal("0.004")
    max_drawdown: Decimal = Decimal("0.10")        # halts new entries
    qty_step: Decimal = Decimal("0.001")
    min_qty: Decimal | None = None
    min_notional: Decimal | None = Decimal("5")


@dataclass
class FuturesTrade:
    side: Side
    entry_time: datetime
    entry_price: Decimal
    quantity: Decimal
    stop: Decimal
    target: Decimal | None
    liquidation_price: Decimal
    margin: Decimal
    entry_fee: Decimal
    funding: Decimal = ZERO
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_fee: Decimal = ZERO
    exit_reason: str | None = None

    @property
    def pnl(self) -> Decimal:
        assert self.exit_price is not None
        sign = 1 if self.side == "long" else -1
        gross = sign * (self.exit_price - self.entry_price) * self.quantity
        return gross - self.entry_fee - self.exit_fee + self.funding


@dataclass
class FuturesResult:
    trades: list[FuturesTrade]
    equity: pd.Series
    refused: dict[str, int]
    halted_at: datetime | None
    config: FuturesConfig = field(default_factory=FuturesConfig)


def _adverse(is_long: bool, rate: Decimal, *, buying: bool) -> Decimal:
    """Slippage multiplier that always works against the trader: buys pay up, sells take less."""
    if buying:
        return 1 + rate if is_long else 1 - rate
    return 1 - rate if is_long else 1 + rate


def _funding_times(index: pd.DatetimeIndex) -> set[datetime]:
    return {t for t in index if t.hour in (0, 8, 16) and t.minute == 0}


def run_futures_backtest(klines: pd.DataFrame, mark: pd.DataFrame, funding: pd.Series,
                         signals: list[FuturesSignal], cfg: FuturesConfig | None = None) -> FuturesResult:
    cfg = cfg or FuturesConfig()
    if not klines.index.equals(mark.index):
        mark = mark.reindex(klines.index).ffill()
    settlements = _funding_times(klines.index)
    rate_at = funding.reindex(klines.index, method="ffill")
    pending: dict[datetime, list[FuturesSignal]] = {}
    for sig in signals:
        bar = klines.index.searchsorted(sig.time, side="right")  # first bar AFTER the signal bar
        if bar < len(klines.index):
            pending.setdefault(klines.index[bar], []).append(sig)

    wallet = cfg.initial_equity
    peak = wallet
    open_trade: FuturesTrade | None = None
    trades: list[FuturesTrade] = []
    refused: dict[str, int] = {}
    equity_vals: list[Decimal] = []
    halted: datetime | None = None
    o = klines["open"].to_numpy(object)
    hi, lo = klines["high"].to_numpy(object), klines["low"].to_numpy(object)
    mhi, mlo, mcl = (mark[c].to_numpy(object) for c in ("mark_high", "mark_low", "mark_close"))

    def close(tr: FuturesTrade, price: Decimal, t: datetime, reason: str) -> Decimal:
        sign = 1 if tr.side == "long" else -1
        fee = abs(price) * tr.quantity * cfg.fee_rate
        tr.exit_price, tr.exit_fee, tr.exit_time, tr.exit_reason = price, fee, t, reason
        return sign * (price - tr.entry_price) * tr.quantity - fee

    for i, t in enumerate(klines.index):
        if open_trade is not None and t in settlements:
            notional = open_trade.quantity * Decimal(mcl[i])
            rate = Decimal(rate_at.iloc[i]) if pd.notna(rate_at.iloc[i]) else ZERO
            paid = notional * rate * (1 if open_trade.side == "long" else -1)
            open_trade.funding -= paid
            wallet -= paid

        # Entries at this bar's open.
        if open_trade is None and halted is None:
            for sig in pending.get(t, []):
                entry = Decimal(o[i]) * _adverse(sig.side == "long", cfg.slippage_rate, buying=True)
                size = size_futures_position(FuturesSizingInput(
                    side=sig.side, equity=wallet, available_margin=wallet, entry=entry, stop=sig.stop,
                    leverage=cfg.leverage, risk_fraction=cfg.risk_fraction, fee_rate=cfg.fee_rate,
                    slippage_rate=cfg.slippage_rate, maintenance_margin_rate=cfg.maintenance_margin_rate,
                    funding_rate_per_8h=ZERO, expected_hold_hours=Decimal(24), total_margin_in_use=ZERO,
                    asset_margin_in_use=ZERO, qty_step=cfg.qty_step, min_qty=cfg.min_qty,
                    min_notional=cfg.min_notional))
                if not size.approved:
                    key = (size.reason or "refused").split(":")[0][:60]
                    refused[key] = refused.get(key, 0) + 1
                    continue
                fee = entry * size.quantity * cfg.fee_rate
                open_trade = FuturesTrade(
                    side=sig.side, entry_time=t, entry_price=entry, quantity=size.quantity,
                    stop=sig.stop, target=sig.target, liquidation_price=size.liquidation_price or ZERO,
                    margin=size.margin, entry_fee=fee)
                wallet -= fee
                break

        # Protective checks within the bar: liquidation, stop (mark), target (last price).
        if open_trade is not None:
            tr = open_trade
            is_long = tr.side == "long"
            liq_hit = mlo[i] <= tr.liquidation_price if is_long else mhi[i] >= tr.liquidation_price
            stop_hit = mlo[i] <= tr.stop if is_long else mhi[i] >= tr.stop
            tgt_hit = tr.target is not None and (
                hi[i] >= tr.target if is_long else lo[i] <= tr.target)
            if liq_hit:
                # Isolated margin: the position's margin is lost, nothing more.
                wallet -= tr.margin
                tr.exit_price, tr.exit_time, tr.exit_reason = tr.liquidation_price, t, "liquidation"
                trades.append(tr)
                open_trade = None
            elif stop_hit:
                gapped = Decimal(o[i]) <= tr.stop if is_long else Decimal(o[i]) >= tr.stop
                px = Decimal(o[i]) if gapped else tr.stop
                exit_px = px * _adverse(is_long, cfg.slippage_rate, buying=False)
                wallet += close(tr, exit_px, t, "stop")
                trades.append(tr)
                open_trade = None
            elif tgt_hit:
                px = tr.target if tr.target is not None else ZERO
                wallet += close(tr, px * _adverse(is_long, cfg.slippage_rate, buying=False), t, "target")
                trades.append(tr)
                open_trade = None

        unreal = ZERO
        if open_trade is not None:
            sign = 1 if open_trade.side == "long" else -1
            unreal = sign * (Decimal(mcl[i]) - open_trade.entry_price) * open_trade.quantity
        equity = wallet + unreal
        equity_vals.append(equity)
        peak = max(peak, equity)
        if halted is None and peak > 0 and (peak - equity) / peak >= cfg.max_drawdown:
            halted = t  # no new entries; an open position is still managed

    if open_trade is not None:
        t_last = klines.index[-1]
        wallet += close(open_trade, Decimal(o[-1]), t_last, "end_of_data")
        trades.append(open_trade)
        equity_vals[-1] = wallet
    equity_series = pd.Series([float(x) for x in equity_vals], index=klines.index, name="equity")
    return FuturesResult(trades=trades, equity=equity_series, refused=refused, halted_at=halted, config=cfg)
