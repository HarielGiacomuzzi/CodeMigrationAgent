# Code Migration Agent

A multi-step AI agent that migrates source code between frameworks (for example Flask → FastAPI or Express → Hono) using the **planning pattern**. It analyzes the code, writes a dependency-ordered migration plan, executes the plan step by step with Claude, and verifies the result. A web UI shows each phase live.

```
 Input: source files + source/target framework
   │
   ▼
 PHASE 1  ANALYSIS      Claude summarizes structure, patterns, dependencies, risks
   │
   ▼
 PHASE 2  PLANNING      Claude writes steps (id, files, depends_on, complexity);
   │                    graphlib orders them and rejects cycles/duplicates
   ▼
 PHASE 3  EXECUTION     each step runs in dependency order and sees the output of earlier steps;
   │                    status: pending → in_progress → completed | failed
   ▼                    (a failed step marks every step that depends on it as failed)
 PHASE 4  VERIFICATION  syntax checks (.py compile, .json parse, .js via node --check;
   │                    code is never executed) + Claude review of the migrated files
   ▼
 Output: migrated files, diffs, executed plan, verification, errors
```

## Project layout

| Path | Responsibility |
|---|---|
| `main.py` | FastAPI app: request validation, `POST /migrate`, `POST /migrate/stream`, `GET /health`, serves the UI |
| `agent/state.py` | Dataclasses: `MigrationState`, `PlanStep`, `Verification`, `Phase`, `StepStatus` |
| `agent/llm.py` | `ClaudeLLM`: one structured-output Claude call per phase (Pydantic schemas) |
| `agent/workflow.py` | `run_migration` orchestrator + `build_plan` dependency ordering |
| `agent/verify.py` | Syntax checks + verification verdict |
| `static/` | Web UI (plain HTML/CSS/JS, no build step) |
| `tests/` | pytest suite; `tests/fakes.py` provides `FakeLLM`, so tests never call the API |

## Requirements

- Python 3.11+
- An Anthropic API key
- Optional: Node.js on `PATH` enables syntax checks for migrated `.js/.mjs/.cjs` files. Without it, those files are reported as `unchecked`.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/uvicorn main:app --reload
```

Open http://localhost:8000. The form is prefilled with a small Flask app. Pick the frameworks and click **Migrate**.

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key (required) |
| `CLAUDE_MODEL` | `claude-opus-5` | Model used for every phase |

### Limits

Each request accepts up to 50 files and 500,000 characters in total. Paths must be relative and cannot contain `..`. Source and target frameworks must differ. Each Claude response is capped at 16,000 tokens. A step whose output would exceed that fails with a `max_tokens` error, so migrate large projects in smaller batches.

## API

### `POST /migrate`

Request:
```json
{
  "files": {"app.py": "from flask import Flask\napp = Flask(__name__)\n"},
  "source_framework": "flask",
  "target_framework": "fastapi"
}
```

Response (`200`, even when the migration itself fails; check `success`):
```json
{
  "success": true,
  "phase": "done",
  "analysis": {"summary": "...", "patterns": ["..."], "dependencies": ["flask"], "potential_issues": []},
  "migrated_files": {"app.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
  "diffs": {"app.py": "--- a/app.py\n+++ b/app.py\n@@ ... @@\n-from flask import Flask\n+from fastapi import FastAPI\n..."},
  "plan": [
    {"id": "s1", "description": "Replace Flask app with FastAPI app", "files": ["app.py"],
     "depends_on": [], "complexity": "low", "status": "completed", "notes": "...", "error": null}
  ],
  "verification": {"passed": true, "syntax": {"app.py": "ok"}, "issues": [], "summary": "..."},
  "errors": []
}
```

- `phase`: `done` when every phase ran, or `failed` when the run stopped early (see `errors`).
- `success`: true only when the run finished, verification passed, and no step failed.
- Invalid input returns `422` before any Claude call.

```bash
curl -s localhost:8000/migrate -H 'content-type: application/json' \
  -d '{"files":{"app.py":"from flask import Flask\napp = Flask(__name__)\n"},"source_framework":"flask","target_framework":"fastapi"}'
```

### `POST /migrate/stream`

Same request body. It responds with `text/event-stream`, one `data: <json>` event per state change:

| `type` | Payload | When |
|---|---|---|
| `phase` | `phase`: `analysis` \| `planning` \| `execution` \| `verification` \| `done` \| `failed` | every phase change |
| `analysis` | `analysis` object | after phase 1 |
| `plan` | `plan`: list of steps | after phase 2 |
| `step` | `step` object | when a step starts and when it ends |
| `result` | `result`: same body as `POST /migrate` | always the last event |

### `GET /health`

`{"status": "ok"}`, used by the Railway healthcheck.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The suite uses `FakeLLM` in place of Claude. It covers state, prompt construction and refusal/truncation handling, plan ordering (cycles, duplicates, unknown dependencies), step-failure propagation, verification, HTTP validation, SSE streaming, and the static UI.

## Deploy to Railway

1. Push this repo to GitHub.
2. In Railway, create a **New Project → Deploy from GitHub repo** and select the repo. Railpack detects Python from `requirements.txt` and `.python-version`.
3. Under **Variables**, add `ANTHROPIC_API_KEY` (and optionally `CLAUDE_MODEL`).
4. `railway.json` sets the start command (`uvicorn main:app --host 0.0.0.0 --port $PORT`) and the `/health` healthcheck.
5. Under **Settings → Networking**, click **Generate Domain** to get the public URL.

CLI alternative: `npm i -g @railway/cli && railway login && railway init && railway up`, then `railway variables --set ANTHROPIC_API_KEY=...` and `railway domain`.

## Security notes

- Migrated code is **never executed**. Verification only parses it (`compile()`, `json.loads`, `node --check`).
- The UI inserts all code and model output as text, never as HTML.
- Paths are validated even though nothing is written to disk.

## Not implemented (extension challenges)

- Rollback of failed migrations
- Parallel execution of independent steps (`build_plan` already computes the dependency graph needed for it)
- Human approval of the plan before execution
