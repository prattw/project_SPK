from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection

from datetime import datetime, timezone

from app.citations import citations_from_chunks
from app.config import settings
from app.context_budget import cap_chunk_records, cap_chunks, pack_chunks_for_llm, prepare_text_for_ingest
from app.doc_metadata import classify_upload_origin, enrich_library_fields
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
        total = self.document_count
        if total == 0:
            return []
        # Page through metadatas; pulling all rows at once overflows SQLite's
        # variable limit on large indexes (hundreds of thousands of chunks).
        sources: set[str] = set()
        batch = 5_000
        offset = 0
        while offset < total:
            result = self._collection.get(include=["metadatas"], limit=batch, offset=offset)
            metadatas = result.get("metadatas") or []
            if not metadatas:
                break
            for m in metadatas:
                sources.add((m or {}).get("source", "unknown"))
            offset += batch
        return sorted(sources)

    def list_documents(self) -> list[dict[str, Any]]:
        """Aggregate per-source metadata for the Documents tab."""
        total = self.document_count
        if total == 0:
            return []

        by_source: dict[str, dict[str, Any]] = {}
        batch = 5_000
        offset = 0
        while offset < total:
            result = self._collection.get(include=["metadatas"], limit=batch, offset=offset)
            metadatas = result.get("metadatas") or []
            if not metadatas:
                break
            for m in metadatas:
                meta = m or {}
                source = meta.get("source", "unknown")
                if source not in by_source:
                    by_source[source] = {
                        "source": source,
                        "doc_number": meta.get("doc_number"),
                        "doc_type": meta.get("doc_type"),
                        "title": meta.get("title"),
                        "category": meta.get("category"),
                        "chunks": 0,
                        "indexed_at": meta.get("indexed_at"),
                        "session_id": meta.get("session_id"),
                        "upload_origin": meta.get("upload_origin"),
                        "part": meta.get("part"),
                        "display_title": meta.get("display_title"),
                        "year_published": meta.get("year_published"),
                        "year_updated": meta.get("year_updated"),
                    }
                entry = by_source[source]
                entry["chunks"] += 1
                for key in (
                    "doc_number",
                    "doc_type",
                    "title",
                    "category",
                    "session_id",
                    "upload_origin",
                    "part",
                    "display_title",
                    "year_published",
                    "year_updated",
                ):
                    if not entry.get(key) and meta.get(key):
                        entry[key] = meta[key]
                indexed_at = meta.get("indexed_at")
                if indexed_at and (
                    not entry.get("indexed_at") or indexed_at > entry["indexed_at"]
                ):
                    entry["indexed_at"] = indexed_at
            offset += batch

        docs: list[dict[str, Any]] = []
        for source, entry in by_source.items():
            entry["updated_at"] = entry.get("indexed_at")
            entry["upload_origin"] = classify_upload_origin(source, entry)
            entry.update(enrich_library_fields(source, entry))
            entry["url"] = None  # filled by API layer via citations helper
            docs.append(entry)

        def _sort_key(doc: dict[str, Any]) -> tuple[str, str]:
            return (doc.get("doc_number") or doc.get("source") or "", doc.get("part") or "")

        return sorted(docs, key=_sort_key)

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

    @staticmethod
    def _stamp_meta(extra_meta: dict[str, str] | None) -> dict[str, str]:
        meta = dict(extra_meta or {})
        meta.setdefault("indexed_at", datetime.now(tz=timezone.utc).isoformat())
        return meta

    def ingest_documents(
        self,
        documents: list[dict[str, Any]],
        extra_meta: dict[str, str] | None = None,
    ) -> tuple[int, list[str]]:
        warnings: list[str] = []
        total = 0
        base_meta = self._stamp_meta(extra_meta)

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
        base_meta = self._stamp_meta(extra_meta)
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
                        "doc_number": (meta or {}).get("doc_number"),
                        "doc_type": (meta or {}).get("doc_type"),
                        "upload_origin": (meta or {}).get("upload_origin"),
                        "score": 1.0,
                    }
                )
        return found

    def _semantic_search(
        self,
        query: str,
        k: int,
        where: dict[str, Any] | None = None,
        score_boost: float = 0.0,
    ) -> list[dict[str, Any]]:
        query_embedding = embed_query(query)
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = self._collection.query(**kwargs)
        except Exception:
            return []

        chunks: list[dict[str, Any]] = []
        if not results["documents"] or not results["documents"][0]:
            return chunks

        for doc, meta, distance in zip(
            results["documents"][0],
            results["metadatas"][0] or [],
            results["distances"][0] or [],
        ):
            m = meta or {}
            base_score = 1 - distance if distance is not None else 0.0
            chunks.append(
                {
                    "text": doc,
                    "source": m.get("source", "unknown"),
                    "page_start": m.get("page_start"),
                    "page_end": m.get("page_end"),
                    "doc_number": m.get("doc_number"),
                    "doc_type": m.get("doc_type"),
                    "upload_origin": m.get("upload_origin"),
                    "score": base_score + score_boost,
                }
            )
        return chunks

    @staticmethod
    def _merge_chunks(*groups: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for group in groups:
            for chunk in group:
                key = f"{chunk['source']}:{chunk.get('page_start')}:{chunk['text'][:80]}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(chunk)
                if len(merged) >= limit:
                    return merged
        return merged

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        focus_sources: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.document_count == 0:
            return []

        k = min(settings.max_retrieval_candidates, self.document_count)
        if top_k is not None:
            k = min(top_k, k)

        focus = [s for s in (focus_sources or []) if s]
        semantic: list[dict[str, Any]] = []

        if focus:
            # Session uploads first — ensures spec/submittal comparisons stay consistent.
            focused = self._semantic_search(
                query,
                k,
                where={"source": {"$in": focus}},
                score_boost=0.15,
            )
            general = self._semantic_search(query, k)
            semantic = self._merge_chunks(focused, general, limit=k)
        else:
            semantic = self._semantic_search(query, k)

        page_refs = self._page_refs(query)
        if page_refs:
            page_chunks = self._chunks_by_pages(page_refs)
            if focus:
                page_chunks = [c for c in page_chunks if c["source"] in focus] + [
                    c for c in page_chunks if c["source"] not in focus
                ]
            return self._merge_chunks(page_chunks, semantic, limit=k + len(page_refs))

        return semantic

    def query(
        self,
        question: str,
        top_k: int | None = None,
        focus_sources: list[str] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if self.document_count == 0:
            return {
                "answer": generate_general_answer(question, history=history),
                "sources": [],
                "citations": [],
                "chunks_used": 0,
                "context_warnings": [
                    "No documents are indexed yet — this answer comes from the AI's "
                    "general knowledge, not your document library. Upload documents "
                    "for grounded, citable answers."
                ],
            }

        candidates = self.retrieve(question, top_k=top_k, focus_sources=focus_sources)
        if not candidates:
            return {
                "answer": generate_general_answer(question, history=history),
                "sources": [],
                "citations": [],
                "chunks_used": 0,
                "context_warnings": [
                    "No relevant sections were found in your uploaded files — this "
                    "answer comes from the AI's general knowledge instead."
                ],
            }

        context, selected, pack_warnings = pack_chunks_for_llm(candidates)
        citations = citations_from_chunks(selected)
        answer = generate_answer(question, context, history=history, citations=citations)
        sources = sorted({c["source"] for c in selected})

        if focus_sources:
            focused_hits = [c for c in selected if c["source"] in focus_sources]
            if focused_hits and len(focused_hits) < len(selected):
                pack_warnings = list(pack_warnings) + [
                    f"Prioritized {len(focused_hits)} section(s) from this session's "
                    f"{len(focus_sources)} uploaded file(s)."
                ]

        return {
            "answer": answer,
            "sources": sources,
            "citations": citations,
            "chunks_used": len(selected),
            "context_warnings": pack_warnings,
        }

    def update_source_metadata(self, source: str, patch: dict[str, str]) -> int:
        """Update metadata on all chunks for a source (e.g. reclassify upload_origin)."""
        result = self._collection.get(where={"source": source}, include=["metadatas"])
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        if not ids:
            return 0

        updated_meta = []
        for meta in metadatas:
            merged = dict(meta or {})
            merged.update(patch)
            updated_meta.append(merged)

        self._collection.update(ids=ids, metadatas=updated_meta)
        return len(ids)

    def reclassify_upload_origins(self) -> int:
        """Persist correct upload_origin (user vs library) for all indexed sources."""
        total = self.document_count
        if total == 0:
            return 0

        by_source: dict[str, dict[str, Any]] = {}
        batch = 5_000
        offset = 0
        while offset < total:
            result = self._collection.get(include=["metadatas"], limit=batch, offset=offset)
            metadatas = result.get("metadatas") or []
            if not metadatas:
                break
            for m in metadatas:
                meta = m or {}
                source = meta.get("source", "unknown")
                if source not in by_source:
                    by_source[source] = {
                        "source": source,
                        "doc_number": meta.get("doc_number"),
                        "category": meta.get("category"),
                        "session_id": meta.get("session_id"),
                        "upload_origin": meta.get("upload_origin"),
                    }
                entry = by_source[source]
                for key in ("doc_number", "category", "session_id", "upload_origin"):
                    if not entry.get(key) and meta.get(key):
                        entry[key] = meta[key]
            offset += batch

        changed = 0
        for source, entry in by_source.items():
            stored = (entry.get("upload_origin") or "").lower()
            correct = classify_upload_origin(source, entry)
            if stored != correct:
                if self.update_source_metadata(source, {"upload_origin": correct}):
                    changed += 1
        return changed

    def reset_index(self) -> None:
        self._chroma.delete_collection(settings.collection_name)
        self._collection = self._get_or_create_collection()


_rag: RAGService | None = None


def get_rag() -> RAGService:
    global _rag
    if _rag is None:
        _rag = RAGService()
    return _rag
