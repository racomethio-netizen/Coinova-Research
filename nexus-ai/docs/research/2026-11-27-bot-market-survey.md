# Trading-bot market survey: what the verifiable evidence says (2026-11-27)

The owner asked for real information on bots advertised as "80% effective", collected
from Reddit and the wider internet. Only sources a third party can check were weighted
heavily: regulators, regulated brokers, exchange data, and large studies.
* Marketing pages and "best bot" lists are reported as such.
* No figure below was produced by NEXUS, except the last section.

## 1. Retail traders as a whole (regulator and broker data)

* **ESMA (EU regulator).** When it introduced its CFD rules in 2018, national regulators
  found that **74–89% of retail CFD accounts lose money**.
  * Brokers must publish their own figure.
  * A 49-broker compilation averages **71%** of accounts losing, from 51% (eToro) to
    82% (Plus500).
* **ASIC (Australian regulator, Report 828, January 2026).** A secondary summary reports
  that only **19% of the most active retail CFD traders** (more than 50 trades a month)
  were profitable after costs. The source is reported, not re-checked here.

## 2. Copy trading (following other traders automatically)

* **The largest available dataset is a vendor study** (YieldFund, 2025): about 100,000
  copy-trading outcomes over 90 days on Binance, Bybit and MEXC.
  * **48% of followers** were profitable.
  * **97% of lead traders** were profitable on their own accounts, but only **44%** made
    money for their followers.
  * The study comes from a company with a commercial interest. No peer-reviewed eToro or
    Binance study was found.

## 3. Named bots and platforms

* **Night Hunter Pro** (a night scalper, sold for about USD 2,000+):
  * reported live drawdowns range from **12% to 30%**, depending on the signal and
    source;
  * reviewers report many 2024–2025 users losing or flat.
  * NEXUS's own test of the night-scalper idea (12 pairs, real Exness spreads) lost in
    every year.
* **Waka Waka** (a grid EA on currency crosses):
  * one Myfxbook system page has been deleted;
  * a review cites its MQL5 signal since 2018 with a **72% maximum equity drawdown**.
  * Grid and averaging down are forbidden by NEXUS rules.
* **Pionex, 3Commas** (crypto grid and DCA platforms):
  * no official or independent statistic on the share of profitable bots was found;
  * review figures ("15–50% a year") are unsourced;
  * users report grids "buying all the way down" in the May 2022 crash.
* **"Best AI bot 2026" lists:**
  * the top entries are often **sponsored**;
  * figures such as "1.2% a day" come from the seller.
  * Honest reviewers (Coin Bureau, CoinGape) state that they do **not** rank bots by
    profit, because no reliable public data exists.
* **US CFTC advisory "AI Won't Turn Trading Bots into Money Machines":**
  * it names huge or guaranteed returns and near-100% "win" rates as red flags;
  * it warns about scams that take exchange API keys and empty the wallet.

## 4. What genuinely good systematic trading looks like (verified records)

