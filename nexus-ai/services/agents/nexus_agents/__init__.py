"""Specialised analysis agents, trade proposals, an independent Risk Manager and trade review.

All deterministic and explainable: no language model sits in the decision path. Agents
only analyse; the Risk Manager can only reject; nothing here places orders. Execution stays
in the trader, which applies the same gates again.
"""

from nexus_agents.agents import (
    AgentReport,
    derivatives_agent,
    market_intelligence_agent,
    portfolio_agent,
    quant_agent,
    sentiment_agent,
    technical_agent,
)
from nexus_agents.proposal import Context, build_proposal
from nexus_agents.review import review_trade

__all__ = ["AgentReport", "Context", "build_proposal", "derivatives_agent",
           "market_intelligence_agent", "portfolio_agent", "quant_agent", "review_trade",
           "sentiment_agent", "technical_agent"]
