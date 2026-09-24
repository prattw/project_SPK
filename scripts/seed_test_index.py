#!/usr/bin/env python3
"""Seed a throwaway Chroma index with representative library filenames.

Used to exercise the Document Library index pages (/library/groups) without an
OpenAI key: chunks are inserted with dummy embeddings, so only metadata matters.

    CHROMA_PERSIST_DIR=./chroma_test python3 scripts/seed_test_index.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.doc_metadata import infer_doc_metadata  # noqa: E402
from app.rag import get_rag  # noqa: E402

SAMPLES = [
    # Engineering
    "ER 1110-2-1150.pdf",
    "EM 1110-2-2704.pdf",
    "EP 1100-2-1.pdf",
    "EC 1110-2-6077.pdf",
    "ETL 1110-2-586.pdf",
    "ecb2026-12.pdf",
    "UFC 3-301-01.pdf",
    "tspwg_3-250-04_05-8.pdf",
    "MIL-STD-3007.pdf",
    "TM 5-300.pdf",
    "spaceplanning_healthfac_110_dec_2022.pdf",
    "CECW-2018-08.pdf",
    "OM 37-345-1.pdf",
    "PN 1110-1-1.pdf",
    "ARN15118_AR 420-1_FINAL.pdf",  # engineering AR series
    "ARN30886-PAM 385-16-000-WEB-1.pdf",  # engineering PAM series
    # Contracting & law
    "FAR.pdf",
    "DFARS.pdf",
    "AFARS.pdf",
    "pgi-part-215.pdf",
    "USC01@119-88.pdf",
    "Official_UAI UDG_Revision 18 June 2024.pdf",
    "Attach-B_IDAC-UAI-clause_final_kb.pdf",
    "ARN43758-AR 27-1-000-WEB-1.pdf",  # legal services AR
    "ARN39012-PAM 11-2-000-WEB-1.pdf",  # management PAM
    # Discipline knowledge
    "Sec02-Accounting Principles (2).pdf",
    "004 FY26 Student Slides.pdf",
    "414526m_c2.pdf",
]


def main() -> int:
    rag = get_rag()
    collection = rag._collection  # noqa: SLF001 — test seeding only
    stamp = datetime.now(tz=timezone.utc).isoformat()

    ids, docs, metas, embeddings = [], [], [], []
    for index, source in enumerate(SAMPLES):
        meta = {k: v for k, v in infer_doc_metadata(source).items() if v}
        meta.update({"source": source, "chunk_index": 0, "indexed_at": stamp, "upload_origin": "library"})
        ids.append(f"seed-{index}")
        docs.append(f"Placeholder text for {source}.")
        metas.append(meta)
        embeddings.append([0.0] * 8)

    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
    rag._invalidate_caches()  # noqa: SLF001
    print(f"Seeded {len(ids)} documents into {collection.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
