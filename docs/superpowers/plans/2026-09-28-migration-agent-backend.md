# Migration Agent Backend Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A FastAPI service whose `POST /migrate` runs a 4-phase planning agent (analysis → planning → execution → verification) that migrates source files between frameworks with Claude, plus `POST /migrate/stream` that emits the same run as Server-Sent Events.

**Architecture:** State lives in dataclasses (`agent/state.py`). Claude calls are isolated behind one class, `ClaudeLLM` (`agent/llm.py`), which returns Pydantic models through structured outputs (`client.messages.parse(output_format=...)`). The orchestrator (`agent/workflow.py`) drives the phases, orders plan steps with stdlib `graphlib`, tracks step status, and reports progress through an async `emit` callback. Verification (`agent/verify.py`) combines deterministic syntax checks with a Claude review. Tests swap `ClaudeLLM` for `FakeLLM`, so no test hits the network.

**Tech Stack:** Python ≥3.11 (dev machine has 3.14), FastAPI, uvicorn, `anthropic` SDK ≥0.125 (`AsyncAnthropic`), Pydantic v2, pytest, httpx (TestClient). Stdlib: `dataclasses`, `graphlib`, `difflib`, `asyncio`.

**Spec:** `requirements.md` (repo root). Decisions made with the user: Python + FastAPI; Claude API only (no offline fallback); Railway config + docs instead of a live deploy (Plan 2).

## Global Constraints

- Run command must be `uvicorn main:app --reload` from the repo root, so `main.py` lives at the root.
- State uses `dataclasses` (spec: "State: dataclasses").
- Plan steps carry status from exactly: `pending`, `in_progress`, `completed`, `failed`.
- Agent phases: `analysis`, `planning`, `execution`, `verification` (plus terminal `pending`/`done`/`failed` bookkeeping values).
- `POST /migrate` accepts source files, source framework, target framework. Its JSON response contains: `success`, `migrated_files`, `plan` (executed plan), `verification`, `errors`.
- Model: default `claude-opus-5`, override via env `CLAUDE_MODEL`. Auth via `ANTHROPIC_API_KEY` (SDK default resolution).
- Tests never call the real API. They use `tests/fakes.py::FakeLLM`.
- Commit directly on `main` after each task. Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Virtualenv: `.venv` at the repo root (`python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`). Run tests with `.venv/bin/python -m pytest`.

## Review Focus

1. **The model returns a bad plan** (dependency cycle, duplicate ids, unknown dependency ids, empty plan). Expected: unknown deps are dropped, the others end the run in phase `failed` with a readable error, and the HTTP response is still 200 JSON. No 500. (Task 4 tests.)
2. **A Claude call fails** (no API key, network error, refusal, `max_tokens` truncation). Expected: `success: false`, `phase: "failed"`, and the error text names the phase. (Task 2 tests `LLMError`; Task 4 tests the analysis failure; Task 5 tests it over HTTP.)
3. **One plan step fails mid-run.** Expected: steps that depend on it become `failed` with "dependency … did not complete", independent steps still run, and `success` is false. (Task 4.)
4. **The model produces code that doesn't parse.** Expected: verification fails, and the issue names the file and line. (Task 3.)
5. **Hostile or oversized input** (no files, `../` paths, absolute paths, >500k chars, >50 files, same source and target framework). Expected: 422 before any Claude call. (Task 5.)

---

## File Structure

```
main.py                  FastAPI app: request validation, /migrate, /migrate/stream, /health
agent/__init__.py        empty
agent/state.py           Phase, StepStatus, PlanStep, Verification, MigrationState (+ to_result, diffs)
agent/llm.py             Pydantic output schemas, LLMError, ClaudeLLM (one method per phase)
agent/verify.py          check_syntax (never executes code) + verify(state, llm)
agent/workflow.py        build_plan (graphlib ordering) + run_migration orchestrator
tests/__init__.py        empty
tests/fakes.py           FakeLLM + SOURCE fixture data
tests/test_state.py
tests/test_llm.py
tests/test_verify.py
tests/test_workflow.py
tests/test_api.py
requirements.txt         runtime deps (Railway installs this)
requirements-dev.txt     -r requirements.txt + pytest + httpx
pyproject.toml           pytest config only
.gitignore
```

---

