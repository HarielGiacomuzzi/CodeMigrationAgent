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
