from enum import StrEnum


class ExchangeContext(StrEnum):
    """Where market data and orders are sourced from / routed to.

    F0 only uses MAINNET_RO. The other contexts exist from day one so the
    adapter API doesn't need a retroactive abstraction in F4 (paper trading)
    or F6 (live trading).
    """

    MAINNET_RO = "mainnet_ro"  # public data, no API key required
    TESTNET = "testnet"  # execution + data on Binance futures testnet
    MAINNET_LIVE = "mainnet_live"  # real money — needs API key with trade perms

    @property
    def needs_api_key(self) -> bool:
        return self in (ExchangeContext.TESTNET, ExchangeContext.MAINNET_LIVE)

    @property
    def is_simulated_data(self) -> bool:
        """Testnet OI/funding are simulated and do NOT reflect real market flow."""
        return self is ExchangeContext.TESTNET
