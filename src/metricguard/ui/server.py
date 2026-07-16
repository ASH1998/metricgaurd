"""Starlette server and static exporter for Mission Control."""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from metricguard.agent.coordinator import PlannedInvestigation
from metricguard.agent.runs import AgentRun, AgentRunStore, RunOrigin, RunStatus
from metricguard.config import settings
from metricguard.ui.contracts import build_mission_control_run


AUTOMATIC_DISCOVERY_GOAL = (
    "Automatically scan the organization for conflicting business metric definitions, "
    "quantify material disagreements, and stage governed resolution proposals."
)


def dashboard_html() -> Path:
    return Path(str(files("metricguard.ui").joinpath("index.html")))


def create_app(
    store: AgentRunStore | None = None,
    *,
    preferred_run_id: str = "",
    replay_mode: bool = False,
    auto_discover: bool = False,
) -> Starlette:
    run_store = store or AgentRunStore()
    background_tasks: dict[str, asyncio.Task[None]] = {}

    def track(run_id: str, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        background_tasks[run_id] = task

        def discard(completed: asyncio.Task[None]) -> None:
            if background_tasks.get(run_id) is completed:
                background_tasks.pop(run_id, None)

        task.add_done_callback(discard)

    def spawn_child(item: PlannedInvestigation, parent: AgentRun) -> str:
        family = item.metric_family
        goal = (
            f"Investigate only the DataHub metric family `{family}`. Prove its most "
            f"material semantic conflict, assess organizational context, and stage a "
            f"resolution only if evidence supports one. Coordinator reason: {item.reason}"
        )
        child = run_store.start(
            goal,
            settings.llm_model,
            origin=RunOrigin.DELEGATED,
            trigger={
                "source": "agent_fan_out",
                "metric_family": family,
                "coordinator_reason": item.reason,
                "priority": item.priority,
            },
            parent_run_id=parent.id,
        )
        track(child.id, _run_investigation(goal, run_store, child))
        return child.id

    def launch(run: AgentRun, *, coordinate: bool) -> None:
        if coordinate:
            from metricguard.agent.coordinator import coordinate_conflict_investigations

            track(
                run.id,
                coordinate_conflict_investigations(run.goal, run_store, run, spawn_child),
            )
        else:
            track(run.id, _run_investigation(run.goal, run_store, run))

    async def index(_: Request) -> FileResponse:
        return FileResponse(dashboard_html(), media_type="text/html")

    async def start_automatic_discovery() -> None:
        """Kick off first-use discovery without asking a user to name an unknown conflict."""
        if replay_mode or not auto_discover or run_store.list():
            return
        run = run_store.start(
            AUTOMATIC_DISCOVERY_GOAL,
            settings.llm_model,
            origin=RunOrigin.AUTOMATIC,
            trigger={"source": "ui_startup", "scope": "organization_wide"},
        )
        launch(run, coordinate=True)

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

    async def get_run_proposals(request: Request) -> JSONResponse:
        run = run_store.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "run not found"}, status_code=404)
        from metricguard.datahub.proposals import ProposalStore

        proposal_store = ProposalStore()
        proposals = [
            proposal.model_dump(mode="json")
            for proposal_id in _proposal_ids(run)
            if (proposal := proposal_store.get(proposal_id)) is not None
        ]
        return JSONResponse({"run_id": run.id, "proposals": proposals})

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
        launch(run, coordinate=_should_coordinate(goal))
        return JSONResponse(
            build_mission_control_run(run).model_dump(mode="json"),
            status_code=202,
        )

    async def stop_run(request: Request) -> JSONResponse:
        if replay_mode:
            return JSONResponse({"error": "replay mode is passive"}, status_code=403)
        run_id = request.path_params["run_id"]
        run = run_store.get(run_id)
        if run is None:
            return JSONResponse({"error": "run not found"}, status_code=404)
        if run.status != RunStatus.RUNNING:
            return JSONResponse(
                {"error": f"run is already {run.status.value}"}, status_code=409,
            )
        canceled = run_store.cancel(run_id)
        task = background_tasks.pop(run_id, None)
        if task is not None:
            task.cancel()
        return JSONResponse(build_mission_control_run(canceled).model_dump(mode="json"))

    async def delete_run(request: Request) -> JSONResponse:
        if replay_mode:
            return JSONResponse({"error": "replay mode is passive"}, status_code=403)
        run_id = request.path_params["run_id"]
        run = run_store.get(run_id)
        if run is None:
            return JSONResponse({"error": "run not found"}, status_code=404)
        if run.status == RunStatus.RUNNING:
            run_store.cancel(run_id)
        task = background_tasks.pop(run_id, None)
        if task is not None:
            task.cancel()
        run_store.delete(run_id)
        return JSONResponse({"deleted": run_id})

    @asynccontextmanager
    async def lifespan(_: Starlette):
        await start_automatic_discovery()
        try:
            yield
        finally:
            tasks = list(background_tasks.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/runs", list_runs),
            Route("/api/runs/{run_id}/proposals", get_run_proposals),
            Route("/api/runs/{run_id}/stop", stop_run, methods=["POST"]),
            Route("/api/runs/{run_id}", delete_run, methods=["DELETE"]),
            Route("/api/runs/{run_id}", get_run),
            Route("/api/stream/{run_id}", stream_run),
            Route("/api/investigations", start_investigation, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def export_run(run: AgentRun, output_dir: Path) -> Path:
    """Emit a zero-backend replay site using the same browser contract."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)
    shutil.copyfile(dashboard_html(), output_dir / "index.html")
    contract = build_mission_control_run(run)
    (data_dir / f"{run.id}.json").write_text(contract.model_dump_json(indent=2), encoding="utf-8")
    index = {
        "schema_version": "1.0",
        "preferred_run_id": run.id,
        "runs": [contract.run.model_dump(mode="json")],
    }
    (data_dir / "index.json").write_text(
        json.dumps(index, indent=2, default=str), encoding="utf-8"
    )
    return output_dir / "index.html"


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _should_coordinate(goal: str) -> bool:
    normalized = " ".join(goal.lower().split())
    broad_signals = (
        "find conflicting",
        "find conflicts",
        "conflicting reports",
        "organization-wide",
        "scan the organization",
        "all metric",
    )
    return any(signal in normalized for signal in broad_signals)


def _proposal_ids(run: AgentRun) -> list[str]:
    proposal_ids: list[str] = []
    for trace in run.tool_traces:
        if trace.name != "tool_stage_canonical_resolution":
            continue
        try:
            result = json.loads(trace.result)
        except json.JSONDecodeError:
            continue
        if not isinstance(result, dict):
            continue
        candidates = [
            *result.get("staged_proposal_ids", []),
            *result.get("already_staged_or_executed_ids", []),
            *[
                item.get("id", "")
                for item in result.get("existing_resolution_proposals", [])
                if isinstance(item, dict)
            ],
        ]
        for proposal_id in candidates:
            if proposal_id and proposal_id not in proposal_ids:
                proposal_ids.append(proposal_id)
    return proposal_ids


async def _run_investigation(goal: str, store: AgentRunStore, run: AgentRun) -> None:
    from metricguard.agent.loop import arun_agent_result

    try:
        await arun_agent_result(goal, verbose=False, store=store, run=run)
    except Exception:
        # arun_agent_result already persists the failed state; the UI consumes it.
        return
