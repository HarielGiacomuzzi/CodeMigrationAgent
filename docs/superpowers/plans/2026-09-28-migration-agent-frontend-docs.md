# Migration Agent Frontend, Deploy & Docs Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A responsive web UI, served by the FastAPI app, that collects source files and frameworks and shows the migration live: a phase stepper, a plan table with step status, the analysis, verification, and a per-file diff/code view. The plan also adds Railway deploy config and a complete README, then pushes `main`.

**Architecture:** Three static files (`static/index.html`, `static/app.js`, `static/style.css`) with no build step and no JS dependencies. They are mounted at `/` by `main.py` after the API routes. `app.js` POSTs to `/migrate/stream` and reads the SSE body with `fetch` + `TextDecoderStream` (`EventSource` only supports GET). Diffs come precomputed from the backend (`result.diffs`, from stdlib `difflib`), so the browser only colors lines. All user and model text is inserted with `textContent`. The frontend never uses `innerHTML`.

**Tech Stack:** Vanilla HTML/CSS/JS, FastAPI `StaticFiles`, Railway (Railpack, `railway.json`).

**Spec:** `requirements.md`. **Depends on:** Plan 1 (`docs/superpowers/plans/2026-09-28-migration-agent-backend.md`) fully merged: `main.py`, `/migrate`, `/migrate/stream` with event types `phase | analysis | plan | step | result`, and `result` keys `success, phase, analysis, migrated_files, diffs, plan, verification, errors`.

## Global Constraints

- Frontend must provide: input of source code files, source/target framework selection, real-time phase display (Analysis → Planning → Execution → Verification), a plan viewer with step status, an output panel with migrated code and a diff view, and a responsive design.
- No JS frameworks, no CDN scripts, no build step.
- Never use `innerHTML`/`outerHTML`/`insertAdjacentHTML` with any data. Use `textContent` / `append(string)`.
- Layout works at 375px wide with no horizontal page scroll. Wide content (table, code) scrolls inside its own box.
- Colors are CSS custom properties on `:root`, with a dark-mode override under `@media (prefers-color-scheme: dark)`.
- Commit directly on `main`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests: `.venv/bin/python -m pytest -q` (venv from Plan 1).

## Review Focus

1. **Source code containing HTML/JS** (`<script>alert(1)</script>` in a file or in model output). Expected: shown as text, never executed. Pinned by the `innerHTML` ban test in Task 1.
2. **The request is rejected with 422** (e.g. same framework on both sides, a `../` path). Expected: a readable message in the status line, not `[object Object]`, and the Migrate button re-enabled. (Task 1: `formatError`.)
3. **The stream closes before a `result` event** (server restart, network drop). Expected: the status says the connection closed early, and the button re-enables. (Task 1: post-loop check.)
4. **Two files with the same path** (e.g. two uploads both named `index.js`). Expected: the submit is blocked with a message naming the path, not a silent overwrite. (Task 1: `collectFiles`.)
5. **A phone-width viewport.** Expected: a single column with no horizontal page scroll. (Task 1: manual check at 375px.)

---

## File Structure

```
static/index.html    markup: input form, phase stepper, analysis, plan table, verification, file tabs, output
static/app.js        form handling, SSE reader, renderers
static/style.css     tokens, light/dark, responsive grid
main.py              (modify) mount static/ at "/" after API routes
tests/test_frontend.py
railway.json         Railway start command + healthcheck
.python-version      Python version pin for Railway
README.md            full project documentation
```

---

### Task 1: Web frontend

**Files:**
- Create: `static/index.html`, `static/app.js`, `static/style.css`
- Modify: `main.py` (append the static mount at the very end of the file, after all routes)
- Test: `tests/test_frontend.py`

**Interfaces:**
- Consumes: `POST /migrate/stream` (SSE; events `{"type":"phase","phase"}`, `{"type":"analysis","analysis":{summary,patterns,dependencies,potential_issues}}`, `{"type":"plan","plan":[step]}`, `{"type":"step","step":step}`, `{"type":"result","result":{...}}`). The step dict has keys `id, description, files, depends_on, complexity, status, notes, error`. Validation errors come back as HTTP 422 with `{"detail":[{"msg":...}, ...]}`.
- Produces: `GET /` serves `index.html`, and `GET /app.js` and `GET /style.css` serve the assets.

