"""
RAG engine backed by PostgreSQL.
Uses Google Vertex AI text-embedding models for embeddings.

Two storage backends:
  1. pgvector (production) — native vector similarity in PostgreSQL
  2. numpy fallback (local dev) — stores embeddings as JSONB, computes
     cosine similarity in Python. Activated automatically when
     pgvector extension is not installed.
"""
from __future__ import annotations

import logging
from typing import List

import numpy as np
from psycopg2.extras import Json, execute_values

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    # Error will be caught if used, for now just log
    logger.error("google-genai not installed. Run: pip install google-genai")

from config.db import get_conn, get_readonly_conn
from config.settings import CONFIG

logger = logging.getLogger("ops_agent.rag")

_EMBED_BATCH_SIZE = 250  # Vertex AI limit per request


def _has_pgvector(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
        )
        return cur.fetchone() is not None


class VertexEmbedder:
    """Wraps the Vertex AI text-embedding model using google-genai SDK (v3)."""

    def __init__(self, model_name: str = "text-embedding-004"):
        self.project = CONFIG["GOOGLE_CLOUD_PROJECT"]
        self.location = CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.model_name = model_name
        
        self.client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location
        )
        self._dim = None

    @property
    def dimension(self) -> int:
        if self._dim is None:
            sample = self.embed("dimension probe")
            self._dim = len(sample)
        return self._dim

    def embed(self, text: str) -> list[float]:
        try:
            res = self.client.models.embed_content(
                model=self.model_name,
                contents=[text],
                config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
            )
            return res.embeddings[0].values
        except Exception as e:
            logger.error(f"Embedding failed for '{text[:50]}...': {e}")
            raise

    def embed_batch(self, texts: List[str]) -> List[list[float]]:
        all_vectors = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i:i + _EMBED_BATCH_SIZE]
            try:
                res = self.client.models.embed_content(
                    model=self.model_name,
                    contents=batch,
                    config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
                )
                all_vectors.extend([emb.values for emb in res.embeddings])
            except Exception as e:
                logger.error(f"Batch embedding failed: {e}")
                raise
        return all_vectors


