"""Trademark screening hook.

A screen returns one status per domain:
  CLEAR        no trademark found for the name
  MATCH        the name matches a registered trademark; the name is removed from results
  NOT_CHECKED  no screen is configured, or the screen could not answer

Until a real source is connected, every name is NOT_CHECKED. The results page says so.
"""

from __future__ import annotations

from typing import Any, Protocol

CLEAR = "CLEAR"
MATCH = "MATCH"
NOT_CHECKED = "NOT_CHECKED"


class TrademarkScreen(Protocol):
    def screen(self, names: list[str]) -> dict[str, str]:
        """Map each name (without extension) to CLEAR, MATCH or NOT_CHECKED."""


class NotConfiguredScreen:
    def screen(self, names: list[str]) -> dict[str, str]:
        return {name: NOT_CHECKED for name in names}


def apply_screen(offers: list[dict[str, Any]], screen: TrademarkScreen) -> list[dict[str, Any]]:
    """Attach a trademark status to each offer and drop names that match a trademark."""
    names = sorted({offer["domain"].rsplit(".", 1)[0] for offer in offers})
    statuses = screen.screen(names)
    kept = []
    for offer in offers:
        status = statuses.get(offer["domain"].rsplit(".", 1)[0], NOT_CHECKED)
        if status == MATCH:
            continue
        kept.append({**offer, "trademark": status})
    return kept
