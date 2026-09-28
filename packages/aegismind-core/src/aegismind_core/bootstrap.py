from __future__ import annotations

import logging
from typing import Any

from aegismind_retrieval.adapters_model import MockEmbedderAdapter, MockRerankerAdapter
from aegismind_retrieval.adapters_vector import MemoryVectorStoreAdapter
from aegismind_retrieval.pipeline import RetrievalPipeline
from aegismind_types import ACL, Chunk

from aegismind_core.memory import ConversationMemory
from aegismind_core.registry import list_adapters, resolve_adapter
from aegismind_core.routes import CoreState

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

# Sample knowledge documents for initial corpus
INITIAL_DOCUMENTS: list[dict[str, Any]] = [
    {
        "id": "doc-arch-01",
        "title": "AegisMind System Architecture and Sovereign Agent",
        "uri": "https://wiki.corp.internal/architecture/sovereign-agent",
        "tenant_id": "corp-default",
        "content": (
            "AegisMind is a sovereign, local AI agent providing personal knowledge orchestration "
            "and conversational retrieval running completely on the local host with Ollama."
        ),
        "allowed_users": ["alice", "bob", "charlie", "anonymous"],
    },
    {
        "id": "doc-sec-02",
        "title": "SEC-892: Local Sandboxing and Tool Execution Safety",
        "uri": "https://jira.corp.internal/browse/SEC-892",
        "tenant_id": "corp-default",
        "content": (
            "AegisMind executes system tools inside a strict sandbox. Filesystem paths outside "
            "designated roots are blocked, destructive commands are intercepted, and notes "
            "are stored in designated local directories."
        ),
        "allowed_users": ["alice", "bob"],
    },
    {
        "id": "doc-pipe-03",
        "title": "The Sovereign Retrieval Pipeline",
        "uri": "https://wiki.corp.internal/retrieval/sovereign-pipeline",
        "tenant_id": "corp-default",
        "content": (
            "AegisMind coordinates local retrieval: query rewriting, dual dense and sparse "
            "embedding, vector search, reciprocal rank fusion, cross-encoder reranking, "
            "MMR diversity reordering, and verifiable citations."
        ),
        "allowed_users": ["alice", "bob", "charlie", "anonymous"],
    },
    {
        "id": "doc-enc-04",
        "title": "Envelope Encryption and KMS Key Hierarchy",
        "uri": "https://wiki.corp.internal/security/envelope-encryption",
        "tenant_id": "corp-default",
        "content": (
            "Data stored in AegisMind is secured using AES-256-GCM envelope encryption. "
            "Document encryption keys (DEKs) are generated per document and encrypted under "
            "a root Key Encryption Key (KEK) managed in local master key storage."
        ),
        "allowed_users": ["alice", "bob"],
    },
    {
        "id": "doc-conn-05",
        "title": "Local Filesystem Connector and Notes Integration",
        "uri": "https://wiki.corp.internal/connectors/local-filesystem",
        "tenant_id": "corp-default",
        "content": (
            "AegisMind connects to local filesystem directories, indexing local markdown and text "
            "documents into the local vector index."
        ),
        "allowed_users": ["alice", "bob", "charlie", "anonymous"],
    },
    {
        "id": "doc-idp-06",
        "title": "Local Identity and Single User Session",
        "uri": "https://wiki.corp.internal/identity/single-user",
        "tenant_id": "corp-default",
        "content": (
            "AegisMind operates in single-user sovereign mode where the local user has access to "
            "all indexed local knowledge and system tools."
        ),
        "allowed_users": ["alice", "bob", "charlie", "anonymous"],
    },
]


async def seed_initial_knowledge(
    vector_store: MemoryVectorStoreAdapter,
    embedder: MockEmbedderAdapter,
) -> list[dict[str, Any]]:
    """Seed initial knowledge corpus."""
    chunks: list[Chunk] = []
    indexed_resources: list[dict[str, Any]] = []

    for _idx, doc in enumerate(INITIAL_DOCUMENTS, start=1):
        content = doc["content"]
        embedding = await embedder.embed_query(content)
        sparse_embedding = await embedder.embed_sparse_query(content)

        chunk = Chunk(
            id=f"chunk-{doc['id']}-01",
            document_id=doc["id"],
            index=1,
            content=content,
            embedding=embedding,
            sparse_embedding=sparse_embedding,
            metadata={
                "title": doc["title"],
                "uri": doc["uri"],
                "tenant_id": doc["tenant_id"],
            },
            acl=ACL(
                is_public="anonymous" in doc["allowed_users"],
                allowed_principals=[f"user:{u}" for u in doc["allowed_users"]],
            ),
        )
        chunks.append(chunk)

        indexed_resources.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "uri": doc["uri"],
                "tenant_id": doc["tenant_id"],
                "chunks_count": 1,
                "chunk_count": 1,
                "type": "document",
                "connector": "core_seed",
                "content": content,
                "allowed_users": doc["allowed_users"],
            }
        )

    await vector_store.upsert(chunks)
    logger.info("Seeded %d knowledge chunks", len(chunks))
    return indexed_resources


