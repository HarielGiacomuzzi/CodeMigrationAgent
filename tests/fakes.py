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
