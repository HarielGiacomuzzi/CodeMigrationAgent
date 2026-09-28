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
