"""Durable audit trail for MetricGuard agent runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from metricguard.config import settings


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ITERATION_LIMIT = "iteration_limit"
    FAILED = "failed"


class ToolTrace(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    error: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentRun(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    goal: str
    model: str
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    tool_traces: list[ToolTrace] = Field(default_factory=list)
    final_answer: str = ""
    error: str = ""


class AgentRunStore:
    """JSON-backed run log; safe to inspect without DataHub or an LLM key."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or settings.contracts_dir.parent / "runs"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.json"

    def path_for(self, run_id: str) -> Path:
        return self._path(run_id)

    def save(self, run: AgentRun) -> Path:
        path = self._path(run.id)
        path.write_text(run.model_dump_json(indent=2))
        return path

    def start(self, goal: str, model: str) -> AgentRun:
        run = AgentRun(goal=goal, model=model)
        self.save(run)
        return run

    def get(self, run_id: str) -> AgentRun | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        return AgentRun.model_validate(json.loads(path.read_text()))

    def list(self) -> list[AgentRun]:
        runs = [
            AgentRun.model_validate(json.loads(path.read_text()))
            for path in self.directory.glob("*.json")
        ]
        return sorted(runs, key=lambda run: run.started_at, reverse=True)

    def record_tool(
        self,
        run: AgentRun,
        name: str,
        arguments: dict[str, Any],
        result: object,
        *,
        error: str = "",
    ) -> AgentRun:
        rendered = str(result)
        if len(rendered) > 50_000:
            rendered = rendered[:50_000] + "\n[trace result truncated at 50,000 characters]"
        run.tool_traces.append(ToolTrace(
            name=name, arguments=arguments, result=rendered, error=error,
        ))
        self.save(run)
        return run

    def complete(
        self,
        run: AgentRun,
        answer: str,
        *,
        status: RunStatus = RunStatus.COMPLETED,
        error: str = "",
    ) -> AgentRun:
        run.status = status
        run.final_answer = answer
        run.error = error
        run.completed_at = datetime.now(timezone.utc)
        self.save(run)
        return run