- [ ] **Step 1: Write the failing tests**

`tests/test_frontend.py`:
```python
from pathlib import Path

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_index_has_every_workflow_region():
    html = client.get("/").text
    for marker in [
        'name="viewport"', 'id="migrate-form"', 'id="source-framework"', 'id="target-framework"',
        'data-phase="analysis"', 'data-phase="planning"', 'data-phase="execution"', 'data-phase="verification"',
        'id="plan"', 'id="verification"', 'id="file-tabs"', 'id="output"', 'value="diff"',
    ]:
        assert marker in html, marker


def test_assets_are_served():
    js = client.get("/app.js")
    assert js.status_code == 200 and "/migrate/stream" in js.text
    assert client.get("/style.css").status_code == 200


def test_api_routes_still_take_precedence():
    assert client.get("/health").json() == {"status": "ok"}


def test_frontend_never_injects_html():
    js = Path("static/app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in js, sink
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_frontend.py -v`
Expected: FAIL (`GET /` returns 404; `static/app.js` missing)

- [ ] **Step 3: Create `static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Code Migration Agent</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="top">
    <h1>Code Migration Agent</h1>
    <p>Analyze → Plan → Execute → Verify</p>
  </header>
  <main class="layout">
    <section class="panel" id="input-panel">
      <h2>Source</h2>
      <form id="migrate-form">
        <div class="frameworks">
          <label>From <input id="source-framework" list="frameworks" required placeholder="e.g. flask"></label>
          <label>To <input id="target-framework" list="frameworks" required placeholder="e.g. fastapi"></label>
          <datalist id="frameworks">
            <option value="flask"></option><option value="fastapi"></option><option value="django"></option>
            <option value="express"></option><option value="hono"></option>
            <option value="react class components"></option><option value="react hooks"></option>
            <option value="vue 2"></option><option value="vue 3"></option>
            <option value="jquery"></option><option value="vanilla js"></option>
            <option value="unittest"></option><option value="pytest"></option>
          </datalist>
        </div>
        <div id="files"></div>
        <div class="file-actions">
          <button type="button" id="add-file">+ Add file</button>
          <label class="button">Upload files<input type="file" id="upload" multiple hidden></label>
        </div>
        <button type="submit" id="run" class="primary">Migrate</button>
      </form>
    </section>

    <section class="panel" id="progress-panel">
      <h2>Progress</h2>
      <ol id="phases">
        <li data-phase="analysis">Analysis</li>
        <li data-phase="planning">Planning</li>
        <li data-phase="execution">Execution</li>
        <li data-phase="verification">Verification</li>
      </ol>
      <p id="status" role="status" aria-live="polite">Idle</p>
      <div id="analysis"></div>
      <h3>Plan</h3>
      <div class="scroll">
        <table id="plan">
          <thead><tr><th>Step</th><th>Description</th><th>Depends on</th><th>Complexity</th><th>Status</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </section>

    <section class="panel wide" id="output-panel">
      <h2>Output</h2>
      <div id="verification"></div>
      <ul id="errors"></ul>
      <div id="file-tabs" role="tablist"></div>
      <div class="view-toggle">
        <label><input type="radio" name="view" value="diff" checked> Diff</label>
        <label><input type="radio" name="view" value="code"> Migrated code</label>
      </div>
      <pre id="output"></pre>
    </section>
  </main>

  <template id="file-template">
    <div class="file">
      <div class="file-head">
        <input class="path" placeholder="path/to/file.py" aria-label="File path">
        <button type="button" class="remove" aria-label="Remove file">×</button>
      </div>
      <textarea class="content" rows="10" spellcheck="false" placeholder="Paste source code" aria-label="File content"></textarea>
    </div>
  </template>
  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create `static/app.js`**

```js
const $ = (selector) => document.querySelector(selector);
const PHASES = ["analysis", "planning", "execution", "verification"];
const SAMPLE = `from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/items/<int:item_id>")
def get_item(item_id):
    return jsonify({"id": item_id})
`;

let plan = [];
let result = null;
let selectedFile = null;