### Task 1: Scaffold + agent state

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `.gitignore`, `agent/__init__.py`, `tests/__init__.py`
- Create: `agent/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Phase(StrEnum)`: `PENDING="pending"`, `ANALYSIS="analysis"`, `PLANNING="planning"`, `EXECUTION="execution"`, `VERIFICATION="verification"`, `DONE="done"`, `FAILED="failed"`
  - `StepStatus(StrEnum)`: `PENDING`, `IN_PROGRESS="in_progress"`, `COMPLETED`, `FAILED`
  - `@dataclass PlanStep(id: str, description: str, files: list[str], depends_on: list[str] = [], complexity: str = "medium", status: StepStatus = PENDING, notes: str = "", error: str | None = None)`
  - `@dataclass Verification(passed: bool, syntax: dict[str, str], issues: list[str], summary: str)`
  - `@dataclass MigrationState(source_files: dict[str, str], source_framework: str, target_framework: str, phase=PENDING, analysis: dict | None = None, plan: list[PlanStep] = [], migrated_files: dict[str, str] = {}, verification: Verification | None = None, errors: list[str] = [])`
  - `MigrationState.current_file(path) -> str`: the migrated version if one exists, else the source, else `""`
  - `MigrationState.diffs() -> dict[str, str]`: unified diff per migrated file
  - `MigrationState.to_result() -> dict`: keys `success, phase, analysis, migrated_files, diffs, plan, verification, errors`

- [ ] **Step 1: Create the scaffold files**

`requirements.txt`:
```
fastapi>=0.115
uvicorn[standard]>=0.30
anthropic>=0.125
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8
httpx>=0.27
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
.env
```

`agent/__init__.py` and `tests/__init__.py`: empty files.

Then run:
```bash
python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
```
Expected: installs without error.

- [ ] **Step 2: Write the failing tests**

`tests/test_state.py`:
```python
import json

from agent.state import MigrationState, Phase, PlanStep, StepStatus, Verification


def make_state():
    return MigrationState({"app.py": "import flask\n"}, "flask", "fastapi")


def test_defaults():
    state = make_state()
    assert state.phase == Phase.PENDING
    assert state.plan == [] and state.migrated_files == {} and state.errors == []
    assert PlanStep("s1", "d", ["app.py"]).status == StepStatus.PENDING


def test_current_file_prefers_migrated_then_source_then_empty():
    state = make_state()
    assert state.current_file("app.py") == "import flask\n"
    state.migrated_files["app.py"] = "import fastapi\n"
    assert state.current_file("app.py") == "import fastapi\n"
    assert state.current_file("new.py") == ""


def test_diffs_show_removed_and_added_lines():
    state = make_state()
    state.migrated_files["app.py"] = "import fastapi\n"
    diff = state.diffs()["app.py"]
    assert "-import flask" in diff and "+import fastapi" in diff
    assert "a/app.py" in diff and "b/app.py" in diff


def test_success_requires_done_passed_and_no_errors():
    state = make_state()
    state.phase = Phase.DONE
    state.verification = Verification(True, {}, [], "ok")
    assert state.to_result()["success"] is True
    state.errors.append("boom")
    assert state.to_result()["success"] is False
    state.errors.clear()
    state.verification.passed = False
    assert state.to_result()["success"] is False


def test_result_is_json_serializable_with_all_keys():
    state = make_state()
    state.plan.append(PlanStep("s1", "d", ["app.py"]))
    result = json.loads(json.dumps(state.to_result()))
    assert set(result) == {"success", "phase", "analysis", "migrated_files", "diffs", "plan", "verification", "errors"}
    assert result["plan"][0]["status"] == "pending"
    assert result["verification"] is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.state'`

- [ ] **Step 4: Implement `agent/state.py`**

