# NEXUS bridge for Exness MetaTrader 5 (read-only)

This small program runs on **your** Windows PC or VPS, next to the MetaTrader 5 terminal.
It reads, and sends to NEXUS:
* prices and closed daily bars;
* contract details (contract size, minimum lot, swaps);
* your account summary;
* your open positions.

It **cannot trade**. It has no order code, and it never asks for or reads your MT5
password. NEXUS never connects to your computer; the bridge connects out.

## Steps

1. **Exness.** Open an **MT5 demo account** in your Exness Personal Area (start with demo).
2. **MT5 terminal.** Install MetaTrader 5 from Exness, log in to that demo account and
   leave it running. The bridge only reads; you do not need to enable algorithmic
   trading. If the terminal refuses access, the bridge prints MT5's exact error.
3. **Python.** Install Python 3.12 (64-bit) from python.org. Then, in a terminal:

   ```
   py -3 -m pip install -r requirements.txt
   ```
4. **Token.**
   * In NEXUS, open *Overview → All markets*, then the *Exness bridge* card, and choose
     **Create bridge token**.
   * Copy the token; it is shown once.
   * Never paste it in chat or email.
5. **Run.** In the same terminal:

   ```
   set NEXUS_URL=https://nexus-ai-fra.onrender.com
   set NEXUS_BRIDGE_TOKEN=<the token>
   py -3 nexus_mt5_bridge.py
   ```

   Within about 5 minutes the *All markets* page shows **Exness: connected (demo
   account, read-only)**, with prices for forex, metals, energies and indices.

**Optional settings:**
* `NEXUS_SYMBOLS=EURUSD,XAUUSD,US500` chooses the symbols. Exness suffixes such as
  `EURUSDm` are found automatically.
* `NEXUS_INTERVAL_SECONDS=300` sets how often the bridge sends.

**Stopping and revoking.** Close the window to stop. In NEXUS, *Revoke* the token if the
machine is lost or the token was exposed.

## Price history for forex research (run once)

The NQS-1 forex strategy is tested on your broker's own prices. To send them:

1. In MT5, open Tools → Options → Charts. Set **Max. bars in chart** to *Unlimited*,
   then close and reopen MT5. Otherwise MT5 only returns recent bars.
2. While the forex market is open (Monday to Friday), with the same two settings as
   above, run:

   ```
   py -3 nexus_mt5_bridge.py --export-history
   ```

**What it sends.** For EURUSD, GBPUSD, USDJPY, AUDUSD and USDCHF:
* M5 bars for the last 3 years, month by month;
* H1 bars for the last 12 years, year by year;
* all daily bars.

**How long it takes.** It takes 10 to 30 minutes. Progress appears in the window, and in
NEXUS under *All markets → Exness bridge → Price history*.

**Running it again is safe.** Months and years NEXUS already holds are skipped.

**Options:**
* `NEXUS_HISTORY_SYMBOLS` changes the pairs (forex only).
* `--export-history-dir C:\nexus_history` writes CSV files to a folder instead of
  sending them.

It only reads prices. Like the normal mode, it has no order function.

## Notes

* **Leverage.** Exness accounts can have very high leverage. NEXUS keeps positions at or
  below 1x of equity (its rule), whatever the account allows.
* **No trading yet.** No forex, commodity or index strategy has passed NEXUS's tests, so
  NEXUS does not trade on Exness.
  * A demo-only order path comes later.
  * Real money needs your explicit authorization per market.
