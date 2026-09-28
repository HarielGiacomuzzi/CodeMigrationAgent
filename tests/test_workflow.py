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


def test_step_with_no_files_sees_all_current_files():
    # FakeLLM prefixes "# <step.id>" to every file it receives, so files touched by s2
    # (which declares no files) prove s2 saw both a.py and b.py.
    steps = [step("s1", files=["a.py"]), step("s2", ["s1"], files=[])]
    state, _ = run(FakeLLM(steps=steps), files={"a.py": "a = 1\n", "b.py": "b = 1\n"})
    assert state.migrated_files["a.py"].startswith("# s2")
    assert state.migrated_files["b.py"].startswith("# s2")


def test_bad_generated_code_fails_verification():
    state, _ = run(FakeLLM(bad_output=True))
    assert state.phase == Phase.DONE
    assert state.verification.passed is False
    assert state.to_result()["success"] is False
