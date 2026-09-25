#!/usr/bin/env python3
"""Checks for the local-model path that do not need Ollama running.

    python3 scripts/test_local_model.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="spk-local-test-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["CHROMA_PERSIST_DIR"] = str(_TMP / "chroma")
os.environ["OPENAI_API_KEY"] = "ollama"
os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:11434/v1"

from app.config import settings  # noqa: E402
from app.embeddings import prepare_embedding_inputs  # noqa: E402
from app.llm import vision_model_name  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


settings.openai_embedding_model = "nomic-embed-text"
check(
    "indexed text gets the document prefix",
    prepare_embedding_inputs(["Door width 36 in"], query=False)
    == ["search_document: Door width 36 in"],
)
check(
    "a question gets the query prefix",
    prepare_embedding_inputs(["minimum door width"], query=True)
    == ["search_query: minimum door width"],
)

settings.openai_embedding_model = "text-embedding-3-small"
check(
    "OpenAI embeddings are not prefixed",
    prepare_embedding_inputs(["minimum door width"], query=True) == ["minimum door width"],
)

settings.openai_model = "qwen2.5:7b-instruct"
settings.openai_vision_model = ""
check("vision falls back to the chat model", vision_model_name() == "qwen2.5:7b-instruct")

settings.openai_vision_model = "qwen2.5vl:3b"
check("a separate vision model is used when set", vision_model_name() == "qwen2.5vl:3b")

print(f"\n{PASSED} passed, {len(FAILED)} failed")
raise SystemExit(1 if FAILED else 0)