// Build an element whose text is set safely (never parsed as HTML).
function el(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function list(items) {
  const ul = el("ul");
  ul.append(...items.map((item) => el("li", item)));
  return ul;
}

function addFile(path = "", content = "") {
  const node = $("#file-template").content.firstElementChild.cloneNode(true);
  node.querySelector(".path").value = path;
  node.querySelector(".content").value = content;
  node.querySelector(".remove").onclick = () => node.remove();
  $("#files").append(node);
}

function collectFiles() {
  const files = {};
  for (const node of document.querySelectorAll("#files .file")) {
    const path = node.querySelector(".path").value.trim();
    if (!path) continue;
    if (path in files) throw new Error(`Duplicate file path: ${path}`);
    files[path] = node.querySelector(".content").value;
  }
  if (!Object.keys(files).length) throw new Error("Add at least one file with a path.");
  return files;
}

function setStatus(text) {
  $("#status").textContent = text;
}

function setPhase(phase) {
  if (phase === "failed") {
    document.querySelector("#phases .active")?.classList.replace("active", "failed");
    return;
  }
  const index = phase === "done" ? PHASES.length : PHASES.indexOf(phase);
  document.querySelectorAll("#phases li").forEach((li, i) => {
    li.className = i < index ? "done" : i === index ? "active" : "";
  });
}

function renderAnalysis(analysis) {
  const box = $("#analysis");
  box.replaceChildren(el("p", analysis.summary));
  for (const [label, items] of [
    ["Patterns", analysis.patterns],
    ["Dependencies", analysis.dependencies],
    ["Potential issues", analysis.potential_issues],
  ]) {
    if (items.length) box.append(el("h4", label), list(items));
  }
}

function cell(content) {
  const td = el("td");
  td.append(content);
  return td;
}

function renderPlan() {
  $("#plan tbody").replaceChildren(...plan.map((step) => {
    const badge = el("span", step.status.replace("_", " "), `badge ${step.status}`);
    const description = step.error ? `${step.description} — ${step.error}` : step.description;
    const row = el("tr");
    row.append(cell(step.id), cell(description), cell(step.depends_on.join(", ") || "—"), cell(step.complexity), cell(badge));
    return row;
  }));
}

function renderOutput() {
  for (const tab of document.querySelectorAll("#file-tabs button")) {
    tab.setAttribute("aria-selected", String(tab.textContent === selectedFile));
  }
  const out = $("#output");
  if (!result || selectedFile === null) {
    out.replaceChildren();
    return;
  }
  if ($("input[name=view]:checked").value === "code") {
    out.textContent = result.migrated_files[selectedFile];
    return;
  }
  const diff = result.diffs[selectedFile] || "(no changes)";
  out.replaceChildren(...diff.split("\n").map((line) => {
    const kind = line.startsWith("@@") ? "hunk"
      : line.startsWith("+") && !line.startsWith("+++") ? "add"
      : line.startsWith("-") && !line.startsWith("---") ? "del" : "";
    return el("span", `${line}\n`, kind);
  }));
}

function renderResult(data) {
  result = data;
  plan = data.plan;
  renderPlan();
  const v = data.verification;
  $("#verification").replaceChildren(...(v ? [
    el("p", v.passed ? "✓ Verification passed" : "✗ Verification failed", v.passed ? "ok" : "bad"),
    el("p", v.summary),
    list(Object.entries(v.syntax).map(([path, check]) => `${path}: syntax ${check}`)),
    list(v.issues),
  ] : []));
  $("#errors").replaceChildren(...data.errors.map((error) => el("li", error)));
  const paths = Object.keys(data.migrated_files);
  selectedFile = paths[0] ?? null;
  $("#file-tabs").replaceChildren(...paths.map((path) => {
    const tab = el("button", path);
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.onclick = () => { selectedFile = path; renderOutput(); };
    return tab;
  }));
  renderOutput();
  setStatus(data.success ? "Migration succeeded." : "Migration finished with problems — see errors and verification.");
}

function reset() {
  plan = [];
  result = null;
  selectedFile = null;
  setPhase("pending");
  renderPlan();
  for (const id of ["#analysis", "#verification", "#errors", "#file-tabs"]) $(id).replaceChildren();
  renderOutput();
}

function handle(event) {
  switch (event.type) {
    case "phase":
      setPhase(event.phase);
      if (PHASES.includes(event.phase)) setStatus(`Running ${event.phase}…`);
      break;
    case "analysis":
      renderAnalysis(event.analysis);
      break;
    case "plan":
      plan = event.plan;
      renderPlan();
      break;
    case "step":
      plan = plan.map((step) => (step.id === event.step.id ? event.step : step));
      renderPlan();
      break;
    case "result":
      renderResult(event.result);
      break;
  }
}

// EventSource is GET-only, so read the POST response body as an SSE stream by hand.
async function* readEvents(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      if (chunk.startsWith("data: ")) yield JSON.parse(chunk.slice(6));
    }
  }
}

