# Market-data sources: terms and availability review (decision D5)

> Engineering research, **not legal advice**. Reviewed 2026-11-10 from public
> pages. Counsel should confirm before any paid or public use (gate G-4).

## Findings

| Source | Data we need | What the public material says | Open questions |
|---|---|---|---|
| **Binance** (spot, `data-api.binance.vision`, archives on `data.binance.vision`) | 15m/1h/4h/1d klines for USDT pairs, ticker, monthly archives with SHA-256 checksums | Binance documents `data-api.binance.vision` as the base URL for public market-data-only use (no authentication). The downloadable datasets have their own **Binance Vision Terms of Use**, which say that use of data derived from the datasets (including charts, models and indicators) is governed by those terms. A developer-forum reply says there is no policy specific to public API market data, but that is a user's reading, not an official statement. Users report **HTTP 451** ("unavailable from a restricted location") from some regions | Commercial display of derived analytics, and redistribution, are not explicitly granted. Geo-blocking of the market-data host depends on the hosting region |
| **Coinbase Exchange** | USD-quoted candles (not USDT) | Public Market Data API use is bound by the **Coinbase Market Data Terms**, which reserve all rights not expressly granted. Coinbase runs an **authorized redistribution program** (named partners), which suggests redistribution needs an agreement | The candles are USD-quoted, so they don't match the BTC/USDT spec |
| **Kraken** | OHLC (public, keyless) | The public endpoints need no account. The global Terms of Service don't address API data redistribution in the material found. The institutional page says to "get in touch" for larger-scale access | Commercial and redistribution use is not addressed |
| **CoinGecko** (already integrated) | Aggregated prices and fundamentals | Commercial plans exist, with attribution | OHLC granularity is too coarse for 15m signals |

Sources:
- [Binance: Market Data Only URLs](https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only)
- [Binance Vision Terms of Use (PDF)](https://data.binance.vision/Binance_Vision-Terms_of_Use.pdf)
- [Binance dev forum: public REST API for commercial use](https://dev.binance.vision/t/public-rest-api-for-commercial-use-case/23067)
- [Binance dev forum: error 451](https://dev.binance.vision/t/api-error-451-unavailable-for-legal-reasons/13828)
- [Coinbase Market Data Terms](https://www.coinbase.com/legal/market_data)
- [Coinbase institutional market data (redistribution partners)](https://www.coinbase.com/institutional/market-data)
- [Kraken Terms of Service](https://www.kraken.com/legal/global-terms)
- [Kraken public endpoint examples](https://support.kraken.com/au/articles/360000919986-public-endpoint-examples-you-can-try-them-directly-in-a-web-browser-)

## Recommendation and what was built

1. **Engineering:** Binance public klines are the first adapter. They have the best
   documentation, native USDT pairs on all four timeframes, and checksummed history.
   The engine is source-agnostic (`CandleSource` protocol), so a second or licensed
   source can be added without schema changes.
2. **Compliance (enforced in code):** `market_sources.terms_reviewed_at` must be
   set by an MFA admin before **production** ingestion runs. Development and staging
   can ingest for internal research.
3. **Before any public or paid display:** get written confirmation from the
   exchange that the intended use is permitted, **or** license data from an authorized
   vendor. Record the outcome in `market_sources.terms_notes`.
4. **Hosting region:** choose one where the market-data host does not return 451.
   Verify with `nexus-engine ingest` from the target region before committing. Never
   use proxies or IP rotation to get around a block.

## Owner decision still needed (D5)
Approve or replace the recommendation above, and choose the hosting region.
