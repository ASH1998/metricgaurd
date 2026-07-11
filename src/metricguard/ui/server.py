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
from metricguard.config import settings
from metricguard.ui.contracts import build_mission_control_run


def dashboard_html() -> Path:
    return Path(str(files("metricguard.ui").joinpath("index.html")))


def create_app(
    store: AgentRunStore | None = None,
    *,
    preferred_run_id: str = "",
    replay_mode: bool = False,
) -> Starlette:
    run_store = store or AgentRunStore()
    background_tasks: set[asyncio.Task[None]] = set()

    async def index(_: Request) -> FileResponse:
        return FileResponse(dashboard_html(), media_type="text/html")

    async def list_runs(_: Request) -> JSONResponse:
        runs = [build_mission_control_run(run).run.model_dump(mode="json") for run in run_store.list()]
        return JSONResponse({
            "schema_version": "1.0",
            "mode": "replay" if replay_mode else "live",
            "preferred_run_id": preferred_run_id,
            "runs": runs,
        })

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
                    if run_store.path_for(run_id).exists():
                        await asyncio.sleep(0.1)
                        continue
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

    async def start_investigation(request: Request) -> JSONResponse:
        if replay_mode:
            return JSONResponse(
                {"error": "replay mode does not accept investigations"},
                status_code=403,
            )
        content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            return JSONResponse(
                {"error": "content-type must be application/json"},
                status_code=415,
            )
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "request body must be JSON"}, status_code=400)
        goal = str(payload.get("goal", "")).strip() if isinstance(payload, dict) else ""
        if len(goal) < 10:
            return JSONResponse(
                {"error": "goal must describe an investigation in at least 10 characters"},
                status_code=422,
            )
        run = run_store.start(goal, settings.llm_model)
        task = asyncio.create_task(_run_investigation(goal, run_store, run))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return JSONResponse(
            build_mission_control_run(run).model_dump(mode="json"),
            status_code=202,
        )

    return Starlette(routes=[
        Route("/", index),
        Route("/api/runs", list_runs),
        Route("/api/runs/{run_id}", get_run),
        Route("/api/stream/{run_id}", stream_run),
        Route("/api/investigations", start_investigation, methods=["POST"]),
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


async def _run_investigation(goal: str, store: AgentRunStore, run: AgentRun) -> None:
    from metricguard.agent.loop import arun_agent_result

    try:
        await arun_agent_result(goal, verbose=False, store=store, run=run)
    except Exception:
        # arun_agent_result already persists the failed state; the UI consumes it.
        return
