# X (Twitter) integration: read-only

Two independent pieces, both on X's **official** API with an **app-only Bearer token**.
No session cookies, no scraping, no user password. Nothing can post, like, follow,
message or delete.

| Piece | What it is | Where the token lives |
|---|---|---|
| Claude Code MCP | X's hosted MCP server `https://api.x.com/mcp?read_only=true` (`.mcp.json`) | your shell: `X_BEARER_TOKEN` |
| NEXUS sentiment | `services/social/nexus_social` worker + `/x/*` API + **X sentiment** page | Render env: `NEXUS_X_BEARER_TOKEN` |

## Why this design

* X's hosted MCP server is X's own integration. `?read_only=true` makes **X's server**
  list only read tools and refuse write tools even if the token could write
  (docs.x.com/tools/mcp). App-only Bearer tokens are read-only by nature.
* NEXUS does not go through MCP: a trading service should not depend on an LLM tool
  protocol. It calls X API v2 directly with a client that can only send `GET` to four
  allowlisted endpoints (`client.py`; tested).

## Cost (X is pay-per-use, no free tier for new apps)

Documented prices (docs.x.com/x-api/getting-started/pricing, checked 2026-11-21): post read
$0.005, user read $0.010, trends $0.010/request, counts $0.005/request. Search is billed
per post returned (and per author expanded). X does not charge again for the same
resource within one UTC day.

NEXUS defaults (all in `nexus_config.Settings`, env prefix `NEXUS_`):

| Setting | Default | Effect |
|---|---|---|
| `X_REFRESH_MINUTES` | 120 | one refresh every 2 h |
| `X_MAX_POSTS_PER_QUERY` | 15 | 2 queries per refresh (coins, watched accounts) |
| `X_DAILY_BUDGET_USD` | 1.00 | hard cap: a call that could exceed it is not made |
| `X_TRENDS_WOEID` / `X_TRENDS_EVERY_HOURS` | 1 / 6 | worldwide trends 4x a day |
| `X_COINS` | BTC,ETH,SOL,XRP | coins analysed |
| `X_WATCH_ACCOUNTS` | whale_alert,binance,coinbase,krakenfx,SECGov | monitored and trusted |

Worst case at defaults: about $1/day (~$30/month). `since_id` means no post is bought
twice; trends and user lookups are cached in Redis. Each call is priced *before* it is
made and the actual cost is recorded after; spend, rate limits and errors are on the
X sentiment page. After an HTTP 429 no call is made until X's reset time.
Claude Code MCP usage is billed by X to the same app.

## What the module computes

* **Score** -100..+100 per coin: credibility x engagement x recency weighted mean of a
  crypto lexicon score per post (negation and emoji aware). Labels: very bearish /
  bearish / neutral / bullish / very bullish. "Insufficient data" when nothing usable.
* **Confidence** 0..1 from the effective number of independent posts, author diversity
  and agreement.
* **Source credibility** 0..1 per post (reach, follower ratio, account age, listed
  count, verification; watched accounts = trusted).
* **Excluded and counted**: spam (giveaway/DM/100x wording, >5 cashtags, many links),
  bot-like accounts (new, very high posting rate, generated usernames, follow ratios,
  default avatar), duplicates and near-duplicates (shingle Jaccard >= 0.8).
* **News alerts**: hack/exploit, regulatory, exchange, whale movement, manipulation
  rumor, breaking, announcement. Each is **confirmed** (a trusted account or >= 2
  independent credible sources) or **unconfirmed** (treated as rumor), with a fake-news
  risk (high when a high-impact claim comes only from a low-credibility account).
* **Trending**: mentions/hour vs the previous 18 h, plus crypto-related X trends.
* **Combined view**: next to Binance price action, regime, volatility percentile, order
  book imbalance/spread, funding rate and open interest (fapi.binance.com; shown as
  unavailable with the reason when unreachable), with alignment/divergence and risk
  flags (e.g. bullish crowd + high funding = crowded longs).
* Telegram alert (`x_alert`) for new confirmed high-impact events, sent once.

## Trading policy (enforced in code, tested)

X data **never opens a trade**, and never acts on a single unconfirmed post. Its only
effect on trading: the trader pauses **new entries** for a coin (or all coins for
market-wide events) while there is **confirmed, high-impact, negative** news from the
last 6 h (`analysis.entry_blocks`, `TradingEngine._x_block`). Manipulation rumors never
block. Missing X data never blocks (X is optional).

## Setup

1. console.x.com: create a Project and App, copy the app's **Bearer token**, add API
   credits (and set a spending limit there too).
2. NEXUS (Render, nexus-ai-fra, Environment): add `NEXUS_X_BEARER_TOKEN`. Save; the
   service restarts and the `x-sentiment` worker starts with the other workers.
3. Claude Code: `export X_BEARER_TOKEN=...` in the shell that starts Claude Code, then
   open Claude Code in this repository; approve the `x-readonly` server from
   `.mcp.json` (`/mcp` shows its status). Alternative without the file:
   `claude mcp add --transport http x-readonly "https://api.x.com/mcp?read_only=true" --header "Authorization: Bearer $X_BEARER_TOKEN"`.

Never paste the token into chat, commit it, or put it in a `NEXT_PUBLIC_` variable.

## Verification status

See docs/ROADMAP.md: the live checks (authentication, live search, MCP `tools/list` in
read-only mode) are recorded there with dates. Until then the integration is tested
only against a mocked X API.