```python
"""Agent state carried across the migration phases."""
import difflib
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    PENDING = "pending"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    DONE = "done"
    FAILED = "failed"


class StepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PlanStep:
    id: str
    description: str
    files: list[str]
    depends_on: list[str] = field(default_factory=list)
    complexity: str = "medium"
    status: StepStatus = StepStatus.PENDING
    notes: str = ""
    error: str | None = None


@dataclass
class Verification:
    passed: bool
    syntax: dict[str, str]  # path -> "ok" | "unchecked" | "error: ..."
    issues: list[str]
    summary: str


@dataclass
class MigrationState:
    source_files: dict[str, str]
    source_framework: str
    target_framework: str
    phase: Phase = Phase.PENDING
    analysis: dict | None = None
    plan: list[PlanStep] = field(default_factory=list)
    migrated_files: dict[str, str] = field(default_factory=dict)
    verification: Verification | None = None
    errors: list[str] = field(default_factory=list)

    def current_file(self, path: str) -> str:
        return self.migrated_files.get(path, self.source_files.get(path, ""))

    def diffs(self) -> dict[str, str]:
        return {
            path: "".join(difflib.unified_diff(
                self.source_files.get(path, "").splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            ))
            for path, content in self.migrated_files.items()
        }

    def to_result(self) -> dict:
        return {
            "success": self.phase == Phase.DONE
            and not self.errors
            and bool(self.verification and self.verification.passed),
            "phase": self.phase,
            "analysis": self.analysis,
            "migrated_files": self.migrated_files,
            "diffs": self.diffs(),
            "plan": [asdict(step) for step in self.plan],
            "verification": asdict(self.verification) if self.verification else None,
            "errors": self.errors,
        }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_state.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements-dev.txt pyproject.toml .gitignore agent/__init__.py agent/state.py tests/__init__.py tests/test_state.py
git commit -m "feat: add migration agent state dataclasses

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Claude LLM layer + FakeLLM

**Files:**
- Create: `agent/llm.py`
- Create: `tests/fakes.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `agent.state.PlanStep`
- Produces:
  - Pydantic models: `Analysis(summary: str, patterns: list[str], dependencies: list[str], potential_issues: list[str])`, `PlannedStep(id: str, description: str, files: list[str], depends_on: list[str], complexity: Literal["low","medium","high"])`, `MigrationPlan(steps: list[PlannedStep])`, `FileChange(path: str, content: str)`, `StepResult(files: list[FileChange], notes: str)`, `Review(passed: bool, issues: list[str], summary: str)`
  - `class LLMError(Exception)`
  - `DEFAULT_MODEL = "claude-opus-5"`, `MAX_TOKENS = 16000`
  - `format_files(files: dict[str, str]) -> str`
  - `class ClaudeLLM(client=None, model: str | None = None)` with async methods:
    - `analyze(files: dict[str,str], source: str, target: str) -> Analysis`
    - `plan(files: dict[str,str], analysis: Analysis, source: str, target: str) -> MigrationPlan`
    - `execute_step(step: PlanStep, files: dict[str,str], source: str, target: str, context: str) -> StepResult`
    - `review(files: dict[str,str], source: str, target: str) -> Review`
  - `tests/fakes.py`: `SOURCE: dict[str,str]`, `DEFAULT_STEPS: list[PlannedStep]`, `class FakeLLM(steps=None, fail_steps=(), fail_analysis=False, review_passed=True, bad_output=False)` with the same four async methods and an `executed: list[str]` record of step ids it ran.

Notes for the implementer: `client.messages.parse(..., output_format=Model)` is the SDK's structured-output helper. It returns a `ParsedMessage` with `.stop_reason` and `.parsed_output` (the validated Pydantic instance, or `None`). Always check `stop_reason` before trusting output: `"refusal"` means the model declined, and `"max_tokens"` means truncated output. `AsyncAnthropic()` builds fine without an API key. A missing key only fails when a request is made. The workflow (Task 4) catches that per phase.

- [ ] **Step 1: Write the failing tests**

`tests/test_llm.py`:
```python
import asyncio
from types import SimpleNamespace

import pytest

from agent.llm import DEFAULT_MODEL, Analysis, ClaudeLLM, LLMError, Review, format_files
from agent.state import PlanStep

ANALYSIS = Analysis(summary="s", patterns=["p"], dependencies=["flask"], potential_issues=[])


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def make_llm(stop_reason="end_turn", parsed=ANALYSIS, model=None):
    client = SimpleNamespace(messages=FakeMessages(SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)))
    return ClaudeLLM(client=client, model=model), client.messages


def test_format_files_wraps_each_file_with_its_path():
    text = format_files({"a.py": "x = 1", "b.py": "y = 2"})
    assert '<file path="a.py">\nx = 1\n</file>' in text
    assert '<file path="b.py">' in text


def test_analyze_sends_files_and_schema_and_returns_parsed():
    llm, messages = make_llm()
    result = asyncio.run(llm.analyze({"app.py": "import flask"}, "flask", "fastapi"))
    assert result == ANALYSIS
    call = messages.calls[0]
    assert call["model"] == DEFAULT_MODEL
    assert call["output_format"] is Analysis
    prompt = call["messages"][0]["content"]
    assert "flask" in prompt and "fastapi" in prompt and "import flask" in prompt


def test_model_override_from_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5")
    llm, messages = make_llm()
    asyncio.run(llm.analyze({"a.py": ""}, "x", "y"))
    assert messages.calls[0]["model"] == "claude-sonnet-5"


def test_execute_step_prompt_names_the_step():
    review = Review(passed=True, issues=[], summary="ok")
    llm, messages = make_llm(parsed=review)
    step = PlanStep("s7", "Convert routes to APIRouter", ["app.py"])
    asyncio.run(llm.execute_step(step, {"app.py": "code"}, "flask", "fastapi", "a flask app"))
    prompt = messages.calls[0]["messages"][0]["content"]
    assert "[s7] Convert routes to APIRouter" in prompt and "a flask app" in prompt


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "message"),
    [("refusal", ANALYSIS, "declined"), ("max_tokens", ANALYSIS, "max_tokens"), ("end_turn", None, "no structured output")],
)
def test_unusable_responses_raise_llm_error(stop_reason, parsed, message):
    llm, _ = make_llm(stop_reason=stop_reason, parsed=parsed)
    with pytest.raises(LLMError, match=message):
        asyncio.run(llm.analyze({"a.py": ""}, "x", "y"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.llm'`

