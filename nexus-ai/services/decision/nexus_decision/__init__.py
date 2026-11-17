"""Decision engine (score 0-100, explained) and no-trade intelligence.

Deterministic by design: every score component is a rule over measured inputs, and every
decision carries the reasons behind it. No language model is involved in execution.
"""

from nexus_decision.engine import Decision, MarketSnapshot, decide
from nexus_decision.no_trade import NoTradeInputs, no_trade_reasons

__all__ = ["Decision", "MarketSnapshot", "NoTradeInputs", "decide", "no_trade_reasons"]
