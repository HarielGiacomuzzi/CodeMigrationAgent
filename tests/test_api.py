import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import main
from tests.fakes import SOURCE, FakeLLM

PAYLOAD = {"files": SOURCE, "source_framework": "flask", "target_framework": "fastapi"}


class SlowAnalyzeLLM(FakeLLM):
    """Delays analyze() so a keepalive tick fires before the next real event."""

    async def analyze(self, files, source, target):
        await asyncio.sleep(0.05)
        return await super().analyze(files, source, target)


@pytest.fixture
def use_llm():
    def install(llm):
        main.app.dependency_overrides[main.get_llm] = lambda: llm
        return TestClient(main.app)

    yield install
    main.app.dependency_overrides.clear()


def test_health(use_llm):
    assert use_llm(FakeLLM()).get("/health").json() == {"status": "ok"}


def test_migrate_returns_full_result(use_llm):
    response = use_llm(FakeLLM()).post("/migrate", json=PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["migrated_files"]["app.py"].startswith("# s2")
    assert [s["status"] for s in body["plan"]] == ["completed", "completed"]
    assert body["verification"]["passed"] is True
    assert body["errors"] == []
    assert "+# s2" in body["diffs"]["app.py"]


def test_llm_failure_is_reported_not_500(use_llm):
    response = use_llm(FakeLLM(fail_analysis=True)).post("/migrate", json=PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False and body["phase"] == "failed"
    assert body["errors"] == ["analysis failed: analysis exploded"]


@pytest.mark.parametrize(
    "payload",
    [
        {**PAYLOAD, "files": {}},
        {**PAYLOAD, "files": {"../etc/passwd": "x"}},
        {**PAYLOAD, "files": {"/abs.py": "x"}},
        {**PAYLOAD, "files": {"  ": "x"}},
        {**PAYLOAD, "files": {"big.py": "x" * 500_001}},
        {**PAYLOAD, "files": {f"f{i}.py": "" for i in range(51)}},
        {**PAYLOAD, "source_framework": ""},
        {**PAYLOAD, "target_framework": "Flask", "source_framework": "flask"},
    ],
)
def test_invalid_requests_rejected_before_any_llm_call(use_llm, payload):
    llm = FakeLLM()
    assert use_llm(llm).post("/migrate", json=payload).status_code == 422
    assert llm.executed == []


def test_stream_emits_phase_events_then_result(use_llm):
    with use_llm(FakeLLM()).stream("POST", "/migrate/stream", json=PAYLOAD) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    phases = [e["phase"] for e in events if e["type"] == "phase"]
    assert phases == ["analysis", "planning", "execution", "verification", "done"]
    assert {e["type"] for e in events} >= {"analysis", "plan", "step"}
    assert events[-1]["type"] == "result" and events[-1]["result"]["success"] is True


def test_stream_headers_disable_caching_and_proxy_buffering(use_llm):
    with use_llm(FakeLLM()).stream("POST", "/migrate/stream", json=PAYLOAD) as response:
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        for _ in response.iter_lines():
            pass


def test_stream_sends_keepalive_ping_when_llm_is_slow(use_llm, monkeypatch):
    monkeypatch.setattr(main, "KEEPALIVE_SECONDS", 0.01)
    with use_llm(SlowAnalyzeLLM()).stream("POST", "/migrate/stream", json=PAYLOAD) as response:
        raw = response.read().decode()
    assert ": ping" in raw
    lines = [line for line in raw.splitlines() if line.startswith("data: ")]
    assert json.loads(lines[-1][6:])["type"] == "result"
