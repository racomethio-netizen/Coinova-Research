"""Turn stored X posts into per-coin sentiment, news alerts, trends and a block-only gate.

Score: -100 (very bearish) .. +100 (very bullish), a credibility-, engagement- and
recency-weighted mean of per-post lexicon scores. Spam, bot-like and duplicate posts are
excluded and counted. Confidence (0..1) grows with the effective number of independent
posts and authors and falls when posts disagree. Nothing here can open a trade.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from nexus_social.quality import assess, dedupe
from nexus_social.text import EVENTS, detect_coins, detect_events, sentiment

CURRENT_HOURS = 6
WINDOW_HOURS = 24
HALF_LIFE_HOURS = 6.0
CREDIBLE = 0.5
POLICY = ("Context only. NEXUS never opens a trade because of X posts, and never on one "
          "post alone. X data can only block new entries when a high-impact negative event "
          "is confirmed by trusted or several independent credible accounts.")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def label(score: int | None) -> str:
    if score is None:
        return "insufficient data"
    if score <= -60:
        return "very bearish"
    if score <= -20:
        return "bearish"
    if score < 20:
        return "neutral"
    if score < 60:
        return "bullish"
    return "very bullish"


def _engagement(p: dict[str, Any]) -> int:
    m = p.get("public_metrics") or {}
    return int(m.get("like_count") or 0) + 2 * int(m.get("retweet_count") or 0) + int(
        m.get("reply_count") or 0) + 2 * int(m.get("quote_count") or 0)


def enrich(posts: list[dict[str, Any]], users: dict[str, dict[str, Any]], coins: list[str],
           trusted: set[str], now: datetime) -> list[dict[str, Any]]:
    """Per-post analysis (coins, sentiment, events, quality, duplicate-of)."""
    posts = sorted(posts, key=lambda p: p.get("created_at") or "")
    dups = dedupe([str(p.get("text") or "") for p in posts])
    out = []
    for p, dup in zip(posts, dups, strict=True):
        text = str(p.get("text") or "")
        user = users.get(str(p.get("author_id")))
        a = assess(p, user, now, trusted)
        s, hits = sentiment(text)
        username = (user or {}).get("username")
        out.append({
            "id": p["id"], "text": text, "created_at": p.get("created_at"),
            "url": f"https://x.com/{username or 'i'}/status/{p['id']}",
            "author": username, "author_name": (user or {}).get("name"),
            "followers": ((user or {}).get("public_metrics") or {}).get("followers_count"),
            "coins": sorted(detect_coins(text, coins)), "sentiment": round(s, 3),
            "cues": hits, "events": [e.code for e in detect_events(text)],
            "engagement": _engagement(p), "credibility": a.credibility,
            "bot_score": a.bot_score, "spam": a.spam, "trusted": a.trusted,
            "excluded": a.excluded, "duplicate_of": None if dup is None else posts[dup]["id"],
            "quality_notes": a.reasons,
        })
    return out


def _weight(p: dict[str, Any], now: datetime) -> float:
    age_h = max(0.0, (now - _dt(p["created_at"])).total_seconds() / 3600)
    eng = 1 + math.log10(1 + int(p["engagement"])) / 2
    cue = 1.0 if p["cues"] else 0.3
    decay: float = 0.5 ** (age_h / HALF_LIFE_HOURS)
    return float(p["credibility"]) * eng * cue * decay


def coin_sentiment(coin: str, posts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    mine = [p for p in posts if coin in p["coins"]
            and now - _dt(p["created_at"]) <= timedelta(hours=WINDOW_HOURS)]
    usable = [p for p in mine if not p["excluded"] and p["duplicate_of"] is None]
    excluded = sum(1 for p in mine if p["excluded"])
    dupes = sum(1 for p in mine if p["duplicate_of"] is not None)

    def score_of(ps: list[dict[str, Any]]) -> tuple[int | None, float, float]:
        ws = [(_weight(p, now), p["sentiment"]) for p in ps]
        tw = sum(w for w, _ in ws)
        if tw <= 0:
            return None, 0.0, 0.0
        mean = sum(w * s for w, s in ws) / tw
        var = sum(w * (s - mean) ** 2 for w, s in ws) / tw
        n_eff = tw ** 2 / sum(w * w for w, _ in ws)
        return round(max(-1.0, min(1.0, mean)) * 100), n_eff, var

    cur = [p for p in usable if now - _dt(p["created_at"]) <= timedelta(hours=CURRENT_HOURS)]
    prev = [p for p in usable if p not in cur]
    score, n_eff, var = score_of(usable)
    cur_score = score_of(cur)[0]
    prev_score = score_of(prev)[0]
    authors = {p["author"] for p in usable if p["author"]}
    confidence = 0.0
    if score is not None:
        confidence = (1 - math.exp(-n_eff / 8)) * max(0.0, 1 - math.sqrt(var)) * min(
            1.0, len(authors) / 5)
    warnings = []
    if len(usable) < 10:
        warnings.append(f"only {len(usable)} usable posts in {WINDOW_HOURS}h: low reliability")
    if mine and (excluded + dupes) / len(mine) > 0.3:
        warnings.append(f"{excluded} spam/bot-like and {dupes} duplicate posts excluded "
                        f"({(excluded + dupes) / len(mine):.0%} of mentions)")
    if score is not None and abs(score) >= 70:
        warnings.append("extreme one-sided sentiment: crowded positioning, contrarian risk")
    if mine and max(_dt(p["created_at"]) for p in mine) < now - timedelta(hours=6):
        warnings.append("no new posts for 6h+: stale")
    drivers = sorted(usable, key=lambda p: -_weight(p, now) * abs(p["sentiment"]))[:4]
    return {
        "coin": coin, "score": score, "label": label(score),
        "confidence": round(confidence, 2), "posts_total": len(mine),
        "posts_used": len(usable), "excluded_spam_bot": excluded, "duplicates": dupes,
        "unique_authors": len(authors),
        "credibility_avg": round(sum(p["credibility"] for p in usable) / len(usable), 2)
        if usable else None,
        "score_last_6h": cur_score, "score_6_to_24h": prev_score,
        "change": None if cur_score is None or prev_score is None else cur_score - prev_score,
        "mentions_last_6h": sum(1 for p in mine if now - _dt(p["created_at"])
                                <= timedelta(hours=CURRENT_HOURS)),
        "mentions_6_to_24h": sum(1 for p in mine if now - _dt(p["created_at"])
                                 > timedelta(hours=CURRENT_HOURS)),
        "warnings": warnings,
        "drivers": [{k: p[k] for k in ("text", "author", "credibility", "sentiment",
                                       "created_at", "url", "trusted")} for p in drivers],
        "latest_post_at": max((p["created_at"] for p in mine), default=None),
    }


def news_alerts(posts: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Event posts grouped by (coin, category) with corroboration and fake-news risk."""
    impact = {e.code: e for e in EVENTS}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in posts:
        if p["excluded"] or now - _dt(p["created_at"]) > timedelta(hours=WINDOW_HOURS):
            continue
        for ev in p["events"]:
            for coin in p["coins"] or ["MARKET"]:
                groups[(coin, ev)].append(p)
    alerts = []
    for (coin, ev), ps in groups.items():
        e = impact[ev]
        credible = {p["author"] for p in ps if p["trusted"] or p["credibility"] >= CREDIBLE}
        trusted = any(p["trusted"] for p in ps)
        confirmed = trusted or len(credible) >= 2
        best = max(ps, key=lambda p: (p["trusted"], p["credibility"], p["engagement"]))
        sent = sum(p["sentiment"] for p in ps) / len(ps)
        direction = e.direction or (1 if sent > 0.2 else -1 if sent < -0.2 else 0)
        if confirmed:
            fake = "low"
        elif best["credibility"] < 0.3 and e.high_impact:
            fake = "high"
        else:
            fake = "medium"
        warn = []
        if not confirmed:
            warn.append("unconfirmed: single or low-credibility source; treat as rumor")
        if ev == "manipulation":
            warn.append("manipulation rumor: do not act on it")
        if fake == "high":
            warn.append("possible fake news: high-impact claim from a low-credibility account")
        alerts.append({
            "coin": coin, "category": ev, "label": e.label, "high_impact": e.high_impact,
            "direction": direction, "status": "confirmed" if confirmed else "unconfirmed",
            "fake_news_risk": fake, "sources": len({p["author"] for p in ps}),
            "credible_sources": len(credible), "headline": best["text"][:280],
            "author": best["author"], "credibility": best["credibility"], "url": best["url"],
            "first_seen": min(p["created_at"] for p in ps),
            "last_seen": max(p["created_at"] for p in ps), "warnings": warn,
        })
    alerts.sort(key=lambda a: (not a["high_impact"], a["status"] != "confirmed",
                               a["last_seen"]), reverse=False)
    return sorted(alerts, key=lambda a: a["last_seen"], reverse=True)


