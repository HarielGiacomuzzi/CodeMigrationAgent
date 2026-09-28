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
    files = {p: state.current_file(p) for p in step.files} if step.files else {**state.source_files, **state.migrated_files}
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
