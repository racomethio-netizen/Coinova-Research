"""Coin detection, crypto sentiment lexicon and news-event detection (pure functions)."""

from __future__ import annotations

import re
from dataclasses import dataclass

COIN_ALIASES: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "eth", "ether"),
    "SOL": ("solana", "sol"), "XRP": ("xrp", "ripple"), "BNB": ("bnb",),
    "DOGE": ("dogecoin", "doge"), "ADA": ("cardano", "ada"), "AVAX": ("avalanche", "avax"),
    "LINK": ("chainlink",), "TON": ("toncoin",), "TRX": ("tron", "trx"),
}
# Bare words that are also ordinary English: only their $cashtag / #hashtag counts.
AMBIGUOUS = {"sol", "ada", "link", "ton", "ether"}

_WORD = re.compile(r"[$#]?[a-z0-9']+")
_URL = re.compile(r"https?://\S+")


def tokens(text: str) -> list[str]:
    return _WORD.findall(_URL.sub(" ", text.lower()))


def coin_terms(coin: str) -> tuple[str, ...]:
    return COIN_ALIASES.get(coin.upper(), (coin.lower(),))


def detect_coins(text: str, coins: list[str]) -> set[str]:
    toks = set(tokens(text))
    found = set()
    for c in coins:
        for term in coin_terms(c):
            if f"${term}" in toks or f"#{term}" in toks or (
                    term not in AMBIGUOUS and term in toks):
                found.add(c.upper())
                break
        else:
            if f"${c.lower()}" in toks or f"#{c.lower()}" in toks:
                found.add(c.upper())
    return found


def search_query(coins: list[str]) -> str:
    """One recent-search query covering every coin (cashtags + unambiguous names)."""
    terms: list[str] = []
    for c in coins:
        terms.append(f"${c.upper()}")
        terms += [t for t in coin_terms(c) if t not in AMBIGUOUS and t != c.lower()]
    return f"({' OR '.join(dict.fromkeys(terms))}) lang:en -is:retweet -is:reply"


# ------------------------------------------------------------------ sentiment
BULLISH: dict[str, float] = {
    "bullish": 1.0, "moon": 0.8, "mooning": 0.9, "pump": 0.5, "pumping": 0.6, "rally": 0.8,
    "rallying": 0.8, "breakout": 0.7, "ath": 0.8, "surge": 0.8, "surges": 0.8, "soar": 0.8,
    "soars": 0.8, "gains": 0.5, "green": 0.4, "buy": 0.4, "buying": 0.5, "accumulate": 0.6,
    "accumulating": 0.6, "long": 0.3, "approval": 0.6, "approved": 0.7, "adoption": 0.6,
    "inflows": 0.7, "inflow": 0.6, "upgrade": 0.4, "partnership": 0.5, "bounce": 0.4,
    "recover": 0.4, "recovery": 0.5, "strong": 0.4, "higher": 0.3, "up": 0.2, "uptrend": 0.7,
    "support": 0.2, "listing": 0.4, "listed": 0.4, "launch": 0.3, "record": 0.4,
}
BEARISH: dict[str, float] = {
    "bearish": 1.0, "dump": 0.8, "dumping": 0.9, "crash": 1.0, "crashing": 1.0, "plunge": 0.9,
    "plunges": 0.9, "selloff": 0.8, "sell": 0.4, "selling": 0.5, "short": 0.3, "red": 0.4,
    "rekt": 0.8, "liquidated": 0.7, "liquidations": 0.6, "hack": 1.0, "hacked": 1.0,
    "exploit": 1.0, "exploited": 1.0, "scam": 0.9, "rug": 1.0, "rugpull": 1.0, "fraud": 1.0,
    "lawsuit": 0.8, "sued": 0.8, "ban": 0.8, "banned": 0.8, "crackdown": 0.8,
    "investigation": 0.6, "outflows": 0.7, "outflow": 0.6, "delist": 0.9, "delisting": 0.9,
    "insolvent": 1.0, "bankruptcy": 1.0, "halt": 0.6, "halted": 0.7, "paused": 0.5,
    "fear": 0.5, "weak": 0.4, "lower": 0.3, "down": 0.2, "downtrend": 0.7, "drop": 0.5,
    "drops": 0.5, "fell": 0.5, "falls": 0.5, "decline": 0.5, "rejected": 0.5, "denied": 0.6,
}
EMOJI_BULL = {"🚀": 0.6, "📈": 0.6, "🟢": 0.3, "💎": 0.3, "🐂": 0.6}
EMOJI_BEAR = {"📉": 0.6, "🔴": 0.3, "🩸": 0.6, "💀": 0.4, "🐻": 0.6}
NEGATORS = {"not", "no", "never", "isn't", "aren't", "wasn't", "don't", "doesn't", "won't",
            "without", "hardly"}


