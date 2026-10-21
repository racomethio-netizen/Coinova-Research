/**
 * Initial supported coin universe (CoinGecko coin IDs).
 *
 * Approximately 100 assets. IDs must match CoinGecko's `/coins/list` output;
 * the market refresh job logs any ID the provider does not return so stale
 * entries can be corrected. Expand only after measuring API limits.
 *
 * Wrapped/bridged duplicates are intentionally excluded. Stablecoins are
 * included for market context but are not scored (see `UNSCORED_COIN_IDS`).
 */
export const COIN_UNIVERSE: readonly string[] = [
  "bitcoin", "ethereum", "tether", "binancecoin", "solana", "ripple", "usd-coin",
  "dogecoin", "cardano", "tron", "avalanche-2", "chainlink", "the-open-network",
  "shiba-inu", "polkadot", "bitcoin-cash", "near", "litecoin", "uniswap", "dai",
  "internet-computer", "ethereum-classic", "aptos", "stellar", "monero", "okb",
  "filecoin", "cosmos", "hedera-hashgraph", "mantle", "arbitrum", "crypto-com-chain",
  "vechain", "optimism", "render-token", "injective-protocol", "the-graph", "maker",
  "immutable-x", "aave", "algorand", "thorchain", "lido-dao", "sui", "pepe",
  "bittensor", "theta-token", "flow", "quant-network", "axie-infinity", "the-sandbox",
  "decentraland", "tezos", "eos", "kaspa", "sei-network", "celestia", "worldcoin-wld",
  "jupiter-exchange-solana", "ondo-finance", "pyth-network", "ethena", "starknet",
  "gala", "curve-dao-token", "chiliz", "iota", "neo", "kucoin-shares", "bitget-token",
  "leo-token", "first-digital-usd", "ethena-usde", "floki", "bonk", "dogwifcoin",
  "pendle", "raydium", "helium", "akash-network", "fetch-ai", "singularitynet",
  "arweave", "ecash", "zcash", "dash", "mina-protocol", "conflux-token", "kava",
  "1inch", "compound-governance-token", "havven", "pancakeswap-token", "gnosis",
  "blur", "ethereum-name-service", "loopring", "basic-attention-token", "zilliqa",
  "enjincoin", "celo", "ankr", "polygon-ecosystem-token", "hyperliquid",
];

/** Stablecoins: shown on market pages but excluded from research scoring. */
export const UNSCORED_COIN_IDS: ReadonlySet<string> = new Set([
  "tether", "usd-coin", "dai", "first-digital-usd", "ethena-usde",
]);

/**
 * Coins whose research scores are visible to free users as a sample.
 * Kept in sync with `cryptocurrencies.is_score_sample` by the market refresh job.
 */
export const SCORE_SAMPLE_COIN_IDS: readonly string[] = [
  "bitcoin", "ethereum", "solana", "chainlink", "cardano",
];
