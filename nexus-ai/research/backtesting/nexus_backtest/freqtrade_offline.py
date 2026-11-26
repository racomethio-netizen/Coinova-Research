"""Run Freqtrade with Binance's market rules from a saved file: no exchange connection.

Freqtrade loads Binance's ``exchangeInfo`` (price tick, lot step, minimum notional) at
start-up, even for a backtest. This container cannot reach api.binance.com (HTTP 451, a
regional restriction that NEXUS respects and does not bypass). The same document is
published on Binance's public market-data host ``data-api.binance.vision``. It is
downloaded once, saved, and served from the file here, so the backtest makes no exchange
calls at all.

Usage (with Freqtrade's own interpreter):
    python freqtrade_offline.py <exchangeInfo.json> backtesting -c ... --strategy ...
"""

from __future__ import annotations

import json
import sys
from typing import Any


def install(path: str) -> None:
    import ccxt  # type: ignore[import-untyped,unused-ignore]
    import ccxt.async_support as ccxt_async  # type: ignore[import-untyped,unused-ignore]

    with open(path) as f:
        info: dict[str, Any] = json.load(f)

    def sync_info(self: Any, params: Any = None) -> dict[str, Any]:
        return info

    async def async_info(self: Any, params: Any = None) -> dict[str, Any]:
        return info

    classes: list[tuple[Any, Any]] = [(ccxt.binance, sync_info), (ccxt_async.binance, async_info)]
    try:
        import ccxt.pro as ccxt_pro  # type: ignore[import-untyped,unused-ignore]

        classes.append((ccxt_pro.binance, async_info))
    except ImportError:
        pass
    for cls, fn in classes:
        cls.publicGetExchangeInfo = fn
        original = cls.__init__

        def init(self: Any, config: Any = None, _orig: Any = original) -> None:
            _orig(self, config or {})
            self.options["fetchMarkets"] = {"types": ["spot"]}   # no futures/option hosts
            self.options["fetchMargins"] = False

        cls.__init__ = init


def main() -> None:
    install(sys.argv[1])
    sys.argv = ["freqtrade", *sys.argv[2:]]
    from freqtrade.main import main as ft_main  # type: ignore[import-not-found,unused-ignore]

    ft_main()


if __name__ == "__main__":
    main()
