| Module | Trades | Win rate | Mean R (net) | Profit factor | Mean R at 2x costs | Max drawdown | p-value | Result |
|---|---|---|---|---|---|---|---|---|
| Forex scalping momentum pullback | 6825 | 35.3% | -0.103 | 0.83 | -0.206 | 83.5% | 1.0000 | FAIL |
| Forex intraday trend breakout | 4222 | 35.1% | -0.093 | 0.85 | -0.168 | 64.5% | 1.0000 | FAIL |
| Forex swing trend following | 281 | 32.0% | -0.130 | 0.80 | -0.166 | 10.0% | 0.9770 | FAIL |
| Crypto scalping volume momentum | 240 | 38.3% | -0.117 | 0.81 | -0.288 | 7.5% | 0.9216 | FAIL |
| Crypto intraday breakout retest | 91 | 40.7% | -0.046 | 0.94 | -0.240 | 3.1% | 0.6783 | not eligible (earlier FAIL stands) |

Crypto swing trend momentum (replication of the 23 Nov pass): PASS — gates {"sharpe_ge_0.8": true, "max_drawdown_le_35pct": true, "both_halves_sharpe_ge_0.5": true, "positive_years_ge_60pct": true, "sharpe_above_buy_and_hold": true, "primary_period_positive": true}
* Forex scalping momentum pullback: failed gates: positive_expectancy, significant_after_holm, profit_factor, both_halves_positive, positive_at_2x_costs, max_drawdown
* Forex intraday trend breakout: failed gates: positive_expectancy, significant_after_holm, profit_factor, both_halves_positive, positive_at_2x_costs, max_drawdown
* Forex swing trend following: failed gates: positive_expectancy, significant_after_holm, profit_factor, both_halves_positive, positive_at_2x_costs
* Crypto scalping volume momentum: failed gates: positive_expectancy, significant_after_holm, profit_factor, both_halves_positive, positive_at_2x_costs
