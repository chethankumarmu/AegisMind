"""Network egress guard for AegisMind sovereign/air-gapped mode.

When SOVEREIGN_MODE or AIR_GAPPED environment variables are set to true,
external connector network requests are blocked at the policy level.
This guard must be checked before any outbound HTTP call in a network-capable connector.

Usage:
    guard = NetworkEgressGuard.from_env()
    guard.assert_network_allowed("github")   # raises SovereignModeViolation if blocked

This guard FAILS SAFE. If mode is unclear, it defaults to the configured policy.
It does NOT silently downgrade to a cloud API.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class SovereignModeViolation(RuntimeError):
    """Raised when a network connector attempts to make external requests in air-gapped mode."""

    def __init__(self, connector_id: str) -> None:
        super().__init__(
            f"Connector '{connector_id}' attempted external network access but AegisMind is "
            f"operating in SOVEREIGN / AIR-GAPPED mode. "
            f"External connectors (Gmail, GitHub, etc.) are blocked. "
            f"Only local connectors (filesystem, local git, SQLite) are permitted. "
            f"To allow external connectors, set AIR_GAPPED=false and SOVEREIGN_MODE=false."
        )
        self.connector_id = connector_id


class NetworkEgressGuard:
    """Centralized policy enforcer for external network access.

    In SOVEREIGN / AIR-GAPPED mode:
    - External connectors MUST NOT make network requests.
    - This guard raises SovereignModeViolation on any attempt.
    - Local connectors (filesystem, local git, SQLite) are always allowed.

    In CONNECTED mode:
    - External connectors may make network requests.
    - All retrieved content is still processed locally (local embeddings, local RAG).
    - Private content is NEVER sent to external LLMs.
    """

    def __init__(self, *, air_gapped: bool = False) -> None:
        self._air_gapped = air_gapped
        if air_gapped:
            logger.info(
                "NetworkEgressGuard: SOVEREIGN / AIR-GAPPED mode active. "
                "External connector network access is BLOCKED."
            )

    @classmethod
    def from_env(cls) -> NetworkEgressGuard:
        """Construct guard from environment variables.

        Checks: AIR_GAPPED, SOVEREIGN_MODE (both accepted for compatibility).
        Truthy values: '1', 'true', 'yes' (case-insensitive).
        """
        truthy = {"1", "true", "yes"}
        air_gapped_raw = os.environ.get("AIR_GAPPED", "false").lower().strip()
        sovereign_raw = os.environ.get("SOVEREIGN_MODE", "false").lower().strip()
        air_gapped = air_gapped_raw in truthy or sovereign_raw in truthy
        return cls(air_gapped=air_gapped)

    @property
    def is_air_gapped(self) -> bool:
        """True when in SOVEREIGN / AIR-GAPPED mode."""
        return self._air_gapped

    @property
    def mode_label(self) -> str:
        """Human-readable mode label."""
        return "SOVEREIGN / AIR-GAPPED" if self._air_gapped else "CONNECTED KNOWLEDGE"

    def assert_network_allowed(self, connector_id: str) -> None:
        """Assert that outbound network access is permitted for this connector.

        Raises:
            SovereignModeViolation: If in air-gapped mode and connector is network-required.
        """
        if self._air_gapped:
            logger.warning(
                "SOVEREIGN_MODE GUARD: Blocked network request from connector '%s'. Mode: %s",
                connector_id,
                self.mode_label,
            )
            raise SovereignModeViolation(connector_id)
        logger.debug(
            "NetworkEgressGuard: Network access permitted for connector '%s'. Mode: %s",
            connector_id,
            self.mode_label,
        )

    def is_network_allowed(self, connector_id: str) -> bool:
        """Return True if network access is permitted (does not raise).

        Use assert_network_allowed() in connectors — this is for policy checks.
        """
        if self._air_gapped:
            logger.warning("SOVEREIGN_MODE GUARD: Network blocked for connector '%s'", connector_id)
            return False
        return True


# Module-level singleton loaded from environment (can be overridden in tests)
_default_guard: NetworkEgressGuard | None = None


def get_network_guard() -> NetworkEgressGuard:
    """Return the process-level network egress guard (lazy init from env)."""
    global _default_guard  # noqa: PLW0603
    if _default_guard is None:
        _default_guard = NetworkEgressGuard.from_env()
    return _default_guard


def set_network_guard(guard: NetworkEgressGuard) -> None:
    """Override the process-level guard (for testing or runtime reconfiguration)."""
    global _default_guard  # noqa: PLW0603
    _default_guard = guard
