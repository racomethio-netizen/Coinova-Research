"""The six strategy modules of the unified brief (section C), in one framework.

Each module has its own settings (a frozen config), its own signal function, its own
historical test (``nexus_backtest.modules_bt``), its own forward record
(``nexus_backtest.module_observer``) and its own on/off switch (``strategy_modules`` table).
A module only proposes; the risk engine decides and nothing here places an order.
"""

from __future__ import annotations

from datetime import timedelta

from nexus_strategies.modules.base import (
    MIN_RR,
    SIGNAL_COLUMNS,
    ModuleSpec,
    crypto_frames,
    fx_frames,
    resample,
)
from nexus_strategies.modules.crypto import (
    CryptoBreakoutRetestConfig,
    CryptoScalpConfig,
    CryptoSwingConfig,
    crypto_breakout_retest_signals,
    crypto_scalp_signals,
)
from nexus_strategies.modules.fx import (
    FxBreakoutConfig,
    FxScalpConfig,
    FxSwingConfig,
    fx_breakout_signals,
    fx_scalp_signals,
    fx_swing_signals,
)

FX_PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF")
CRYPTO = ("BTCUSDT", "ETHUSDT")

MODULES: dict[str, ModuleSpec] = {m.code: m for m in (
    ModuleSpec("fx_scalp_pullback", "Forex scalping momentum pullback", "forex", "scalping",
               FX_PAIRS, "5m", "5m", FxScalpConfig(), fx_scalp_signals, False,
               timedelta(minutes=5)),
    ModuleSpec("fx_intraday_breakout", "Forex intraday trend breakout", "forex", "intraday",
               FX_PAIRS, "5m", "5m", FxBreakoutConfig(), fx_breakout_signals, False,
               timedelta(minutes=5)),
    ModuleSpec("fx_swing_trend", "Forex swing trend following", "forex", "swing",
               FX_PAIRS, "1d", "1h", FxSwingConfig(), fx_swing_signals, False,
               timedelta(days=3)),
    ModuleSpec("crypto_scalp_volume", "Crypto scalping volume momentum", "crypto", "scalping",
               CRYPTO, "5m", "5m", CryptoScalpConfig(), crypto_scalp_signals, True,
               timedelta(minutes=5)),
    ModuleSpec("crypto_breakout_retest", "Crypto intraday breakout retest", "crypto",
               "intraday", CRYPTO, "1h", "5m", CryptoBreakoutRetestConfig(),
               crypto_breakout_retest_signals, True, timedelta(minutes=5),
               evidence="earlier_fail",
               note="The existing breakout-and-retest rule. Failed its pre-registered test "
                    "on 23 Nov 2026 (profit factor 0.71); that verdict stands."),
    ModuleSpec("crypto_swing_trend", "Crypto swing trend momentum", "crypto", "swing",
               CRYPTO, "1d", "1d", CryptoSwingConfig(), None, True, timedelta(days=1),
               evidence="earlier_pass",
               note="\"Catching Crypto Trends\": passed its pre-registered test on 23 Nov "
                    "2026 (Sharpe 1.11, max drawdown 15.6%) and runs as the trend-paper "
                    "forward paper test.",
               extra={"forward_worker": "trend-paper"}),
)}


def get(code: str) -> ModuleSpec:
    try:
        return MODULES[code]
    except KeyError:
        raise LookupError(f"unknown strategy module {code!r}") from None


__all__ = ["CRYPTO", "FX_PAIRS", "MIN_RR", "MODULES", "SIGNAL_COLUMNS", "ModuleSpec",
           "crypto_frames", "fx_frames", "get", "resample"]
