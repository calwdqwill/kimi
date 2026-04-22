"""
Extensibility hooks — interfaces for future modules.

These are intentionally left as stubs. Implementations can be plugged in
later without changing the core dashboard logic.
"""

import abc
from typing import Optional


class SignalGenerator(abc.ABC):
    """Generate trading signals from spread / Z-score state."""

    @abc.abstractmethod
    def generate(
        self,
        zscore: Optional[float],
        spread_pct: Optional[float],
    ) -> Optional[dict]:
        """
        Return a signal dict or None.
        Example signal: {"side": "buy_spread", "confidence": 0.8}
        """
        raise NotImplementedError


class ExecutionEngine(abc.ABC):
    """Execute orders on MOEX / Hyperliquid."""

    @abc.abstractmethod
    def place_order(
        self,
        side: str,
        size: float,
        price: Optional[float] = None,
    ) -> dict:
        """
        Place an order and return execution metadata.
        """
        raise NotImplementedError


class RiskManager(abc.ABC):
    """Pre-trade risk checks."""

    @abc.abstractmethod
    def can_execute(
        self,
        signal: dict,
        portfolio_state: dict,
    ) -> bool:
        """
        Return True if the signal is allowed to execute under current risk limits.
        """
        raise NotImplementedError
