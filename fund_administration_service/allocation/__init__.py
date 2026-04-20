"""Capital allocation routing (treasury -> strategy wallets)."""

from fund_administration_service.allocation.capital_router import (
    AllocationTarget,
    CapitalRouter,
)

__all__ = ["AllocationTarget", "CapitalRouter"]
