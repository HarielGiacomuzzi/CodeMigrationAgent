import asyncio
from types import SimpleNamespace

import pytest

from agent.llm import DEFAULT_MODEL, Analysis, ClaudeLLM, LLMError, Review, format_files
from agent.state import PlanStep

ANALYSIS = Analysis(summary="s", patterns=["p"], dependencies=["flask"], potential_issues=[])


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def make_llm(stop_reason="end_turn", parsed=ANALYSIS, model=None):
    client = SimpleNamespace(messages=FakeMessages(SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)))
    return ClaudeLLM(client=client, model=model), client.messages


def test_format_files_wraps_each_file_with_its_path():
    text = format_files({"a.py": "x = 1", "b.py": "y = 2"})
    assert '<file path="a.py">\nx = 1\n</file>' in text
    assert '<file path="b.py">' in text


def test_analyze_sends_files_and_schema_and_returns_parsed():
    llm, messages = make_llm()
    result = asyncio.run(llm.analyze({"app.py": "import flask"}, "flask", "fastapi"))
    assert result == ANALYSIS
    call = messages.calls[0]
    assert call["model"] == DEFAULT_MODEL
    assert call["output_format"] is Analysis
    prompt = call["messages"][0]["content"]
    assert "flask" in prompt and "fastapi" in prompt and "import flask" in prompt


def test_model_override_from_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5")
    llm, messages = make_llm()
    asyncio.run(llm.analyze({"a.py": ""}, "x", "y"))
    assert messages.calls[0]["model"] == "claude-sonnet-5"


def test_execute_step_prompt_names_the_step():
    review = Review(passed=True, issues=[], summary="ok")
    llm, messages = make_llm(parsed=review)
    step = PlanStep("s7", "Convert routes to APIRouter", ["app.py"])
    asyncio.run(llm.execute_step(step, {"app.py": "code"}, "flask", "fastapi", "a flask app"))
    prompt = messages.calls[0]["messages"][0]["content"]
    assert "[s7] Convert routes to APIRouter" in prompt and "a flask app" in prompt


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "message"),
    [
        ("refusal", ANALYSIS, "declined"),
        ("max_tokens", ANALYSIS, "max_tokens"),
        ("model_context_window_exceeded", ANALYSIS, "context window"),
        ("end_turn", None, "no structured output"),
    ],
)
def test_unusable_responses_raise_llm_error(stop_reason, parsed, message):
    llm, _ = make_llm(stop_reason=stop_reason, parsed=parsed)
    with pytest.raises(LLMError, match=message):
        asyncio.run(llm.analyze({"a.py": ""}, "x", "y"))