* **Darwinex** (a regulated broker; every strategy's track record is from real accounts).
  * One long-running strategy ($SYO, since January 2018) shows **15.2% a year** with a
    **13% maximum drawdown**, before fees of 1.2% a year plus 20% of profits.
  * Strategies with 16-year records show drawdowns of **23–34%**.
* **Professional trend-following funds (SG Trend Index, the 10 largest CTAs).**
  * Cambridge Associates and Man Group describe long-term positive, uncorrelated
    returns.
  * The index also lost **20.4% from May 2024 to May 2025**, its second-largest
    drawdown since 2000.

**Conclusion.** Verified, durable systematic results look like **10–20% a year with
drawdowns of 13–35%**.
* Claims far above that ("80% win rate", several percent a month) are either high-risk
  grid or martingale systems, which win small and often until one large loss, or they
  are unverified.
* A win rate says nothing on its own.
  * NEXUS's adaptive grid won 89% of its trades and lost 25%.
  * The Binance ±10% grid won almost every round trip and lost 64%.

## 5. Where NEXUS stands

* The NEXUS volatility-control bot ranked 1st of 6 in the bot league (BTC + ETH,
  2021–2026): **15.3% a year**, worst drop −59% (crypto). The report is
  `2026-11-27-bot-league-report.md`.
* Its return is in the range of verified good systems. Its drawdown is larger, because
  the underlying asset is crypto.
* Its real-time evidence (the paper test) started on 2026-11-27.

## Sources

* ESMA CFD intervention and loss disclosures:
  * [Finance Magnates: where CFD traders lose the most](https://www.financemagnates.com/forex/brokers/which-brokers-house-the-most-winning-clients-post-esma/)
  * [BrokerRank: retail loss rates](https://brokerrank.net/research/retail-loss-rates)
  * [ESMA decision (EUR-Lex)](https://eur-lex.europa.eu/eli/dec/2018/796/oj/eng)
* [ASIC Report 828 (January 2026)](https://download.asic.gov.au/media/tq0he35c/rep828-published-20-january-2026.pdf)
* Copy trading:
  * [YieldFund 90-day multi-exchange study](https://yieldfund.com/is-copy-trading-profitable-a-90-day-multi-exchange-study/)
  * [Copy trading (Wikipedia)](https://en.wikipedia.org/wiki/Copy_trading)
* Night Hunter Pro:
  * [Myfxbook page](https://www.myfxbook.com/members/MischenkoValeria/night-hunter-pro-all-pairs/8562210)
  * [NYC Servers review](https://newyorkcityservers.com/blog/night-hunter-pro-review)
  * [Top FX Managers review](https://topfxmanagers.com/night-hunter-pro-review/)
* Waka Waka:
  * [Myfxbook page](https://www.myfxbook.com/members/MischenkoValeria/waka-waka-ea/8379251)
  * [CheaperForex review](https://cheaperforex.com/waka-waka-ea-mt4-download-guide/)
* Pionex and 3Commas:
  * [BotVerdict Pionex review](https://botverdict.com/articles/pionex-review-2026-grid-trading-bot-platform-with-built-in-exchange-features/)
  * [TradeAlgo 3Commas review](https://www.tradealgo.com/trading-guides/crypto/3commas-review)
  * [uwuu DCA bot test](https://uwuu.ai/blog/dca-bot-crypto)
* "Best bot" lists:
  * [AMBCrypto (sponsored)](https://ambcrypto.com/8-most-reliable-ai-crypto-trading-bots-in-2026-reviewed-and-tested-for-real-results/)
  * [Coin Bureau](https://coinbureau.com/explore/best-crypto-ai-trading-bots)
  * [CoinGape](https://coingape.com/top-cryptocurrency-trading-bots/)
* [CFTC advisory: AI Won't Turn Trading Bots into Money Machines](https://www.cftc.gov/LearnandProtect/AdvisoriesAndArticles/AITradingBots.html)
* High win rates and martingale:
  * [MQL5: 99.9% win rate](https://www.mql5.com/en/blogs/post/778258)
  * [MQL5: a 95% win rate is a warning sign](https://www.mql5.com/en/blogs/post/775015)
  * [DEV: the 80% win-rate trap](https://dev.to/guardlabs_team/the-80-win-rate-trap-why-a-winning-bot-quietly-loses-money-18mj)
* Darwinex:
  * [All DARWINs](https://www.darwinex.com/all-darwins)
  * [Drawdown calculation](https://help.darwinex.com/drawdown-calculation-at-darwinex)
* Trend following:
  * [Cambridge Associates](https://www.cambridgeassociates.com/insight/does-trend-followings-recent-struggle-signal-that-the-strategy-is-structurally-broken/)
  * [Man Group: why patience matters during market stress](https://www.man.com/it/insights/why-patience-matters-during-market-stress)
