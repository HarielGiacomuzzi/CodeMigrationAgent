import asyncio
import shutil

import pytest

from agent.llm import LLMError
from agent.state import MigrationState, PlanStep, StepStatus
from agent.verify import check_syntax, verify
from tests.fakes import FakeLLM


class ReviewFailsLLM(FakeLLM):
    async def review(self, files, source, target):
        raise LLMError("boom")


def test_python_ok_and_error_with_line_number():
    assert check_syntax("a.py", "x = 1\n") == "ok"
    result = check_syntax("a.py", "x = 1\ndef broken(:\n")
    assert result.startswith("error: line 2")


def test_python_null_byte_is_an_error_not_a_crash():
    assert check_syntax("a.py", "x = 1\0").startswith("error")


def test_json_checked():
    assert check_syntax("p.json", '{"a": 1}') == "ok"
    assert check_syntax("p.json", "{a: 1}").startswith("error: line 1")


def test_unknown_suffix_is_unchecked():
    assert check_syntax("App.vue", "<template>") == "unchecked"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_javascript_checked_with_node():
    assert check_syntax("a.js", "const x = 1;\n") == "ok"
    assert "SyntaxError" in check_syntax("a.js", "const = ;\n")


def make_state(files, statuses=(StepStatus.COMPLETED,)):
    state = MigrationState({"app.py": "x = 1\n"}, "flask", "fastapi")
    state.migrated_files = files
    state.plan = [PlanStep(f"s{i}", "d", ["app.py"], status=s) for i, s in enumerate(statuses)]
    return state


def test_verify_passes_clean_migration():
    result = asyncio.run(verify(make_state({"app.py": "y = 2\n"}), FakeLLM()))
    assert result.passed and result.syntax == {"app.py": "ok"} and result.issues == []


def test_verify_fails_on_syntax_error_naming_file():
    result = asyncio.run(verify(make_state({"app.py": "def broken(:\n"}), FakeLLM()))
    assert not result.passed
    assert any(issue.startswith("app.py: error") for issue in result.issues)


def test_verify_fails_when_review_fails():
    result = asyncio.run(verify(make_state({"app.py": "y = 2\n"}), FakeLLM(review_passed=False)))
    assert not result.passed and "still imports flask" in result.issues


def test_verify_fails_when_a_step_did_not_complete():
    state = make_state({"app.py": "y = 2\n"}, statuses=(StepStatus.COMPLETED, StepStatus.FAILED))
    result = asyncio.run(verify(state, FakeLLM()))
    assert not result.passed and any("s1" in issue for issue in result.issues)


def test_verify_fails_with_nothing_migrated():
    result = asyncio.run(verify(make_state({}), FakeLLM()))
    assert not result.passed and result.issues == ["no files were migrated"]


def test_verify_keeps_syntax_results_when_review_call_fails():
    result = asyncio.run(verify(make_state({"app.py": "y = 2\n"}), ReviewFailsLLM()))
    assert result.passed is False
    assert result.syntax == {"app.py": "ok"}
    assert "review failed: boom" in result.issues