function formatError(body) {
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  return String(body.detail ?? "request failed");
}

$("#migrate-form").onsubmit = async (event) => {
  event.preventDefault();
  let files;
  try {
    files = collectFiles();
  } catch (error) {
    setStatus(error.message);
    return;
  }
  reset();
  $("#run").disabled = true;
  setStatus("Starting…");
  try {
    const response = await fetch("/migrate/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files,
        source_framework: $("#source-framework").value.trim(),
        target_framework: $("#target-framework").value.trim(),
      }),
    });
    if (!response.ok) throw new Error(formatError(await response.json().catch(() => ({}))));
    for await (const message of readEvents(response)) handle(message);
    if (!result) setStatus("Connection closed before the migration finished. Try again.");
  } catch (error) {
    setStatus(`Error: ${error.message}`);
  } finally {
    $("#run").disabled = false;
  }
};

$("#add-file").onclick = () => addFile();
$("#upload").onchange = async (event) => {
  for (const file of event.target.files) addFile(file.name, await file.text());
  event.target.value = "";
};
document.querySelectorAll("input[name=view]").forEach((radio) => { radio.onchange = renderOutput; });

$("#source-framework").value = "flask";
$("#target-framework").value = "fastapi";
addFile("app.py", SAMPLE);
```

- [ ] **Step 5: Create `static/style.css`**

```css
:root {
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1c2330;
  --muted: #5b6575;
  --border: #d9dde4;
  --accent: #2f5bd3;
  --accent-text: #ffffff;
  --ok: #1a7f45;
  --bad: #c0362c;
  --warn: #9a6700;
  --add-bg: #e6f6ea;
  --del-bg: #fdecea;
  --hunk: #6b4fbb;
  --code-bg: #f1f3f6;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #11151c;
    --panel: #1a2029;
    --text: #e3e7ee;
    --muted: #98a2b3;
    --border: #2d3542;
    --accent: #6d8ff0;
    --accent-text: #0b0f16;
    --ok: #4cc27f;
    --bad: #f07167;
    --warn: #e0b54a;
    --add-bg: #173424;
    --del-bg: #3d1c1a;
    --hunk: #b39cf0;
    --code-bg: #121720;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}

.top { padding: 20px 16px 4px; max-width: 1280px; margin: 0 auto; }
.top h1 { margin: 0; font-size: 1.5rem; }
.top p { margin: 4px 0 0; color: var(--muted); }

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  padding: 16px;
  max-width: 1280px;
  margin: 0 auto;
}
.wide { grid-column: 1 / -1; }

@media (max-width: 800px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
}

.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  min-width: 0;
}
.panel h2 { margin: 0 0 12px; font-size: 1.1rem; }
.panel h3 { margin: 16px 0 8px; font-size: 1rem; }

.frameworks { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.frameworks label { flex: 1 1 140px; display: flex; flex-direction: column; gap: 4px; color: var(--muted); }

input, textarea, button, .button {
  font: inherit;
  color: var(--text);
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
}
textarea {
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: var(--code-bg);
  resize: vertical;
}
button, .button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: progress; }
.primary { background: var(--accent); color: var(--accent-text); border-color: var(--accent); width: 100%; margin-top: 12px; padding: 10px; }

.file { margin-bottom: 12px; }
.file-head { display: flex; gap: 8px; margin-bottom: 6px; }
.file-head .path { flex: 1; min-width: 0; }
.file-actions { display: flex; gap: 8px; flex-wrap: wrap; }

