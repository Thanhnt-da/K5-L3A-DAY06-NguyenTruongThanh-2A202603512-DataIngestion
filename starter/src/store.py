"""Store and search vectors in PostgreSQL + pgvector.

- One `chunks` table for all strategies; primary key (strategy, chunk_id), so
  re-ingesting the same data updates rows instead of duplicating them.
- Exact (sequential-scan) cosine search: no ANN index, so filters never cut
  results short. Score = cosine similarity = 1 - cosine distance (`<=>`).
- Strategy F adds keyword (PostgreSQL full-text) ranking fused with the dense ranking.
- `embedding_cache` keeps vectors per (model, text hash) so re-runs and
  strategy C (same chunks as B) do not pay for the same text twice.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

from .models import Chunk

FILTERABLE = {"chapter": "chapter", "section": "section", "document_id": "document_id"}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vec(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.database_url = database_url
        self.dimensions = dimensions

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, connect_timeout=5)

    def create_schema(self) -> None:
        d = self.dimensions
        with self.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    strategy      text NOT NULL,
                    chunk_id      text NOT NULL,
                    document_id   text NOT NULL,
                    source        text NOT NULL,
                    page          int  NOT NULL,
                    printed_page  int,
                    chapter       int,
                    section       text,
                    section_title text,
                    content       text NOT NULL,
                    char_count    int  NOT NULL,
                    content_hash  text NOT NULL,
                    metadata      jsonb NOT NULL DEFAULT '{{}}',
                    embedding     vector({d}) NOT NULL,
                    ingested_at   timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (strategy, chunk_id))""")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_filter_idx ON chunks (strategy, chapter, section)")
            # page_end: last page of a chunk (= page except for strategy Z, which may span pages)
            conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_end int")
            conn.execute("UPDATE chunks SET page_end = page WHERE page_end IS NULL")
            # Keyword index for hybrid search (strategy F): English stemming, stop words removed
            conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_tsv tsvector "
                         "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (content_tsv)")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    model     text NOT NULL,
                    text_hash text NOT NULL,
                    embedding vector({d}) NOT NULL,
                    PRIMARY KEY (model, text_hash))""")
            row = conn.execute(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            ).fetchone()
            if row and row[0] != d:
                raise RuntimeError(f"chunks.embedding is vector({row[0]}) but model has {d} dims")

    # ---- embedding cache -------------------------------------------------
    def cached_vectors(self, model: str, hashes: list[str]) -> dict[str, list[float]]:
        if not hashes:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT text_hash, embedding::text FROM embedding_cache WHERE model = %s AND text_hash = ANY(%s)",
                (model, hashes)).fetchall()
        return {h: json.loads(v) for h, v in rows}

    def cache_vectors(self, model: str, items: dict[str, list[float]]) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO embedding_cache (model, text_hash, embedding) VALUES (%s, %s, %s::vector) "
                "ON CONFLICT DO NOTHING",
                [(model, h, _vec(v)) for h, v in items.items()])

    # ---- write -----------------------------------------------------------
    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> dict[str, int]:
        """Insert/update chunks of one strategy; delete that strategy's obsolete chunks of the same documents."""
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        if any(len(v) != self.dimensions for v in vectors):
            raise ValueError(f"every vector must have {self.dimensions} dims")
        ids = [c.chunk_id for c in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk_id in input")
        documents = sorted({c.document_id for c in chunks})
        rows = []
        for c, v in zip(chunks, vectors):
            md = c.metadata
            rows.append((strategy, c.chunk_id, c.document_id, c.source, c.page, md.get("page_end", c.page),
                         md.get("printed_page"),
                         md.get("chapter"), md.get("section"), md.get("section_title"), c.text,
                         len(c.text), text_hash(c.text), json.dumps(md), _vec(v)))
        with self.connect() as conn, conn.cursor() as cur:   # one transaction
            cur.execute("SELECT count(*) FROM chunks WHERE strategy = %s", (strategy,))
            before = cur.fetchone()[0]
            cur.execute("DELETE FROM chunks WHERE strategy = %s AND document_id = ANY(%s) "
                        "AND NOT (chunk_id = ANY(%s))", (strategy, documents, ids))
            deleted = cur.rowcount
            cur.executemany("""
                INSERT INTO chunks (strategy, chunk_id, document_id, source, page, page_end, printed_page, chapter,
                                    section, section_title, content, char_count, content_hash, metadata, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::vector)
                ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                    document_id = EXCLUDED.document_id, source = EXCLUDED.source, page = EXCLUDED.page,
                    page_end = EXCLUDED.page_end,
                    printed_page = EXCLUDED.printed_page, chapter = EXCLUDED.chapter,
                    section = EXCLUDED.section, section_title = EXCLUDED.section_title,
                    content = EXCLUDED.content, char_count = EXCLUDED.char_count,
                    content_hash = EXCLUDED.content_hash, metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding, ingested_at = now()""", rows)
            cur.execute("SELECT count(*) FROM chunks WHERE strategy = %s", (strategy,))
            after = cur.fetchone()[0]
        return {"rows_before": before, "rows_after": after, "obsolete_deleted": deleted, "input_chunks": len(chunks)}

    # ---- read ------------------------------------------------------------
    def all_chunks(self, strategy: str) -> list[dict[str, Any]]:
        sql = f"SELECT {self.COLUMNS} FROM chunks WHERE strategy = %(strategy)s ORDER BY page, chunk_id"
        return self._fetch(sql, {"strategy": strategy})

    def count(self, strategy: str) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT count(*) FROM chunks WHERE strategy = %s", (strategy,)).fetchone()[0]

    @staticmethod
    def _where(strategy: str, filters: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        where, params = ["strategy = %(strategy)s"], {"strategy": strategy}
        for i, (key, value) in enumerate((filters or {}).items()):
            if key not in FILTERABLE:
                raise ValueError(f"filter on {key!r} not allowed; use one of {sorted(FILTERABLE)}")
            where.append(f"{FILTERABLE[key]} = %(f{i})s")   # column from whitelist, value parameterized
            params[f"f{i}"] = value
        return " AND ".join(where), params

    COLUMNS = ("chunk_id, document_id, source, page, page_end, printed_page, chapter, section, "
               "section_title, content")

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Dense search: exact cosine ranking."""
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query vector must have {self.dimensions} dims")
        where, params = self._where(strategy, filters)
        params.update(qv=_vec(query_vector), k=top_k)
        sql = (f"SELECT {self.COLUMNS}, 1 - (embedding <=> %(qv)s::vector) AS score FROM chunks "
               f"WHERE {where} ORDER BY embedding <=> %(qv)s::vector, chunk_id LIMIT %(k)s")
        return self._fetch(sql, params)

    def hybrid_search(self, strategy: str, query_vector: list[float], query_text: str, top_k: int = 3,
                      filters: dict[str, Any] | None = None, vector_weight: float = 0.5,
                      candidates: int = 50, rrf_k: int = 60) -> list[dict[str, Any]]:
        """Hybrid search (strategy F): Reciprocal Rank Fusion of dense and keyword rankings.

        fused = w / (rrf_k + dense_rank) + (1 - w) / (rrf_k + keyword_rank), over the top
        `candidates` of each list. Keyword ranking = PostgreSQL full-text ts_rank_cd with the
        question's words OR-ed (an AND query would match almost nothing for natural questions).
        """
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query vector must have {self.dimensions} dims")
        if not 0.0 <= vector_weight <= 1.0:
            raise ValueError("vector_weight must be in [0, 1]")
        where, params = self._where(strategy, filters)
        params.update(qv=_vec(query_vector), qt=query_text, w=vector_weight, n=candidates,
                      rrf=rrf_k, k=top_k)
        sql = f"""
            WITH q AS (SELECT NULLIF(replace(plainto_tsquery('english', %(qt)s)::text, '&', '|'), '')::tsquery AS tsq),
            dense AS (
                SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> %(qv)s::vector, chunk_id) AS r
                FROM chunks WHERE {where}
                ORDER BY embedding <=> %(qv)s::vector, chunk_id LIMIT %(n)s),
            keyword AS (
                SELECT chunk_id, row_number() OVER (ORDER BY ts_rank_cd(content_tsv, q.tsq) DESC, chunk_id) AS r
                FROM chunks, q WHERE {where} AND q.tsq IS NOT NULL AND content_tsv @@ q.tsq
                ORDER BY ts_rank_cd(content_tsv, q.tsq) DESC, chunk_id LIMIT %(n)s),
            fused AS (
                SELECT chunk_id, sum(s) AS fused, max(dr) AS dense_rank, max(kr) AS keyword_rank FROM (
                    SELECT chunk_id, %(w)s::float / (%(rrf)s + r) AS s, r AS dr, NULL::bigint AS kr FROM dense
                    UNION ALL
                    SELECT chunk_id, (1 - %(w)s::float) / (%(rrf)s + r), NULL, r FROM keyword) u
                GROUP BY chunk_id)
            SELECT {', '.join('c.' + col.strip() for col in self.COLUMNS.split(','))},
                   1 - (c.embedding <=> %(qv)s::vector) AS score,
                   f.fused AS fused_score, f.dense_rank, f.keyword_rank
            FROM fused f JOIN chunks c ON c.strategy = %(strategy)s AND c.chunk_id = f.chunk_id
            ORDER BY f.fused DESC, c.chunk_id LIMIT %(k)s"""
        return self._fetch(sql, params)

    def _fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
