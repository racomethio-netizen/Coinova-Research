"""Multi-exchange gateway: common spot-only adapter interface and CCXT implementation."""

from nexus_exchange.base import ExchangeAdapter
from nexus_exchange.capabilities import CAPABILITIES
from nexus_exchange.factory import build_adapter

__all__ = ["CAPABILITIES", "ExchangeAdapter", "build_adapter"]
