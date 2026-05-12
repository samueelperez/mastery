"""Abstract base class for liquidation data providers.

Every concrete provider implements `get_heatmap` and `health_check`. The
service layer iterates over all enabled providers and merges their outputs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.liquidation.models import ProviderHeatmap, ProviderName, TimeframeLiteral


class BaseLiquidationProvider(ABC):
    """Contract for any liquidation data provider."""

    # Set by concrete subclasses. Must match a value of `ProviderName`.
    name: ClassVar[ProviderName]

    # Maximum age (seconds) a snapshot can be before being considered stale.
    # The service excludes stale providers from the merge.
    max_age_seconds: ClassVar[int]

    # Whether this provider is enabled. Used to defer Coinglass without
    # removing code.
    enabled: ClassVar[bool] = True

    @abstractmethod
    async def get_heatmap(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        current_price: float,
        max_distance_pct: float = 10.0,
    ) -> ProviderHeatmap:
        """Return raw buckets for this provider's view of the heatmap.

        Args:
            symbol: Internal symbol, e.g. 'BTCUSDT'. Provider is responsible
                for mapping to its own symbol space (e.g. 'BTC' for Hyperliquid).
            timeframe: '1h', '4h', or '1d'. Influences the temporal window
                the provider looks back over.
            current_price: Reference price for distance calculations.
            max_distance_pct: Only return buckets within ±max_distance_pct
                of current_price. Default 10%.

        Returns:
            ProviderHeatmap with buckets list (may be empty) and warnings.

        Must NOT raise on transient errors; instead return empty buckets
        with warnings populated. Only raise on programmer errors
        (invalid symbol, etc).
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Quick sanity check. Return True if the provider can serve data
        right now."""
        ...

    @abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        """Whether this provider has coverage for the given symbol."""
        ...
