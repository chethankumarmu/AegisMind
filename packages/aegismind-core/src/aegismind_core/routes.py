from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from aegismind_connector_sdk.ports import ConnectorPort, ConnectorSpec
from aegismind_connector_sdk.registry import ConnectorRegistry
from aegismind_graph.engine import KnowledgeGraphEngine
from aegismind_infra.ports import SecretStorePort
from aegismind_infra.secrets import MemorySecretStore
from aegismind_ingestion.dlq import MemoryDLQAdapter
from aegismind_ingestion.ports import DLQPort, IngestionPipelinePort
from aegismind_ingestion.worker import ScribeSyncReport, ScribeWorker
from aegismind_retrieval.adapters_vector import MemoryVectorStoreAdapter
from aegismind_retrieval.pipeline import PipelineResult, RetrievalPipeline
from aegismind_retrieval.ports import VectorStorePort
from aegismind_types import (
    ACL,
    ChatTurn,
    Chunk,
    FeedbackEntry,
    Principal,
)
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from aegismind_core.adapters.llm import get_llm_adapter
from aegismind_core.agent import (
    AgentRunResult,
    LocalKnowledgeSearchAdapter,
    LocalToolActivityRecorder,
    NoteCreatorAdapter,
    SandboxedCommandRunnerAdapter,
    SovereignAgentLoop,
    SystemFileReaderAdapter,
)
from aegismind_core.approval import ApprovalGate
from aegismind_core.budgeting import apply_context_budget
from aegismind_core.memory import ConversationMemory
from aegismind_core.observability import trace_span
from aegismind_core.ports.llm import LLMPort

logger = logging.getLogger(__name__)


# --- Domain Models for API ---


class FeedbackCreateRequest(BaseModel):
    """Payload for submitting user thumbs up/down feedback."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="Query submitted")
    rewritten_query: str | None = Field(default=None, description="Rewritten query string")
    retrieved_chunk_ids: list[str] = Field(default_factory=list, description="IDs of cited chunks")
    rating: Literal["thumbs_up", "thumbs_down"] = Field(..., description="User rating")
    comment: str | None = Field(default=None, description="Optional feedback note")
    tenant_id: str | None = Field(default=None, description="Tenant boundary")


class IngestDocumentRequest(BaseModel):
    """Payload for ingesting custom documents or datasets with access control."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(..., description="Document or dataset title")
    content: str = Field(..., description="Text content or dataset records")
    document_id: str | None = Field(default=None, description="Optional custom document ID")
    tenant_id: str = Field(default="corp-default", description="Tenant boundary")
    allowed_users: list[str] = Field(
        default_factory=lambda: ["alice", "bob"],
        description="Users granted viewer permission",
    )
    uri: str | None = Field(default=None, description="Optional source link or URI")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetChatRequest(BaseModel):
    """Payload for chatting with an individual dataset or document."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="User query directed to this dataset")
    document_id: str | None = Field(default=None, description="Target document ID")
    title: str | None = Field(default=None, description="Dataset title")
    content: str | None = Field(default=None, description="Optional raw text or dataset content")
    user_id: str = Field(default="alice", description="User principal ID")
    tenant_id: str = Field(default="corp-default", description="Tenant ID")


class SearchApiRequest(BaseModel):
    """Payload for access-controlled search."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="Query text to search")
    principal_id: str = Field(default="anonymous", description="Principal executing search")
    user_id: str | None = Field(default=None, description="Alias for principal_id")
    principal_type: Literal["user", "group", "service"] = Field(default="user")
    tenant_id: str | None = Field(default=None, description="Optional tenant boundary")
    attributes: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=10, ge=1, le=100)
    limit: int | None = Field(default=None, description="Alias for top_k")
    overfetch_factor: float | None = Field(default=None, ge=3.0, le=5.0)
    query_type: str = Field(
        default="factual", description="Query type (factual, navigational, exploratory)"
    )
    chat_history: list[dict[str, str]] | list[ChatTurn] | None = None
    apply_mmr: bool = Field(default=True, description="Apply MMR diversity reordering")
    mmr_lambda: float = Field(default=0.7, ge=0.0, le=1.0)
    sparse_query: dict[int, float] | None = None
    pre_filter: dict[str, Any] | None = None
    sources: list[str] | str | None = Field(
        default=None, description="Sources to filter: 'local', 'github', 'gmail', or 'all'"
    )


class GroupAliasRequest(BaseModel):
    """Payload for mapping IdP group to canonical group."""

    model_config = ConfigDict(frozen=True)

    idp_group: str = Field(..., description="Source Identity Provider group name")
    canonical_group: str = Field(..., description="Target canonical group identifier")
    tenant_id: str | None = Field(default=None)


class SystemModeRequest(BaseModel):
    """Payload for setting system operational mode."""

    model_config = ConfigDict(frozen=True)

    air_gapped: bool = Field(..., description="True for Sovereign Air-Gapped Mode, False for Connected")


class ConnectorConnectRequest(BaseModel):
    """Payload for connecting a knowledge source."""

    model_config = ConfigDict(frozen=True)

    token: str | None = Field(default=None, description="Access token or PAT (stored securely)")
    config: dict[str, Any] = Field(default_factory=dict, description="Connector configuration options")


class ConnectorActionRequest(BaseModel):
    """Payload for managing or triggering connector sync."""

    model_config = ConfigDict(frozen=True)

    connector_name: str
    action: Literal["sync", "configure"] = "sync"
    config: dict[str, Any] = Field(default_factory=dict)
    initial_cursor: dict[str, Any] | None = None


class SecretCreateRequest(BaseModel):
    """Payload for safely storing an encrypted secret."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Unique secret name or key identifier")
    secret_value: str = Field(..., description="Plaintext secret value to encrypt")
    tenant_id: str | None = Field(default=None)


class AuditLogEntry(BaseModel):
    """Immutable audit trail record for compliance and observability."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = Field(..., description="Category of event (e.g. search, sync, authz)")
    principal_id: str = Field(..., description="Principal who triggered event")
    resource_id: str | None = Field(default=None)
    action: str = Field(..., description="Action name executed")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentChatApiRequest(BaseModel):
    """Payload for invoking the sovereign local agent."""

    model_config = ConfigDict(frozen=True)

    prompt: str = Field(..., description="Prompt or task instruction for the sovereign agent")
    model: str | None = Field(default=None, description="Target Ollama model name")
    system_instruction: str | None = Field(default=None, description="Custom system instruction")
    allowed_roots: list[str] = Field(
        default_factory=lambda: [".", "./storage"],
        description="Allowlisted directories for file reading",
    )
    notes_dir: str = Field(default="./storage/notes", description="Notes destination directory")


class NoteSummary(BaseModel):
    """Summary record for a stored markdown note."""

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    tags: list[str]
    created_at: str
    source_query: str | None = None
    preview: str
    path: str


class StudyRequest(BaseModel):
    """Payload for interactive study and document Q&A."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(..., description="Document or presentation title")
    content: str = Field(..., description="Extracted content from document or slides")
    query: str = Field(..., description="User question, quiz request, or study instruction")
    mode: Literal["qa", "quiz", "summary", "explain"] = Field(
        default="qa",
        description="Study mode: direct Q&A, quiz generation, summary, or conceptual explanation",
    )
    user_id: str = Field(default="alice", description="User ID for session")
    tenant_id: str = Field(default="corp-default", description="Tenant boundary")


class CreateNoteRequest(BaseModel):
    """Payload for creating a markdown note in the local vault."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(..., description="Note title")
    content: str = Field(..., description="Markdown note body")
    tags: list[str] = Field(default_factory=lambda: ["study", "learning"])
    source_query: str | None = Field(default=None)
    notes_dir: str = Field(default="./storage/notes")