def sentiment(text: str) -> tuple[float, int]:
    """(score in [-1, 1], number of sentiment cues). Negation within 3 words flips a cue."""
    toks = tokens(text)
    total, hits = 0.0, 0
    for i, t in enumerate(toks):
        w = t.lstrip("$#")
        v = BULLISH.get(w, 0.0) - BEARISH.get(w, 0.0)
        if v:
            if any(x in NEGATORS for x in toks[max(0, i - 3):i]):
                v = -v * 0.7
            total += v
            hits += 1
    for e, v in EMOJI_BULL.items():
        n = text.count(e)
        total += v * min(n, 3)
        hits += n
    for e, v in EMOJI_BEAR.items():
        n = text.count(e)
        total -= v * min(n, 3)
        hits += n
    if hits == 0:
        return 0.0, 0
    return max(-1.0, min(1.0, total / max(1.0, hits ** 0.5 * 1.2))), hits


# ------------------------------------------------------------------ events
@dataclass(frozen=True)
class EventType:
    code: str
    label: str
    pattern: re.Pattern[str]
    direction: int        # -1 negative for price, +1 positive, 0 unclear
    high_impact: bool


EVENTS: tuple[EventType, ...] = (
    EventType("hack", "Hack / exploit", re.compile(
        r"\b(hack(ed)?|exploit(ed)?|drain(ed)?|stolen|breach)\b", re.I), -1, True),
    EventType("regulatory", "Regulatory", re.compile(
        r"\b(sec|cftc|doj|regulat\w*|lawsuit|sued|charges?|ban(ned)?|crackdown|etf|"
        r"approv(al|ed)|den(ied|ial)|subpoena|sanction\w*)\b", re.I), 0, True),
    EventType("exchange", "Exchange", re.compile(
        r"\b(withdrawals? (paused|halted|suspended)|insolven\w*|bankrupt\w*|delist(ing|ed)?|"
        r"list(ing|ed|s)|outage|proof of reserves|maintenance)\b", re.I), 0, True),
    EventType("whale", "Whale movement", re.compile(
        r"(🚨|\bwhale\b|\btransferred\b|\bmoved\b).{0,80}\b[\d,]{3,}\s*#?\$?(btc|eth|sol|xrp|usdt|usdc)\b",
        re.I), 0, False),
    EventType("manipulation", "Manipulation rumor", re.compile(
        r"\b(rumou?r\w*|unconfirmed|insider|pump and dump|p&d|spoof\w*|wash trad\w*|"
        r"manipulat\w*|coordinated)\b", re.I), 0, True),
    EventType("breaking", "Breaking news", re.compile(
        r"\b(breaking|just in|urgent|developing)\b", re.I), 0, False),
    EventType("announcement", "Announcement", re.compile(
        r"\b(announc\w*|partnership|launch(es|ed)?|mainnet|upgrade|hard ?fork|integration)\b",
        re.I), 1, False),
)


def detect_events(text: str) -> list[EventType]:
    return [e for e in EVENTS if e.pattern.search(text)]
