"""Unit tests for app/coding_agent.py.

The LLM itself (chat_with_tools) is mocked throughout — these tests exercise
the agent's control loop, tool implementations, and safety boundaries
(workspace path containment, command timeouts, step-limit termination)
without needing a real model or network access.

Run with:  python -m pytest tests/test_coding_agent.py -v
"""
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app import coding_agent as ca
from app.config import settings


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    content: str | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)


def tool_call(name: str, arguments: dict, call_id: str | None = None) -> FakeToolCall:
    return FakeToolCall(
        id=call_id or f"call_{uuid.uuid4().hex[:8]}",
        function=FakeFunction(name=name, arguments=json.dumps(arguments)),
    )


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """Point settings.data_dir at a throwaway directory for the whole test."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


@pytest.fixture()
def job_id():
    return f"test-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# workspace_dir / path containment
# ---------------------------------------------------------------------------


def test_workspace_dir_is_created_under_data_path(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    assert ws.is_dir()
    assert ws == tmp_data_dir / "_agent_output" / "code_workspace" / job_id


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "sub/../../escape.txt",
        "/etc/passwd",
        "",
    ],
)
def test_resolve_in_workspace_rejects_path_traversal(tmp_data_dir, job_id, bad_path):
    ws = ca.workspace_dir(job_id)
    assert ca._resolve_in_workspace(ws, bad_path) is None


def test_resolve_in_workspace_accepts_nested_relative_path(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    resolved = ca._resolve_in_workspace(ws, "src/main.py")
    assert resolved is not None
    assert resolved.parent.parent == ws.resolve()


def test_write_file_tool_rejects_traversal(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    result = ca._tool_write_file(ws, {"path": "../escape.txt", "content": "x"})
    assert "error" in result
    assert not (ws.parent / "escape.txt").exists()


def test_read_file_tool_rejects_traversal(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    # Plant a real file just outside the workspace to make sure it's truly unreachable.
    outside = ws.parent / "secret.txt"
    outside.write_text("do not read me")
    result = ca._tool_read_file(ws, {"path": "../secret.txt"})
    assert "error" in result


def test_write_then_read_file_roundtrip(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    write_result = ca._tool_write_file(ws, {"path": "hello.py", "content": "print('hi')\n"})
    assert write_result["saved_as"] == "hello.py"
    read_result = ca._tool_read_file(ws, {"path": "hello.py"})
    assert read_result["content"] == "print('hi')\n"


def test_write_file_rejects_oversized_content(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    huge = "x" * (ca.MAX_WRITE_CHARS + 1)
    result = ca._tool_write_file(ws, {"path": "big.txt", "content": huge})
    assert "error" in result


def test_list_files_reports_everything_written(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    ca._tool_write_file(ws, {"path": "a.py", "content": "pass"})
    ca._tool_write_file(ws, {"path": "sub/b.py", "content": "pass"})
    result = ca._tool_list_files(ws, {})
    paths = {f["path"] for f in result["files"]}
    assert paths == {"a.py", "sub/b.py"}


# ---------------------------------------------------------------------------
# run_command: real subprocess behavior (safe, local-only commands)
# ---------------------------------------------------------------------------


def test_run_command_captures_stdout_and_exit_code(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    result = ca._tool_run_command(ws, {"command": "echo hello"})
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]
    assert result["timed_out"] is False


def test_run_command_captures_nonzero_exit_and_stderr(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    result = ca._tool_run_command(ws, {"command": "echo oops 1>&2; exit 7"})
    assert result["exit_code"] == 7
    assert "oops" in result["stderr"]


def test_run_command_runs_with_cwd_set_to_workspace(tmp_data_dir, job_id):
    ws = ca.workspace_dir(job_id)
    ca._tool_write_file(ws, {"path": "marker.txt", "content": "present"})
    result = ca._tool_run_command(ws, {"command": "cat marker.txt"})
    assert "present" in result["stdout"]


def test_run_command_enforces_timeout(tmp_data_dir, job_id, monkeypatch):
    ws = ca.workspace_dir(job_id)
    monkeypatch.setattr(settings, "coding_agent_command_timeout_s", 1)
    result = ca._tool_run_command(ws, {"command": "sleep 5"})
    assert result["timed_out"] is True
    assert result.get("exit_code") is None


def test_run_command_clamps_requested_timeout_to_server_max(tmp_data_dir, job_id, monkeypatch):
    ws = ca.workspace_dir(job_id)
    monkeypatch.setattr(settings, "coding_agent_command_timeout_s", 1)
    # Model asks for 999s; server must clamp to the configured max (1s) and still time out quickly.
    result = ca._tool_run_command(ws, {"command": "sleep 5", "timeout_seconds": 999})
    assert result["timed_out"] is True


def test_run_command_truncates_huge_output(tmp_data_dir, job_id, monkeypatch):
    ws = ca.workspace_dir(job_id)
    monkeypatch.setattr(settings, "coding_agent_max_output_chars", 100)
    result = ca._tool_run_command(ws, {"command": "python3 -c \"print('x' * 5000)\""})
    assert len(result["stdout"]) < 5000
    assert "truncated" in result["stdout"]


# ---------------------------------------------------------------------------
# run_coding_agent: full ReAct loop, with chat_with_tools mocked
# ---------------------------------------------------------------------------


def test_agent_loop_writes_runs_and_gives_final_answer(tmp_data_dir, monkeypatch):
    responses = [
        FakeMessage(
            content="",
            tool_calls=[tool_call("write_file", {"path": "greet.py", "content": "print('hi')"})],
        ),
        FakeMessage(content="", tool_calls=[tool_call("run_command", {"command": "python3 greet.py"})]),
        FakeMessage(content="Wrote greet.py and verified it prints 'hi'. Run with: python3 greet.py"),
    ]
    call_log = []

    def fake_chat_with_tools(messages, tools, **kwargs):
        call_log.append((len(messages), len(tools)))
        return responses[len(call_log) - 1]

    monkeypatch.setattr(ca, "chat_with_tools", fake_chat_with_tools)

    steps_seen = []
    result = ca.run_coding_agent("write a script that prints hi", on_step=steps_seen.append)

    assert "verified it prints" in result["answer"]
    assert len(result["steps"]) == 2
    assert result["steps"][0]["tool"] == "write_file"
    assert result["steps"][0]["ok"] is True
    assert result["steps"][1]["tool"] == "run_command"
    assert any(f["path"] == "greet.py" for f in result["files"])
    assert len(steps_seen) == 2  # on_step fired once per tool call

    # The file really exists on disk under this job's workspace.
    ws = ca.workspace_dir(result["job_id"])
    assert (ws / "greet.py").read_text() == "print('hi')"


def test_agent_loop_handles_model_requesting_unknown_tool(tmp_data_dir, monkeypatch):
    responses = [
        FakeMessage(content="", tool_calls=[tool_call("delete_everything", {})]),
        FakeMessage(content="I don't have that tool; here's what I can do instead."),
    ]

    def fake_chat_with_tools(messages, tools, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(ca, "chat_with_tools", fake_chat_with_tools)

    result = ca.run_coding_agent("do something unsupported")
    assert result["steps"][0]["tool"] == "delete_everything"
    assert result["steps"][0]["ok"] is False
    assert "unknown tool" in result["steps"][0]["summary"].lower()


def test_agent_loop_stops_at_max_steps_and_asks_for_final_answer(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(settings, "coding_agent_max_steps", 2)

    def always_call_run_command(messages, tools, **kwargs):
        if not tools:
            # Final forced call (no tools passed) — simulate giving up gracefully.
            return FakeMessage(content="Ran out of steps; here's my best attempt so far.")
        return FakeMessage(content="", tool_calls=[tool_call("run_command", {"command": "echo loop"})])

    monkeypatch.setattr(ca, "chat_with_tools", always_call_run_command)

    result = ca.run_coding_agent("loop forever")
    assert len(result["steps"]) == 2  # bounded by coding_agent_max_steps
    assert "ran out of steps" in result["answer"].lower()


def test_agent_loop_recovers_from_tool_exception(tmp_data_dir, monkeypatch):
    def raise_boom(ws, a):
        raise RuntimeError("boom")

    monkeypatch.setattr(ca, "_tool_write_file", raise_boom)

    responses = [
        FakeMessage(content="", tool_calls=[tool_call("write_file", {"path": "x.py", "content": "pass"})]),
        FakeMessage(content="Hit an error writing the file."),
    ]

    def fake_chat_with_tools(messages, tools, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(ca, "chat_with_tools", fake_chat_with_tools)

    # run_coding_agent builds its tool_impls dict referencing the (now-patched)
    # module-level functions via closures created at call time, so the patch
    # above takes effect for this call.
    result = ca.run_coding_agent("write something that will fail")
    assert result["steps"][0]["ok"] is False
    assert "boom" in result["steps"][0]["summary"]


def test_agent_job_id_is_reused_as_workspace_name(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(
        ca, "chat_with_tools", lambda messages, tools, **kwargs: FakeMessage(content="done, nothing written")
    )
    result = ca.run_coding_agent("no-op request", job_id="fixed-job-123")
    assert result["job_id"] == "fixed-job-123"
    assert ca.workspace_dir("fixed-job-123").exists()
