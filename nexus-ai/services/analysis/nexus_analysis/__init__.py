"""Market analysis: market structure (swings, BOS, CHoCH), support/resistance, supply and
demand zones, liquidity sweeps, BTC correlation.

Every function is causal: the value at bar ``t`` uses only bars with ``open_time <= t``.
A swing high/low is only known ``right`` bars after it forms, so it is reported at the
confirmation bar, never at its own bar.
"""

from nexus_analysis.correlation import rolling_correlation
from nexus_analysis.levels import supply_demand_zones, support_resistance
from nexus_analysis.structure import StructureState, market_structure, swings
from nexus_analysis.sweeps import liquidity_sweeps

__all__ = [
    "StructureState",
    "liquidity_sweeps",
    "market_structure",
    "rolling_correlation",
    "supply_demand_zones",
    "support_resistance",
    "swings",
]