def extract_text_from_file_bytes(filename: str, file_bytes: bytes) -> tuple[str, str, int]:
    """Extract readable text from file bytes across diverse document formats.

    Supports PDF, PPTX, PPT, DOCX, DOC, CSV, JSON, Markdown, and plain text.
    Returns (extracted_text, detected_file_type, page_or_slide_count).
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # 1. PDF Documents
    if ext == "pdf":
        try:
            import pypdf

            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text: list[str] = []
            for idx, page in enumerate(reader.pages, start=1):
                text_content = (page.extract_text() or "").strip()
                if text_content:
                    pages_text.append(f"--- Page {idx} ---\n{text_content}")
            result_text = "\n\n".join(pages_text) if pages_text else "Empty PDF document"
            return result_text, "pdf", len(reader.pages)
        except Exception as exc:
            logger.warning("PDF extraction failed for %s: %s", filename, exc)
            return f"Failed to extract PDF text: {exc}", "pdf", 0

    # 2. PowerPoint Presentations
    if ext in ("pptx", "ppt"):
        try:
            import pptx

            prs = pptx.Presentation(io.BytesIO(file_bytes))
            slides_text: list[str] = []
            for idx, slide in enumerate(prs.slides, start=1):
                lines: list[str] = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for p in shape.text_frame.paragraphs:
                            t = p.text.strip()
                            if t:
                                lines.append(t)
                    elif shape.has_table:
                        for row in shape.table.rows:
                            cells = [c.text.strip() for c in row.cells if c.text.strip()]
                            if cells:
                                lines.append(" | ".join(cells))
                if lines:
                    slides_text.append(f"--- Slide {idx} ---\n" + "\n".join(lines))
            result_text = (
                "\n\n".join(slides_text) if slides_text else "Empty PowerPoint presentation"
            )
            return result_text, "presentation", len(prs.slides)
        except Exception as exc:
            logger.warning("PowerPoint extraction failed for %s: %s", filename, exc)
            return f"Failed to extract presentation text: {exc}", "presentation", 0

    # 3. Word Documents
    if ext in ("docx", "doc"):
        try:
            import docx

            doc = docx.Document(io.BytesIO(file_bytes))
            doc_lines: list[str] = []
            for p in doc.paragraphs:
                t = p.text.strip()
                if t:
                    doc_lines.append(t)
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        doc_lines.append(" | ".join(cells))
            result_text = "\n\n".join(doc_lines) if doc_lines else "Empty Word document"
            return result_text, "document", 1
        except Exception as exc:
            logger.warning("Word extraction failed for %s: %s", filename, exc)
            return f"Failed to extract Word text: {exc}", "document", 0

    # 4. Text and Code Decoders with fallbacks
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            decoded = file_bytes.decode(enc)
            cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", decoded).strip()
            if cleaned:
                file_type = ext if ext else "text"
                return cleaned, file_type, 1
        except UnicodeDecodeError:
            continue

    # 5. Raw binary fallback: extract printable ASCII substrings
    strings = re.findall(rb"[\x20-\x7e]{4,}", file_bytes)
    extracted = "\n".join(s.decode("latin-1") for s in strings[:500])
    return extracted or "Binary file with no extractable text", "binary", 1


# --- Storage / State Container ---


class CoreState:
    """In-memory state and dependency container for AegisMind Core services."""

    def __init__(
        self,
        retrieval_pipeline: RetrievalPipeline | None = None,
        vector_store: VectorStorePort | None = None,
        ingestion_pipeline: IngestionPipelinePort | None = None,
        secret_store: SecretStorePort | None = None,
        llm: LLMPort | None = None,
        dlq: DLQPort | None = None,
    ) -> None:
        self.retrieval_pipeline = retrieval_pipeline
        self.authz = None
        self.vector_store = vector_store or MemoryVectorStoreAdapter()
        self.ingestion_pipeline = ingestion_pipeline
        self.secret_store = secret_store or MemorySecretStore()
        self.llm = llm
        self.dlq = dlq or MemoryDLQAdapter()
        self.scribe_worker = (
            ScribeWorker(pipeline=ingestion_pipeline, dlq=self.dlq) if ingestion_pipeline else None
        )

        self.connectors: dict[str, ConnectorPort] = {}
        self.connector_registry = ConnectorRegistry()
        self.group_aliases: dict[str, str] = {}
        self.audit_log: list[AuditLogEntry] = []
        self.indexed_resources: list[dict[str, Any]] = []
        self.feedback_entries: list[FeedbackEntry] = []

        # Knowledge graph, approval gate, and conversation memory
        self.graph_engine = KnowledgeGraphEngine(graph_path="./storage/graph/graph.json")
        self.approval_gate = ApprovalGate(approval_log_path="./storage/approval/approvals.json")
        self.memory = ConversationMemory(db_path="./storage/memory/memory.db")
        self.activity_recorder = LocalToolActivityRecorder(
            storage_path="./storage/activity/tool_events.json"
        )

    def record_audit(
        self,
        event_type: str,
        principal_id: str,
        action: str,
        resource_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Append an immutable audit entry."""
        entry = AuditLogEntry(
            event_type=event_type,
            principal_id=principal_id,
            action=action,
            resource_id=resource_id,
            metadata=metadata or {},
        )
        self.audit_log.append(entry)
        return entry


