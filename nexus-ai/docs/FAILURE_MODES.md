# Failure modes: behaviour and proof

Every scenario from the owner's Step 14 list, what NEXUS does, and the automated test that
demonstrates it (`scripts/test.sh` runs all of them; integration tests need the test
Postgres and Redis). Principle throughout: when in doubt, do not trade; never resubmit an
order whose outcome is unknown; never invent data.

| Scenario | Behaviour | Test(s) |
|---|---|---|
| Binance disconnect | Streams reconnect with backoff and report state; health marks the venue down; a missing or stale book blocks entries; paper refuses fills on books older than 10 s | `unit/test_stream.py::test_reconnects_after_errors_and_stale_and_reports_state`, `integration/test_market_ingest.py::test_health_transitions`, `integration/test_paper_trader.py::test_stale_or_missing_book_blocks_trading`, `unit/test_paper_realism.py::test_stale_book_is_an_outage_not_a_fill` |
| API timeout | A submit timeout marks the order `unknown`; it is looked up by client order ID and never resent; new entries are blocked while anything is unknown | `integration/test_execution.py::test_timeout_goes_unknown_never_resubmits_then_reconciles`, `integration/test_paper_trader.py::test_timeout_after_send_is_reconciled_not_resubmitted`, `exchange/test_binance_futures.py::test_timeout_on_submit_is_unknown_and_never_resubmitted` |
| Invalid API key | The connection test reports "authentication failed" with Binance's code only (no key material); account views show the error | `unit/test_connection.py::test_explain_auth_failure_keeps_binance_code_only` |
| Binance IP ban / rate limit | Account calls: a -1003/418 ban is stored, no request-time calls until it ends. Public market data (spot and futures, every worker and the API): one 429/418 pauses ALL callers of that API until Binance's stated time (Retry-After / "banned until"); unknown back-off state = do not call; missed hours are recorded as gaps | `unit/test_binance_ban.py`, `unit/test_binance_guard.py`, `integration/test_api_security.py::test_live_account_views` |
| Duplicate signal | The same strategy/bar is recorded once (idempotent) | `integration/test_paper_trader.py::test_kill_switch_and_duplicate_signals` |
| Duplicate order | One active order per pair/side, enforced by a database unique index | `integration/test_execution.py::test_duplicate_active_order_blocked_by_database`, `integration/test_schema.py::test_no_duplicate_open_exposure_across_exchanges` |
| Partial fill | Fills are applied once (idempotent); the position equals what actually filled; paper simulates partials | `integration/test_execution.py::test_partial_fills_idempotent_across_refreshes`, `unit/test_paper_realism.py::test_participation_cap_creates_partial_fills` |
| Missing candles | Gaps are detected and reported at ingest; the decision gate refuses to trade without enough history | `integration/test_market_ingest.py::test_sync_and_backfill_paginates_and_reports_gaps`, `integration/test_paper_trader.py::test_decision_gate_refuses_without_candle_history` |
| Stale data | Stale books and stale market analysis are rejected | `integration/test_paper_trader.py::test_stale_or_missing_book_blocks_trading`, `unit/test_agents.py::test_stale_market_data_is_rejected` |
| Sudden market crash | A gap through the stop exits at the best available price (bounded IOC), the position closes, and the review flags a risk failure | `integration/test_paper_trader.py::test_crash_through_the_stop_exits_at_market_and_is_flagged`, `unit/test_backtest.py::test_gap_through_stop_fills_at_open` |
| Extreme volatility | `high_vol` regime: strategies do not signal and the trader rejects it; the NO TRADE rule blocks top-decile ATR | `integration/test_paper_trader.py::test_high_volatility_regime_is_never_traded`, `unit/test_agents.py::test_risk_manager_cannot_be_overridden` |
| Daily loss limit reached | New entries are rejected for the rest of the day | `integration/test_paper_trader.py::test_daily_loss_limit_blocks_new_entries`, `unit/test_risk.py` |
| Database failure | The trader loop logs and keeps running; nothing is sent; the kill-switch record being unreadable blocks entries; `/health/ready` reports the failure | `integration/test_paper_trader.py::test_database_outage_mid_run_is_survived_without_orders`, `integration/test_paper_trader.py::test_unreadable_kill_switch_record_blocks_entries`, `integration/test_api.py::test_ready_reports_failure_honestly` |
| Redis failure | The kill switch reads as engaged (fail closed); signal handling stops before any order; a lost kill switch is restored from the audit log | `integration/test_paper_trader.py::test_redis_outage_fails_closed`, `unit/test_risk.py::TestKillSwitch::test_fails_closed`, `integration/test_paper_trader.py::test_kill_switch_restored_from_audit_log_after_redis_loss` |
| Restart with an open position | A new engine reloads the portfolio and keeps managing the stop | `integration/test_paper_trader.py::test_restart_with_open_position_keeps_protecting_it` |
| Worker crash | Supervised workers restart with capped backoff | `unit/test_workers.py::test_inline_restarts_crashes_and_stops_cleanly` |
| AI service unavailable | Missing analysis, X data or derivatives = "unavailable" and NO TRADE; X being unconfigured never blocks or enables trading | `integration/test_api_security.py::test_agent_proposals_say_no_trade_without_data_and_reviews_empty`, `unit/test_social.py::test_trader_x_gate_blocks_only_listed_coins_and_ignores_missing_data` |
| News misinformation | A single low-credibility X post is "unconfirmed" with fake-news risk and never blocks or triggers anything; news can only pause entries | `unit/test_social.py::test_single_low_credibility_rumor_is_unconfirmed_and_never_blocks`, `integration/test_paper_trader.py::test_news_block_stops_entries` |
| Stale market data | Newest closed 5m bar of a traded pair ended > 15 min ago: risk state NO_NEW_TRADES until fresh data arrives | `integration/test_paper_trader.py::test_stale_candles_put_risk_state_in_no_new_trades` |
| Balances differ from the exchange (live) | Exchange holds less than the ledger: CLOSE_ONLY, resting entries cancelled, audit + alert, ledger untouched; fetch failure or ban: NO_NEW_TRADES | `integration/test_paper_trader.py::test_live_balance_reconciliation`, `integration/test_paper_trader.py::test_reconciliation_mismatch_means_close_only_and_cancels_entries` |
| Restart with a restricted risk state | The state is re-derived from durable inputs; no fake transition is logged | `integration/test_paper_trader.py::test_risk_state_blocks_signals_and_survives_restart` |
| Restart during order execution | An order left `pending_submit` for 2 minutes becomes `unknown` and is resolved by client order ID: adopted with its fills if the exchange has it, rejected with funds released if not. Never resubmitted. Fill, ledger and position are written in one transaction | `integration/test_execution.py::test_crash_after_submit_before_recording_is_recovered_by_client_id`, `::test_crash_before_submit_is_rejected_and_funds_released`, `::test_failure_inside_fill_application_leaves_no_partial_state`, `integration/test_paper_trader.py::test_crash_between_submit_and_record_in_trader_cycle_recovers` |
| Approaching Binance's request limit | At 80% of the per-minute weight every caller pauses until the next minute | `unit/test_binance_guard.py::test_weight_budget_pauses_every_caller_until_next_minute` |

## Known limits

* Redis down while a signal is being handled leaves that signal row undecided (no order is
  sent; the next bar produces a new signal).
* Live-exchange behaviour is verified against Binance demo/testnet only when the owner
  runs the order tests; production order paths are never exercised automatically.
