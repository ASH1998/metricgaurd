"""Starlette server and static exporter for Mission Control."""

from __future__ import annotations

import asyncio
import json
import shutil
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from metricguard.agent.runs import AgentRun, AgentRunStore
from metricguard.ui.contracts import build_mission_control_run


def dashboard_html() -> Path:
    return Path(str(files("metricguard.ui").joinpath("index.html")))


def create_app(store: AgentRunStore | None = None, *, preferred_run_id: str = "") -> Starlette:
    run_store = store or AgentRunStore()

    async def index(_: Request) -> FileResponse:
        return FileResponse(dashboard_html(), media_type="text/html")

    async def list_runs(_: Request) -> JSONResponse:
        runs = [build_mission_control_run(run).run.model_dump(mode="json") for run in run_store.list()]
        return JSONResponse({"schema_version": "1.0", "preferred_run_id": preferred_run_id, "runs": runs})

    async def get_run(request: Request) -> JSONResponse:
        run = run_store.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "run not found"}, status_code=404)
        return JSONResponse(build_mission_control_run(run).model_dump(mode="json"))

    async def stream_run(request: Request) -> StreamingResponse:
        run_id = request.path_params["run_id"]

        async def events():
            seen = -1
            while True:
                run = run_store.get(run_id)
                if run is None:
                    yield _sse("error", {"error": "run not found"})
                    return
                contract = build_mission_control_run(run)
                if len(contract.timeline) != seen:
                    seen = len(contract.timeline)
                    yield _sse("run", contract.model_dump(mode="json"))
                if run.completed_at is not None:
                    yield _sse("complete", {"run_id": run_id})
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.75)

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    return Starlette(routes=[
        Route("/", index),
        Route("/api/runs", list_runs),
        Route("/api/runs/{run_id}", get_run),
        Route("/api/stream/{run_id}", stream_run),
    ])


def export_run(run: AgentRun, output_dir: Path) -> Path:
    """Emit a zero-backend replay site using the same browser contract."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)
    shutil.copyfile(dashboard_html(), output_dir / "index.html")
    contract = build_mission_control_run(run)
    (data_dir / f"{run.id}.json").write_text(contract.model_dump_json(indent=2))
    index = {
        "schema_version": "1.0",
        "preferred_run_id": run.id,
        "runs": [contract.run.model_dump(mode="json")],
    }
    (data_dir / "index.json").write_text(json.dumps(index, indent=2, default=str))
    return output_dir / "index.html"


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
