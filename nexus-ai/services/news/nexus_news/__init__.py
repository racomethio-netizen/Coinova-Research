"""News-risk monitoring from public, free feeds.

Feeds: Federal Reserve press releases, SEC press releases, Kraken and Coinbase status pages.
A feed that cannot be read is reported as UNKNOWN, never as "no news". Unknown news risk
blocks new trades (see nexus_decision.no_trade).
"""

from nexus_news.feeds import FEEDS, Item, fetch_items, parse_feed
from nexus_news.scoring import NewsRisk, score_items

__all__ = ["FEEDS", "Item", "NewsRisk", "fetch_items", "parse_feed", "score_items"]
