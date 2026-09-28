"""Connector registry for AegisMind.

The ConnectorRegistry manages the lifecycle of connector instances:
- Registration (by name/ID)
- Status tracking (connected/disconnected, sync state)
- Credential storage references (via SecretStore — never raw tokens)
- Account information caching
- Sync state persistence

The registry is the single source of truth for all connector metadata.
It replaces the raw dict[str, ConnectorPort] in CoreState with a typed,
lifecycle-aware registry.

Security:
- OAuth tokens and API keys are NEVER stored in the registry.
- Credentials are stored in the SecretStore under a namespaced key.
- The registry only stores the SecretStore key name, not the secret value.
- Account metadata (email, name) is safe to cache; tokens are not.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aegismind_connector_sdk.ports import ConnectorPort, ConnectorSpec, ConnectorSyncStatus

logger = logging.getLogger(__name__)

ConnectorStatus = Literal["registered", "connected", "disconnected", "error"]


class ConnectorRegistration(BaseModel):
    """Full registration record for a connector instance in the registry."""

    model_config = ConfigDict(frozen=True)

    connector_id: str = Field(..., description="Unique connector instance ID")
    connector_type: str = Field(
        ..., description="Connector type name (e.g. github, gmail, filesystem)"
    )
    display_name: str = Field(..., description="Human-readable connector display name")
    status: ConnectorStatus = Field(default="registered", description="Connector lifecycle status")

    # Credential reference — NEVER the credential value
    credential_key: str | None = Field(
        default=None,
        description="SecretStore key name for this connector's credentials (not the value)",
    )

    # Account info (safe metadata, not tokens)
    account_id: str | None = Field(default=None, description="Connected account identifier")
    account_email: str | None = Field(default=None, description="Connected account email")
    account_name: str | None = Field(default=None, description="Connected account display name")

    # Sync state
    sync_status: ConnectorSyncStatus | None = Field(default=None, description="Current sync state")
    last_sync_at: str | None = Field(default=None, description="ISO timestamp of last sync")
    indexed_documents: int = Field(default=0, description="Total indexed document count")
    indexed_chunks: int = Field(default=0, description="Total indexed chunk count")

    # Config (no secrets)
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-secret connector configuration",
    )

    # Timestamps
    registered_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="Registration timestamp",
    )
    connected_at: str | None = Field(default=None, description="Connection timestamp")
    disconnected_at: str | None = Field(default=None, description="Disconnection timestamp")

    # Operational flags from spec
    network_required: bool = Field(default=False)
    air_gapped_capable: bool = Field(default=True)


class ConnectorRegistry:
    """In-memory registry of connector instances with lifecycle management.

    Thread-safety: This is a single-process in-memory registry. For production
    multi-process deployments, back this with a persistent store.
    """

    def __init__(self) -> None:
        self._connectors: dict[str, ConnectorPort] = {}
        self._registrations: dict[str, ConnectorRegistration] = {}

    def register(
        self,
        name: str,
        connector: ConnectorPort,
        config: dict[str, Any] | None = None,
    ) -> ConnectorRegistration:
        """Register a connector instance.

        Args:
            name: Unique connector name/ID (also used as display name if not in spec).
            connector: ConnectorPort implementation instance.
            config: Optional non-secret configuration dict.

        Returns:
            ConnectorRegistration record.
        """
        spec: ConnectorSpec = connector.spec()
        registration = ConnectorRegistration(
            connector_id=name,
            connector_type=spec.name,
            display_name=spec.description or spec.name,
            status="registered",
            config=config or {},
            network_required=spec.network_required,
            air_gapped_capable=spec.air_gapped_capable,
        )
        self._connectors[name] = connector
        self._registrations[name] = registration
        logger.info(
            "Connector registered: id=%s type=%s air_gapped=%s network=%s",
            name,
            spec.name,
            spec.air_gapped_capable,
            spec.network_required,
        )
        return registration

    def get_connector(self, connector_id: str) -> ConnectorPort | None:
        """Retrieve a registered ConnectorPort by ID."""
        return self._connectors.get(connector_id)

    def get_registration(self, connector_id: str) -> ConnectorRegistration | None:
        """Retrieve a connector's registration record by ID."""
        return self._registrations.get(connector_id)

    def list_registrations(self) -> list[ConnectorRegistration]:
        """Return all connector registration records."""
        return list(self._registrations.values())

    def list_connectors(self) -> dict[str, ConnectorPort]:
        """Return raw dict of connector instances (for backward compatibility)."""
        return dict(self._connectors)

    def update_status(
        self,
        connector_id: str,
        status: ConnectorStatus,
        *,
        account_id: str | None = None,
        account_email: str | None = None,
        account_name: str | None = None,
        credential_key: str | None = None,
    ) -> ConnectorRegistration | None:
        """Update connector status and optional account metadata."""
        reg = self._registrations.get(connector_id)
        if reg is None:
            return None

        updates: dict[str, Any] = {"status": status}
        if status == "connected":
            updates["connected_at"] = datetime.now(UTC).isoformat()
            updates["disconnected_at"] = None
        elif status == "disconnected":
            updates["disconnected_at"] = datetime.now(UTC).isoformat()
            updates["credential_key"] = None

        if account_id is not None:
            updates["account_id"] = account_id
        if account_email is not None:
            updates["account_email"] = account_email
        if account_name is not None:
            updates["account_name"] = account_name
        if credential_key is not None:
            updates["credential_key"] = credential_key

        updated = reg.model_copy(update=updates)
        self._registrations[connector_id] = updated
        return updated

    def update_sync_status(
        self,
        connector_id: str,
        sync_status: ConnectorSyncStatus,
    ) -> ConnectorRegistration | None:
        """Update connector sync state and aggregate document counts."""
        reg = self._registrations.get(connector_id)
        if reg is None:
            return None

        updates: dict[str, Any] = {"sync_status": sync_status}
        if sync_status.status == "completed":
            updates["last_sync_at"] = datetime.now(UTC).isoformat()
            updates["indexed_documents"] = (
                reg.indexed_documents
                + sync_status.docs_added
                + sync_status.docs_modified
                - sync_status.docs_deleted
            )
            updates["indexed_chunks"] = sync_status.indexed_chunks

        updated = reg.model_copy(update=updates)
        self._registrations[connector_id] = updated
        return updated

    def deregister(self, connector_id: str) -> bool:
        """Remove a connector from the registry."""
        if connector_id not in self._connectors:
            return False
        del self._connectors[connector_id]
        del self._registrations[connector_id]
        logger.info("Connector deregistered: id=%s", connector_id)
        return True

    def generate_connector_id(self, connector_type: str) -> str:
        """Generate a unique connector instance ID."""
        return f"{connector_type}_{uuid.uuid4().hex[:8]}"
