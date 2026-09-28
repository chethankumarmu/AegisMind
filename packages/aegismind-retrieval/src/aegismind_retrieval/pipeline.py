from __future__ import annotations

import logging
import math
import time
from typing import Any

from aegismind_types import ChatTurn, Citation, Principal, SearchResult, TokenConsistency
from pydantic import BaseModel, ConfigDict, Field

from aegismind_retrieval.mmr import maximal_marginal_relevance
from aegismind_retrieval.ports import (
    EmbedderPort,
    QueryRewriterPort,
    RerankerPort,
    TelemetryPort,
    VectorStorePort,
)
from aegismind_retrieval.rrf import fuse_dense_sparse
from aegismind_retrieval.telemetry import NoOpTelemetryAdapter

logger = logging.getLogger(__name__)

QUERY_TYPE_OVERFETCH: dict[str, float] = {
    "factual": 3.0,
    "navigational": 3.5,
    "exploratory": 4.5,
}


class PipelineResult(BaseModel):
    """Result returned by the Sovereign Retrieval Pipeline."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="Original query string executed")
    rewritten_query: str | None = Field(
        default=None,
        description="Rewritten query string after conversational coreference resolution",
    )
    query_type: str = Field(default="factual", description="Category of query executed")
    deny_rate: float = Field(
        default=0.0,
        description="Fraction of candidate chunks discarded due to authorization denial",
    )
    results: list[SearchResult] = Field(
        default_factory=list,
        description="Top reranked search results with attached citations",
    )
    total_candidates_evaluated: int = Field(
        ...,
        description="Total overfetched candidate chunks evaluated",
    )
    authorized_candidates_count: int = Field(
        ...,
        description="Count of candidate chunks surviving evaluation",
    )
    overfetch_factor: float = Field(
        ...,
        description="Effective overfetch multiplier applied",
    )


class RetrievalPipeline:
    """The Sovereign Retrieval Pipeline for hybrid local retrieval.

    Executes the streamlined retrieval lifecycle:
    0. Conversational query rewriting against chat history.
    1. Dual dense and sparse lexical embedding.
    2. Dual dense and lexical vector search with local pre-filtering.
    3. Reciprocal Rank Fusion (RRF) combining dense and lexical candidate lists.
    4. Overfetch selection based on query type.
    5. Cross-encoder reranking on candidate chunks.
    6. Maximal Marginal Relevance (MMR) diversity reordering.
    7. Deep citation attachment.
    """

    def __init__(
        self,
        vector_store: VectorStorePort,
        embedder: EmbedderPort,
        reranker: RerankerPort,
        query_rewriter: QueryRewriterPort | None = None,
        telemetry: TelemetryPort | None = None,
    ) -> None:
        self.vector_store = vector_store
        self.embedder = embedder
        self.reranker = reranker
        self.query_rewriter = query_rewriter
        self.telemetry = telemetry or NoOpTelemetryAdapter()

    async def execute(
        self,
        query: str,
        principal: Principal,
        chat_history: list[ChatTurn] | None = None,
        query_type: str = "factual",
        sparse_query: dict[int, float] | None = None,
        pre_filter: dict[str, Any] | None = None,
        top_k: int = 10,
        overfetch_factor: float | None = None,
        consistency: TokenConsistency | None = None,
        apply_mmr: bool = True,
        mmr_lambda: float = 0.7,
    ) -> PipelineResult:
        """Execute the Sovereign Retrieval Pipeline with query rewriting, hybrid RRF, and MMR."""
        t_pipeline_start = time.perf_counter()

        # Stage 0: Conversational query rewriting
        t0 = time.perf_counter()
        effective_query = query
        rewritten_query_log: str | None = None
        if self.query_rewriter is not None and chat_history:
            effective_query = await self.query_rewriter.rewrite_query(query, chat_history)
            rewritten_query_log = effective_query
            logger.info(
                "Stage 0 query rewritten: original='%s', rewritten='%s'",
                query,
                effective_query,
            )
        else:
            logger.info("Stage 0 query rewrite skipped: user='%s', query='%s'", principal.id, query)
        self.telemetry.record_stage_latency("stage_0_query_rewriting", time.perf_counter() - t0)

        # Stage 1: Dual dense and sparse lexical embedding
        t1 = time.perf_counter()
        query_vector = await self.embedder.embed_query(effective_query)
        if sparse_query is not None:
            effective_sparse_query = sparse_query
        else:
            effective_sparse_query = await self.embedder.embed_sparse_query(effective_query)
        self.telemetry.record_stage_latency("stage_1_embedding", time.perf_counter() - t1)

        # Stage 2: Dual dense and lexical search
        t2 = time.perf_counter()
        coarse_filter = dict(pre_filter or {})
        if principal.tenant_id and "tenant_id" not in coarse_filter:
            coarse_filter["tenant_id"] = principal.tenant_id
        if "groups" not in coarse_filter and "groups" in principal.attributes:
            coarse_filter["groups"] = principal.attributes["groups"]

        # Determine overfetch factor based on query type
        if overfetch_factor is not None:
            clamped_overfetch = max(3.0, min(5.0, float(overfetch_factor)))
        else:
            target_mult = QUERY_TYPE_OVERFETCH.get(query_type, 4.0)
            clamped_overfetch = max(3.0, min(5.0, target_mult))

        fetch_pool_size = int(math.ceil(top_k * clamped_overfetch * 1.5))

        if hasattr(self.vector_store, "query_dense") and hasattr(
            self.vector_store, "query_lexical"
        ):
            dense_candidates = await self.vector_store.query_dense(
                vector=query_vector,
                pre_filter=coarse_filter,
                top_k=fetch_pool_size,
            )
            lexical_candidates = await self.vector_store.query_lexical(
                query_text=effective_query,
                sparse_vector=effective_sparse_query,
                pre_filter=coarse_filter,
                top_k=fetch_pool_size,
            )
        else:
            dense_candidates = await self.vector_store.query(
                vector=query_vector,
                pre_filter=coarse_filter,
                top_k=fetch_pool_size,
            )
            lexical_candidates = await self.vector_store.query(
                sparse_vector=effective_sparse_query,
                pre_filter=coarse_filter,
                top_k=fetch_pool_size,
            )
        self.telemetry.record_stage_latency("stage_2_search", time.perf_counter() - t2)

        # Stage 3: Reciprocal Rank Fusion (RRF)
        t3 = time.perf_counter()
        fused_candidates = fuse_dense_sparse(dense_candidates, lexical_candidates)
        self.telemetry.record_stage_latency("stage_3_rrf", time.perf_counter() - t3)

        # Stage 4: Overfetch selection based on query type multiplier
        t4 = time.perf_counter()
        candidates_to_evaluate = int(math.ceil(top_k * clamped_overfetch))
        candidates = fused_candidates[:candidates_to_evaluate]
        total_evaluated = len(candidates)
        self.telemetry.record_stage_latency("stage_4_overfetch", time.perf_counter() - t4)

        if not candidates:
            self.telemetry.record_stage_latency(
                "total_query", time.perf_counter() - t_pipeline_start
            )
            return PipelineResult(
                query=query,
                rewritten_query=rewritten_query_log,
                query_type=query_type,
                deny_rate=0.0,
                results=[],
                total_candidates_evaluated=0,
                authorized_candidates_count=0,
                overfetch_factor=clamped_overfetch,
            )

        # Stage 5: Cross-encoder reranking
        t5 = time.perf_counter()
        reranked = await self.reranker.rerank(
            query=effective_query,
            candidates=candidates,
            top_n=len(candidates),
        )
        rerank_duration = time.perf_counter() - t5
        self.telemetry.record_reranker_latency(rerank_duration)
        self.telemetry.record_stage_latency("stage_5_reranking", rerank_duration)

        # Stage 6: MMR diversity re-ordering
        t6 = time.perf_counter()
        if apply_mmr and len(reranked) > 1:
            diversified = maximal_marginal_relevance(
                candidates=reranked,
                top_n=top_k,
                lambda_mult=mmr_lambda,
            )
        else:
            diversified = reranked[:top_k]
        self.telemetry.record_stage_latency("stage_6_mmr", time.perf_counter() - t6)

        # Stage 7: Attach deep-linked citations
        t7 = time.perf_counter()
        results: list[SearchResult] = []
        for item in diversified:
            chunk = item.chunk
            doc_title = str(chunk.metadata.get("title", f"Document {chunk.document_id}"))
            chunk_uri = chunk.metadata.get("uri") or f"doc://{chunk.document_id}#chunk={chunk.id}"
            chunk_tenant = chunk.metadata.get("tenant_id") or principal.tenant_id

            snippet = chunk.content[:200] + ("..." if len(chunk.content) > 200 else "")
            citation = Citation(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                title=doc_title,
                uri=chunk_uri,
                snippet=snippet,
                score=round(item.score, 4),
                tenant_id=chunk_tenant,
                metadata=chunk.metadata,
            )

            results.append(
                SearchResult(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    title=doc_title,
                    uri=chunk_uri,
                    text=chunk.content,
                    score=round(item.score, 4),
                    tenant_id=chunk_tenant,
                    citation=citation,
                )
            )
        self.telemetry.record_stage_latency("stage_7_citations", time.perf_counter() - t7)
        self.telemetry.record_stage_latency("total_query", time.perf_counter() - t_pipeline_start)

        return PipelineResult(
            query=query,
            rewritten_query=rewritten_query_log,
            query_type=query_type,
            deny_rate=0.0,
            results=results,
            total_candidates_evaluated=total_evaluated,
            authorized_candidates_count=total_evaluated,
            overfetch_factor=clamped_overfetch,
        )