def discover_connectors() -> dict[str, Any]:
    """Discover available connectors via entry points or direct imports."""
    connectors: dict[str, Any] = {}
    try:
        discovered_names = list_adapters("connectors")
        for name in discovered_names:
            try:
                connector_cls = resolve_adapter("connectors", name)
                connectors[name] = connector_cls()
            except Exception as exc:
                logger.debug("Could not instantiate discovered connector '%s': %s", name, exc)
    except Exception as exc:
        logger.debug("Entry point connector discovery skipped: %s", exc)

    standard_connectors = [
        ("local_filesystem", "aegismind_connector_local_filesystem", "LocalFilesystemConnector"),
        ("github", "aegismind_connector_github", "GitHubConnector"),
        ("gmail", "aegismind_connector_gmail", "GmailConnector"),
    ]

    for name, module_name, class_name in standard_connectors:
        if name not in connectors:
            try:
                import importlib

                mod = importlib.import_module(module_name)
                cls = getattr(mod, class_name, None)
                if cls is not None:
                    connectors[name] = cls()
                    logger.info("Discovered connector: %s (%s)", name, module_name)
            except ImportError:
                logger.debug("Connector module '%s' not installed; skipping", module_name)
            except Exception as exc:
                logger.debug("Failed loading fallback connector '%s': %s", name, exc)

    return connectors


async def init_default_core_state() -> CoreState:
    """Initialize a fully operational CoreState with pre-seeded pipeline and knowledge."""
    from aegismind_retrieval.adapters_model import MockQueryRewriterAdapter

    vector_store = MemoryVectorStoreAdapter()
    embedder = MockEmbedderAdapter(dimension=64)
    reranker = MockRerankerAdapter()
    query_rewriter = MockQueryRewriterAdapter()

    from aegismind_core.adapters.llm import get_llm_adapter
    from aegismind_core.observability import PrometheusTelemetryAdapter

    pipeline = RetrievalPipeline(
        vector_store=vector_store,
        embedder=embedder,
        reranker=reranker,
        query_rewriter=query_rewriter,
        telemetry=PrometheusTelemetryAdapter(),
    )

    llm = get_llm_adapter()

    state = CoreState(
        retrieval_pipeline=pipeline,
        vector_store=vector_store,
        llm=llm,
    )

    indexed = await seed_initial_knowledge(vector_store, embedder)
    state.indexed_resources = indexed
    state.connectors = discover_connectors()

    # Register all discovered connectors in the ConnectorRegistry
    for connector_name, connector_instance in state.connectors.items():
        try:
            state.connector_registry.register(connector_name, connector_instance)
        except Exception as exc:
            logger.warning("Failed to register connector '%s' in registry: %s", connector_name, exc)

    # Seed knowledge graph
    try:
        from aegismind_graph.models import KnowledgeEdge, KnowledgeNode

        graph = state.graph_engine
        if graph.get_stats()["total_nodes"] == 0:
            alice = KnowledgeNode(
                id="person:alice",
                entity_type="person",
                name="Alice",
                properties={"role": "Engineering Lead"},
            )
            bob = KnowledgeNode(
                id="person:bob",
                entity_type="person",
                name="Bob",
                properties={"role": "Security Architect"},
            )
            alpha = KnowledgeNode(
                id="project:alpha",
                entity_type="project",
                name="Project Alpha",
                properties={"status": "active"},
            )
            graph.add_node(alice)
            graph.add_node(bob)
            graph.add_node(alpha)
            graph.add_edge(
                KnowledgeEdge(source="person:alice", target="project:alpha", relation="leads")
            )
            graph.add_edge(
                KnowledgeEdge(source="person:bob", target="project:alpha", relation="secures")
            )
            logger.info("Seeded default knowledge graph")
    except Exception as exc:
        logger.warning("Could not seed graph: %s", exc)

    # Initialize conversation memory
    try:
        state.memory = ConversationMemory(db_path="./storage/memory/memory.db")
        logger.info("Conversation memory initialized")
    except Exception as exc:
        logger.warning("Could not initialize memory: %s", exc)

    return state