- [ ] **Step 3: Implement `agent/llm.py`**

```python
"""Claude-backed reasoning for each migration phase, returned as validated Pydantic models."""
import os
from typing import Literal

import anthropic
from pydantic import BaseModel

from agent.state import PlanStep

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 16000

SYSTEM_PROMPT = (
    "You are a senior engineer migrating code between frameworks. "
    "Preserve behavior exactly, write idiomatic code for the target framework, "
    "and do not invent requirements that are not in the source."
)


class LLMError(Exception):
    """The model returned no usable structured output."""


class Analysis(BaseModel):
    summary: str
    patterns: list[str]
    dependencies: list[str]
    potential_issues: list[str]


class PlannedStep(BaseModel):
    id: str
    description: str
    files: list[str]
    depends_on: list[str]
    complexity: Literal["low", "medium", "high"]


class MigrationPlan(BaseModel):
    steps: list[PlannedStep]


class FileChange(BaseModel):
    path: str
    content: str


class StepResult(BaseModel):
    files: list[FileChange]
    notes: str


class Review(BaseModel):
    passed: bool
    issues: list[str]
    summary: str


def format_files(files: dict[str, str]) -> str:
    return "\n\n".join(f'<file path="{path}">\n{content}\n</file>' for path, content in files.items())


class ClaudeLLM:
    def __init__(self, client=None, model: str | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = model or os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)

    async def _ask(self, prompt: str, schema: type[BaseModel]):
        response = await self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            raise LLMError("model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"response hit max_tokens ({MAX_TOKENS}); migrate fewer or smaller files at once")
        if response.parsed_output is None:
            raise LLMError("model returned no structured output")
        return response.parsed_output

    async def analyze(self, files: dict[str, str], source: str, target: str) -> Analysis:
        return await self._ask(
            f"Analyze this {source} code before migrating it to {target}. "
            "Summarize its structure, list the framework patterns it uses, its dependencies, "
            "and anything likely to be hard to migrate.\n\n" + format_files(files),
            Analysis,
        )

    async def plan(self, files: dict[str, str], analysis: Analysis, source: str, target: str) -> MigrationPlan:
        return await self._ask(
            f"Create a step-by-step plan to migrate this code from {source} to {target}. "
            "Give each step a short unique id (s1, s2, ...), the file paths it creates or edits, "
            "the ids of steps that must finish first, and a complexity estimate. "
            "One step per coherent change, usually 2-6 steps.\n\n"
            f"Analysis:\n{analysis.model_dump_json(indent=2)}\n\n" + format_files(files),
            MigrationPlan,
        )

    async def execute_step(
        self, step: PlanStep, files: dict[str, str], source: str, target: str, context: str
    ) -> StepResult:
        return await self._ask(
            f"You are migrating a project from {source} to {target}. Perform only this step:\n"
            f"[{step.id}] {step.description}\n\n"
            f"Project context: {context}\n\n"
            "The current contents of the files this step touches are below (empty means a new file). "
            "Return the complete new content of every file you change or create.\n\n" + format_files(files),
            StepResult,
        )

    async def review(self, files: dict[str, str], source: str, target: str) -> Review:
        return await self._ask(
            f"Review this code, migrated from {source} to {target}. "
            "Set passed=false only for problems that would break the program or change its behavior. "
            "List those problems, and any leftover use of the source framework, in issues.\n\n"
            + format_files(files),
            Review,
        )
```

- [ ] **Step 4: Create `tests/fakes.py`** (Tasks 3–5 use it)