class PgVectorRAGEngine:

    def __init__(self):
        embed_model_name = CONFIG.get("EMBEDDING_MODEL", "text-embedding-004")
        self.embedder = VertexEmbedder(embed_model_name)
        self.embed_dim = self.embedder.dimension
        logger.info(
            f"Vertex AI embedder ready: model={embed_model_name}, "
            f"dim={self.embed_dim}"
        )

        with get_conn() as conn:
            self._use_pgvector = _has_pgvector(conn)

        if self._use_pgvector:
            logger.info("pgvector available — using native vector search")
        else:
            logger.info(
                "pgvector not available — using numpy fallback for search"
            )

        self._ensure_tables()

    # ── Schema bootstrap ──

    def _ensure_tables(self):
        with get_conn() as conn:
            with conn.cursor() as cur:
                if self._use_pgvector:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    cur.execute(f"""
                        CREATE TABLE IF NOT EXISTS rag_documents (
                            id          TEXT PRIMARY KEY,
                            content     TEXT NOT NULL,
                            metadata    JSONB DEFAULT '{{}}'::jsonb,
                            collection  TEXT NOT NULL,
                            embedding   vector({self.embed_dim}),
                            tsv         tsvector,
                            created_at  TIMESTAMPTZ DEFAULT NOW()
                        );
                    """)
                    # Ensure column exists if table was created earlier
                    cur.execute("ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS tsv tsvector;")
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_rag_embedding
                        ON rag_documents
                        USING hnsw (embedding vector_cosine_ops)
                        WITH (m = 16, ef_construction = 64);
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_rag_tsv 
                        ON rag_documents USING GIN (tsv);
                    """)
                else:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS rag_documents (
                            id          TEXT PRIMARY KEY,
                            content     TEXT NOT NULL,
                            metadata    JSONB DEFAULT '{}'::jsonb,
                            collection  TEXT NOT NULL,
                            embedding   JSONB DEFAULT '[]'::jsonb,
                            tsv         TEXT,
                            created_at  TIMESTAMPTZ DEFAULT NOW()
                        );
                    """)
                    cur.execute("ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS tsv TEXT;")
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rag_collection
                    ON rag_documents (collection);
                """)
            conn.commit()

    def get_conn_readonly(self):
        """Context-managed read-only database connection."""
        return get_readonly_conn()

    # ── Indexing ──

    def index_documents(self, documents: list[dict], collection: str):
        texts = [doc["content"] for doc in documents]
        embeddings = self.embedder.embed_batch(texts)

        with get_conn() as conn:
            with conn.cursor() as cur:
                if self._use_pgvector:
                    values = [
                        (
                            doc["id"],
                            doc["content"],
                            Json(doc.get("metadata", {})),
                            collection,
                            emb,
                            doc["content"],
                        )
                        for doc, emb in zip(documents, embeddings)
                    ]
                    execute_values(
                        cur,
                        """INSERT INTO rag_documents
                               (id, content, metadata, collection, embedding, tsv)
                           VALUES %s
                           ON CONFLICT (id) DO UPDATE SET
                             content   = EXCLUDED.content,
                             metadata  = EXCLUDED.metadata,
                             embedding = EXCLUDED.embedding,
                             tsv       = EXCLUDED.tsv""",
                        values,
                        template="(%s, %s, %s, %s, %s::vector, to_tsvector('english', %s))",
                    )
                else:
                    for doc, emb in zip(documents, embeddings):
                        cur.execute("""
                            INSERT INTO rag_documents
                                (id, content, metadata, collection, embedding, tsv)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE SET
                              content   = EXCLUDED.content,
                              metadata  = EXCLUDED.metadata,
                              embedding = EXCLUDED.embedding,
                              tsv       = EXCLUDED.tsv
                        """, (
                            doc["id"],
                            doc["content"],
                            Json(doc.get("metadata", {})),
                            collection,
                            Json(emb),
                            doc["content"],
                        ))
            conn.commit()
        logger.info(
            f"Indexed {len(documents)} docs into collection '{collection}'"
        )

    # ── Embedding ──

    def embed_query(self, text: str) -> list[float]:
        """Compute an embedding vector for *text*.

        Call this once per user message, then pass the vector into
        ``search()`` via *query_embedding* to avoid redundant API calls.
        """
        return self.embedder.embed(text)

    # ── Search ──

    def search(self, query: str, collection: str,
               top_k: int = 5,
               query_embedding: list[float] | None = None,
               hybrid: bool = True) -> list[dict]:
        if self._use_pgvector:
            if hybrid:
                return self._search_hybrid(query, collection, top_k, query_embedding)
            return self._search_pgvector(query, collection, top_k,
                                         query_embedding)
        return self._search_numpy(query, collection, top_k, query_embedding)

    def _search_pgvector(self, query: str, collection: str,
                         top_k: int,
                         query_embedding: list[float] | None = None,
                         ) -> list[dict]:
        query_emb = query_embedding or self.embedder.embed(query)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, content, metadata,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM rag_documents
                    WHERE collection = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (query_emb, collection, query_emb, top_k))
                rows = cur.fetchall()
        return [
            {"id": r[0], "content": r[1], "metadata": r[2],
             "similarity": r[3]}
            for r in rows
        ]

    def _search_hybrid(self, query: str, collection: str,
                       top_k: int,
                       query_embedding: list[float] | None = None,
                       ) -> list[dict]:
        query_emb = query_embedding or self.embedder.embed(query)
        # RRF k parameter
        k = 60
        
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    WITH vector_search AS (
                      SELECT id, content, metadata,
                             1 - (embedding <=> %s::vector) AS similarity,
                             ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) as rank_vector
                      FROM rag_documents
                      WHERE collection = %s
                      LIMIT {top_k * 2}
                    ),
                    text_search AS (
                      SELECT id, content, metadata,
                             ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, plainto_tsquery('english', %s)) DESC) as rank_text
                      FROM rag_documents
                      WHERE collection = %s 
                        AND tsv @@ plainto_tsquery('english', %s)
                      LIMIT {top_k * 2}
                    )
                    SELECT 
                        COALESCE(v.id, t.id),
                        COALESCE(v.content, t.content),
                        COALESCE(v.metadata, t.metadata),
                        ((1.0 / ({k} + COALESCE(v.rank_vector, 1000))) + 
                         (1.0 / ({k} + COALESCE(t.rank_text, 1000))))::float as rrf_score,
                        COALESCE(v.similarity, 0.0) as similarity
                    FROM vector_search v
                    FULL OUTER JOIN text_search t ON v.id = t.id
                    ORDER BY rrf_score DESC
                    LIMIT %s;
                """, (query_emb, query_emb, collection, query, collection, query, top_k))
                rows = cur.fetchall()
        
        return [
            {"id": r[0], "content": r[1], "metadata": r[2], "rrf_score": r[3], "similarity": r[4]}
            for r in rows
        ]

    def list_collection(self, collection: str) -> list[dict]:
        """Fetch all documents in a collection without semantic searching."""
        with self.get_conn_readonly() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, content, metadata
                    FROM rag_documents
                    WHERE collection = %s
                    ORDER BY id ASC
                """, (collection,))
                rows = cur.fetchall()
        return [
            {"id": r[0], "content": r[1], "metadata": r[2]}
            for r in rows
        ]

    def _search_numpy(self, query: str, collection: str,
                       top_k: int,
                       query_embedding: list[float] | None = None,
                       ) -> list[dict]:
        query_emb = np.array(
            query_embedding if query_embedding is not None
            else self.embedder.embed(query)
        )
        query_words = set(query.lower().split())
        
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, content, metadata, embedding
                    FROM rag_documents
                    WHERE collection = %s
                """, (collection,))
                rows = cur.fetchall()

        if not rows:
            return []

        results = []
        for row in rows:
            doc_id, content, metadata, raw_emb = row
            doc_emb = np.array(raw_emb)
            
            # Semantic similarity
            norm_q = np.linalg.norm(query_emb)
            norm_d = np.linalg.norm(doc_emb)
            sim = float(np.dot(query_emb, doc_emb) / (norm_q * norm_d)) if norm_q > 0 and norm_d > 0 else 0.0
            
            # Simple keyword overlap (for non-pgvector fallback)
            doc_words = set(content.lower().split())
            keyword_score = len(query_words & doc_words) / len(query_words) if query_words else 0.0
            
            # Combine 50/50 for fallback
            combined = (0.5 * sim) + (0.5 * keyword_score)
            
            results.append({
                "id": doc_id, "content": content,
                "metadata": metadata, "similarity": sim,
                "rrf_score": combined # naming for consistency
            })

        results.sort(key=lambda r: r["rrf_score"], reverse=True)
        return results[:top_k]

    # ── Convenience wrappers ──

    def index_tools(self, tool_docs: list[dict]):
        self.index_documents(tool_docs, collection="tools")

    def search_tools(self, query: str, top_k: int = 5,
                     query_embedding: list[float] | None = None) -> list[dict]:
        return self.search(query, collection="tools", top_k=top_k,
                           query_embedding=query_embedding)

    def search_kb(self, query: str, top_k: int = 5,
                  query_embedding: list[float] | None = None) -> list[dict]:
        return self.search(query, collection="kb_articles", top_k=top_k,
                           query_embedding=query_embedding)

    def search_sops(self, query: str, top_k: int = 5,
                    query_embedding: list[float] | None = None) -> list[dict]:
        return self.search(query, collection="sops", top_k=top_k,
                           query_embedding=query_embedding)

    def search_past_incidents(self, query: str, top_k: int = 3,
                              query_embedding: list[float] | None = None,
                              ) -> list[dict]:
        return self.search(query, collection="past_incidents", top_k=top_k,
                           query_embedding=query_embedding)

    def index_past_incident(
        self,
        incident_id: str,
        summary: str,
        root_cause: str,
        resolution: str,
        workflows_involved: list[str],
        category: str = "",
    ):
        doc = {
            "id": incident_id,
            "content": (
                f"{summary}\n"
                f"Root Cause: {root_cause}\n"
                f"Resolution: {resolution}"
            ),
            "metadata": {
                "summary": summary,
                "root_cause": root_cause,
                "resolution": resolution,
                "workflows": workflows_involved,
                "category": category,
            },
        }
        self.index_documents([doc], collection="past_incidents")


_rag_engine = None


def get_rag_engine() -> PgVectorRAGEngine:
    """Lazy singleton — only connects to PostgreSQL on first use."""
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = PgVectorRAGEngine()
    return _rag_engine
