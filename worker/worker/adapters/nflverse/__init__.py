"""nflverse adapter — the NFL half of the sport seam (CLAUDE.md §3)."""

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.mapping import SPORT

__all__ = ["NflverseClient", "NflverseError", "SPORT"]