```python
"""Deterministic stand-in for ClaudeLLM so tests never hit the network."""
from agent.llm import Analysis, FileChange, LLMError, MigrationPlan, PlannedStep, Review, StepResult

SOURCE = {"app.py": "from flask import Flask\napp = Flask(__name__)\n"}

DEFAULT_STEPS = [
    PlannedStep(id="s1", description="Swap imports", files=["app.py"], depends_on=[], complexity="low"),
    PlannedStep(id="s2", description="Convert routes", files=["app.py"], depends_on=["s1"], complexity="medium"),
]


class FakeLLM:
    def __init__(self, steps=None, fail_steps=(), fail_analysis=False, review_passed=True, bad_output=False):
        self.steps = DEFAULT_STEPS if steps is None else steps
        self.fail_steps = set(fail_steps)
        self.fail_analysis = fail_analysis
        self.review_passed = review_passed
        self.bad_output = bad_output
        self.executed: list[str] = []

    async def analyze(self, files, source, target):
        if self.fail_analysis:
            raise LLMError("analysis exploded")
        return Analysis(summary=f"{source} app", patterns=["routes"], dependencies=[source], potential_issues=[])

    async def plan(self, files, analysis, source, target):
        return MigrationPlan(steps=self.steps)

    async def execute_step(self, step, files, source, target, context):
        self.executed.append(step.id)
        if step.id in self.fail_steps:
            raise LLMError(f"{step.id} exploded")
        tail = "def broken(:\n" if self.bad_output else ""
        return StepResult(
            files=[FileChange(path=p, content=f"# {step.id}\n{c}{tail}") for p, c in files.items()],
            notes=f"did {step.id}",
        )

    async def review(self, files, source, target):
        issues = [] if self.review_passed else ["still imports flask"]
        return Review(passed=self.review_passed, issues=issues, summary="reviewed")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add agent/llm.py tests/fakes.py tests/test_llm.py
git commit -m "feat: add Claude structured-output layer for migration phases

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Verification

**Files:**
- Create: `agent/verify.py`
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `MigrationState`, `Verification`, `StepStatus` (Task 1); an llm with `review(files, source, target) -> Review` (Task 2 / `FakeLLM`)
- Produces:
  - `check_syntax(path: str, content: str) -> str`: returns `"ok"`, `"unchecked"`, or `"error: <detail>"`. Checks `.py` (via `compile`), `.json`, and `.js/.mjs/.cjs` (via `node --check`, only if `node` is on PATH). **It never executes the code.**
  - `async verify(state: MigrationState, llm) -> Verification`

- [ ] **Step 1: Write the failing tests**

`tests/test_verify.py`:
```python
import asyncio
import shutil

import pytest

from agent.state import MigrationState, PlanStep, StepStatus
from agent.verify import check_syntax, verify
from tests.fakes import FakeLLM


def test_python_ok_and_error_with_line_number():
    assert check_syntax("a.py", "x = 1\n") == "ok"
    result = check_syntax("a.py", "x = 1\ndef broken(:\n")
    assert result.startswith("error: line 2")


def test_python_null_byte_is_an_error_not_a_crash():
    assert check_syntax("a.py", "x = 1\0").startswith("error")


def test_json_checked():
    assert check_syntax("p.json", '{"a": 1}') == "ok"
    assert check_syntax("p.json", "{a: 1}").startswith("error: line 1")


def test_unknown_suffix_is_unchecked():
    assert check_syntax("App.vue", "<template>") == "unchecked"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_javascript_checked_with_node():
    assert check_syntax("a.js", "const x = 1;\n") == "ok"
    assert "SyntaxError" in check_syntax("a.js", "const = ;\n")


def make_state(files, statuses=(StepStatus.COMPLETED,)):
    state = MigrationState({"app.py": "x = 1\n"}, "flask", "fastapi")
    state.migrated_files = files
    state.plan = [PlanStep(f"s{i}", "d", ["app.py"], status=s) for i, s in enumerate(statuses)]
    return state


def test_verify_passes_clean_migration():
    result = asyncio.run(verify(make_state({"app.py": "y = 2\n"}), FakeLLM()))
    assert result.passed and result.syntax == {"app.py": "ok"} and result.issues == []


def test_verify_fails_on_syntax_error_naming_file():
    result = asyncio.run(verify(make_state({"app.py": "def broken(:\n"}), FakeLLM()))
    assert not result.passed
    assert any(issue.startswith("app.py: error") for issue in result.issues)


def test_verify_fails_when_review_fails():
    result = asyncio.run(verify(make_state({"app.py": "y = 2\n"}), FakeLLM(review_passed=False)))
    assert not result.passed and "still imports flask" in result.issues


def test_verify_fails_when_a_step_did_not_complete():
    state = make_state({"app.py": "y = 2\n"}, statuses=(StepStatus.COMPLETED, StepStatus.FAILED))
    result = asyncio.run(verify(state, FakeLLM()))
    assert not result.passed and any("s1" in issue for issue in result.issues)


