"""Source credibility, bot and spam heuristics, duplicate detection (pure functions).

These are heuristics, not proof: every score is reported with the reasons behind it so
the dashboard can show why a post was down-weighted or excluded.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_DIGITS_TAIL = re.compile(r"\d{5,}$")
_SPAM = re.compile(
    r"\b(giveaway|airdrop|free \w+ coins?|dm (me|us)|join (my|our)|telegram group|"
    r"whatsapp|100x|1000x|guaranteed|signals? group|vip group|link in bio|"
    r"send \d+ ?(btc|eth)|double your)\b", re.I)
_CASHTAG = re.compile(r"\$[A-Za-z]{2,10}\b")
_LINK = re.compile(r"https?://\S+")
_NORM_DROP = re.compile(r"https?://\S+|@\w+|[^a-z0-9$# ]")


@dataclass
class Assessment:
    credibility: float              # 0..1, weight used in aggregation
    bot_score: float                # 0..1 (higher = more bot-like)
    spam: bool
    trusted: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def excluded(self) -> bool:
        return self.spam or self.bot_score >= 0.6


def _age_days(created_at: str | None, now: datetime) -> float | None:
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400)


def assess(post: dict[str, Any], user: dict[str, Any] | None, now: datetime,
           trusted: set[str]) -> Assessment:
    reasons: list[str] = []
    text = str(post.get("text") or "")
    username = str((user or {}).get("username") or "").lower()
    is_trusted = username in trusted
    m = (user or {}).get("public_metrics") or {}
    followers = int(m.get("followers_count") or 0)
    following = int(m.get("following_count") or 0)
    tweets = int(m.get("tweet_count") or 0)
    listed = int(m.get("listed_count") or 0)
    age = _age_days((user or {}).get("created_at"), now)

    bot = 0.0
    if user is None:
        bot += 0.3
        reasons.append("author unknown")
    if age is not None and age < 30:
        bot += 0.35
        reasons.append(f"account {age:.0f} days old")
    if age and tweets / max(age, 1) > 150:
        bot += 0.3
        reasons.append(f"{tweets / max(age, 1):.0f} posts/day")
    if _DIGITS_TAIL.search(username):
        bot += 0.15
        reasons.append("auto-generated-looking username")
    if followers < 20 and following > 500:
        bot += 0.25
        reasons.append("follows many, followed by few")
    if "default_profile_images" in str((user or {}).get("profile_image_url") or ""):
        bot += 0.15
        reasons.append("default avatar")
    bot = 0.0 if is_trusted else min(1.0, bot)

    cashtags = len(set(_CASHTAG.findall(text)))
    spam = False
    if _SPAM.search(text):
        spam = True
        reasons.append("promotional/giveaway wording")
    if cashtags > 5:
        spam = True
        reasons.append(f"{cashtags} different cashtags")
    if len(_LINK.findall(text)) > 2:
        spam = True
        reasons.append("many links")
    if is_trusted:
        spam = False

    if is_trusted:
        cred = 0.95
        reasons.append("watched/trusted account")
    else:
        verified = bool((user or {}).get("verified")) or (user or {}).get("verified_type") in (
            "business", "government")
        reach = min(1.0, math.log10(1 + followers) / 6)           # 1M followers -> 1.0
        ratio = min(1.0, (followers + 1) / (following + 1) / 10)
        maturity = 0.0 if age is None else min(1.0, age / 365)
        listed_s = min(1.0, math.log10(1 + listed) / 3)
        cred = 0.1 + 0.35 * reach + 0.15 * ratio + 0.15 * maturity + 0.1 * listed_s
        if verified:
            cred += 0.15
            reasons.append("verified")
        cred *= (1 - bot)
        cred = max(0.0, min(0.9, cred))
    return Assessment(credibility=round(cred, 3), bot_score=round(bot, 3), spam=spam,
                      trusted=is_trusted, reasons=reasons)


def normalized(text: str) -> str:
    return " ".join(_NORM_DROP.sub(" ", text.lower()).split())


def _shingles(s: str, k: int = 4) -> set[str]:
    w = s.split()
    return {" ".join(w[i:i + k]) for i in range(max(1, len(w) - k + 1))}


def near_duplicate(a: str, b: str, threshold: float = 0.8) -> bool:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return a == b
    return len(sa & sb) / len(sa | sb) >= threshold


def dedupe(texts: list[str]) -> list[int | None]:
    """For each text, the index of an earlier text it duplicates (None = original)."""
    out: list[int | None] = []
    kept: list[tuple[int, str]] = []
    for i, t in enumerate(texts):
        n = normalized(t)
        dup = next((j for j, k in kept if k == n or near_duplicate(k, n)), None)
        out.append(dup)
        if dup is None:
            kept.append((i, n))
    return out