def entry_blocks(alerts: list[dict[str, Any]], now: datetime,
                 hours: int = CURRENT_HOURS) -> dict[str, str]:
    """Coins whose NEW entries should pause: confirmed, high-impact, negative, recent.
    MARKET-wide events block every coin. This can only ever block, never trigger."""
    out: dict[str, str] = {}
    for a in alerts:
        if (a["status"] == "confirmed" and a["high_impact"] and a["direction"] < 0
                and a["category"] != "manipulation"
                and now - _dt(a["last_seen"]) <= timedelta(hours=hours)):
            out.setdefault(a["coin"], f"X: confirmed {a['label'].lower()} news "
                                      f"({a['credible_sources']} credible sources, "
                                      f"{a['last_seen'][:16]} UTC)")
    return out


def trending(per_coin: list[dict[str, Any]], x_trends: list[dict[str, Any]],
             coins: list[str]) -> dict[str, Any]:
    rows = []
    for c in per_coin:
        cur = c["mentions_last_6h"] / CURRENT_HOURS
        prev = c["mentions_6_to_24h"] / (WINDOW_HOURS - CURRENT_HOURS)
        rows.append({"coin": c["coin"], "mentions_per_hour": round(cur, 2),
                     "change": None if prev == 0 else round(cur / prev - 1, 2)})
    terms = {t.lower() for c in coins for t in (c, f"${c}", f"#{c}")} | {
        "crypto", "bitcoin", "ethereum", "solana", "xrp", "binance", "etf"}
    crypto_trends = [t for t in x_trends if any(term in str(t.get("trend_name", "")).lower()
                                                for term in terms)]
    return {"coins": sorted(rows, key=lambda r: -r["mentions_per_hour"]),
            "x_trends": crypto_trends}


