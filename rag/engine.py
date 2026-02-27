"""
RAG engine backed by PostgreSQL + pgvector.
Uses sentence-transformers for local embeddings and pgvector for
similarity search.
"""

import logging

import psycopg2
from psycopg2.extras import Json, execute_values
from sentence_transformers import SentenceTransformer

from config.settings import CONFIG

logger = logging.getLogger("ops_agent.rag")


class PgVectorRAGEngine:

    def __init__(self):
        self.dsn = CONFIG["POSTGRES_DSN"]
        self.embed_model = SentenceTransformer(
            CONFIG.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        )
        self.embed_dim = self.embed_model.get_sentence_embedding_dimension()
        self._ensure_tables()

    # ── Connection ──

    def _get_conn(self):
        return psycopg2.connect(self.dsn)

    # ── Schema bootstrap ──

    def _ensure_tables(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS rag_documents (
                        id          TEXT PRIMARY KEY,
                        content     TEXT NOT NULL,
                        metadata    JSONB DEFAULT '{{}}'::jsonb,
                        collection  TEXT NOT NULL,
                        embedding   vector({self.embed_dim}),
                        created_at  TIMESTAMPTZ DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rag_embedding
                    ON rag_documents
                    USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100);
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rag_collection
                    ON rag_documents (collection);
                """)
            conn.commit()

    # ── Embedding ──

    def _embed(self, text: str) -> list[float]:
        return self.embed_model.encode(text).tolist()

    # ── Indexing ──

    def index_documents(self, documents: list[dict], collection: str):
        """
        Upsert documents into pgvector.
        Each doc: {"id": str, "content": str, "metadata": dict}
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                values = []
                for doc in documents:
                    emb = self._embed(doc["content"])
                    values.append((
                        doc["id"],
                        doc["content"],
                        Json(doc.get("metadata", {})),
                        collection,
                        emb,
                    ))
                execute_values(
                    cur,
                    """INSERT INTO rag_documents
                           (id, content, metadata, collection, embedding)
                       VALUES %s
                       ON CONFLICT (id) DO UPDATE SET
                         content   = EXCLUDED.content,
                         metadata  = EXCLUDED.metadata,
                         embedding = EXCLUDED.embedding""",
                    values,
                    template="(%s, %s, %s, %s, %s::vector)",
                )
            conn.commit()
        logger.info(
            f"Indexed {len(documents)} docs into collection '{collection}'"
        )

    # ── Search ──

    def search(self, query: str, collection: str,
               top_k: int = 5) -> list[dict]:
        query_emb = self._embed(query)
        with self._get_conn() as conn:
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

    # ── Convenience wrappers ──

    def index_tools(self, tool_docs: list[dict]):
        self.index_documents(tool_docs, collection="tools")

    def search_tools(self, query: str, top_k: int = 5) -> list[dict]:
        return self.search(query, collection="tools", top_k=top_k)

    def search_kb(self, query: str, top_k: int = 5) -> list[dict]:
        return self.search(query, collection="kb_articles", top_k=top_k)

    def search_sops(self, query: str, top_k: int = 5) -> list[dict]:
        return self.search(query, collection="sops", top_k=top_k)

    def search_past_incidents(self, query: str,
                              top_k: int = 3) -> list[dict]:
        return self.search(query, collection="past_incidents", top_k=top_k)

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

