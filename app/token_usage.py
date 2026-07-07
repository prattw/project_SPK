from __future__ import annotations

import contextvars

from app.usage import TokenTotals

_current: contextvars.ContextVar[TokenTotals | None] = contextvars.ContextVar(
    "token_usage", default=None
)


def start_tracking() -> TokenTotals:
    totals = TokenTotals()
    _current.set(totals)
    return totals


def get_tracking() -> TokenTotals:
    return _current.get() or TokenTotals()


def record_chat_usage(*, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    totals = _current.get()
    if not totals:
        return
    totals.prompt_tokens += max(0, prompt_tokens)
    totals.completion_tokens += max(0, completion_tokens)


def record_embedding_tokens(count: int) -> None:
    totals = _current.get()
    if not totals:
        return
    totals.embedding_tokens += max(0, count)
