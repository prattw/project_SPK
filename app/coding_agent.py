"""Experimental local coding agent (off by default; see ENABLE_CODING_AGENT).

A ReAct-style tool-calling loop, structurally similar to app/agent.py, but
aimed at writing and *running* code instead of searching documents. The model
can write files into a scratch workspace, list/read what's there, and execute
shell commands — so it can actually test what it wrote (run a script, run a
test, check a linter) and fix mistakes before handing the result back, rather
than handing over a single untested guess the way a plain chat answer does.

Safety model (read before enabling): `run_command` executes real shell
commands with the same OS permissions as whatever account runs this server
process. There is no container or VM sandbox — this mirrors how Cursor's own
agent runs commands directly on your machine, not a hosted, multi-tenant
code-execution service. The only real boundaries here are:
  - a dedicated per-run working directory (so generated files land somewhere
    predictable and downloadable, not scattered across the filesystem),
  - a command timeout, so a hung process can't block the run forever, and
  - output truncation, so a runaway command can't exhaust memory.
A command is free to `cd` elsewhere, read/write anywhere the OS user can, or
use the network if it's reachable. Only enable ENABLE_CODING_AGENT on a
personal, single-user, local/offline machine — never on a shared or
internet-facing deployment. app/main.py additionally refuses to start this
agent whenever the access roster is enabled, as defense in depth.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import settings
from app.llm import chat_with_tools

CODING_AGENT_SYSTEM_PROMPT = """You are a local, offline coding agent for Project SPK, running entirely on this \
laptop — no internet, no OpenAI API, nothing leaves this machine unless a command you run explicitly reaches out. \
You help a USACE user write and run scripts/small programs, including for disconnected or field (deployed) \
conditions where there may be no internet access at all.

You have a scratch workspace directory on disk and three tools: write a file into it, list what's there, read a \
file back, and run a shell command (its working directory is the workspace). Use them like a real engineer would:

1. Write the code to a file (do not just print code in your final answer — write it with write_file).
2. Run it (or run a quick test/sanity check) with run_command to see the ACTUAL output, not a guess.
3. If it fails or the output looks wrong, fix the file and run it again. Keep iterating until it actually works.
4. Only give your final answer once you've verified the code runs successfully, or you've hit a real blocker you \
can't resolve yourself (missing credentials, a package that needs internet access you don't have, etc.) — in that \
case say exactly what's blocking it.

Rules:
- Prefer the standard library over third-party packages. If you do need to `pip install` something, say so plainly \
in your final answer and flag that it requires internet access — this machine may be offline when the user \
actually runs it.
- Never invent command output. Only describe what a tool actually returned.
- Keep code commented and readable for someone who may not be a professional developer.
- Do not run destructive commands (formatting drives, deleting anything outside the workspace, modifying system \
configuration) — stay inside the workspace directory for file operations.
- When you're done, give a final plain-text answer (no further tool call) that states: exactly which file(s) you \
wrote, the exact command to run them, any dependency that needs installing first, and a one-line summary of what \
you verified actually happened when you ran it."""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write (or overwrite) a text file in the scratch workspace. Creates parent "
                "directories as needed. Use a relative path like 'script.py' or 'src/main.py'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path within the workspace, e.g. 'script.py'.",
                    },
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read back a text file already written in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within the workspace."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List every file currently in the workspace, with size in bytes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command (bash) with its working directory set to the workspace. "
                "Returns stdout, stderr, and the exit code. Use this to actually execute what you "
                "wrote — run the script, run a test, check `--help`, etc. — never assume it works."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."},
                    "timeout_seconds": {
                        "type": "integer",
                        "description": (
                            f"Max seconds to wait (default/cap {settings.coding_agent_command_timeout_s})."
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
]

_PATH_SAFE = re.compile(r"^[^\x00]+$")  # just rejects embedded NUL; real containment check is below
MAX_WRITE_CHARS = 2_000_000  # 2MB of text per file — generous for scripts, bounds disk usage


def workspace_dir(job_id: str) -> Path:
    base = settings.data_path / "_agent_output" / "code_workspace" / job_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_in_workspace(workspace: Path, rel_path: str) -> Path | None:
    """Resolve a model-supplied relative path, rejecting anything that escapes the workspace."""
    if not rel_path or not _PATH_SAFE.match(rel_path):
        return None
    candidate = (workspace / rel_path).resolve()
    workspace_resolved = workspace.resolve()
    try:
        candidate.relative_to(workspace_resolved)
    except ValueError:
        return None
    return candidate


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated, {len(text) - limit} more characters]"


def _tool_write_file(workspace: Path, args: dict[str, Any]) -> dict[str, Any]:
    rel_path = str(args.get("path") or "").strip()
    content = args.get("content")
    if content is None:
        return {"error": "content is required"}
    content = str(content)
    if len(content) > MAX_WRITE_CHARS:
        return {"error": f"content too large ({len(content)} chars, max {MAX_WRITE_CHARS})"}
    target = _resolve_in_workspace(workspace, rel_path)
    if target is None:
        return {"error": f"invalid or out-of-workspace path: {rel_path!r}"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"saved_as": str(target.relative_to(workspace)), "bytes": len(content.encode("utf-8"))}