#phases { display: flex; list-style: none; padding: 0; margin: 0 0 12px; gap: 6px; flex-wrap: wrap; }
#phases li {
  flex: 1 1 100px;
  text-align: center;
  padding: 8px 4px;
  border-radius: 6px;
  border: 1px solid var(--border);
  color: var(--muted);
}
#phases li.active { border-color: var(--accent); color: var(--accent); font-weight: 600; animation: pulse 1.2s ease-in-out infinite; }
#phases li.done { border-color: var(--ok); color: var(--ok); }
#phases li.failed { border-color: var(--bad); color: var(--bad); font-weight: 600; }
@keyframes pulse { 50% { opacity: 0.55; } }
@media (prefers-reduced-motion: reduce) { #phases li.active { animation: none; } }

#status { color: var(--muted); margin: 0 0 8px; }

.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }

.badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; border: 1px solid currentColor; white-space: nowrap; }
.badge.pending { color: var(--muted); }
.badge.in_progress { color: var(--accent); }
.badge.completed { color: var(--ok); }
.badge.failed { color: var(--bad); }

.ok { color: var(--ok); font-weight: 600; }
.bad { color: var(--bad); font-weight: 600; }
#errors { color: var(--bad); }

#file-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
#file-tabs button[aria-selected="true"] { border-color: var(--accent); color: var(--accent); font-weight: 600; }
.view-toggle { display: flex; gap: 16px; margin-bottom: 8px; color: var(--muted); }

#output {
  margin: 0;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  overflow: auto;
  max-height: 70vh;
  font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
}
#output span { display: block; white-space: pre; }
#output .add { background: var(--add-bg); }
#output .del { background: var(--del-bg); }
#output .hunk { color: var(--hunk); }
```

- [ ] **Step 6: Mount the static directory in `main.py`**

Add the imports at the top of `main.py`, next to the existing imports:
```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```
(`from pathlib import PurePosixPath` already exists. Change it to `from pathlib import Path, PurePosixPath`.)

Append at the **very end** of `main.py`, after every `@app` route, so API routes win:
```python
# Serve the web UI last so the API routes above take precedence.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (Plan 1 tests + 4 new)

Run: `node --check static/app.js`
Expected: no output, exit 0

- [ ] **Step 8: Manual browser check with the fake LLM** (controller performs this, since it needs a browser)

Start the server with `FakeLLM` (no API key or network needed):
```bash
.venv/bin/python -c "import uvicorn, main; from tests.fakes import FakeLLM; main.app.dependency_overrides[main.get_llm] = lambda: FakeLLM(); uvicorn.run(main.app, port=8765)"
```
Open `http://localhost:8765/` and check:
- The sample `app.py` is prefilled. Clicking **Migrate** walks all four phases to done (green), the plan table shows s1/s2 `completed`, verification shows "✓ Verification passed", and the `app.py` tab shows a colored diff. Switching to "Migrated code" shows the full file.
- Setting both frameworks to `flask` shows a readable 422 message in the status line.
- Adding a second file named `app.py` shows "Duplicate file path: app.py".
- At 375px width, the page has a single column and no horizontal page scroll.
Stop the server afterwards.

- [ ] **Step 9: Commit**

```bash
git add static/ main.py tests/test_frontend.py
git commit -m "feat: add web frontend with live phase, plan, and diff views

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Railway config, README, push

**Files:**
- Create: `railway.json`, `.python-version`, `README.md`

**Interfaces:**
- Consumes: everything above (documents it; no code changes)
- Produces: deployable repo and docs; `main` pushed to `origin`

- [ ] **Step 1: Create `railway.json`**

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "deploy": {
    "startCommand": "uvicorn main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/health"
  }
}
```

- [ ] **Step 2: Create `.python-version`**

```
3.12
```

- [ ] **Step 3: Create `README.md`**

````markdown
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
````

- [ ] **Step 4: Final verification**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 failures

Run: `git status --short`
Expected: only `railway.json`, `.python-version`, and `README.md` are untracked. `requirements.md` and `docs/` should already be committed; if not, add them in this commit.

- [ ] **Step 5: Commit and push**

```bash
git add railway.json .python-version README.md requirements.md docs/
git commit -m "docs: add README and Railway deploy config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push origin main
```
Expected: push succeeds to `git@github.com:HarielGiacomuzzi/CodeMigrationAgent.git`.
