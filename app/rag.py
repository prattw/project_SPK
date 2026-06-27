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
from app.llm import generate_answer
from app.regulatory_retrieval import library_subqueries, question_needs_library_regulations
from app.section_search import (
    detect_page_section,
    extract_section_numbers,
    normalize_section_number,
    section_search_variants,
    sections_from_filenames,
)

PAGE_REF_PATTERN = re.compile(
    r"\b(?:page|pg|p)\s*\.?\s*(\d{1,5})\b",
    re.IGNORECASE,
)


class RAGService:
    def __init__(self) -> None:
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(path=str(settings.chroma_path))
        self._collection = self._get_or_create_collection()
        self._library_sources_cache: list[str] | None = None

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

    @staticmethod
    def _section_refs(question: str, explicit: list[str] | None = None) -> list[str]:
        found = extract_section_numbers(question)
        for raw in explicit or []:
            norm = normalize_section_number(raw)
            if norm and norm not in found:
                found.append(norm)
        return found

    def _append_get_results(
        self,
        result: dict[str, Any],
        found: list[dict[str, Any]],
        seen: set[str],
        *,
        score: float,
    ) -> None:
        for doc_id, doc, meta in zip(
            result.get("ids") or [],
            result.get("documents") or [],
            result.get("metadatas") or [],
        ):
            if doc_id in seen:
                continue
            seen.add(doc_id)
            m = meta or {}
            found.append(
                {
                    "text": doc,
                    "source": m.get("source", "unknown"),
                    "page_start": m.get("page_start"),
                    "page_end": m.get("page_end"),
                    "doc_number": m.get("doc_number"),
                    "doc_type": m.get("doc_type"),
                    "upload_origin": m.get("upload_origin"),
                    "spec_section": m.get("spec_section"),
                    "score": score,
                }
            )

    def _chunks_by_sections(
        self,
        sections: list[str],
        *,
        focus_sources: list[str] | None = None,
        include_library: bool = True,
    ) -> list[dict[str, Any]]:
        if not sections or self.document_count == 0:
            return []

        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        per_section = max(4, settings.section_search_limit // max(len(sections), 1))
        focus = [s for s in (focus_sources or []) if s]
        focus_set = set(focus)

        def search_scope(library_only: bool) -> list[str] | None:
            if library_only:
                return None
            return focus if focus else None

        def matches_scope(source: str, library_only: bool) -> bool:
            if not focus_set:
                return True
            in_focus = source in focus_set
            return (not in_focus) if library_only else in_focus

        scopes: list[tuple[bool, float]] = [(False, 1.25)]
        if focus and include_library:
            scopes.append((True, 1.15))
        elif not focus:
            scopes = [(False, 1.2)]

        for section in sections:
            norm = normalize_section_number(section)
            if not norm:
                continue

            for library_only, score in scopes:
                source_filter = search_scope(library_only)
                try:
                    where: dict[str, Any] = {"spec_section": {"$eq": norm}}
                    if source_filter:
                        where = {"$and": [where, {"source": {"$in": source_filter}}]}
                    result = self._collection.get(
                        where=where,
                        include=["documents", "metadatas"],
                        limit=per_section,
                    )
                    for doc_id, doc, meta in zip(
                        result.get("ids") or [],
                        result.get("documents") or [],
                        result.get("metadatas") or [],
                    ):
                        src = (meta or {}).get("source", "unknown")
                        if not matches_scope(src, library_only):
                            continue
                        self._append_get_results(
                            {"ids": [doc_id], "documents": [doc], "metadatas": [meta]},
                            found,
                            seen,
                            score=score,
                        )
                except Exception:
                    pass

                for needle in section_search_variants(norm):
                    try:
                        kwargs: dict[str, Any] = {
                            "where_document": {"$contains": needle},
                            "include": ["documents", "metadatas"],
                            "limit": per_section,
                        }
                        if source_filter:
                            kwargs["where"] = {"source": {"$in": source_filter}}
                        result = self._collection.get(**kwargs)
                        for doc_id, doc, meta in zip(
                            result.get("ids") or [],
                            result.get("documents") or [],
                            result.get("metadatas") or [],
                        ):
                            src = (meta or {}).get("source", "unknown")
                            if not matches_scope(src, library_only):
                                continue
                            self._append_get_results(
                                {"ids": [doc_id], "documents": [doc], "metadatas": [meta]},
                                found,
                                seen,
                                score=score,
                            )
                    except Exception:
                        continue

        return found[: settings.section_search_limit]

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
        current_spec_section = ""

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

            current_spec_section = detect_page_section(text, current_spec_section)
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
                            **({"spec_section": current_spec_section} if current_spec_section else {}),
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
                m = meta or {}
                found.append(
                    {
                        "text": doc,
                        "source": m.get("source", "unknown"),
                        "page_start": m.get("page_start"),
                        "page_end": m.get("page_end"),
                        "doc_number": m.get("doc_number"),
                        "doc_type": m.get("doc_type"),
                        "upload_origin": m.get("upload_origin"),
                        "spec_section": m.get("spec_section"),
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
                    "spec_section": m.get("spec_section"),
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

    def _library_source_names(self) -> list[str]:
        if self._library_sources_cache is not None:
            return self._library_sources_cache
        names = [
            doc["source"]
            for doc in self.list_documents()
            if doc.get("upload_origin") == "library"
        ]
        self._library_sources_cache = names
        return names

    def _retrieve_library_regulatory(
        self,
        question: str,
        focus_set: set[str],
        *,
        slots: int,
    ) -> list[dict[str, Any]]:
        """Dedicated passes over the Document Library for UFC / ER / US Code, etc."""
        if slots <= 0 or self.document_count == 0:
            return []

        library_names = set(self._library_source_names()) - focus_set
        if not library_names:
            broad = self._semantic_search(question, slots * 2)
            return [c for c in broad if c.get("source") not in focus_set][:slots]

        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        per_query = max(2, settings.library_subquery_slots)

        for subquery in library_subqueries(question):
            hits = self._semantic_search(subquery, per_query * 3)
            for chunk in hits:
                if chunk.get("source") not in library_names:
                    continue
                key = f"{chunk['source']}:{chunk.get('page_start')}:{chunk['text'][:80]}"
                if key in seen:
                    continue
                seen.add(key)
                chunk["score"] = (chunk.get("score") or 0) + 0.12
                found.append(chunk)
                if len(found) >= slots:
                    return found[:slots]

        return found[:slots]

    def _expand_section_neighbors(
        self,
        section_chunks: list[dict[str, Any]],
        *,
        page_span: int = 12,
    ) -> list[dict[str, Any]]:
        """Pull nearby pages from the same spec after a section header is found."""
        if not section_chunks:
            return []

        expanded: list[dict[str, Any]] = []
        seen: set[str] = set()

        for seed in section_chunks:
            source = seed.get("source")
            page = seed.get("page_start")
            if not source or page is None:
                continue
            try:
                result = self._collection.get(
                    where={
                        "$and": [
                            {"source": {"$eq": source}},
                            {"page_start": {"$gte": int(page)}},
                            {"page_start": {"$lte": int(page) + page_span}},
                        ]
                    },
                    include=["documents", "metadatas"],
                    limit=20,
                )
            except Exception:
                continue

            for doc_id, doc, meta in zip(
                result.get("ids") or [],
                result.get("documents") or [],
                result.get("metadatas") or [],
            ):
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                m = meta or {}
                expanded.append(
                    {
                        "text": doc,
                        "source": m.get("source", source),
                        "page_start": m.get("page_start"),
                        "page_end": m.get("page_end"),
                        "doc_number": m.get("doc_number"),
                        "doc_type": m.get("doc_type"),
                        "upload_origin": m.get("upload_origin"),
                        "spec_section": m.get("spec_section"),
                        "score": 1.3,
                    }
                )
        return expanded

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        focus_sources: list[str] | None = None,
        include_library: bool = True,
        explicit_sections: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.document_count == 0:
            return []

        focus = [s for s in (focus_sources or []) if s]
        focus_set = set(focus)
        use_library = include_library and bool(focus)
        k = min(
            settings.max_retrieval_candidates_with_library if use_library else settings.max_retrieval_candidates,
            self.document_count,
        )
        if top_k is not None:
            k = min(top_k, k)

        library_slots = settings.library_retrieval_slots if use_library else 0
        focus_k = max(k - library_slots, k // 3) if library_slots else k

        if focus and not include_library:
            semantic = self._semantic_search(
                query,
                k,
                where={"source": {"$in": focus}},
                score_boost=0.15,
            )
        elif focus:
            focused = self._semantic_search(
                query,
                focus_k,
                where={"source": {"$in": focus}},
                score_boost=0.15,
            )
            library_reg = self._retrieve_library_regulatory(query, focus_set, slots=library_slots)
            broad = self._semantic_search(query, k)
            library_broad = [c for c in broad if c.get("source") not in focus_set][: max(4, library_slots // 3)]
            semantic = self._merge_chunks(focused, library_reg, library_broad, broad, limit=k)
        else:
            semantic = self._semantic_search(query, k)

        section_refs = self._section_refs(query, explicit_sections)
        if focus:
            for sec in sections_from_filenames(focus):
                if sec not in section_refs:
                    section_refs.append(sec)

        if section_refs:
            section_chunks = self._chunks_by_sections(
                section_refs,
                focus_sources=focus or None,
                include_library=include_library,
            )
            if focus and not section_chunks:
                section_chunks = self._chunks_by_sections(
                    section_refs,
                    focus_sources=None,
                    include_library=True,
                )
            section_chunks = self._expand_section_neighbors(section_chunks)
            extra = min(len(section_chunks), settings.section_search_limit)
            semantic = self._merge_chunks(section_chunks, semantic, limit=k + extra)

        page_refs = self._page_refs(query)
        if page_refs:
            page_chunks = self._chunks_by_pages(page_refs)
            if focus:
                page_chunks = [c for c in page_chunks if c["source"] in focus_set] + [
                    c for c in page_chunks if c["source"] not in focus_set
                ]
            return self._merge_chunks(page_chunks, semantic, limit=k + len(page_refs))

        return semantic

    def query(
        self,
        question: str,
        top_k: int | None = None,
        focus_sources: list[str] | None = None,
        include_library: bool = True,
        explicit_sections: list[str] | None = None,
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

        candidates = self.retrieve(
            question,
            top_k=top_k,
            focus_sources=focus_sources,
            include_library=include_library,
            explicit_sections=explicit_sections,
        )
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

        context, selected, pack_warnings = pack_chunks_for_llm(
            candidates,
            focus_sources=focus_sources,
            min_library_chunks=(
                settings.min_library_chunks_in_context
                if focus_sources and include_library
                else 0
            ),
            max_focus_chunks_per_source=(
                settings.max_focus_chunks_per_source if focus_sources else settings.max_chunks_per_source
            ),
        )
        citations = citations_from_chunks(selected)
        answer = generate_answer(question, context, history=history, citations=citations)
        sources = sorted({c["source"] for c in selected})

        pack_warnings = list(pack_warnings)
        if focus_sources and include_library:
            lib_in_selected = [c for c in selected if c.get("source") not in set(focus_sources)]
            lib_names = sorted({c.get("source") for c in lib_in_selected})
            if lib_names:
                pack_warnings.append(
                    "Document Library sources in this answer: "
                    + ", ".join(lib_names[:8])
                    + ("…" if len(lib_names) > 8 else "")
                )
            elif question_needs_library_regulations(question):
                pack_warnings.append(
                    "No Document Library (UFC/ER/US Code) sections fit the context budget — "
                    "try a follow-up question asking only about applicable USACE/UFC requirements."
                )

        section_hits = self._section_refs(question, explicit_sections)
        if focus_sources:
            for sec in sections_from_filenames(focus_sources):
                if sec not in section_hits:
                    section_hits.append(sec)
        if section_hits:
            section_in_context = {
                norm
                for c in selected
                for norm in [normalize_section_number(c.get("spec_section") or "")]
                + extract_section_numbers(c.get("text") or "")
                if norm
            }
            missing = [s for s in section_hits if s not in section_in_context]
            if missing:
                pack_warnings.append(
                    "Section lookup requested "
                    + ", ".join(missing)
                    + " — re-upload spec PDFs to refresh section tags if results look incomplete."
                )
            else:
                pack_warnings.append(
                    "Retrieved specification section(s): " + ", ".join(section_hits) + "."
                )

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