def _tool_read_file(workspace: Path, args: dict[str, Any]) -> dict[str, Any]:
    rel_path = str(args.get("path") or "").strip()
    target = _resolve_in_workspace(workspace, rel_path)
    if target is None:
        return {"error": f"invalid or out-of-workspace path: {rel_path!r}"}
    if not target.is_file():
        return {"error": f"no such file in workspace: {rel_path!r}"}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": str(exc)}
    return {"path": rel_path, "content": _truncate(content, settings.coding_agent_max_output_chars * 2)}


def _tool_list_files(workspace: Path, _: dict[str, Any]) -> dict[str, Any]:
    files = []
    for p in sorted(workspace.rglob("*")):
        if p.is_file():
            files.append({"path": str(p.relative_to(workspace)), "bytes": p.stat().st_size})
    return {"files": files}


def _tool_run_command(workspace: Path, args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"error": "command is required"}
    requested_timeout = args.get("timeout_seconds")
    try:
        timeout = int(requested_timeout) if requested_timeout else settings.coding_agent_command_timeout_s
    except (TypeError, ValueError):
        timeout = settings.coding_agent_command_timeout_s
    timeout = max(1, min(timeout, settings.coding_agent_command_timeout_s))

    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": _truncate(proc.stdout, settings.coding_agent_max_output_chars),
            "stderr": _truncate(proc.stderr, settings.coding_agent_max_output_chars),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": None,
            "stdout": _truncate(exc.stdout or "", settings.coding_agent_max_output_chars),
            "stderr": _truncate(exc.stderr or "", settings.coding_agent_max_output_chars),
            "timed_out": True,
            "error": f"command timed out after {timeout}s",
        }
    except OSError as exc:
        return {"error": str(exc)}


def _summarize_result(tool: str, result: dict[str, Any]) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    if tool == "write_file":
        return f"wrote {result.get('saved_as')} ({result.get('bytes')} bytes)"
    if tool == "read_file":
        return f"read {result.get('path')}"
    if tool == "list_files":
        return f"{len(result.get('files', []))} file(s) in workspace"
    if tool == "run_command":
        if result.get("timed_out"):
            return "command timed out"
        return f"exit code {result.get('exit_code')}"
    return "done"


def _history_messages(history: list[dict[str, str]] | None, limit: int = 8) -> list[dict[str, Any]]:
    if not history:
        return []
    out: list[dict[str, Any]] = []
    for item in history[-limit:]:
        role = item.get("role")
        content = (item.get("content") or item.get("text") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def run_coding_agent(
    question: str,
    *,
    job_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the write/execute/iterate coding agent loop in its own workspace dir.

    Returns {"answer": str, "steps": [...], "files": [{"path", "bytes"}], "job_id": str}.
    `job_id` doubles as both the job identifier and the workspace folder name so
    generated files can be downloaded later via that id; pass one in if the
    caller already created a job, otherwise one is generated.
    """
    job_id = job_id or uuid.uuid4().hex
    workspace = workspace_dir(job_id)

    tool_impls: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "write_file": lambda a: _tool_write_file(workspace, a),
        "read_file": lambda a: _tool_read_file(workspace, a),
        "list_files": lambda a: _tool_list_files(workspace, a),
        "run_command": lambda a: _tool_run_command(workspace, a),
    }

    messages: list[dict[str, Any]] = [{"role": "system", "content": CODING_AGENT_SYSTEM_PROMPT}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": question})

    steps: list[dict[str, Any]] = []

    def finalize(answer: str) -> dict[str, Any]:
        files = _tool_list_files(workspace, {}).get("files", [])
        return {"answer": answer, "steps": steps, "files": files, "job_id": job_id}

    for _ in range(max(1, settings.coding_agent_max_steps)):
        message = chat_with_tools(messages, TOOLS)
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if not tool_calls:
            return finalize((message.content or "").strip())

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}

            impl = tool_impls.get(name)
            t0 = time.time()
            if impl is None:
                result: dict[str, Any] = {"error": f"Unknown tool '{name}'"}
            else:
                try:
                    result = impl(args)
                except Exception as exc:  # noqa: BLE001 — surface to the model, don't crash the agent
                    result = {"error": str(exc)}
            elapsed_ms = int((time.time() - t0) * 1000)

            step = {
                "tool": name,
                "args": {k: (v if k != "content" else f"<{len(str(v))} chars>") for k, v in args.items()},
                "elapsed_ms": elapsed_ms,
                "ok": "error" not in result,
                "summary": _summarize_result(name, result),
            }
            steps.append(step)
            if on_step:
                on_step(step)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result)[: settings.coding_agent_max_output_chars + 500],
                }
            )

    messages.append(
        {
            "role": "user",
            "content": (
                "You've used all available tool calls for this run. Give your best final answer now, in "
                "plain text, including which file(s) you wrote and how to run them."
            ),
        }
    )
    message = chat_with_tools(messages, [])
    return finalize((message.content or "").strip())