def combine(sent: dict[str, Any], market: dict[str, Any] | None) -> dict[str, Any]:
    """Side-by-side context: X sentiment vs price action, volatility, book, derivatives."""
    if not market or market.get("error"):
        return {"available": False, "note": (market or {}).get("error")
                or "no market analysis for this coin (not a traded pair)"}
    hyps = market.get("hypotheses") or []
    best = max(hyps, key=lambda h: h.get("score") or 0, default=None)
    tech = market.get("direction")
    s = sent.get("score")
    if s is None or sent.get("confidence", 0) < 0.3:
        alignment = "sentiment too thin to compare"
    elif tech is None:
        alignment = "no clear technical direction"
    elif (s > 20 and tech == "long") or (s < -20 and tech == "short"):
        alignment = "aligned"
    elif (s > 20 and tech == "short") or (s < -20 and tech == "long"):
        alignment = "divergent: crowd and price action disagree"
    else:
        alignment = "neutral sentiment"
    risks = []
    fr = market.get("funding_rate")
    if fr is not None and s is not None and s > 40 and fr > 0.0005:
        risks.append("bullish crowd with high positive funding: crowded longs")
    if fr is not None and s is not None and s < -40 and fr < -0.0005:
        risks.append("bearish crowd with negative funding: crowded shorts")
    ap = market.get("atr_percentile")
    if ap is not None and ap > 0.9:
        risks.append("volatility in the top 10% of its range")
    ob = market.get("order_book") or {}
    return {"available": True, "technical_direction": tech,
            "technical_score": None if best is None else best.get("score"),
            "price": market.get("price"), "change_24h": market.get("change_24h"),
            "regime": market.get("regime"), "atr_percentile": ap,
            "book_imbalance": ob.get("imbalance"), "spread_bps": ob.get("spread_bps"),
            "funding_rate": fr, "open_interest": market.get("open_interest"),
            "derivatives_note": market.get("futures_data_note"),
            "alignment": alignment, "risk_flags": risks, "policy": POLICY}
