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
