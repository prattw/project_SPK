"""Smoke tests for the /agent/code/* HTTP wiring in app/main.py.

These exercise the actual FastAPI routes end-to-end (via TestClient), with
the coding-agent loop itself and OpenAI calls mocked out, so they verify the
wiring (feature flag, roster double-gate, job lifecycle, file download path
safety) without needing a real OPENAI_API_KEY or network access.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import jobs as jobs_module
from app.config import settings
from app.main import app
from app.usage import init_usage_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "usage_db_path", str(tmp_path / "usage.db"))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    init_usage_db()
    return TestClient(app)


def _enable_coding_agent(monkeypatch):
    monkeypatch.setattr(settings, "enable_coding_agent", True)
    monkeypatch.setattr(settings, "access_roster", "")  # roster off → not a shared deployment


def test_health_reports_coding_agent_disabled_by_default(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["coding_agent_enabled"] is False


def test_health_reports_coding_agent_enabled_when_flag_and_roster_allow(client, monkeypatch):
    _enable_coding_agent(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["coding_agent_enabled"] is True


def test_health_stays_disabled_if_roster_is_on_even_with_flag(client, monkeypatch):
    # Defense in depth: flag alone must not be enough on a roster-gated (shared) deployment.
    monkeypatch.setattr(settings, "enable_coding_agent", True)
    monkeypatch.setattr(settings, "access_roster", "someone@usace.army.mil")
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["coding_agent_enabled"] is False


def test_agent_code_run_404_when_disabled(client, monkeypatch):
    # No access roster, so no auth is required — isolates the feature-flag check itself.
    monkeypatch.setattr(settings, "access_roster", "")
    resp = client.post("/agent/code/run", json={"question": "write a hello world script"})
    assert resp.status_code == 404
    assert "ENABLE_CODING_AGENT" in resp.json()["detail"]


def test_agent_code_run_404_when_roster_enabled_despite_flag(client, monkeypatch):
    # Authenticate via APP_API_KEY so the roster-gate check (not auth) is what's under test.
    monkeypatch.setattr(settings, "enable_coding_agent", True)
    monkeypatch.setattr(settings, "access_roster", "someone@usace.army.mil")
    monkeypatch.setattr(settings, "app_api_key", "test-app-api-key")
    resp = client.post(
        "/agent/code/run",
        json={"question": "write a hello world script"},
        headers={"X-API-Key": "test-app-api-key"},
    )
    assert resp.status_code == 404
    assert "roster" in resp.json()["detail"].lower()


def test_agent_code_run_starts_job_and_job_status_reports_files(client, monkeypatch):
    _enable_coding_agent(monkeypatch)

    def fake_run_coding_agent(question, *, job_id=None, history=None, on_step=None):
        if on_step:
            on_step({"tool": "write_file", "args": {}, "elapsed_ms": 1, "ok": True, "summary": "wrote x.py"})
        return {
            "answer": "Wrote x.py — run it with `python3 x.py`.",
            "steps": [{"tool": "write_file", "args": {}, "elapsed_ms": 1, "ok": True, "summary": "wrote x.py"}],
            "files": [{"path": "x.py", "bytes": 42}],
            "job_id": job_id,
        }

    monkeypatch.setattr(jobs_module, "run_coding_agent", fake_run_coding_agent, raising=False)
    # run_coding_agent_job imports app.coding_agent.run_coding_agent locally, so patch it there too.
    import app.coding_agent as coding_agent_module

    monkeypatch.setattr(coding_agent_module, "run_coding_agent", fake_run_coding_agent)

    resp = client.post("/agent/code/run", json={"question": "write a hello world script"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert job_id

    for _ in range(50):
        status_resp = client.get(f"/jobs/{job_id}")
        body = status_resp.json()
        if body["status"] == "done":
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"job never completed: {body}")

    assert body["kind"] == "coding_agent"
    assert body["agent_result"]["answer"].startswith("Wrote x.py")
    assert body["agent_files"] == [{"path": "x.py", "bytes": 42}]

    dl = client.get(f"/agent/code/files/{job_id}/x.py")
    assert dl.status_code == 404  # fake run never actually wrote the file to disk

    workspace = coding_agent_module.workspace_dir(job_id)
    (workspace / "x.py").write_text("print('hi')\n")
    dl = client.get(f"/agent/code/files/{job_id}/x.py")
    assert dl.status_code == 200
    assert dl.content == b"print('hi')\n"


def test_agent_code_files_rejects_path_traversal(client, monkeypatch):
    _enable_coding_agent(monkeypatch)
    import app.coding_agent as coding_agent_module

    fake_job = jobs_module.Job(id="job-traversal", kind="coding_agent", status="done")
    jobs_module._jobs[fake_job.id] = fake_job  # noqa: SLF001 — test-only direct registry insert
    coding_agent_module.workspace_dir(fake_job.id)

    resp = client.get(f"/agent/code/files/{fake_job.id}/../../../../etc/passwd")
    assert resp.status_code in (400, 404)


def test_agent_code_files_404_for_unknown_job(client, monkeypatch):
    _enable_coding_agent(monkeypatch)
    resp = client.get("/agent/code/files/does-not-exist/x.py")
    assert resp.status_code == 404
