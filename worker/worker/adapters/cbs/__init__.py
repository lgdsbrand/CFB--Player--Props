"""CBS Sports adapter — weekly NFL fantasy projections (migration 0093)."""

from worker.adapters.cbs.client import CbsClient, CbsError

__all__ = ["CbsClient", "CbsError"]
