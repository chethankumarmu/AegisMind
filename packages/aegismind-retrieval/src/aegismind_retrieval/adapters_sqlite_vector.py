from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aegismind_types import ACL, Chunk

from aegismind_retrieval.ports import ScoredChunk, VectorStorePort
from aegismind_retrieval.rrf import fuse_dense_sparse

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SqliteVectorStoreAdapter(VectorStorePort):
    """Embedded persistent SQLite vector store.

    Stores chunks, embeddings, metadata, content hashes, and soft-delete tombstones.
    Provides offline sovereign vector search with zero external database dependencies.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._mem_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._mem_conn is not None:
            yield self._mem_conn
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    chunk_index INTEGER DEFAULT 0,
                    content TEXT NOT NULL,
                    contextual_prefix TEXT,
                    embedding_json TEXT,
                    sparse_json TEXT,
                    acl_json TEXT,
                    metadata_json TEXT,
                    content_hash TEXT,
                    is_deleted INTEGER DEFAULT 0,
                    deleted_at TEXT,
                    version INTEGER DEFAULT 1
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_deleted ON chunks(is_deleted)")
            conn.commit()

    def _row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        deleted_at = None
        if row["deleted_at"]:
            try:
                deleted_at = datetime.fromisoformat(row["deleted_at"])
            except ValueError:
                pass

        embedding = json.loads(row["embedding_json"]) if row["embedding_json"] else None
        sparse_embedding = (
            {int(k): float(v) for k, v in json.loads(row["sparse_json"]).items()}
            if row["sparse_json"]
            else None
        )
        acl_data = json.loads(row["acl_json"]) if row["acl_json"] else {}
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}

        return Chunk(
            id=row["id"],
            document_id=row["document_id"],
            index=row["chunk_index"],
            content=row["content"],
            contextual_prefix=row["contextual_prefix"],
            embedding=embedding,
            sparse_embedding=sparse_embedding,
            acl=ACL(**acl_data),
            metadata=metadata,
            content_hash=row["content_hash"],
            is_deleted=bool(row["is_deleted"]),
            deleted_at=deleted_at,
            version=row["version"],
        )

    async def upsert(self, chunks: list[Chunk]) -> None:
        """Insert or update chunks into SQLite vector store."""
        if not chunks:
            return
        with self._connection() as conn:
            for c in chunks:
                emb_json = json.dumps(c.embedding) if c.embedding else None
                sparse_json = json.dumps(c.sparse_embedding) if c.sparse_embedding else None
                acl_json = json.dumps(c.acl.model_dump())
                meta_json = json.dumps(c.metadata)
                del_at_str = c.deleted_at.isoformat() if c.deleted_at else None

                conn.execute(
                    """
                    INSERT INTO chunks (
                        id, document_id, chunk_index, content, contextual_prefix,
                        embedding_json, sparse_json, acl_json, metadata_json,
                        content_hash, is_deleted, deleted_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content = excluded.content,
                        contextual_prefix = excluded.contextual_prefix,
                        embedding_json = excluded.embedding_json,
                        sparse_json = excluded.sparse_json,
                        acl_json = excluded.acl_json,
                        metadata_json = excluded.metadata_json,
                        content_hash = excluded.content_hash,
                        is_deleted = excluded.is_deleted,
                        deleted_at = excluded.deleted_at,
                        version = excluded.version
                    """,
                    (
                        c.id,
                        c.document_id,
                        c.index,
                        c.content,
                        c.contextual_prefix,
                        emb_json,
                        sparse_json,
                        acl_json,
                        meta_json,
                        c.content_hash,
                        1 if c.is_deleted else 0,
                        del_at_str,
                        c.version,
                    ),
                )
            conn.commit()
        logger.debug("Upserted %d chunks into SQLite vector store", len(chunks))

    def _matches_filter(self, chunk: Chunk, pre_filter: dict[str, Any] | None) -> bool:
        if chunk.is_deleted:
            return False
        if not pre_filter:
            return True

        expected_tenant = pre_filter.get("tenant_id") or pre_filter.get("tenant")
        if expected_tenant is not None:
            chunk_tenant = chunk.metadata.get("tenant_id") or chunk.metadata.get("tenant")
            if chunk_tenant is not None and str(chunk_tenant) != str(expected_tenant):
                return False

        allowed_groups = pre_filter.get("allowed_groups") or pre_filter.get("groups")
        if allowed_groups is not None:
            group_set = {str(g) for g in allowed_groups}
            chunk_groups = set(chunk.metadata.get("groups", []))
            for p in chunk.acl.allowed_principals:
                if p.startswith("group:"):
                    chunk_groups.add(p[len("group:") :])
                else:
                    chunk_groups.add(p)
            if chunk_groups and not (group_set & chunk_groups):
                return False

        for k, v in pre_filter.items():
            if k in {"tenant_id", "tenant", "allowed_groups", "groups"}:
                continue
            if k in {"source_type", "source_types", "source"}:
                chunk_src = (
                    chunk.metadata.get("source_type")
                    or chunk.metadata.get("source")
                    or chunk.metadata.get("connector")
                )
                if isinstance(v, (list, set, tuple)):
                    if chunk_src not in v:
                        return False
                elif chunk_src != v:
                    return False
                continue
            if isinstance(v, (list, set, tuple)):
                if chunk.metadata.get(k) not in v:
                    return False
            elif chunk.metadata.get(k) != v:
                return False

        return True

    async def query_dense(
        self,
        vector: list[float],
        pre_filter: dict[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[ScoredChunk]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM chunks WHERE is_deleted = 0").fetchall()

        scored: list[ScoredChunk] = []
        for r in rows:
            chunk = self._row_to_chunk(r)
            if not self._matches_filter(chunk, pre_filter):
                continue
            if chunk.embedding:
                score = _cosine_similarity(vector, chunk.embedding)
                scored.append(ScoredChunk(chunk=chunk, score=round(score, 6)))

        scored.sort(key=lambda sc: sc.score, reverse=True)
        return scored[:top_k]

    async def query_lexical(
        self,
        query_text: str,
        sparse_vector: dict[int, float] | None = None,
        pre_filter: dict[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[ScoredChunk]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM chunks WHERE is_deleted = 0").fetchall()

        q_words = set(query_text.lower().split())
        scored: list[ScoredChunk] = []
        for r in rows:
            chunk = self._row_to_chunk(r)
            if not self._matches_filter(chunk, pre_filter):
                continue
            doc_words = set(chunk.content.lower().split())
            overlap = len(q_words & doc_words)
            lex_score = overlap / max(1, len(q_words))
            scored.append(ScoredChunk(chunk=chunk, score=round(lex_score, 4)))

        scored.sort(key=lambda sc: sc.score, reverse=True)
        return scored[:top_k]

    async def query(
        self,
        vector: list[float] | None = None,
        sparse_vector: dict[int, float] | None = None,
        pre_filter: dict[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[ScoredChunk]:
        if vector is not None and sparse_vector is not None:
            dense_results = await self.query_dense(vector, pre_filter=pre_filter, top_k=top_k * 2)
            sparse_results = await self.query_lexical(
                query_text="", sparse_vector=sparse_vector, pre_filter=pre_filter, top_k=top_k * 2
            )
            return fuse_dense_sparse(dense_results, sparse_results)[:top_k]
        if vector is not None:
            return await self.query_dense(vector, pre_filter=pre_filter, top_k=top_k)
        return []

    async def delete(self, chunk_ids: list[str]) -> bool:
        if not chunk_ids:
            return False
        with self._connection() as conn:
            placeholders = ",".join("?" for _ in chunk_ids)
            query = f"DELETE FROM chunks WHERE id IN ({placeholders})"  # noqa: S608
            conn.execute(query, chunk_ids)
            conn.commit()
        return True

    async def get_by_document(self, document_id: str) -> list[Chunk]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE document_id = ? AND is_deleted = 0",
                (document_id,),
            ).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    async def soft_delete_document(self, document_id: str) -> int:
        now_str = datetime.now(UTC).isoformat()
        with self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE chunks
                SET is_deleted = 1, deleted_at = ?
                WHERE document_id = ? AND is_deleted = 0
                """,
                (now_str, document_id),
            )
            conn.commit()
            count = cur.rowcount
        logger.debug("Soft-deleted %d chunks for document %s in SQLite", count, document_id)
        return count

    async def vacuum_tombstones(self, older_than_seconds: int = 86400) -> int:
        now = datetime.now(UTC)
        with self._connection() as conn:
            rows = conn.execute("SELECT id, deleted_at FROM chunks WHERE is_deleted = 1").fetchall()
            to_delete: list[str] = []
            for r in rows:
                if r["deleted_at"]:
                    try:
                        dt = datetime.fromisoformat(r["deleted_at"])
                        if (now - dt).total_seconds() >= older_than_seconds:
                            to_delete.append(r["id"])
                    except ValueError:
                        to_delete.append(r["id"])
                else:
                    to_delete.append(r["id"])

            if to_delete:
                placeholders = ",".join("?" for _ in to_delete)
                query = f"DELETE FROM chunks WHERE id IN ({placeholders})"  # noqa: S608
                conn.execute(query, to_delete)
                conn.commit()
        logger.info("Vacuumed %d tombstoned chunks from SQLite", len(to_delete))
        return len(to_delete)
