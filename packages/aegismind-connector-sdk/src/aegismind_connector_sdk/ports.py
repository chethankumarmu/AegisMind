from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

from aegismind_types import Record
from pydantic import BaseModel, ConfigDict, Field


class ConnectorSpec(BaseModel):
    """Specification describing connector metadata, capabilities, and configuration schema."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Unique connector name identifier")
    version: str = Field(default="0.0.1", description="Connector release version")
    description: str = Field(
        default="",
        description="Human-readable description of what this connector does",
    )
    documentation_url: str | None = Field(
        default=None,
        description="Link to connector setup and documentation",
    )
    config_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema defining required connector configuration",
    )
    supports_incremental: bool = Field(
        default=False,
        description="Whether connector supports cursor-based incremental sync",
    )
    supported_destination_sync_modes: list[str] = Field(
        default_factory=lambda: ["full_refresh", "incremental"],
        description="Sync modes supported by this connector",
    )
    supported_auth: list[str] = Field(
        default_factory=lambda: ["none"],
        description="Supported authentication mechanisms (e.g. oauth2, bearer, api_key, none)",
    )
    # Operational mode flags
    network_required: bool = Field(
        default=False,
        description="Whether this connector requires outbound network access",
    )
    air_gapped_capable: bool = Field(
        default=True,
        description="Whether this connector can operate in air-gapped sovereign mode",
    )
    processing_location: Literal["local", "remote", "hybrid"] = Field(
        default="local",
        description="Where data processing occurs",
    )
    embedding_location: Literal["local", "remote"] = Field(
        default="local",
        description="Where embeddings are generated",
    )
    vector_store_location: Literal["local", "remote"] = Field(
        default="local",
        description="Where vectors are persisted",
    )


class ConnectorAccountInfo(BaseModel):
    """Normalized account/identity information from a connected external source."""

    model_config = ConfigDict(frozen=True)

    connector_id: str = Field(..., description="Connector instance identifier")
    account_id: str = Field(..., description="Account or subject identifier from source")
    display_name: str | None = Field(default=None, description="Human-readable account name")
    email: str | None = Field(default=None, description="Account email address")
    avatar_url: str | None = Field(default=None, description="Avatar or profile image URL")
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw provider account metadata",
    )


class ConnectorSyncStatus(BaseModel):
    """Current synchronization status for a connector instance."""

    model_config = ConfigDict(frozen=True)

    connector_id: str = Field(..., description="Connector instance identifier")
    status: Literal["idle", "pending", "running", "completed", "partial", "failed"] = Field(
        default="idle",
        description="Current sync lifecycle state",
    )
    last_sync_at: str | None = Field(
        default=None, description="ISO timestamp of last completed sync"
    )
    last_sync_run_id: str | None = Field(default=None, description="Last sync run identifier")
    docs_added: int = Field(default=0, description="Documents added in last sync")
    docs_modified: int = Field(default=0, description="Documents modified in last sync")
    docs_deleted: int = Field(default=0, description="Documents deleted in last sync")
    docs_unchanged: int = Field(default=0, description="Documents unchanged in last sync")
    docs_failed: int = Field(default=0, description="Documents that failed processing")
    indexed_documents: int = Field(default=0, description="Total indexed document count")
    indexed_chunks: int = Field(default=0, description="Total indexed chunk count")
    errors: list[str] = Field(default_factory=list, description="Recent sync error messages")
    cursor: dict[str, Any] = Field(
        default_factory=dict,
        description="Current incremental sync cursor state",
    )


class SourceInfo(BaseModel):
    """Metadata about a discoverable source within a connector (e.g. a repository, mailbox)."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(..., description="Unique source identifier")
    display_name: str = Field(..., description="Human-readable source name")
    source_type: str = Field(..., description="Source type (e.g. repository, mailbox, folder)")
    description: str | None = Field(default=None, description="Optional source description")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Source-specific metadata")
    is_selected: bool = Field(default=False, description="Whether this source is selected for sync")


@runtime_checkable
class ConnectorPort(Protocol):
    """Core protocol defining the contract for all AegisMind ingestion connectors.

    Connectors are READ-ONLY data sources. They fetch external content and normalize
    it into Records which flow through the existing AegisMind ingestion pipeline.
    They do NOT implement RAG, LLM, or separate search systems.
    """

    def spec(self) -> ConnectorSpec:
        """Return the connector specification and schema metadata."""
        ...

    async def check(self) -> bool:
        """Verify connectivity, credentials, and source availability."""
        ...

    def read(
        self,
        state: dict[str, Any] | None = None,
    ) -> AsyncIterator[Record]:
        """Read records incrementally or fully from source data stream.

        Args:
            state: Optional state dictionary holding cursor values from previous sync.

        Yields:
            Record instances ready for ingestion and indexing.
        """
        ...
