# NEXUS multi-asset platform

Goal: one trading system for crypto, stocks, commodities and forex, with **one switch per
market**. It is not four separate bots. The audit and the full stage plan are in
docs/MULTI_ASSET_AUDIT_2026-11-26.md.

## Stage status

| Stage | Status |
|---|---|
| 1. Market registry and per-market autonomy switches | **done** (2026-11-26) |
| 2. Instrument and session model, base-currency accounting | not started |
| 3. Research data and pre-registered backtests for stocks, commodities, forex | not started; needs the owner to approve a data source |
| 4. Interactive Brokers connector (stocks, ETFs, commodity futures), paper account first | not started; needs an IBKR account and a host for IB Gateway |
| 5. Forex connector (MT5 on a Windows host, or a Linux-native broker API) | not started; needs the owner's choice of broker and host |
| 6. Cross-market risk budget and consolidated dashboard | not started |
| 7. LIVE, one market at a time | owner authorization per market |

## The per-market switch (Stage 1)

Code: `packages/common/nexus_common/markets.py`.

| Setting | Meaning |
|---|---|
| OFF | No new signals or orders. Open positions keep their stops, targets and kill switch. |
| RESEARCH | Data and analysis. Signals are recorded with their risk verdict; no orders. |
| PAPER | Signals that pass every check become paper orders. |
| LIVE | Real orders. Needs that market's own phrase (`ENABLE LIVE CRYPTO`, `ENABLE LIVE STOCKS`, …), the owner's 2FA code and the market's live checks. A trader started in live mode is also required. |

**Rules.**
* **A market switch only narrows.** The effective mode is the more restrictive of the
  global operating mode and the market's switch. For example:
  * global PAPER_TRADING + crypto RESEARCH = signals only;
  * global PAPER_TRADING + crypto LIVE = still paper.
* **Durable and audited.** Each change is a `market_autonomy_set` row in the append-only
  audit log (actor, reason, previous value). Only the owner's rows count.
* **Defaults.** Crypto defaults to PAPER, today's behaviour. Stocks, commodities and
  forex default to OFF.
* **Fail closed.** A stored value that can't be read, or one the market can't support,
  means OFF.
* **Only supported modes can be chosen.** Stocks, commodities and forex accept only OFF
  until their data source, paper engine and broker connector exist; the API explains
  what is missing.
* **Where it is enforced for crypto:**
  * the main trader (paper and live), which combines the switch with the operating mode
    each cycle;
  * the spot engine and the NEXUS bot (same runner), which record the signal with the
    reason "crypto market switch is …" and place no order.

  Paper research ledgers (forward tests, grids, rebalancing, volatility target) place no
  broker orders and are not affected.

### API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/markets` | Every market: switch, allowed settings, effective mode, venues with connector status, what the owner must provide, LIVE blockers, crypto paper positions |
| POST | `/api/v1/markets/{market}/autonomy` | Body `{"autonomy": "RESEARCH", "reason": "…"}`. LIVE also needs `confirm` (the phrase) and `totp`. Owner only, with the CSRF header. |

Dashboard: **Overview → All markets** (`/multi-asset/`).

### Configuration

Stage 1 adds no environment variables. The switches live in the database, so they survive
restarts and deploys and need no redeploy to change. Example:

```bash
# Watch crypto signals without paper orders for a while (owner session cookie + CSRF header)
curl -X POST https://<host>/api/v1/markets/crypto/autonomy \
  -H 'Content-Type: application/json' -H 'x-nexus-csrf: 1' -b cookies.txt \
  -d '{"autonomy": "RESEARCH", "reason": "observe before resuming paper"}'
```

## Deployment notes for the coming connectors

* **Interactive Brokers (Stage 4).**
  * IB Gateway (Java, about 1 GB RAM) must run where NEXUS can reach it. It needs the
    owner's login and periodic two-factor confirmation (to verify at connection time).
  * Plan: a small separate host or private service. **Not Railway.**
  * Start with an IBKR paper account.
* **MetaTrader 5 (Stage 5).**
  * The official `MetaTrader5` Python package works only on Windows with the MT5 terminal
    running, so it needs a Windows host acting as a bridge.
  * A Linux-native broker API (for example OANDA v20) is the alternative.
* **Credentials.**
  * Broker keys are stored encrypted like exchange keys.
  * No withdrawal or transfer rights.
  * Read-only first.
* **Region.** Brokers accept clients by country. NEXUS never bypasses a regional
  restriction.
