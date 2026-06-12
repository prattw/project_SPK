from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection

from app.config import settings
from app.context_budget import cap_chunk_records, cap_chunks, pack_chunks_for_llm, prepare_text_for_ingest
from app.embeddings import embed_query, embed_texts
from app.llm import generate_answer, generate_general_answer

PAGE_REF_PATTERN = re.compile(
    r"\b(?:page|pg|p)\s*\.?\s*(\d{1,5})\b",
    re.IGNORECASE,
)


class RAGService:
    def __init__(self) -> None:
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(path=str(settings.chroma_path))
        self._collection = self._get_or_create_collection()

    def _get_or_create_collection(self) -> Collection:
        return self._chroma.get_or_create_collection(
            name=settings.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def document_count(self) -> int:
        return self._collection.count()

    def list_sources(self) -> list[str]:
        if self.document_count == 0:
            return []
        result = self._collection.get(include=["metadatas"])
        sources = sorted(
            {(m or {}).get("source", "unknown") for m in (result.get("metadatas") or [])}
        )
        return sources

    @staticmethod
    def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
        text = text.strip()
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = end - overlap
        return chunks

    @staticmethod
    def _chunk_id(source: str, index: int, content: str, page: int | None = None) -> str:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        prefix = f"{source}::p{page}" if page is not None else source
        return f"{prefix}::{index}::{digest}"

    @staticmethod
    def _page_refs(question: str) -> list[int]:
        return sorted({int(m.group(1)) for m in PAGE_REF_PATTERN.finditer(question)})

    def _upsert_batch(
        self,
        source: str,
        records: list[tuple[str, dict[str, Any]]],
        id_offset: int = 0,
    ) -> int:
        if not records:
            return 0

        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for i, (text, meta) in enumerate(records):
            page = meta.get("page_start")
            idx = id_offset + i
            ids.append(self._chunk_id(source, idx, text, page=page))
            texts.append(text)
            metadatas.append(meta)

        embeddings = embed_texts(texts)
        self._collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return len(texts)

    def _upsert_records(
        self,
        source: str,
        records: list[tuple[str, dict[str, Any]]],
    ) -> tuple[int, list[str]]:
        if not records:
            return 0, []

        records, warnings = cap_chunk_records(records, source)
        return self._upsert_batch(source, records), warnings

    def ingest_documents(
        self,
        documents: list[dict[str, Any]],
        extra_meta: dict[str, str] | None = None,
    ) -> tuple[int, list[str]]:
        warnings: list[str] = []
        total = 0

        for doc in documents:
            source = doc["source"]
            prepared, prep_warnings = prepare_text_for_ingest(doc["text"], source)
            warnings.extend(prep_warnings)

            raw_chunks = self.chunk_text(
                prepared,
                settings.chunk_size,
                settings.chunk_overlap,
            )
            capped, chunk_warnings = cap_chunks(raw_chunks, source)
            warnings.extend(chunk_warnings)

            base_meta = dict(extra_meta or {})
            records = [
                (text, {**base_meta, "source": source, "chunk_index": i})
                for i, text in enumerate(capped)
            ]
            count, upsert_warnings = self._upsert_records(source, records)
            warnings.extend(upsert_warnings)
            total += count

        return total, warnings

    def ingest_pdf_pages(
        self,
        source: str,
        page_iter: Iterator[tuple[int, str]],
        pages_total: int,
        progress_callback: Callable[[int, int], None] | None = None,
        initial_warnings: list[str] | None = None,
        extra_meta: dict[str, str] | None = None,
    ) -> tuple[int, list[str], int]:
        """Index a PDF page-by-page (optimized for 2000+ page specs)."""
        warnings: list[str] = list(initial_warnings or [])
        base_meta = dict(extra_meta or {})
        pending: list[tuple[str, dict[str, Any]]] = []
        pages_indexed = 0
        total_chunks = 0
        chunk_budget = settings.max_chunks_per_file
        flush_size = settings.pdf_embed_flush_chunks
        every = settings.pdf_progress_every_pages

        def flush() -> None:
            nonlocal pending, total_chunks
            if not pending:
                return
            total_chunks += self._upsert_batch(source, pending, id_offset=total_chunks)
            pending = []

        for page_num, raw in page_iter:
            pages_indexed = page_num
            if progress_callback and (page_num % every == 0 or page_num == pages_total):
                progress_callback(page_num, pages_total)

            text = (raw or "").strip()
            if not text:
                continue

            body = f"Page {page_num} of {source}\n{text}"
            subchunks = self.chunk_text(
                body,
                settings.chunk_size,
                settings.chunk_overlap,
            )
            for chunk_index, chunk in enumerate(subchunks):
                if total_chunks + len(pending) >= chunk_budget:
                    warnings.append(
                        f"{source}: stopped at page {page_num} — reached {chunk_budget:,} chunk limit."
                    )
                    flush()
                    return total_chunks, warnings, pages_indexed

                pending.append(
                    (
                        chunk,
                        {
                            **base_meta,
                            "source": source,
                            "page_start": page_num,
                            "page_end": page_num,
                            "chunk_index": chunk_index,
                        },
                    )
                )
                if len(pending) >= flush_size:
                    flush()

        flush()
        if progress_callback:
            progress_callback(pages_indexed, pages_total)

        return total_chunks, warnings, pages_indexed

    def delete_source(self, source: str) -> int:
        existing = self._collection.get(where={"source": source}, include=[])
        ids = existing.get("ids") or []
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def _chunks_by_pages(self, pages: list[int]) -> list[dict[str, Any]]:
        if not pages or self.document_count == 0:
            return []

        found: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for page in pages:
            try:
                result = self._collection.get(
                    where={
                        "$and": [
                            {"page_start": {"$lte": page}},
                            {"page_end": {"$gte": page}},
                        ]
                    },
                    include=["documents", "metadatas"],
                    limit=6,
                )
            except Exception:
                continue

            for doc_id, doc, meta in zip(
                result.get("ids") or [],
                result.get("documents") or [],
                result.get("metadatas") or [],
            ):
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
                found.append(
                    {
                        "text": doc,
                        "source": (meta or {}).get("source", "unknown"),
                        "page_start": (meta or {}).get("page_start"),
                        "page_end": (meta or {}).get("page_end"),
                        "score": 1.0,
                    }
                )
        return found

    def retrieve(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        if self.document_count == 0:
            return []

        k = min(settings.max_retrieval_candidates, self.document_count)
        if top_k is not None:
            k = min(top_k, k)

        query_embedding = embed_query(query)
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        chunks: list[dict[str, Any]] = []
        if not results["documents"] or not results["documents"][0]:
            semantic: list[dict[str, Any]] = []
        else:
            semantic = []
            for doc, meta, distance in zip(
                results["documents"][0],
                results["metadatas"][0] or [],
                results["distances"][0] or [],
            ):
                m = meta or {}
                semantic.append(
                    {
                        "text": doc,
                        "source": m.get("source", "unknown"),
                        "page_start": m.get("page_start"),
                        "page_end": m.get("page_end"),
                        "doc_number": m.get("doc_number"),
                        "doc_type": m.get("doc_type"),
                        "score": 1 - distance if distance is not None else None,
                    }
                )

        page_refs = self._page_refs(query)
        if page_refs:
            page_chunks = self._chunks_by_pages(page_refs)
            # Page-specific chunks first, then semantic; dedupe by text prefix.
            seen: set[str] = set()
            merged: list[dict[str, Any]] = []
            for chunk in page_chunks + semantic:
                key = f"{chunk['source']}:{chunk.get('page_start')}:{chunk['text'][:80]}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(chunk)
            return merged[: k + len(page_refs)]

        return semantic

    def query(self, question: str, top_k: int | None = None) -> dict[str, Any]:
        if self.document_count == 0:
            return {
                "answer": generate_general_answer(question),
                "sources": [],
                "chunks_used": 0,
                "context_warnings": [
                    "No documents are indexed yet — this answer comes from the AI's "
                    "general knowledge, not your document library. Upload documents "
                    "for grounded, citable answers."
                ],
            }

        candidates = self.retrieve(question, top_k=top_k)
        if not candidates:
            return {
                "answer": generate_general_answer(question),
                "sources": [],
                "chunks_used": 0,
                "context_warnings": [
                    "No relevant sections were found in your uploaded files — this "
                    "answer comes from the AI's general knowledge instead."
                ],
            }

        context, selected, pack_warnings = pack_chunks_for_llm(candidates)
        answer = generate_answer(question, context)
        sources = sorted({c["source"] for c in selected})

        return {
            "answer": answer,
            "sources": sources,
            "chunks_used": len(selected),
            "context_warnings": pack_warnings,
        }

    def reset_index(self) -> None:
        self._chroma.delete_collection(settings.collection_name)
        self._collection = self._get_or_create_collection()


_rag: RAGService | None = None


def get_rag() -> RAGService:
    global _rag
    if _rag is None:
        _rag = RAGService()
    return _rag
