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
