# NEXUS risk policy

The risk engine is deterministic code. No AI model, agent or signal can override it. A trade
reaches the execution engine only after every check below passes. Changes to the limits
need an explicit owner mandate (CLAUDE.md).

## Limits (paper defaults since 2026-11-21; ceilings in brackets)

| Control | Value |
|---|---|
| Risk per trade | 0.25% of equity (ceiling 1%) |
| Daily loss limit | 1% (ceiling 2%) |
| Weekly loss limit | 3% (ceiling 4%) |
| Maximum drawdown | 10%: engages the kill switch |
| Open positions | 3 maximum |
| Portfolio exposure | 50% of equity |
| Single-asset concentration | 25% |
| Correlated exposure | 35% (correlation >= 0.7) |
| Cooldown after a losing exit | 120 minutes (floor 30) |
| Losing streak | risk reduced after 2 losses in a row, entries paused after 4 (owner reset) |
| Spread | 25 bps maximum |
| Price age | 60 s maximum |
| Futures | disabled; when enabled: isolated margin, leverage 2-5x, never raised automatically |
| Never | martingale, averaging down, sizing from leverage alone, trading without a stop |

Position size = risk budget / (stop distance + fees + slippage), then capped by cash,
exposure, concentration and the exchange's quantity and notional rules
(`nexus_risk/sizing.py`).

## Risk states (`nexus_risk/state.py`)

| State | Entries | Resting entry orders | Open positions | Triggered by |
|---|---|---|---|---|
| NORMAL | yes, full risk | kept | managed | nothing restricting |
| REDUCED_RISK | yes, reduced size | kept | managed | a loss limit >= 80% used (x0.5), a losing streak (streak multiplier), owner floor |
| NO_NEW_TRADES | no | kept | managed (stops, targets, trailing) | daily or weekly loss limit, unknown orders, a position that cannot be priced, stale market data (newest closed 5m bar ended > 15 min ago), balance check unavailable, paused streak, owner floor |
| CLOSE_ONLY | no | cancelled | managed | exchange balances LOWER than the NEXUS ledger, owner floor |
| EMERGENCY_STOP | no | cancelled | closed only on the owner's explicit request | kill switch, maximum drawdown |

Rules:

* The strictest triggered state wins; every reason is shown on the Risk page and in the
  audit log.
* The state is re-derived every trader cycle (~30 s) from durable inputs: equity snapshots
  (Postgres), the kill switch (Redis + audit-log copy), the owner floor (audit log), order
  and reconciliation state. A restart cannot reset a loss limit or the emergency lock.
* Every change of state is written to the append-only audit log (`risk_state_changed`, with
  from, to and reasons) and sent to Telegram.
* Owner floor: the owner can set a minimum state (NORMAL to CLOSE_ONLY). It only adds
  restrictions. Making it stricter is always one click; relaxing it in live mode needs 2FA.
  EMERGENCY_STOP is the kill switch: engaging is one click, clearing needs 2FA and is never
  automatic.
* Unreadable inputs fail closed: an unreadable floor counts as NO_NEW_TRADES, an unreadable
  kill-switch record as EMERGENCY_STOP for that cycle.

## Balance reconciliation (live mode; `nexus_execution/reconcile.py`)

* At most once a minute the trader asks Binance for the account balances and compares them
  with the NEXUS ledger, per asset.
* The exchange must hold at least what NEXUS believes it holds (tolerance 0.1%, for fees
  and rounding). More on the exchange is fine (your own funds) and is shown as
  "unmanaged".
* Less on the exchange = MISMATCH = CLOSE_ONLY until you investigate. A failed or banned
  fetch = UNAVAILABLE = NO_NEW_TRADES.
* Never edits the ledger or the account. The exchange's report is stored separately
  (balances with source `exchange`).
* Paper mode: not applicable (the paper ledger is the record).

## Operating modes (`nexus_common/opmode.py`)

| Mode | Signals recorded | Orders |
|---|---|---|
| Analysis only | no | none |
| Signals only | yes, with the full risk verdict | none |
| Paper trading (default) | yes | paper orders on real order books |
| Live autonomous | yes | real orders, only from a trader the server started in live mode |

Open positions are protected (stops, targets, trailing, kill switch) in every mode.
Lowering the mode is always one step. Live autonomous needs the typed phrase, 2FA and
every gate: server configuration, owner approval, kill switch clear, verified API key
without withdrawals, balance reconciliation OK, risk state allowing entries, a validated
strategy, and a funded live ledger. Stored in the audit log; unreadable = Analysis only.

## Live ledger

The live trader only uses money you explicitly assign to it: typed phrase + 2FA, at most
the free balance Binance reported in the last 10 minutes. Nothing moves on Binance.

## Binance request budget

Every response reports the IP's used request weight. At 50% of the limit NEXUS logs a
warning; at 80% every NEXUS caller pauses until the next minute. A 429/418 pauses all
callers until the time Binance gives.

## Emergency controls

* STOP TRADING: engage the kill switch (Risk page), one click, no 2FA.
* CLOSE POSITIONS: separate action, only while the kill switch is engaged, typed
  confirmation phrase, plus 2FA for live.
