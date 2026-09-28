"""Source identity and document change-detection system for AegisMind connectors.

Every ingested object must have a stable, deterministic identity so that:
- The same document is never duplicated across syncs.
- Modified documents are updated, not re-created.
- Deleted documents are soft-deleted (tombstoned) from the vector store.
- Unchanged documents are skipped (embedding reuse via content_hash).

Change states:
    NEW       - First time this external_id is seen.
    MODIFIED  - Content hash differs from last indexed version.
    UNCHANGED - Content hash matches; no re-indexing needed.
    DELETED   - Was in previous state but not found in current source scan.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

ChangeState = Literal["NEW", "MODIFIED", "UNCHANGED", "DELETED"]


class SourceIdentity(BaseModel):
    """Stable identity record for a connector-sourced document.

    This is the authoritative source-of-truth for deduplication and change detection.
    It must survive: ingestion → chunking → embedding → vector storage → retrieval.
    """

    model_config = ConfigDict(frozen=True)

    connector_id: str = Field(..., description="Connector instance identifier")
    source_id: str = Field(
        ...,
        description="Logical source group ID (e.g. repository owner/name, mailbox address)",
    )
    document_id: str = Field(..., description="Stable AegisMind-internal document identifier")
    external_id: str = Field(..., description="Original identifier from the source system")
    source_type: str = Field(
        ...,
        description="Source type label (e.g. 'github', 'gmail', 'filesystem', 'sqlite')",
    )
    source_url: str | None = Field(
        default=None,
        description="Canonical URL pointing to the source document (for citations)",
    )
    content_hash: str = Field(..., description="SHA-256 hash of normalized content")
    source_version: str | None = Field(
        default=None,
        description="Source-system version identifier (commit SHA, ETag, revision, etc.)",
    )
    modified_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when source document was last modified",
    )
    indexed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when AegisMind last indexed this document",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific metadata preserved for RAG citations",
    )


class ChangeDetectionResult(BaseModel):
    """Result of comparing current source state against previous indexed state."""

    model_config = ConfigDict(frozen=True)

    external_id: str
    change_state: ChangeState
    identity: SourceIdentity | None = None
    previous_hash: str | None = None
    current_hash: str | None = None


def compute_document_id(connector_id: str, external_id: str) -> str:
    """Compute a stable, deterministic AegisMind document ID.

    The ID is derived from connector_id + external_id so it is reproducible
    across syncs and does not change when content changes.
    """
    raw = f"{connector_id}::{external_id}".encode()
    digest = hashlib.sha256(raw).hexdigest()[:24]
    return f"doc_{connector_id[:8]}_{digest}"


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of normalized text content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def detect_change(
    external_id: str,
    current_hash: str,
    state: dict[str, str],
) -> ChangeState:
    """Detect change state by comparing current_hash against stored state.

    Args:
        external_id: Source-system document identifier.
        current_hash: SHA-256 hash of current content.
        state: Dictionary mapping external_id → last_known_hash.

    Returns:
        ChangeState: NEW, MODIFIED, or UNCHANGED.
    """
    previous_hash = state.get(external_id)
    if previous_hash is None:
        return "NEW"
    if previous_hash != current_hash:
        return "MODIFIED"
    return "UNCHANGED"


def detect_deletions(
    seen_external_ids: set[str],
    state: dict[str, str],
) -> list[str]:
    """Return external_ids that were previously indexed but not seen in current scan.

    Args:
        seen_external_ids: Set of external_ids observed in the current sync pass.
        state: Dictionary mapping external_id → last_known_hash from previous sync.

    Returns:
        List of external_ids that should be treated as DELETED.
    """
    return [eid for eid in state if eid not in seen_external_ids]


class IncrementalSyncState(BaseModel):
    """Persistent state for incremental synchronization.

    Stored as the connector's cursor dict between syncs.
    Tracks content hashes for change detection and deletion detection.
    """

    model_config = ConfigDict(frozen=True)

    # content_hashes: {external_id: sha256_content_hash}
    content_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="Last known content hash per external_id",
    )
    # source_versions: {external_id: version_string (commit SHA, ETag, etc.)}
    source_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Last known source version per external_id",
    )
    last_sync_at: str | None = Field(
        default=None,
        description="ISO timestamp of last completed sync",
    )
    cursor: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific pagination cursor",
    )

    def with_hash(self, external_id: str, content_hash: str) -> IncrementalSyncState:
        """Return a new state with the given external_id hash recorded."""
        new_hashes = {**self.content_hashes, external_id: content_hash}
        return self.model_copy(update={"content_hashes": new_hashes})

    def without_id(self, external_id: str) -> IncrementalSyncState:
        """Return a new state with the given external_id removed (for deletions)."""
        new_hashes = {k: v for k, v in self.content_hashes.items() if k != external_id}
        new_versions = {k: v for k, v in self.source_versions.items() if k != external_id}
        return self.model_copy(
            update={"content_hashes": new_hashes, "source_versions": new_versions}
        )

    def as_cursor_dict(self) -> dict[str, Any]:
        """Serialize as a cursor dict compatible with ConnectorPort.read(state=...)."""
        return {
            "content_hashes": self.content_hashes,
            "source_versions": self.source_versions,
            "last_sync_at": self.last_sync_at,
            "cursor": self.cursor,
        }

    @classmethod
    def from_cursor_dict(cls, cursor: dict[str, Any] | None) -> IncrementalSyncState:
        """Deserialize from a cursor dict returned by a previous sync."""
        if not cursor:
            return cls()
        return cls(
            content_hashes=cursor.get("content_hashes", {}),
            source_versions=cursor.get("source_versions", {}),
            last_sync_at=cursor.get("last_sync_at"),
            cursor=cursor.get("cursor", {}),
        )
