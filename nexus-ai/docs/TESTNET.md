# Binance Spot TESTNET round trip

Proves real connectivity and execution with **test funds** (testnet.binance.vision). It
never uses a production key.

## Why it runs on the server

* testnet.binance.vision answers HTTP 451 ("restricted location") to the development
  container, as the main API does. NEXUS never bypasses that.
* The Render service in Frankfurt can reach Binance, so the run happens there, started by
  the owner.

## Owner steps

1. **Create testnet keys.**
   * Open https://testnet.binance.vision, log in with GitHub, choose **Generate HMAC_SHA256
     Key**.
   * Testnet keys hold only test funds. Still, do not paste them in chat.
2. **Store them in NEXUS.** Dashboard → **Connection** → add a key, environment **testnet**.
   They are stored encrypted, exactly like production keys.
3. **Run it.** Dashboard → **Connection** → **Binance Spot TESTNET round trip**: enter your
   two-factor code and press **Run**.

## What one run does (`nexus_execution/testnet_run.py`)

| Step | Evidence recorded |
|---|---|
| Connectivity | `fetch_time` latency |
| Exchange info | tick, step, minimum amount and notional for BTC/USDT |
| Balances | testnet USDT and BTC before and after |
| Market data | order book (best bid / ask), about 300 closed 1h candles |
| Signal | EMA50, EMA200, RSI14, ADX14, ATR14, 14-bar momentum, five checks, LONG or NO_TRADE |
| Risk | size from 0.25% of testnet USDT with a 1.5 ATR stop, fees and slippage; rejected sizes are not sent |
| Buy | marketable LIMIT IOC; deterministic UUIDv4 client ID; looked up before submit |
| Reconcile | status, filled, average price, fee, read back from Binance by client ID |
| Sell | LIMIT IOC of the filled quantity, reconciled the same way |
| Restart drill | the same buy is "placed" again and must be found, not resubmitted |

* The evidence is stored in the append-only audit log (`testnet_round_trip`) and shown on
  the Connection page.
* `GET /api/v1/system/testnet/round-trip` returns the last ten runs. Keys never appear.

## Start NEXUS locally (Docker)

```bash
cd nexus-ai
cp .env.example .env              # fill in secrets locally; never commit .env
docker compose up -d --build      # API, workers, dashboard, PostgreSQL, Redis, Nginx
docker compose exec api alembic upgrade head
docker compose exec api python -m nexus_api.cli create-owner
```

Production runs on Render (`DEPLOY_RENDER.md` in the deploy repository).
