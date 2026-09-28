"""HTTP entry point. Run with: uvicorn main:app --reload"""
import asyncio
import json
from functools import cache
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from agent.llm import ClaudeLLM
from agent.state import MigrationState
from agent.workflow import run_migration

MAX_FILES = 50
MAX_TOTAL_CHARS = 500_000

app = FastAPI(title="Code Migration Agent")


class MigrateRequest(BaseModel):
    files: dict[str, str] = Field(min_length=1, max_length=MAX_FILES, description="path -> source code")
    source_framework: str = Field(min_length=1, max_length=100)
    target_framework: str = Field(min_length=1, max_length=100)

    @field_validator("files")
    @classmethod
    def check_files(cls, files: dict[str, str]) -> dict[str, str]:
        if sum(len(content) for content in files.values()) > MAX_TOTAL_CHARS:
            raise ValueError(f"total source size exceeds {MAX_TOTAL_CHARS} characters")
        for path in files:
            parts = PurePosixPath(path).parts
            if not path.strip() or path.startswith("/") or ".." in parts:
                raise ValueError(f"invalid file path: {path!r}")
        return files

    @model_validator(mode="after")
    def check_frameworks(self):
        if self.source_framework.strip().lower() == self.target_framework.strip().lower():
            raise ValueError("source and target framework must differ")
        return self


@cache
def get_llm() -> ClaudeLLM:
    return ClaudeLLM()


def new_state(request: MigrateRequest) -> MigrationState:
    return MigrationState(request.files, request.source_framework, request.target_framework)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/migrate")
async def migrate(request: MigrateRequest, llm: ClaudeLLM = Depends(get_llm)) -> dict:
    state = await run_migration(new_state(request), llm)
    return state.to_result()


@app.post("/migrate/stream")
async def migrate_stream(request: MigrateRequest, llm: ClaudeLLM = Depends(get_llm)) -> StreamingResponse:
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def events():
        task = asyncio.create_task(run_migration(new_state(request), llm, queue.put))
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "result":
                    break
        finally:
            task.cancel()  # client disconnected mid-run; no-op when already finished

    return StreamingResponse(events(), media_type="text/event-stream")


# Serve the web UI last so the API routes above take precedence.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
