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
