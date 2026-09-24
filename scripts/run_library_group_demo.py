#!/usr/bin/env python3
"""Run Project SPK locally with a seeded library to exercise the index pages.

Seeds numbered publications (filed by inference) plus a stand-in "Master Library"
folder filed explicitly onto the Discipline Knowledge page, so the UI can be
checked without an OpenAI key or the production corpus.

    python3 scripts/run_library_group_demo.py [port]
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEMO_USER = "demo.user@usace.army.mil"

_TMP = Path(tempfile.mkdtemp(prefix="spk-lib-demo-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["CHROMA_PERSIST_DIR"] = str(_TMP / "chroma")
os.environ["OPENAI_API_KEY"] = "demo-key-not-used"
os.environ["ACCESS_ROSTER"] = DEMO_USER
os.environ["USAGE_ADMIN_EMAILS"] = DEMO_USER
os.environ["APP_API_KEY"] = ""
os.environ["WARM_INDEX_ON_STARTUP"] = "false"

import app.embeddings as embeddings  # noqa: E402

embeddings.embed_texts = lambda texts: [[0.0] * 8 for _ in texts]
embeddings.embed_query = lambda text: [0.0] * 8

import app.rag as rag_module  # noqa: E402

rag_module.embed_texts = embeddings.embed_texts
rag_module.embed_query = embeddings.embed_query

from app.doc_metadata import infer_doc_metadata  # noqa: E402
from app.library_groups import DISCIPLINE_KNOWLEDGE, GROUP_META_KEY  # noqa: E402
from app.rag import get_rag  # noqa: E402

# Numbered publications — these land on their pages by filename inference.
INFERRED = [
    "ER 1110-2-1150.pdf",
    "ER 1110-1-8162.pdf",
    "EM 1110-2-2704.pdf",
    "EP 1100-2-1.pdf",
    "EC 1110-2-6077.pdf",
    "UFC 3-301-01.pdf",
    "UFC 1-200-01.pdf",
    "MIL-STD-3007.pdf",
    "ARN15118_AR 420-1_FINAL.pdf",
    "FAR.pdf",
    "DFARS.pdf",
    "AFARS.pdf",
    "USC01@119-88.pdf",
    "ARN43758-AR 27-1-000-WEB-1.pdf",
]

# Stand-in for the Master Library folder: no publication numbering to read, so
# these are filed explicitly rather than guessed at.
MASTER_LIBRARY = [
    "Steel Construction Manual 15th Edition.pdf",
    "Concrete Mix Design Fundamentals.pdf",
    "Geotechnical Engineering Handbook.pdf",
    "Hydrology for Civil Engineers.pdf",
    "Structural Dynamics Course Notes.pdf",
    "Soil Mechanics Reference Tables.pdf",
    "Surveying Methods and Practice.pdf",
    "Electrical Systems Design Guide.pdf",
    "HVAC Load Calculation Workbook.pdf",
    "Cost Estimating Field Reference.pdf",
    "Sacramento District Lessons Learned.pdf",
    "Submittal Review Checklist.pdf",
    "UFC 4-010-01 Training Extract.pdf",  # would infer engineering on its own
    "AR 420-1 Class Handout.pdf",  # would infer engineering on its own
]


def seed() -> None:
    rag = get_rag()
    collection = rag._collection  # noqa: SLF001 — demo seeding only
    stamp = datetime.now(tz=timezone.utc).isoformat()

    ids, docs, metas, vectors = [], [], [], []

    def add(index: int, source: str, group: str | None) -> None:
        meta = {k: v for k, v in infer_doc_metadata(source).items() if v}
        meta.update(
            {
                "source": source,
                "chunk_index": 0,
                "indexed_at": stamp,
                "upload_origin": "library",
            }
        )
        if group:
            meta[GROUP_META_KEY] = group
        ids.append(f"seed-{index}")
        docs.append(f"Placeholder text for {source}.")
        metas.append(meta)
        vectors.append([0.0] * 8)

    for i, source in enumerate(INFERRED):
        add(i, source, None)
    for i, source in enumerate(MASTER_LIBRARY, start=len(INFERRED)):
        add(i, source, DISCIPLINE_KNOWLEDGE)

    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=vectors)
    rag._invalidate_caches()  # noqa: SLF001


def main() -> int:
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8020
    seed()

    from app.main import app

    print(f"Seeded {len(INFERRED)} inferred + {len(MASTER_LIBRARY)} explicitly filed documents")
    print(f"Sign in as: {DEMO_USER}")
    print(f"Serving on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
