from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__version__ = "0.0.1"


class Principal(BaseModel):
    """Identity principal within AegisMind (user, group, or service)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique principal identifier")
    type: Literal["user", "group", "service"] = Field(
        default="user",
        description="Type of principal",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Tenant identifier for multi-tenant isolation",
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible key-value attributes for principal",
    )


class Permission(BaseModel):
    """Fine-grained permission relation."""

    model_config = ConfigDict(frozen=True)

    resource: str = Field(..., description="Resource identifier, e.g. document:doc_123")
    relation: str = Field(..., description="Relation name, e.g. reader, writer, or viewer")
    subject: str = Field(..., description="Subject identifier, e.g. user:alice")


class ACL(BaseModel):
    """Access Control List definition governing entity access."""

    model_config = ConfigDict(frozen=True)

    allowed_principals: list[str] = Field(
        default_factory=list,
        description="List of principal identifiers allowed to access",
    )
    denied_principals: list[str] = Field(
        default_factory=list,
        description="List of principal identifiers explicitly denied access",
    )
    is_public: bool = Field(
        default=False,
        description="Whether resource is publicly accessible to all authenticated users",
    )


class Record(BaseModel):
    """Raw record ingested from an external system."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique internal record identifier")
    source: str = Field(
        ...,
        description="Source system identifier, e.g. confluence or google_drive",
    )
    external_id: str = Field(..., description="Original identifier from the source system")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw document content or attributes from source",
    )
    acl: ACL = Field(
        default_factory=ACL,
        description="ACL associated with the ingested record",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when record was ingested",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when record was last updated",
    )


class Document(BaseModel):
    """Processed document ready for indexing and retrieval."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique document identifier")
    uri: str | None = Field(
        default=None,
        description="Canonical URI pointing to source document location",
    )
    title: str = Field(..., description="Human-readable title of document")
    mime_type: str = Field(
        default="text/plain",
        description="MIME type of document content",
    )
    content: str = Field(..., description="Normalized text content")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible metadata attributes",
    )
    acl: ACL = Field(
        default_factory=ACL,
        description="Access control list governing document read access",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Tenant identifier for multi-tenant tenancy boundary",
    )


class Chunk(BaseModel):
    """Segmented unit of text derived from a Document for vector retrieval."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique chunk identifier")
    document_id: str = Field(..., description="Identifier of parent Document")
    index: int = Field(default=0, description="Sequential index within parent document")
    content: str = Field(..., description="Text segment content")
    contextual_prefix: str | None = Field(
        default=None,
        description="Optional contextual header or summary prefix prepended to chunk",
    )
    embedding: list[float] | None = Field(
        default=None,
        description="Dense vector representation",
    )
    sparse_embedding: dict[int, float] | None = Field(
        default=None,
        description="Sparse token weight representation, e.g. SPLADE or BM25",
    )
    acl: ACL = Field(
        default_factory=ACL,
        description="Access control list inherited from parent document",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata inherited or specific to chunk",
    )
    content_hash: str | None = Field(
        default=None,
        description="SHA-256 hash of chunk content for versioning and re-embedding avoidance",
    )
    is_deleted: bool = Field(
        default=False,
        description="Soft-delete tombstone flag for document updates",
    )
    deleted_at: datetime | None = Field(
        default=None,
        description="Timestamp when chunk was tombstoned",
    )
    version: int = Field(
        default=1,
        description="Monotonically increasing document version number",
    )


class Citation(BaseModel):
    """Source attribution referencing a retrieved chunk."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(..., description="Identifier of cited chunk")
    document_id: str = Field(..., description="Identifier of cited parent document")
    title: str = Field(..., description="Title of cited parent document")
    uri: str | None = Field(default=None, description="Canonical URI of cited document")
    snippet: str = Field(..., description="Excerpt or snippet text supporting answer")
    score: float = Field(..., description="Relevance or reranker score")
    tenant_id: str | None = Field(default=None, description="Tenant boundary")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Source-specific chunk metadata"
    )


class SearchResult(BaseModel):
    """Single scored result returned from retrieval pipeline."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(..., description="Identifier of retrieved chunk")
    document_id: str = Field(..., description="Identifier of parent document")
    title: str = Field(..., description="Title of parent document")
    uri: str | None = Field(default=None, description="Canonical URI of parent document")
    text: str = Field(..., description="Text content of retrieved chunk")
    score: float = Field(..., description="Similarity or relevance score")
    tenant_id: str | None = Field(default=None, description="Tenant boundary")
    citation: Citation | None = Field(
        default=None,
        description="Associated citation details",
    )


class TokenConsistency(BaseModel):
    """Consistency requirement for authorization checks."""

    model_config = ConfigDict(frozen=True)

    token: str | None = Field(
        default=None,
        description="Consistency token string",
    )
    at_least_as_fresh: bool = Field(
        default=True,
        description="Whether check requires at_least_as_fresh consistency guarantee",
    )


class Filter(BaseModel):
    """Metadata and tenancy filter applied during search."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str | None = Field(
        default=None,
        description="Optional tenant boundary to filter results",
    )
    groups: list[str] = Field(
        default_factory=list,
        description="User group identifiers for group-based filtering",
    )
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary field matching criteria",
    )


class ChatTurn(BaseModel):
    """Single turn of conversational history for query rewriting."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant", "system"] = Field(..., description="Role of speaker")
    content: str = Field(..., description="Text content of message")


class FeedbackEntry(BaseModel):
    """User feedback capture entry for answer evaluation."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique feedback record identifier")
    query: str = Field(..., description="Original search query text")
    rewritten_query: str | None = Field(
        default=None,
        description="Query after conversational rewriting",
    )
    retrieved_chunk_ids: list[str] = Field(
        default_factory=list,
        description="List of chunk IDs cited in the answer",
    )
    rating: Literal["thumbs_up", "thumbs_down"] = Field(
        ...,
        description="Binary user rating for the response",
    )
    comment: str | None = Field(default=None, description="Optional user commentary")
    tenant_id: str | None = Field(default=None, description="Tenant boundary")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when feedback was submitted",
    )


__all__ = [
    "ACL",
    "ChatTurn",
    "Citation",
    "Chunk",
    "Document",
    "FeedbackEntry",
    "Filter",
    "Permission",
    "Principal",
    "Record",
    "SearchResult",
    "TokenConsistency",
]