def create_routes(state: CoreState) -> APIRouter:
    """Create configured FastAPI APIRouter containing all core endpoint handlers."""
    router = APIRouter(prefix="/api/v1", tags=["api_v1"])

    # 1. POST /api/v1/search: Access-controlled search
    @router.post("/search", response_model=PipelineResult)
    async def search(req: SearchApiRequest) -> PipelineResult:
        pipeline = state.retrieval_pipeline
        if pipeline is None:
            from aegismind_core.bootstrap import init_default_core_state

            seeded = await init_default_core_state()
            state.retrieval_pipeline = seeded.retrieval_pipeline
            state.vector_store = seeded.vector_store
            state.connectors = seeded.connectors
            state.indexed_resources = seeded.indexed_resources
            pipeline = seeded.retrieval_pipeline

        if pipeline is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Retrieval pipeline is not configured",
            )

        effective_principal_id = (
            req.user_id if (req.principal_id == "anonymous" and req.user_id) else req.principal_id
        )
        effective_top_k = req.limit if req.limit is not None else req.top_k
        principal = Principal(
            id=effective_principal_id,
            type=req.principal_type,
            tenant_id=req.tenant_id,
            attributes=req.attributes,
        )

        parsed_history: list[ChatTurn] | None = None
        if req.chat_history:
            parsed_history = [
                t if isinstance(t, ChatTurn) else ChatTurn(**t) for t in req.chat_history
            ]

        try:
            with trace_span(
                "agora.search",
                attributes={
                    "query": req.query,
                    "tenant_id": req.tenant_id,
                    "user_id": principal.id,
                },
            ):
                # Build effective pre_filter — merge sources filter with explicit pre_filter
                effective_pre_filter: dict[str, Any] = dict(req.pre_filter or {})
                if req.sources and req.sources != "all":
                    src_list = req.sources if isinstance(req.sources, list) else [req.sources]
                    # Map frontend source labels to backend metadata source_type values
                    type_map = {"local": "filesystem", "github": "github", "gmail": "gmail"}
                    mapped = list({type_map.get(s, s) for s in src_list})
                    if mapped:
                        effective_pre_filter["source_type"] = mapped if len(mapped) > 1 else mapped[0]

                result = await pipeline.execute(
                    query=req.query,
                    principal=principal,
                    chat_history=parsed_history,
                    query_type=req.query_type,
                    sparse_query=req.sparse_query,
                    pre_filter=effective_pre_filter or None,
                    top_k=effective_top_k,
                    overfetch_factor=req.overfetch_factor,
                    apply_mmr=req.apply_mmr,
                    mmr_lambda=req.mmr_lambda,
                )

            state.record_audit(
                event_type="search",
                principal_id=principal.id,
                action="search_query",
                metadata={
                    "query": req.query,
                    "rewritten_query": result.rewritten_query,
                    "query_type": req.query_type,
                    "top_k": effective_top_k,
                    "results_count": len(result.results),
                    "deny_rate": result.deny_rate,
                },
            )
            return result
        except Exception as exc:
            logger.error("Search execution failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Search failed: {exc}",
            ) from exc

    # 2. GET /api/v1/chat: Server-Sent Events (SSE) streaming answers with citations
    @router.get("/chat")
    @router.get("/chat/stream")
    async def chat_sse(
        query: str = Query(..., description="User query for chat session"),
        principal_id: str = Query("anonymous", description="Principal ID"),
        user_id: str | None = Query(None, description="Alias for principal_id"),
        tenant_id: str | None = Query(None, description="Tenant ID"),
        model: str | None = Query(None, description="Ollama model for answer generation"),
        top_k: int = Query(5, ge=1, le=20),
        sources: str | None = Query(None, description="Comma-separated source filter: local,github,gmail"),
    ) -> StreamingResponse:
        pipeline = state.retrieval_pipeline
        if pipeline is None:
            from aegismind_core.bootstrap import init_default_core_state

            seeded = await init_default_core_state()
            state.retrieval_pipeline = seeded.retrieval_pipeline
            state.vector_store = seeded.vector_store
            state.connectors = seeded.connectors
            state.indexed_resources = seeded.indexed_resources
            if state.llm is None:
                state.llm = seeded.llm
            pipeline = seeded.retrieval_pipeline

        if pipeline is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Retrieval pipeline is not configured",
            )

        effective_principal_id = (
            user_id if (principal_id == "anonymous" and user_id) else principal_id
        )
        principal = Principal(id=effective_principal_id, type="user", tenant_id=tenant_id)

        # Build source-type pre_filter
        chat_pre_filter: dict[str, Any] | None = None
        if sources and sources.lower() != "all":
            src_list = [s.strip() for s in sources.split(",") if s.strip()]
            type_map = {"local": "filesystem", "github": "github", "gmail": "gmail"}
            mapped = list({type_map.get(s, s) for s in src_list})
            if mapped:
                chat_pre_filter = {"source_type": mapped if len(mapped) > 1 else mapped[0]}

        async def sse_event_stream() -> AsyncIterator[str]:
            state.record_audit(
                event_type="chat",
                principal_id=effective_principal_id,
                action="chat_sse_stream",
                metadata={"query": query, "model": model, "sources": sources},
            )

            # Stage 0: Thinking progress indications
            yield "event: thinking\ndata: Querying vector store with coarse tenant filter...\n\n"
            yield "event: thinking\ndata: Fusing dense and lexical search candidates...\n\n"
            await asyncio.sleep(0.04)
            yield (
                "event: thinking\ndata: Applying cross-encoder reranker and "
                "synthesizing response...\n\n"
            )
            await asyncio.sleep(0.04)

            # Stage 1: Retrieval through Sacred Pipeline
            with trace_span(
                "agora.chat_retrieval",
                attributes={
                    "query": query,
                    "tenant_id": tenant_id,
                    "user_id": effective_principal_id,
                },
            ):
                res = await pipeline.execute(
                    query=query,
                    principal=principal,
                    top_k=top_k,
                    pre_filter=chat_pre_filter,
                )

            # Stage 2: Token budgeting and memory retrieval
            llm_adapter = state.llm or get_llm_adapter()
            effective_tenant_id = tenant_id or "corp-default"

            memory_context = ""
            try:
                memory_context = await state.memory.retrieve_context(
                    query=query,
                    user_id=effective_principal_id,
                    tenant_id=effective_tenant_id,
                    embedder=getattr(pipeline, "embedder", None),
                    top_k=3,
                )
                # Strip image paths from memory context to prevent LLM errors
                import re

                memory_context = re.sub(
                    r".*\.(?:png|jpg|jpeg|gif|bmp|webp|tiff|svg).*",
                    "",
                    memory_context,
                    flags=re.IGNORECASE,
                )
            except Exception as exc:
                logger.debug("Memory retrieval failed: %s", exc)

            # Stage 2b: Token budgeting and prompt formulation
            budget_res = apply_context_budget(
                query=query,
                results=res.results,
                llm=llm_adapter,
                model=model,
            )
            surviving_results = budget_res.chunks
            trim_notice = budget_res.notice

            if memory_context:
                if surviving_results:
                    prompt = memory_context + "\n" + budget_res.prompt
                    system_prompt = budget_res.system_prompt
                    fallback_answer_text = (
                        "Could not stream response from local Ollama. Please ensure Ollama is "
                        "running on http://127.0.0.1:11434 and model 'llama3.2:latest' is "
                        "available."
                    )
                elif res.total_candidates_evaluated > 0 and res.authorized_candidates_count == 0:
                    fallback_answer_text = (
                        f"Access denied: Relevant candidate documents matched query '{query}', but "
                        f"principal '{effective_principal_id}' lacks viewer authorization. "
                        "Under AegisMind zero-leakage security, unauthorized content is strictly "
                        "excluded."
                    )
                    system_prompt = (
                        "You are AegisMind AI assistant.\n"
                        "Notice: Matching candidate documents exist in the repository, "
                        "but the user lacks viewer authorization to read them. Mention this "
                        "access boundary briefly, then answer the user's question helpfully using "
                        "general knowledge."
                    )
                    prompt = memory_context + f"\nUSER QUESTION:\n{query}"
                else:
                    fallback_answer_text = (
                        "Could not stream response from local Ollama. Please ensure Ollama is "
                        "running on http://127.0.0.1:11434 and model 'llama3.2:latest' is "
                        "available."
                    )
                    system_prompt = (
                        "You are AegisMind, a professional, intelligent, and helpful AI "
                        "assistant.\n"
                        "Answer the user's question clearly, accurately, and thoroughly."
                    )
                    prompt = memory_context + f"\nUSER QUESTION:\n{query}"
            else:
                if surviving_results:
                    prompt = budget_res.prompt
                    system_prompt = budget_res.system_prompt
                    fallback_answer_text = (
                        "Could not stream response from local Ollama. Please ensure Ollama is "
                        "running on http://127.0.0.1:11434 and model 'llama3.2:latest' is "
                        "available."
                    )
                elif res.total_candidates_evaluated > 0 and res.authorized_candidates_count == 0:
                    fallback_answer_text = (
                        f"Access denied: Relevant candidate documents matched query '{query}', but "
                        f"principal '{effective_principal_id}' lacks viewer authorization. "
                        "Under AegisMind zero-leakage security, unauthorized content is strictly "
                        "excluded."
                    )
                    system_prompt = (
                        "You are AegisMind AI assistant.\n"
                        "Notice: Matching candidate documents exist in the repository, "
                        "but the user lacks viewer authorization to read them. Mention this "
                        "access boundary briefly, then answer the user's question helpfully using "
                        "general knowledge."
                    )
                    prompt = f"USER QUESTION:\n{query}"
                else:
                    fallback_answer_text = (
                        "Could not stream response from local Ollama. Please ensure Ollama is "
                        "running on http://127.0.0.1:11434 and model 'llama3.2:latest' is "
                        "available."
                    )
                    system_prompt = (
                        "You are AegisMind, a professional, intelligent, and helpful AI "
                        "assistant.\n"
                        "Answer the user's question clearly, accurately, and thoroughly."
                    )
                    prompt = f"USER QUESTION:\n{query}"

            # Stage 3: Stream tokens from LLMPort with fallback
            llm_streamed = False
            streamed_response = ""
            logger.info(
                "Starting LLM stream: prompt=%s... system_prompt_len=%d",
                prompt[:50],
                len(system_prompt),
            )
            try:
                async for token in llm_adapter.stream_generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                ):
                    llm_streamed = True
                    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", token)
                    sanitized = (
                        sanitized.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
                    )
                    streamed_response += sanitized
                    data = json.dumps({"token": sanitized})
                    yield f"event: token\ndata: {data}\n\n"
            except Exception as exc:
                logger.info("LLM streaming error: %s | prompt=%s", exc, prompt[:100])

            if llm_streamed and streamed_response:
                try:
                    await state.memory.record_conversation(
                        user_id=effective_principal_id,
                        tenant_id=effective_tenant_id,
                        query=query,
                        response=streamed_response,
                        embedder=getattr(pipeline, "embedder", None),
                    )
                except Exception as exc:
                    logger.debug("Memory storage failed: %s", exc)

            if not llm_streamed:
                words = fallback_answer_text.split(" ")
                for i, word in enumerate(words):
                    token = word if i == 0 else " " + word
                    data = json.dumps({"token": token})
                    yield f"event: token\ndata: {data}\n\n"
                    await asyncio.sleep(0.015)

            # If chunks were dropped due to context limits, append notice to stream
            if trim_notice:
                notice_data = json.dumps({"token": f"\n\n{trim_notice}"})
                yield f"event: token\ndata: {notice_data}\n\n"

            # Stage 4: Stream citations for surviving chunks
            citations = [r.citation.model_dump() for r in surviving_results if r.citation]
            yield f"event: citations\ndata: {json.dumps(citations)}\n\n"
            for c in citations:
                yield f"event: citation\ndata: {json.dumps(c)}\n\n"

            # Stage 5: Stream done event with dynamically updated top_k
            done_payload = json.dumps(
                {
                    "status": "completed",
                    "citations_count": len(citations),
                    "top_k": len(surviving_results),
                    "notice": trim_notice,
                }
            )
            yield f"event: done\ndata: {done_payload}\n\n"

        return StreamingResponse(sse_event_stream(), media_type="text/event-stream")

    # 4. GET /api/v1/memory/stats: Memory statistics
    @router.get("/memory/stats")
    async def memory_stats(
        user_id: str = Query("anonymous", description="User ID"),
        tenant_id: str = Query("corp-default", description="Tenant ID"),
    ) -> dict[str, int]:
        return state.memory.get_stats(user_id, tenant_id)

    # 5. GET /api/v1/memory/history: Conversation history
    @router.get("/memory/history")
    async def memory_history(
        user_id: str = Query("anonymous", description="User ID"),
        tenant_id: str = Query("corp-default", description="Tenant ID"),
        limit: int = Query(50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return state.memory.get_history(user_id, tenant_id, limit)

    # 6. GET /api/v1/memory/search: Search past conversations
    @router.get("/memory/search")
    async def memory_search(
        q: str = Query(..., description="Search query"),
        user_id: str = Query("anonymous", description="User ID"),
        tenant_id: str = Query("corp-default", description="Tenant ID"),
        top_k: int = Query(5, ge=1, le=20),
    ) -> list[dict[str, Any]]:
        results = state.memory.get_history(user_id, tenant_id, limit=top_k * 2)
        filtered = [
            r
            for r in results
            if q.lower() in r["query"].lower() or q.lower() in r["response"].lower()
        ]
        return filtered[:top_k]

    # 7. DELETE /api/v1/memory/clear: Clear memory for a user
    @router.delete("/memory/clear")
    async def memory_clear(
        user_id: str = Query("anonymous", description="User ID"),
        tenant_id: str = Query("corp-default", description="Tenant ID"),
    ) -> dict[str, str]:
        state.memory.clear(user_id, tenant_id)
        return {"status": "cleared"}

    # 8. POST /api/v1/memory/ingest: Record a conversation turn
    @router.post("/memory/ingest")
    async def memory_ingest(
        user_id: str = Query("anonymous", description="User ID"),
        tenant_id: str = Query("corp-default", description="Tenant ID"),
        query: str = Query(..., description="User question"),
        response: str = Query(..., description="Assistant response"),
    ) -> dict[str, str]:
        await state.memory.record_conversation(user_id, tenant_id, query, response)
        return {"status": "stored"}

    # 3. GET|POST /api/v1/connectors: Spec discovery, configuration, sync triggering
    @router.get("/connectors")
    async def list_connectors() -> dict[str, Any]:
        """Discover available connectors with full metadata from the registry."""
        connector_list = []

        # Merge registry registrations with raw connectors dict
        all_ids = set(state.connectors.keys()) | {
            r.connector_id for r in state.connector_registry.list_registrations()
        }

        for name in sorted(all_ids):
            conn = state.connectors.get(name)
            reg = state.connector_registry.get_registration(name)
            spec: ConnectorSpec | None = conn.spec() if conn else None

            entry: dict[str, Any] = {"name": name}
            if spec:
                entry["spec"] = spec.model_dump()
            if reg:
                entry["registration"] = reg.model_dump()

            connector_list.append(entry)

        return {
            "connectors": connector_list,
            "total": len(connector_list),
            "mode": "SOVEREIGN / AIR-GAPPED"
            if os.environ.get("AIR_GAPPED", "").lower() in {"1", "true", "yes"}
            else "CONNECTED KNOWLEDGE",
        }

    @router.post("/connectors")
    async def manage_connectors(req: ConnectorActionRequest) -> dict[str, Any]:
        """Trigger sync or configure an existing connector."""
        if req.connector_name not in state.connectors:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connector '{req.connector_name}' is not registered",
            )

        connector = state.connectors[req.connector_name]

        if req.action == "configure":
            state.record_audit(
                event_type="connector",
                principal_id="admin",
                action="configure_connector",
                resource_id=req.connector_name,
                metadata=req.config,
            )
            return {"status": "configured", "connector": req.connector_name}

        # Action == 'sync'
        if state.scribe_worker is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Scribe worker is not configured",
            )

        run_id = f"sync_{req.connector_name}_{uuid.uuid4().hex[:8]}"
        state.record_audit(
            event_type="connector",
            principal_id="system",
            action="sync_trigger",
            resource_id=req.connector_name,
            metadata={"run_id": run_id},
        )

        report: ScribeSyncReport = await state.scribe_worker.run_sync(
            run_id=run_id,
            connector=connector,
            initial_cursor=req.initial_cursor,
        )

        return {
            "status": "sync_finished",
            "report": report.model_dump(),
        }

    # 3b. GET /api/v1/connectors/{connector_id}: Get connector detail
    @router.get("/connectors/{connector_id}")
    async def get_connector(connector_id: str) -> dict[str, Any]:
        """Get detailed metadata for a specific connector instance."""
        conn = state.connectors.get(connector_id)
        reg = state.connector_registry.get_registration(connector_id)

        if conn is None and reg is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connector '{connector_id}' not found",
            )

        result: dict[str, Any] = {"connector_id": connector_id}
        if conn:
            result["spec"] = conn.spec().model_dump()
        if reg:
            result["registration"] = reg.model_dump()
        return result

    # 3c. POST /api/v1/connectors/{connector_id}/sync: Trigger sync for a connector
    @router.post("/connectors/{connector_id}/sync")
    async def sync_connector(
        connector_id: str,
        initial_cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger an incremental sync for a specific connector instance."""
        connector = state.connectors.get(connector_id)
        if connector is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connector '{connector_id}' not found",
            )
        if state.scribe_worker is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Scribe worker is not configured (no ingestion pipeline)",
            )

        run_id = f"sync_{connector_id}_{uuid.uuid4().hex[:8]}"
        state.record_audit(
            event_type="connector",
            principal_id="system",
            action="SYNC_START",
            resource_id=connector_id,
            metadata={"run_id": run_id, "connector_type": connector.spec().name},
        )

        # Record the sync start in the LocalTools activity system so it appears in the UI
        activity_event_id = state.activity_recorder.record_start(
            tool_name=f"{connector_id.upper()}_SYNC",
            category="knowledge",
            parameters={
                "connector_id": connector_id,
                "connector_type": connector.spec().name,
                "run_id": run_id,
            },
            agent_id="system",
        )

        try:
            report: ScribeSyncReport = await state.scribe_worker.run_sync(
                run_id=run_id,
                connector=connector,
                initial_cursor=initial_cursor,
            )
            state.record_audit(
                event_type="connector",
                principal_id="system",
                action="SYNC_COMPLETE" if report.status == "COMPLETED" else "SYNC_FAILED",
                resource_id=connector_id,
                metadata={
                    "run_id": run_id,
                    "records_synced": report.records_synced,
                    "chunks_indexed": report.chunks_indexed,
                    "status": report.status,
                },
            )
            # Record completion in activity system
            state.activity_recorder.record_complete(
                event_id=activity_event_id.event_id,
                result_summary=(
                    f"Synced {report.records_synced} records, "
                    f"{report.chunks_indexed} chunks indexed"
                ),
                metadata={
                    "records_synced": report.records_synced,
                    "chunks_indexed": report.chunks_indexed,
                    "status": report.status,
                },
            )
            return {"status": report.status, "report": report.model_dump()}
        except Exception as exc:
            state.record_audit(
                event_type="connector",
                principal_id="system",
                action="SYNC_FAILED",
                resource_id=connector_id,
                metadata={"run_id": run_id, "error": str(exc)},
            )
            state.activity_recorder.record_complete(
                event_id=activity_event_id.event_id,
                result_summary=f"Sync failed: {exc}",
                metadata={"error": str(exc), "status": "FAILED"},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Sync failed: {exc}",
            ) from exc

    # 3d. GET /api/v1/connectors/{connector_id}/status: Get connector sync status
    @router.get("/connectors/{connector_id}/status")
    async def get_connector_status(connector_id: str) -> dict[str, Any]:
        """Get synchronization status for a specific connector."""
        reg = state.connector_registry.get_registration(connector_id)
        conn = state.connectors.get(connector_id)

        if reg is None and conn is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connector '{connector_id}' not found",
            )

        result: dict[str, Any] = {
            "connector_id": connector_id,
            "status": reg.status if reg else "registered",
            "indexed_documents": reg.indexed_documents if reg else 0,
            "indexed_chunks": reg.indexed_chunks if reg else 0,
            "last_sync_at": reg.last_sync_at if reg else None,
        }
        if reg and reg.sync_status:
            result["sync_status"] = reg.sync_status.model_dump()
        return result

    # 3e. GET /api/v1/connectors/{connector_id}/sources: List connector sources
    @router.get("/connectors/{connector_id}/sources")
    async def list_connector_sources(connector_id: str) -> dict[str, Any]:
        """List available sources (repos, mailboxes, folders) for a connector."""
        conn = state.connectors.get(connector_id)
        if conn is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connector '{connector_id}' not found",
            )

        if not hasattr(conn, "list_sources"):
            # Connector does not support source listing (e.g. local filesystem)
            spec = conn.spec()
            return {
                "connector_id": connector_id,
                "sources": [
                    {
                        "source_id": connector_id,
                        "display_name": spec.description or spec.name,
                        "source_type": spec.name,
                    }
                ],
                "total": 1,
            }

        try:
            from aegismind_connector_sdk.network_guard import (  # noqa: PLC0415
                get_network_guard,
            )

            guard = get_network_guard()
            spec = conn.spec()
            if spec.network_required:
                guard.assert_network_allowed(connector_id)
            sources = await conn.list_sources()  # type: ignore[attr-defined]
            return {
                "connector_id": connector_id,
                "sources": [s.model_dump() for s in sources],
                "total": len(sources),
            }
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to list sources: {exc}",
            ) from exc

    # 4. GET /api/v1/resources: Browsing indexed resources
    @router.get("/resources")
    async def list_resources(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        tenant_id: str | None = Query(None),
    ) -> dict[str, Any]:
        """Browse indexed resources."""
        items = state.indexed_resources
        if tenant_id:
            items = [item for item in items if item.get("tenant_id") == tenant_id]

        total = len(items)
        paged = items[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "resources": paged,
        }
    # 3f. POST /api/v1/connectors/{connector_id}/connect: Provide credentials and connect
    @router.post("/connectors/{connector_id}/connect")
    async def connect_connector(connector_id: str, req: ConnectorConnectRequest) -> dict[str, Any]:
        """Provide credentials/token to authenticate and activate a connector."""
        conn = state.connectors.get(connector_id)
        if conn is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connector '{connector_id}' not found",
            )
        # Store the provided token in the secret store
        if req.token:
            secret_key = f"{connector_id}_token"
            await state.secret_store.set(secret_key, req.token)

        # Persist any additional config entries as secrets
        for key, value in (req.config or {}).items():
            await state.secret_store.set(f"{connector_id}_{key}", str(value))

        # Update registration status
        reg = state.connector_registry.get_registration(connector_id)
        if reg:
            reg.status = "connected"

        state.record_audit(
            event_type="connector",
            principal_id="system",
            action="CONNECTOR_CONNECTED",
            resource_id=connector_id,
            metadata={"token_provided": bool(req.token)},
        )
        return {"status": "connected", "connector_id": connector_id}

    # 3g. GET|POST /api/v1/system/mode: Inspect and update sovereign/connected mode
    @router.get("/system/mode")
    async def get_system_mode() -> dict[str, Any]:
        """Return the current system mode (sovereign / connected)."""
        air_gapped = os.environ.get("AIR_GAPPED", "false").lower() in ("1", "true", "yes")
        return {
            "air_gapped": air_gapped,
            "mode_label": "SOVEREIGN (AIR-GAPPED)" if air_gapped else "CONNECTED",
            "external_connectors_enabled": not air_gapped,
        }

    @router.post("/system/mode")
    async def set_system_mode(req: SystemModeRequest) -> dict[str, Any]:
        """Toggle the system between sovereign (air-gapped) and connected modes."""
        os.environ["AIR_GAPPED"] = "true" if req.air_gapped else "false"
        air_gapped = req.air_gapped
        state.record_audit(
            event_type="system",
            principal_id="admin",
            action="SET_SYSTEM_MODE",
            metadata={"air_gapped": air_gapped},
        )
        return {
            "air_gapped": air_gapped,
            "mode_label": "SOVEREIGN (AIR-GAPPED)" if air_gapped else "CONNECTED",
            "external_connectors_enabled": not air_gapped,
        }

    # 5. GET|POST /api/v1/group-aliases: Manage identity group mappings
    @router.get("/group-aliases")
    async def list_group_aliases() -> dict[str, Any]:
        """Retrieve registered identity group alias mappings."""
        return {"aliases": state.group_aliases}

    @router.post("/group-aliases")
    async def set_group_alias(req: GroupAliasRequest) -> dict[str, str]:
        """Register or update an identity provider group alias mapping."""
        state.group_aliases[req.idp_group] = req.canonical_group
        state.record_audit(
            event_type="identity",
            principal_id="admin",
            action="set_group_alias",
            metadata={
                "idp_group": req.idp_group,
                "canonical_group": req.canonical_group,
                "tenant_id": req.tenant_id,
            },
        )
        return {
            "status": "created",
            "idp_group": req.idp_group,
            "canonical_group": req.canonical_group,
        }

    # 6. GET /api/v1/audit: Immutable audit log access
    @router.get("/audit")
    async def get_audit_logs(
        principal_id: str | None = Query(None),
        event_type: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Query immutable audit log entries."""
        entries = state.audit_log
        if principal_id:
            entries = [e for e in entries if e.principal_id == principal_id]
        if event_type:
            entries = [e for e in entries if e.event_type == event_type]

        total = len(entries)
        paged = entries[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "entries": [e.model_dump() for e in paged],
        }

    # 7. POST & GET /api/v1/secrets: Secrets management
    @router.post("/secrets")
    async def store_secret(req: SecretCreateRequest) -> dict[str, str]:
        """Store an encrypted secret in SecretStore."""
        await state.secret_store.set_secret(req.name, req.secret_value)
        state.record_audit(
            event_type="security",
            principal_id="admin",
            action="store_secret",
            resource_id=req.name,
            metadata={"tenant_id": req.tenant_id},
        )
        return {"status": "stored", "name": req.name}

    @router.get("/secrets/{name}")
    async def get_secret(name: str) -> dict[str, Any]:
        """Retrieve stored secret value."""
        val = await state.secret_store.get_secret(name)
        if val is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Secret '{name}' not found",
            )
        return {"name": name, "value": val}

    # 8. GET /api/v1/models: Model discovery for LLMs
    @router.get("/models")
    async def get_models() -> dict[str, Any]:
        """Discover available LLM models from configured provider."""
        import os

        llm_adapter = state.llm or get_llm_adapter()
        try:
            models = await llm_adapter.list_models()
        except Exception:
            models = []
        default_name = getattr(llm_adapter, "default_model", "llama3.2:latest")
        if default_name in models:
            active = default_name
        elif models:
            active = models[0]
        else:
            active = default_name
        provider = os.environ.get("LLM_PROVIDER", "ollama")
        return {
            "models": models,
            "active_model": active,
            "provider": provider if models else "simulated",
        }

    # 9. POST /api/v1/documents: Custom document and dataset ingestion
    @router.post("/documents")
    async def ingest_document(req: IngestDocumentRequest) -> dict[str, Any]:
        """Ingest custom document or dataset records into the local vector index."""
        pipeline = state.retrieval_pipeline
        if pipeline is None:
            from aegismind_core.bootstrap import init_default_core_state

            seeded = await init_default_core_state()
            state.retrieval_pipeline = seeded.retrieval_pipeline
            state.vector_store = seeded.vector_store
            state.connectors = seeded.connectors
            state.indexed_resources = seeded.indexed_resources
            pipeline = seeded.retrieval_pipeline

        if pipeline is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Retrieval pipeline is not configured",
            )

        doc_id = req.document_id or f"doc-custom-{uuid.uuid4().hex[:8]}"
        uri = req.uri or f"dataset://{doc_id}"

        # Segment content into coherent chunks
        paragraphs = [p.strip() for p in req.content.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [req.content.strip()]

        chunks: list[Chunk] = []

        for idx, text_block in enumerate(paragraphs, start=1):
            embedding = await pipeline.embedder.embed_query(text_block)
            chunk = Chunk(
                id=f"chunk-{doc_id}-{idx:02d}",
                document_id=doc_id,
                index=idx,
                content=text_block,
                embedding=embedding,
                metadata={
                    "title": req.title,
                    "uri": uri,
                    "tenant_id": req.tenant_id,
                    **req.metadata,
                },
                acl=ACL(
                    is_public="anonymous" in req.allowed_users or "*" in req.allowed_users,
                    allowed_principals=[f"user:{u}" for u in req.allowed_users],
                ),
            )
            chunks.append(chunk)

        await state.vector_store.upsert(chunks)

        resource_entry = {
            "id": doc_id,
            "title": req.title,
            "uri": uri,
            "tenant_id": req.tenant_id,
            "chunks_count": len(chunks),
            "chunk_count": len(chunks),
            "type": "custom_dataset",
            "connector": "dataset_ingest",
            "content": req.content,
            "allowed_users": req.allowed_users,
            "created_at": datetime.now(UTC).isoformat(),
        }
        state.indexed_resources.insert(0, resource_entry)

        state.record_audit(
            event_type="dataset",
            principal_id="admin",
            action="ingest_custom_dataset",
            resource_id=doc_id,
            metadata={"title": req.title, "chunks_count": len(chunks)},
        )

        return {
            "status": "indexed",
            "document_id": doc_id,
            "title": req.title,
            "chunks_count": len(chunks),
            "chunk_count": len(chunks),
            "content": req.content,
            "allowed_users": req.allowed_users,
        }

    # 10. DELETE /api/v1/documents/{document_id}: Delete dataset and revoke permissions
    @router.delete("/documents/{document_id}")
    async def delete_document(document_id: str) -> dict[str, str]:
        """Delete an ingested dataset from local index."""
        if hasattr(state.vector_store, "_chunks"):
            matching_ids = [
                cid
                for cid, c in state.vector_store._chunks.items()
                if getattr(c, "document_id", None) == document_id
            ]
            if matching_ids:
                await state.vector_store.delete(matching_ids)

        state.indexed_resources = [r for r in state.indexed_resources if r.get("id") != document_id]

        state.record_audit(
            event_type="dataset",
            principal_id="admin",
            action="delete_custom_dataset",
            resource_id=document_id,
        )
        return {"status": "deleted", "document_id": document_id}

    # POST /api/v1/documents/parse-file: Extract text and metadata from any uploaded file
    @router.post("/documents/parse-file")
    async def parse_document_file(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        """Extract text content and slide/page metadata from any uploaded file."""
        file_bytes = await file.read()
        filename = file.filename or "uploaded_file"
        extracted_text, file_type, count = extract_text_from_file_bytes(filename, file_bytes)

        raw_name = filename.rsplit(".", 1)[0]
        title = raw_name.replace("_", " ").replace("-", " ").title()

        return {
            "filename": filename,
            "title": title,
            "content": extracted_text,
            "file_type": file_type,
            "char_count": len(extracted_text),
            "page_count": count,
        }

    # POST /api/v1/documents/upload: Upload, parse, and index any file type
    @router.post("/documents/upload")
    async def upload_document_file(
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form()] = None,
        tenant_id: Annotated[str, Form()] = "corp-default",
        allowed_users: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        """Upload, parse, and index any file type into the local vector index."""
        pipeline = state.retrieval_pipeline
        if pipeline is None:
            from aegismind_core.bootstrap import init_default_core_state

            seeded = await init_default_core_state()
            state.retrieval_pipeline = seeded.retrieval_pipeline
            state.vector_store = seeded.vector_store
            state.connectors = seeded.connectors
            state.indexed_resources = seeded.indexed_resources
            pipeline = seeded.retrieval_pipeline

        if pipeline is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Retrieval pipeline is not configured",
            )

        file_bytes = await file.read()
        filename = file.filename or "uploaded_file"
        extracted_text, file_type, count = extract_text_from_file_bytes(filename, file_bytes)

        doc_title = title or filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        uri = f"upload://{filename}"

        allowed_list = [u.strip() for u in (allowed_users or "alice,bob").split(",") if u.strip()]
        if not allowed_list:
            allowed_list = ["alice", "bob"]

        paragraphs = [p.strip() for p in extracted_text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [extracted_text.strip() or "Empty document"]

        chunks: list[Chunk] = []
        for idx, text_block in enumerate(paragraphs, start=1):
            embedding = await pipeline.embedder.embed_query(text_block)
            chunk = Chunk(
                id=f"chunk-{doc_id}-{idx:02d}",
                document_id=doc_id,
                index=idx,
                content=text_block,
                embedding=embedding,
                metadata={
                    "title": doc_title,
                    "uri": uri,
                    "tenant_id": tenant_id,
                    "filename": filename,
                    "file_type": file_type,
                    "page_count": count,
                },
                acl=ACL(
                    is_public="anonymous" in allowed_list or "*" in allowed_list,
                    allowed_principals=[f"user:{u}" for u in allowed_list],
                ),
            )
            chunks.append(chunk)

        await state.vector_store.upsert(chunks)

        resource_entry = {
            "id": doc_id,
            "title": doc_title,
            "uri": uri,
            "tenant_id": tenant_id,
            "chunks_count": len(chunks),
            "chunk_count": len(chunks),
            "type": file_type,
            "connector": "file_upload",
            "content": extracted_text,
            "allowed_users": allowed_list,
            "file_type": file_type,
            "page_count": count,
            "char_count": len(extracted_text),
            "created_at": datetime.now(UTC).isoformat(),
        }
        state.indexed_resources.insert(0, resource_entry)

        state.record_audit(
            event_type="dataset",
            principal_id="admin",
            action="upload_custom_file",
            resource_id=doc_id,
            metadata={"title": doc_title, "chunks_count": len(chunks), "file_type": file_type},
        )

        return {
            "status": "indexed",
            "document_id": doc_id,
            "title": doc_title,
            "chunks_count": len(chunks),
            "chunk_count": len(chunks),
            "content": extracted_text,
            "char_count": len(extracted_text),
            "file_type": file_type,
            "page_count": count,
            "allowed_users": allowed_list,
        }

    # POST /api/v1/study/ask: Interactive Q&A, quiz, or summary on uploaded document/slides
    @router.post("/study/ask")
    async def study_ask(req: StudyRequest) -> dict[str, Any]:
        """Study, query, quiz, or summarize an uploaded document or slides."""
        llm_adapter = state.llm or get_llm_adapter()

        if req.mode == "quiz":
            system_prompt = (
                "You are AegisMind AI Study Partner. Based strictly on the provided material, "
                "create an interactive study quiz with 3-4 questions to test comprehension. "
                "For each question, provide 4 options (A, B, C, D) followed by the correct "
                "answer with an explanation referencing specific slides or sections."
            )
            prompt = (
                f"DOCUMENT TITLE: {req.title}\n\n"
                f"DOCUMENT CONTENT / SLIDES:\n{req.content[:16000]}\n\n"
                f"USER INSTRUCTION: {req.query or 'Generate an interactive quiz on key concepts.'}"
            )
        elif req.mode == "summary":
            system_prompt = (
                "You are AegisMind AI Study Partner. Provide a structured, high-yield summary "
                "of the provided document or slides. Break down the core concepts and takeaways."
            )
            prompt = (
                f"DOCUMENT TITLE: {req.title}\n\n"
                f"DOCUMENT CONTENT / SLIDES:\n{req.content[:16000]}\n\n"
                f"USER INSTRUCTION: {req.query or 'Summarize the key takeaways and main points.'}"
            )
        elif req.mode == "explain":
            system_prompt = (
                "You are AegisMind AI Study Partner. Explain the concepts in the provided "
                "document or slides in an educational manner with clear examples."
            )
            prompt = (
                f"DOCUMENT TITLE: {req.title}\n\n"
                f"DOCUMENT CONTENT / SLIDES:\n{req.content[:16000]}\n\n"
                f"USER QUESTION: {req.query}"
            )
        else:
            system_prompt = (
                "You are AegisMind AI Study Partner. Answer the user's question accurately "
                "based on the provided document or slides. Cite relevant slide or page numbers."
            )
            prompt = (
                f"DOCUMENT TITLE: {req.title}\n\n"
                f"DOCUMENT CONTENT / SLIDES:\n{req.content[:16000]}\n\n"
                f"USER QUESTION: {req.query}"
            )

        try:
            answer = await llm_adapter.generate(
                prompt=prompt,
                system_prompt=system_prompt,
            )
            if not answer or not answer.strip():
                raise ValueError("Empty completion from LLM")
        except Exception as exc:
            logger.info("Study LLM generation using fallback: %s", exc)
            if req.mode == "quiz":
                answer = (
                    f"### Interactive Study Quiz for: {req.title}\n\n"
                    "**Question 1:** What is the primary theme established in this material?\n"
                    "- A) Foundational concepts and architecture principles\n"
                    "- B) Unrelated external benchmarks\n"
                    "- C) Deprecated legacy conventions\n"
                    "- D) General configuration\n\n"
                    "*Correct Answer:* **A**: Establishes foundational concepts and principles.\n\n"
                    "**Question 2:** How are the key topics structured across the content?\n"
                    "- A) Organized into systematic sections for incremental learning\n"
                    "- B) Unstructured and without context\n"
                    "- C) Purely speculative without verification\n"
                    "- D) Minimalist reference only\n\n"
                    "*Correct Answer:* **A**: Systematic sections support structured learning.\n\n"
                    "*(Tip: Ask follow-up questions to drill into any specific slide or page!)*"
                )
            elif req.mode == "summary":
                lines = [
                    t.strip()
                    for t in req.content.split("\n")
                    if t.strip() and not t.startswith("---")
                ]
                bullet_preview = "\n".join(f"- {item}" for item in lines[:8])
                answer = (
                    f"### Key Takeaways: {req.title}\n\n"
                    f"**Overview:** This study material covers {len(lines)} content blocks.\n\n"
                    f"**Highlights:**\n{bullet_preview}\n\n"
                    "**Summary:** Structured reference material for study and interactive review."
                )
            else:
                answer = (
                    f"Based on the study document **{req.title}**, regarding '{req.query}':\n\n"
                    "The document discusses core concepts relevant to your inquiry across "
                    "sections. Ask follow-up questions or generate a quiz to test your mastery."
                )

        # Record into long-term conversational memory
        try:
            await state.memory.record_conversation(
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                query=f"[Study: {req.title}] {req.query}",
                response=answer.strip(),
            )
        except Exception as exc:
            logger.debug("Study memory storage failed: %s", exc)

        state.record_audit(
            event_type="study",
            principal_id=req.user_id,
            action="document_study_qa",
            resource_id=req.title,
            metadata={"mode": req.mode, "query": req.query},
        )

        return {
            "title": req.title,
            "answer": answer.strip(),
            "mode": req.mode,
            "memory_saved": True,
        }

    # POST /api/v1/datasets/{document_id}/chat and POST /api/v1/datasets/chat
    @router.post("/datasets/{document_id}/chat")
    @router.post("/datasets/chat")
    async def chat_with_dataset(
        req: DatasetChatRequest,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Query Ollama or LLM grounded specifically on an individual dataset or document."""
        effective_doc_id = document_id or req.document_id or ""
        effective_title = req.title or "Dataset"
        effective_content = req.content or ""

        # Attempt to locate content if not directly provided
        if not effective_content and effective_doc_id:
            for r in state.indexed_resources:
                if r.get("id") == effective_doc_id:
                    if not req.title:
                        effective_title = r.get("title", effective_title)
                    effective_content = r.get("content", "")
                    break

            if not effective_content and hasattr(state.vector_store, "_chunks"):
                doc_chunks = [
                    c.content
                    for c in state.vector_store._chunks.values()
                    if getattr(c, "document_id", None) == effective_doc_id
                ]
                if doc_chunks:
                    effective_content = "\n\n".join(doc_chunks)

        if not effective_content:
            effective_content = f"Dataset: {effective_title}"

        # Retrieve long-term memory context if available
        memory_context = ""
        try:
            pipeline = state.retrieval_pipeline
            embedder = getattr(pipeline, "embedder", None)
            memory_context = await state.memory.retrieve_context(
                query=req.query,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                embedder=embedder,
                top_k=3,
            )
        except Exception as exc:
            logger.debug("Dataset chat memory retrieval skipped: %s", exc)

        system_prompt = (
            "You are AegisMind Dataset Assistant. You answer user questions strictly, "
            "accurately, and professionally with respect to the user's specific dataset "
            "and documents.\n"
            "Rules:\n"
            "1. Ground your answers directly on the records, facts, and structure provided "
            "in the dataset context.\n"
            "2. If the user asks for numbers, summaries, comparisons, or specific records, "
            "explain them clearly.\n"
            "3. If information is not present in the dataset, clearly state that it is not "
            "found in the dataset before providing any helpful general guidance.\n"
            "4. Maintain a direct, concise, and helpful tone."
        )

        content_sample = effective_content[:12000]
        prompt = (
            f"DATASET TITLE: {effective_title}\nDATASET RECORDS / CONTENT:\n{content_sample}\n\n"
        )
        if memory_context:
            prompt += f"LONG-TERM USER CONTEXT:\n{memory_context}\n\n"
        prompt += f"USER QUESTION WRITTEN FOR THIS DATASET:\n{req.query}"

        llm_adapter = state.llm or get_llm_adapter()
        answer = ""
        try:
            answer = await llm_adapter.generate(
                prompt=prompt,
                system_prompt=system_prompt,
            )
            if not answer or not answer.strip():
                raise ValueError("Empty completion from LLM")
        except Exception as exc:
            logger.info("Dataset chat LLM fallback triggered: %s", exc)
            answer = (
                f"### Dataset Insights for: {effective_title}\n\n"
                f"Regarding your query '{req.query}':\n\n"
                f"Based on the indexed records in {effective_title}, the records contain "
                f"{len(effective_content.splitlines())} lines of data. "
                "The records directly address your operational inquiry. "
                "(Local Ollama response fallback applied; verify Ollama is active on http://127.0.0.1:11434)."
            )

        # Record conversation into long-term memory
        try:
            await state.memory.record_conversation(
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                query=f"[Dataset: {effective_title}] {req.query}",
                response=answer.strip(),
            )
        except Exception as exc:
            logger.debug("Dataset memory storage failed: %s", exc)

        state.record_audit(
            event_type="dataset",
            principal_id=req.user_id,
            action="dataset_chat_qa",
            resource_id=effective_doc_id or effective_title,
            metadata={"title": effective_title, "query": req.query},
        )

        return {
            "document_id": effective_doc_id,
            "title": effective_title,
            "answer": answer.strip(),
            "memory_saved": True,
        }

    # 11. POST & GET /api/v1/feedback: User thumbs up/down and answer evaluation hook
    @router.post("/feedback", response_model=FeedbackEntry)
    async def submit_feedback(req: FeedbackCreateRequest) -> FeedbackEntry:
        """Store thumbs up/down rating with query, cited chunks, and rewritten query."""
        entry = FeedbackEntry(
            id=f"fb_{uuid.uuid4().hex[:12]}",
            query=req.query,
            rewritten_query=req.rewritten_query,
            retrieved_chunk_ids=req.retrieved_chunk_ids,
            rating=req.rating,
            comment=req.comment,
            tenant_id=req.tenant_id,
        )
        state.feedback_entries.insert(0, entry)
        state.record_audit(
            event_type="feedback",
            principal_id="user",
            action="feedback_submission",
            metadata={
                "feedback_id": entry.id,
                "rating": entry.rating,
                "query": entry.query,
                "chunk_count": len(entry.retrieved_chunk_ids),
            },
        )
        return entry

    @router.get("/feedback")
    async def list_feedback(
        rating: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Query user feedback entries."""
        items = state.feedback_entries
        if rating:
            items = [item for item in items if item.rating == rating]

        total = len(items)
        paged = items[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "entries": [e.model_dump() for e in paged],
        }

    # 13. GET /api/v1/metrics: Prometheus metrics endpoint
    @router.get("/metrics", include_in_schema=False)
    def api_metrics() -> Response:
        """Prometheus metrics exposition endpoint."""
        from aegismind_core.observability import render_prometheus_metrics

        return Response(
            content=render_prometheus_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # 14. GET /api/v1/health: Liveness probe
    @router.get("/health", tags=["health"])
    async def liveness_probe() -> dict[str, str]:
        """Liveness probe: verifies process is alive and accepting traffic."""
        return {"status": "ok", "service": "aegismind-core"}

    # 15. GET /api/v1/readiness: Deep readiness probe
    @router.get("/readiness", tags=["health"])
    async def readiness_probe(response: Response) -> dict[str, Any]:
        """Deep readiness probe: checks vector store, embedder, and LLM."""
        is_ready, checks = await perform_readiness_check(state)
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unhealthy", "checks": checks}
        return {"status": "ready", "checks": checks}

    # 16. Dead-Letter Queue (DLQ) endpoints
    @router.get("/dlq", tags=["dlq"])
    async def list_dlq(
        connector_id: str | None = Query(None, description="Filter by connector ID"),
        status: str | None = Query(
            None, description="Filter by status (pending, retried, resolved, abandoned)"
        ),
        limit: int = Query(50, ge=1, le=500, description="Max items to retrieve"),
    ) -> dict[str, Any]:
        """Query dead-letter queue items."""
        items = await state.dlq.list_items(connector_id=connector_id, status=status, limit=limit)
        return {
            "total": len(items),
            "limit": limit,
            "items": [item.model_dump() for item in items],
        }

    @router.post("/dlq/{item_id}/retry", tags=["dlq"])
    async def retry_dlq_item(item_id: str) -> dict[str, Any]:
        """Trigger reprocessing attempt for a dead-letter queue item."""
        existing = await state.dlq.get_item(item_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"DLQ item '{item_id}' not found")
        updated = await state.dlq.update_status(item_id, status="retried")
        state.record_audit(
            event_type="dlq",
            principal_id="system",
            action="dlq_retry",
            resource_id=item_id,
            metadata={
                "connector_id": existing.connector_id,
                "retry_count": updated.retry_count if updated else 0,
            },
        )
        return {
            "status": "retried",
            "item": updated.model_dump() if updated else None,
        }

    @router.delete("/dlq/{item_id}", tags=["dlq"])
    async def delete_dlq_item(item_id: str) -> dict[str, Any]:
        """Purge or acknowledge a dead-letter queue item."""
        deleted = await state.dlq.delete_item(item_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"DLQ item '{item_id}' not found")
        return {"status": "deleted", "id": item_id}

    # Helper for frontmatter parsing
    def _parse_note_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith("---"):
            return {}, content
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content
        frontmatter_raw = parts[1]
        body = parts[2].strip()
        meta: dict[str, Any] = {}
        for line in frontmatter_raw.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if v.startswith("[") and v.endswith("]"):
                    inner = v[1:-1].strip()
                    meta[k] = [t.strip().strip("\"'") for t in inner.split(",") if t.strip()]
                else:
                    meta[k] = v
        return meta, body

    # 13. POST /api/v1/agent/chat: Local Sovereign Agent chat with sandboxed tool calling
    @router.post("/agent/chat", response_model=AgentRunResult, tags=["agent"])
    async def agent_chat(req: AgentChatApiRequest) -> AgentRunResult:
        """Run the sovereign agent ReAct loop with local sandboxed tools."""
        from pathlib import Path

        from aegismind_retrieval.adapters_local_embed import LocalDeterministicEmbedderAdapter

        vs = state.vector_store
        embedder = (
            getattr(state.retrieval_pipeline, "embedder", None)
            if state.retrieval_pipeline
            else None
        )
        if embedder is None:
            embedder = LocalDeterministicEmbedderAdapter(dimension=64)

        search_tool = LocalKnowledgeSearchAdapter(vector_store=vs, embedder=embedder)
        file_tool = SystemFileReaderAdapter(allowed_roots=req.allowed_roots)
        note_tool = NoteCreatorAdapter(notes_dir=req.notes_dir)
        command_tool = SandboxedCommandRunnerAdapter(
            audit_recorder=state.record_audit,
            working_dir=Path.cwd(),
        )

        agent_loop = SovereignAgentLoop(
            search_tool=search_tool,
            file_reader_tool=file_tool,
            note_tool=note_tool,
            command_tool=command_tool,
            model=req.model,
            audit_recorder=state.record_audit,
            activity_recorder=state.activity_recorder,
        )

        result = await agent_loop.run(
            prompt=req.prompt,
            system_instruction=req.system_instruction,
        )

        state.record_audit(
            event_type="agent",
            principal_id="local_agent",
            action="agent_chat_complete",
            metadata={
                "prompt": req.prompt,
                "total_tool_calls": result.total_tool_calls,
                "actions": [a.tool_name for a in result.actions_taken],
            },
        )
        return result

    # 14. GET /api/v1/notes: List saved notes with frontmatter metadata
    @router.get("/notes", response_model=list[NoteSummary], tags=["notes"])
    async def list_notes(
        notes_dir: str = Query("./storage/notes", description="Notes directory"),
        tag: str | None = Query(None, description="Filter by tag"),
    ) -> list[NoteSummary]:
        """List notes stored by the local sovereign agent."""
        from pathlib import Path

        dir_path = Path(notes_dir).resolve()  # noqa: ASYNC240
        if not dir_path.exists():  # noqa: ASYNC240
            return []

        summaries: list[NoteSummary] = []
        for file_path in dir_path.glob("*.md"):  # noqa: ASYNC240
            if not file_path.is_file():  # noqa: ASYNC240
                continue
            try:
                raw_text = file_path.read_text(encoding="utf-8", errors="replace")  # noqa: ASYNC240
                meta, body = _parse_note_frontmatter(raw_text)
                title = meta.get("title") or file_path.stem.replace("_", " ").title()
                raw_tags = meta.get("tags") or []
                tags = raw_tags if isinstance(raw_tags, list) else [str(raw_tags)]
                created_at = (
                    meta.get("created_at")
                    or datetime.fromtimestamp(file_path.stat().st_ctime, tz=UTC).isoformat()  # noqa: ASYNC240
                )
                source_query = meta.get("source_query")

                if tag and tag.lower() not in [t.lower() for t in tags]:
                    continue

                preview = body[:200] + ("..." if len(body) > 200 else "")
                summaries.append(
                    NoteSummary(
                        slug=file_path.name,
                        title=title,
                        tags=tags,
                        created_at=str(created_at),
                        source_query=source_query,
                        preview=preview,
                        path=str(file_path),
                    )
                )
            except Exception as exc:
                logger.warning("Error reading note %s: %s", file_path, exc)

        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries

    # 15. GET /api/v1/notes/{slug}: Get full note content
    @router.get("/notes/{slug}", tags=["notes"])
    async def get_note_detail(
        slug: str,
        notes_dir: str = Query("./storage/notes"),
    ) -> dict[str, Any]:
        """Retrieve full content of a specific note."""
        from pathlib import Path

        file_path = (Path(notes_dir) / slug).resolve()  # noqa: ASYNC240
        notes_root = Path(notes_dir).resolve()  # noqa: ASYNC240
        if not (file_path == notes_root or notes_root in file_path.parents):
            raise HTTPException(status_code=403, detail="Invalid note path traversal")
        if not file_path.exists() or not file_path.is_file():  # noqa: ASYNC240
            raise HTTPException(status_code=404, detail=f"Note '{slug}' not found")

        raw_text = file_path.read_text(encoding="utf-8", errors="replace")  # noqa: ASYNC240
        meta, body = _parse_note_frontmatter(raw_text)
        return {
            "slug": slug,
            "title": meta.get("title") or file_path.stem,
            "tags": meta.get("tags", []),
            "created_at": meta.get("created_at"),
            "source_query": meta.get("source_query"),
            "content": body,
            "raw": raw_text,
        }

    # 15b. POST /api/v1/notes: Save a study note or Q&A into the vault
    @router.post("/notes", tags=["notes"])
    async def create_vault_note(req: CreateNoteRequest) -> dict[str, Any]:
        """Save a note into the local markdown vault."""
        start_t = asyncio.get_event_loop().time()
        adapter = NoteCreatorAdapter(notes_dir=req.notes_dir)
        msg = await adapter.create_note(
            title=req.title,
            content=req.content,
            tags=req.tags,
            source_query=req.source_query,
        )
        duration_ms = (asyncio.get_event_loop().time() - start_t) * 1000.0
        success = not msg.startswith("NOTE_CREATION_FAILED")
        state.activity_recorder.record_event(
            tool_name="create_note",
            parameters={"title": req.title, "tags": req.tags, "notes_dir": req.notes_dir},
            status="success" if success else "failed",
            duration_ms=duration_ms,
            category="notes",
            approval_required=False,
            result_summary=msg,
            error=msg if not success else None,
        )
        return {"status": "created", "message": msg, "title": req.title}

    # 16. GET /api/v1/agent/tools: Live/recent feed of agent tool calls for UI transparency
    @router.get("/agent/tools", tags=["agent"])
    async def list_agent_tools(
        limit: int = Query(50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        """Return chronological feed of recent local sovereign agent tool actions."""
        events = state.activity_recorder.get_events(limit=limit)
        if events:
            results = []
            for ev in events:
                results.append(
                    {
                        "id": ev.event_id,
                        "event_id": ev.event_id,
                        "timestamp": ev.timestamp,
                        "event_type": "agent_tool",
                        "principal_id": ev.agent_id,
                        "action": f"invoke_{ev.tool_name}",
                        "tool_name": ev.tool_name,
                        "category": ev.category,
                        "status": ev.status,
                        "duration_ms": ev.duration_ms,
                        "source": ev.source,
                        "approval_required": ev.approval_required,
                        "error": ev.error,
                        "result_summary": ev.result_summary,
                        "metadata": {
                            "arguments": ev.parameters,
                            "success": ev.status == "success",
                            "result_summary": ev.result_summary,
                            "duration_ms": ev.duration_ms,
                            "status": ev.status,
                            "category": ev.category,
                            "error": ev.error,
                            **ev.metadata,
                        },
                    }
                )
            return results
        agent_entries = [
            e.model_dump() for e in reversed(state.audit_log) if e.event_type == "agent_tool"
        ]
        return agent_entries[:limit]

    # 16b. GET /api/v1/local-tools/activity: Historical local tool activity
    @router.get("/local-tools/activity", tags=["agent"])
    async def get_local_tools_activity(
        category: str | None = Query(None, description="Filter by category"),
        status: str | None = Query(None, description="Filter by status (running, success, failed)"),
        limit: int = Query(50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        """Return historical local agent tool activity ledger."""
        events = state.activity_recorder.get_events(category=category, status=status, limit=limit)
        return [e.model_dump() for e in events]

    # 16c. GET /api/v1/local-tools/events: SSE real-time updates
    @router.get("/local-tools/events", tags=["agent"])
    async def get_local_tools_events(request: Request) -> StreamingResponse:
        """Stream real-time local agent tool activity events via Server-Sent Events."""

        async def event_generator() -> AsyncIterator[str]:
            queue = state.activity_recorder.subscribe()
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        data_str = json.dumps(event.model_dump())
                        yield f"data: {data_str}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                state.activity_recorder.unsubscribe(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 16d. DELETE /api/v1/local-tools/activity: Clear activity history
    @router.delete("/local-tools/activity", tags=["agent"])
    async def clear_local_tools_activity() -> dict[str, Any]:
        """Clear local agent tool activity ledger."""
        state.activity_recorder.clear()
        return {"status": "cleared", "count": 0}

    # 17. GET /api/v1/graph/nodes: Query knowledge graph
    @router.get("/graph/nodes", tags=["graph"])
    async def graph_nodes(
        query: str | None = Query(None),
        entity_type: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        result = state.graph_engine.query(query=query, entity_type=entity_type, limit=limit)
        return {
            "nodes": [n.model_dump() for n in result.nodes],
            "edges": [e.model_dump() for e in result.edges],
            "total_count": result.total_count,
        }

    @router.get("/graph/edges", tags=["graph"])
    async def graph_edges(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
        edges = state.graph_engine._edges[-limit:] if limit > 0 else state.graph_engine._edges
        return {"nodes": [], "edges": [e.model_dump() for e in edges], "total_count": len(edges)}

    @router.get("/graph/stats", tags=["graph"])
    async def graph_stats() -> dict[str, Any]:
        return cast(dict[str, Any], state.graph_engine.get_stats())

    @router.get("/graph/query", tags=["graph"])
    async def graph_query(
        query: str | None = Query(None),
        entity_type: str | None = Query(None),
        node_id: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        result = state.graph_engine.query(
            query=query, entity_type=entity_type, node_id=node_id, limit=limit
        )
        return {
            "nodes": [n.model_dump() for n in result.nodes],
            "edges": [e.model_dump() for e in result.edges],
            "total_count": result.total_count,
        }

    # 18. POST /api/v1/approval/propose
    @router.post("/approval/propose", tags=["approval"])
    async def propose_action(
        tool_name: str,
        arguments: dict[str, Any],
        reasoning: str,
        risk_level: str = "read",
    ) -> dict[str, str]:
        proposal = state.approval_gate.propose(
            tool_name=tool_name, arguments=arguments, reasoning=reasoning, risk_level=risk_level
        )
        return {"id": proposal.id, "status": proposal.status.value}

    # 19. GET /api/v1/approval/pending
    @router.get("/approval/pending", tags=["approval"])
    async def pending_approvals() -> dict[str, Any]:
        pending = state.approval_gate.get_pending()
        return {
            "pending_actions": [
                {
                    "proposal": p.proposal.model_dump(),
                    "wait_seconds": p.wait_seconds,
                    "summary": p.summary,
                }
                for p in pending
            ],
            "total": len(pending),
        }

    # 20. POST /api/v1/approval/decide
    @router.post("/approval/decide", tags=["approval"])
    async def decide_approval(
        proposal_id: str,
        approved: bool,
        reason: str = "",
        reviewed_by: str = "user",
    ) -> dict[str, str]:
        from aegismind_approval.models import ApprovalDecision

        decision = ApprovalDecision(
            proposal_id=proposal_id, approved=approved, reason=reason, reviewed_by=reviewed_by
        )
        result = state.approval_gate.decide(decision)
        return {"status": "approved" if result else "rejected", "proposal_id": proposal_id}

    # 21. GET /api/v1/approval/stats
    @router.get("/approval/stats", tags=["approval"])
    async def approval_stats() -> dict[str, Any]:
        return cast(dict[str, Any], state.approval_gate.get_stats())

    return router


async def perform_readiness_check(state: CoreState) -> tuple[bool, dict[str, str]]:
    """Execute deep readiness checks across all backing services."""
    checks: dict[str, str] = {}
    all_ok = True

    # 1. Vector store ping / healthcheck
    try:
        vs = state.vector_store
        if hasattr(vs, "db_pool") and vs.db_pool is not None:
            async with vs.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["vector_store"] = "ok"
        elif hasattr(vs, "ping"):
            await vs.ping()
            checks["vector_store"] = "ok"
        else:
            await vs.query_dense([0.0], top_k=1)
            checks["vector_store"] = "ok (in-memory)"
    except Exception as exc:
        logger.warning("Readiness probe: vector store check failed: %s", exc)
        checks["vector_store"] = f"error: {exc}"
        all_ok = False

    # 3. TEI embedder ping
    try:
        pipe = state.retrieval_pipeline
        if pipe and pipe.embedder:
            if hasattr(pipe.embedder, "ping"):
                await pipe.embedder.ping()
                checks["embedder"] = "ok"
            else:
                await pipe.embedder.embed_query("ping")
                checks["embedder"] = "ok"
        else:
            checks["embedder"] = "disabled"
    except Exception as exc:
        logger.warning("Readiness probe: embedder check failed: %s", exc)
        checks["embedder"] = f"error: {exc}"
        all_ok = False

    # 4. TEI reranker ping
    try:
        pipe = state.retrieval_pipeline
        if pipe and pipe.reranker:
            if hasattr(pipe.reranker, "ping"):
                await pipe.reranker.ping()
                checks["reranker"] = "ok"
            else:
                await pipe.reranker.rerank(query="ping", candidates=[], top_n=0)
                checks["reranker"] = "ok"
        else:
            checks["reranker"] = "disabled"
    except Exception as exc:
        logger.warning("Readiness probe: reranker check failed: %s", exc)
        checks["reranker"] = f"error: {exc}"
        all_ok = False

    # 5. LLM provider check
    try:
        if state.llm is not None:
            if hasattr(state.llm, "health_check"):
                ok = await state.llm.health_check()
                checks["llm"] = "ok" if ok else "degraded"
            else:
                checks["llm"] = "ok"
        else:
            checks["llm"] = "disabled"
    except Exception as exc:
        logger.warning("Readiness probe: LLM check failed: %s", exc)
        checks["llm"] = f"error: {exc}"

    # 6. Air-gapped local operation mode
    air_gapped = os.environ.get("AIR_GAPPED", "false").lower() in {"1", "true", "yes"}
    checks["air_gapped_rag"] = "active" if air_gapped else "supported"

    return all_ok, checks


def __getattr__(name: str) -> Any:
    if name == "app":
        from aegismind_core.app import app as _app

        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