def test_verify_fails_with_nothing_migrated():
    result = asyncio.run(verify(make_state({}), FakeLLM()))
    assert not result.passed and result.issues == ["no files were migrated"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.verify'`

- [ ] **Step 3: Implement `agent/verify.py`**

```python
"""Phase 4: static syntax checks plus a Claude review of the migrated files."""
import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent.state import MigrationState, StepStatus, Verification

NODE_SUFFIXES = {".js", ".mjs", ".cjs"}


def check_syntax(path: str, content: str) -> str:
    """Return "ok", "unchecked" or "error: <detail>". Parses only; never executes the code."""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            compile(content, path, "exec")
        except SyntaxError as exc:
            return f"error: line {exc.lineno}: {exc.msg}"
        except ValueError as exc:  # e.g. null bytes
            return f"error: {exc}"
        return "ok"
    if suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return f"error: line {exc.lineno}: {exc.msg}"
        return "ok"
    if suffix in NODE_SUFFIXES and shutil.which("node"):
        return _node_check(content, suffix)
    return "unchecked"


def _node_check(content: str, suffix: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        file = Path(tmp) / f"check{suffix}"
        file.write_text(content)
        try:
            proc = subprocess.run(["node", "--check", str(file)], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return "unchecked"
    if proc.returncode == 0:
        return "ok"
    lines = proc.stderr.strip().splitlines()
    return "error: " + next((line for line in lines if "Error" in line), lines[-1] if lines else "node --check failed")


async def verify(state: MigrationState, llm) -> Verification:
    if not state.migrated_files:
        return Verification(False, {}, ["no files were migrated"], "Nothing to verify.")
    syntax = await asyncio.to_thread(
        lambda: {path: check_syntax(path, content) for path, content in state.migrated_files.items()}
    )
    review = await llm.review(state.migrated_files, state.source_framework, state.target_framework)
    syntax_issues = [f"{path}: {result}" for path, result in syntax.items() if result.startswith("error")]
    incomplete = [step.id for step in state.plan if step.status != StepStatus.COMPLETED]
    issues = syntax_issues + ([f"steps not completed: {', '.join(incomplete)}"] if incomplete else []) + review.issues
    passed = not syntax_issues and not incomplete and review.passed
    return Verification(passed, syntax, issues, review.summary)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_verify.py -v`
Expected: 10 passed (9 plus 1 skipped if node is missing)

- [ ] **Step 5: Commit**

```bash
git add agent/verify.py tests/test_verify.py
git commit -m "feat: add verification phase with syntax checks and Claude review

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Workflow orchestrator

**Files:**
- Create: `agent/workflow.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `MigrationState, Phase, PlanStep, StepStatus` (Task 1); `PlannedStep`, llm methods (Task 2); `verify` (Task 3)
- Produces:
  - `build_plan(steps: list[PlannedStep]) -> list[PlanStep]`: returns steps in dependency order, drops unknown/self dependency ids, and raises `ValueError` on an empty plan, duplicate ids, or a cycle.
  - `Emit = Callable[[dict], Awaitable[None]]`
  - `async run_migration(state: MigrationState, llm, emit: Emit = <no-op>) -> MigrationState`: never raises. It emits these events, in order:
    - `{"type": "phase", "phase": <Phase>}` on every phase change, ending with `"done"` or `"failed"`
    - `{"type": "analysis", "analysis": dict}`
    - `{"type": "plan", "plan": [step dict, ...]}`
    - `{"type": "step", "step": step dict}` when a step goes `in_progress` and again when it ends
    - `{"type": "result", "result": state.to_result()}`, always last

- [ ] **Step 1: Write the failing tests**

`tests/test_workflow.py`:
```python
import asyncio

import pytest

from agent.llm import PlannedStep
from agent.state import MigrationState, Phase, StepStatus
from agent.workflow import build_plan, run_migration
from tests.fakes import SOURCE, FakeLLM


def step(id, deps=(), files=("app.py",)):
    return PlannedStep(id=id, description=f"do {id}", files=list(files), depends_on=list(deps), complexity="low")


def run(llm, files=SOURCE):
    events = []

    async def emit(event):
        events.append(event)

    state = asyncio.run(run_migration(MigrationState(dict(files), "flask", "fastapi"), llm, emit))
    return state, events


def test_build_plan_puts_dependencies_first():
    ids = [s.id for s in build_plan([step("s2", ["s1"]), step("s1")])]
    assert ids.index("s1") < ids.index("s2")


def test_build_plan_drops_unknown_and_self_dependencies():
    [only] = build_plan([step("s1", ["zz", "s1"])])
    assert only.depends_on == [] and only.status == StepStatus.PENDING


@pytest.mark.parametrize(
    ("steps", "message"),
    [([], "empty"), ([step("s1"), step("s1")], "duplicate"), ([step("a", ["b"]), step("b", ["a"])], "cycle")],
)
def test_build_plan_rejects_bad_plans(steps, message):
    with pytest.raises(ValueError, match=message):
        build_plan(steps)


def test_happy_path_runs_all_phases_in_order():
    state, events = run(FakeLLM())
    assert [e["phase"] for e in events if e["type"] == "phase"] == [
        "analysis", "planning", "execution", "verification", "done",
    ]
    assert [s.status for s in state.plan] == [StepStatus.COMPLETED, StepStatus.COMPLETED]
    assert state.migrated_files["app.py"].startswith("# s2\n# s1\n")  # s2 built on s1's output
    assert state.analysis["summary"] == "flask app"
    assert events[-1]["type"] == "result" and events[-1]["result"]["success"] is True


def test_step_events_track_progress():
    _, events = run(FakeLLM())
    statuses = [(e["step"]["id"], e["step"]["status"]) for e in events if e["type"] == "step"]
    assert statuses == [("s1", "in_progress"), ("s1", "completed"), ("s2", "in_progress"), ("s2", "completed")]


def test_failed_step_skips_dependents_but_runs_independent_steps():
    llm = FakeLLM(steps=[step("s1", files=["a.py"]), step("s2", ["s1"], ["a.py"]), step("s3", files=["b.py"])],
                  fail_steps={"s1"})
    state, _ = run(llm, files={"a.py": "a = 1\n", "b.py": "b = 1\n"})
    by_id = {s.id: s for s in state.plan}
    assert by_id["s1"].status == StepStatus.FAILED and "s1 exploded" in by_id["s1"].error
    assert by_id["s2"].status == StepStatus.FAILED and "s1" in by_id["s2"].error
    assert by_id["s3"].status == StepStatus.COMPLETED
    assert "s2" not in llm.executed
    assert state.phase == Phase.DONE and state.to_result()["success"] is False
    assert any("s1 exploded" in e for e in state.errors)


def test_analysis_failure_ends_run_as_failed_with_result_event():
    state, events = run(FakeLLM(fail_analysis=True))
    assert state.phase == Phase.FAILED
    assert state.errors == ["analysis failed: analysis exploded"]
    assert events[-1]["type"] == "result" and events[-1]["result"]["success"] is False


def test_cyclic_plan_fails_in_planning():
    state, _ = run(FakeLLM(steps=[step("a", ["b"]), step("b", ["a"])]))
    assert state.phase == Phase.FAILED and "planning failed" in state.errors[0]


def test_bad_generated_code_fails_verification():
    state, _ = run(FakeLLM(bad_output=True))
    assert state.phase == Phase.DONE
    assert state.verification.passed is False
    assert state.to_result()["success"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workflow.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.workflow'`

- [ ] **Step 3: Implement `agent/workflow.py`**

```python
"""Planning-pattern orchestrator: analysis -> planning -> execution -> verification."""
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from graphlib import CycleError, TopologicalSorter

from agent.llm import PlannedStep
from agent.state import MigrationState, Phase, PlanStep, StepStatus
from agent.verify import verify

log = logging.getLogger(__name__)

Emit = Callable[[dict], Awaitable[None]]


async def _no_emit(event: dict) -> None:
    pass


def build_plan(steps: list[PlannedStep]) -> list[PlanStep]:
    if not steps:
        raise ValueError("model returned an empty plan")
    by_id: dict[str, PlannedStep] = {}
    for step in steps:
        if step.id in by_id:
            raise ValueError(f"duplicate step id {step.id!r}")
        by_id[step.id] = step
    graph = {s.id: [d for d in s.depends_on if d in by_id and d != s.id] for s in steps}
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError(f"plan has a dependency cycle: {exc.args[1]}") from exc
    return [
        PlanStep(id=i, description=by_id[i].description, files=by_id[i].files,
                 depends_on=graph[i], complexity=by_id[i].complexity)
        for i in order
    ]


async def run_migration(state: MigrationState, llm, emit: Emit = _no_emit) -> MigrationState:
    source, target = state.source_framework, state.target_framework

    async def set_phase(phase: Phase) -> None:
        state.phase = phase
        await emit({"type": "phase", "phase": phase})

    try:
        await set_phase(Phase.ANALYSIS)
        analysis = await llm.analyze(state.source_files, source, target)
        state.analysis = analysis.model_dump()
        await emit({"type": "analysis", "analysis": state.analysis})

        await set_phase(Phase.PLANNING)
        plan = await llm.plan(state.source_files, analysis, source, target)
        state.plan = build_plan(plan.steps)
        await emit({"type": "plan", "plan": [asdict(s) for s in state.plan]})

        await set_phase(Phase.EXECUTION)
        for step in state.plan:
            await _execute_step(state, step, llm, analysis.summary, emit)

        await set_phase(Phase.VERIFICATION)
        state.verification = await verify(state, llm)
        await set_phase(Phase.DONE)
    except Exception as exc:  # agent boundary: any failure becomes part of the result, never a 500
        log.exception("migration failed during %s", state.phase)
        state.errors.append(f"{state.phase} failed: {exc}")
        await set_phase(Phase.FAILED)
    await emit({"type": "result", "result": state.to_result()})
    return state


async def _execute_step(state: MigrationState, step: PlanStep, llm, context: str, emit: Emit) -> None:
    status = {s.id: s.status for s in state.plan}
    blocked = [d for d in step.depends_on if status[d] != StepStatus.COMPLETED]
    if blocked:
        step.status = StepStatus.FAILED
        step.error = f"skipped: dependency {', '.join(blocked)} did not complete"
        await emit({"type": "step", "step": asdict(step)})
        return
    step.status = StepStatus.IN_PROGRESS
    await emit({"type": "step", "step": asdict(step)})
    files = {p: state.current_file(p) for p in step.files} if step.files else dict(state.migrated_files or state.source_files)
    try:
        result = await llm.execute_step(step, files, state.source_framework, state.target_framework, context)
        for change in result.files:
            state.migrated_files[change.path] = change.content
        step.notes = result.notes
        step.status = StepStatus.COMPLETED
    except Exception as exc:  # one failed step must not abort the others
        log.exception("step %s failed", step.id)
        step.status = StepStatus.FAILED
        step.error = str(exc)
        state.errors.append(f"step {step.id}: {exc}")
    await emit({"type": "step", "step": asdict(step)})
```

Note: a step with no `files` sees every current file. A step returning a path outside `step.files` is accepted, because that is how it creates new files.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workflow.py -v`
Expected: 11 passed

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add agent/workflow.py tests/test_workflow.py
git commit -m "feat: add planning-pattern workflow orchestrator

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: FastAPI endpoints

**Files:**
- Create: `main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `MigrationState` (Task 1), `ClaudeLLM` (Task 2), `run_migration` (Task 4)
- Produces:
  - `app: FastAPI`
  - `get_llm() -> ClaudeLLM` (FastAPI dependency, cached; tests override it through `app.dependency_overrides`)
  - `MigrateRequest(files: dict[str,str], source_framework: str, target_framework: str)`
  - `MAX_FILES = 50`, `MAX_TOTAL_CHARS = 500_000`
  - `POST /migrate` → the JSON from `MigrationState.to_result()`
  - `POST /migrate/stream` → `text/event-stream`. Each event is `data: <json>\n\n`, and the event shapes are those listed in Task 4. The last event is `type: "result"`.
  - `GET /health` → `{"status": "ok"}`
  - Plan 2 adds the static frontend mount to this file.

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py`:
```python
import json

import pytest
from fastapi.testclient import TestClient

import main
from tests.fakes import SOURCE, FakeLLM

PAYLOAD = {"files": SOURCE, "source_framework": "flask", "target_framework": "fastapi"}


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Implement `main.py`**

```python
"""HTTP entry point. Run with: uvicorn main:app --reload"""
import asyncio
import json
from functools import cache
from pathlib import PurePosixPath

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from agent.llm import ClaudeLLM
from agent.state import MigrationState
from agent.workflow import run_migration

MAX_FILES = 50
MAX_TOTAL_CHARS = 500_000

app = FastAPI(title="Code Migration Agent")


class MigrateRequest(BaseModel):
    files: dict[str, str] = Field(min_length=1, max_length=MAX_FILES, description="path -> source code")
    source_framework: str = Field(min_length=1, max_length=100)
    target_framework: str = Field(min_length=1, max_length=100)

    @field_validator("files")
    @classmethod
    def check_files(cls, files: dict[str, str]) -> dict[str, str]:
        if sum(len(content) for content in files.values()) > MAX_TOTAL_CHARS:
            raise ValueError(f"total source size exceeds {MAX_TOTAL_CHARS} characters")
        for path in files:
            parts = PurePosixPath(path).parts
            if not path.strip() or path.startswith("/") or ".." in parts:
                raise ValueError(f"invalid file path: {path!r}")
        return files

    @model_validator(mode="after")
    def check_frameworks(self):
        if self.source_framework.strip().lower() == self.target_framework.strip().lower():
            raise ValueError("source and target framework must differ")
        return self


@cache
def get_llm() -> ClaudeLLM:
    return ClaudeLLM()


def new_state(request: MigrateRequest) -> MigrationState:
    return MigrationState(request.files, request.source_framework, request.target_framework)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/migrate")
async def migrate(request: MigrateRequest, llm: ClaudeLLM = Depends(get_llm)) -> dict:
    state = await run_migration(new_state(request), llm)
    return state.to_result()


@app.post("/migrate/stream")
async def migrate_stream(request: MigrateRequest, llm: ClaudeLLM = Depends(get_llm)) -> StreamingResponse:
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def events():
        task = asyncio.create_task(run_migration(new_state(request), llm, queue.put))
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "result":
                    break
        finally:
            task.cancel()  # client disconnected mid-run; no-op when already finished

    return StreamingResponse(events(), media_type="text/event-stream")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: 12 passed

- [ ] **Step 5: Run the whole suite and a smoke check of the server**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

Run: `.venv/bin/uvicorn main:app --port 8765 & sleep 2; curl -s localhost:8765/health; kill %1`
Expected: `{"status":"ok"}`

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_api.py
git commit -m "feat: add /migrate and /migrate/stream endpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
