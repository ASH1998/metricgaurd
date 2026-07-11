"""MCP-backed DataHub client.

Connects to the DataHub MCP Server (https://github.com/acryldata/mcp-server-datahub)
via langchain-mcp-adapters, which also lets the same tools be handed straight
to the agent loop.

Transports (config):
  DATAHUB_MCP_TRANSPORT=stdio   -> runs DATAHUB_MCP_COMMAND (default: uvx mcp-server-datahub)
                                   auth via DATAHUB_GMS_URL / DATAHUB_TOKEN env
  DATAHUB_MCP_TRANSPORT=http    -> connects to DATAHUB_MCP_URL (streamable HTTP)

NOTE (Day 1-2 verification): the exact MCP tool names exposed by the server
must be confirmed against the live instance. `_CAPABILITIES` below maps our
interface to candidate tool names; `describe_tools()` prints what the server
actually exposes so the mapping can be locked down quickly.
"""

from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from typing import Any

from langchain_core.tools import BaseTool

from metricguard.config import settings
from metricguard.datahub.base import DataHubClient, WriteAction

# our capability -> tool name on the DataHub MCP server.
# VERIFIED against the live server (DataHub Core v1.5.0.6) on 2026-07-05 via
# `metricguard datahub tools` + a direct probe. These are the real names, not guesses.
_CAPABILITIES: dict[str, list[str]] = {
    # read side
    "search": ["search"],
    "get_dataset_queries": ["get_dataset_queries"],
    "get_lineage": ["get_lineage"],
    "get_entities": ["get_entities"],
    # write side — stock DataHub Core entities only (context.md).
    # NOTE: the server exposes NO create-glossary-term and NO create-incident tool.
    # `add_terms` only *attaches* a pre-existing term; incidents are out of MCP scope.
    "add_tags": ["add_tags"],
    "add_terms": ["add_terms"],
    "add_structured_properties": ["add_structured_properties"],
    "save_document": ["save_document"],
    "update_description": ["update_description"],
}


def _mcp_connections() -> dict[str, dict[str, Any]]:
    if settings.datahub_mcp_transport == "http":
        if not settings.datahub_mcp_url:
            raise RuntimeError("DATAHUB_MCP_TRANSPORT=http but DATAHUB_MCP_URL is empty")
        return {"datahub": {"transport": "streamable_http", "url": settings.datahub_mcp_url}}
    # stdio default — server reads DATAHUB_GMS_URL / DATAHUB_TOKEN from env
    command, *args = settings.datahub_mcp_command.split()
    env = {
        "DATAHUB_GMS_URL": settings.datahub_gms_url,
        "DATAHUB_GMS_TOKEN": settings.datahub_token,
        # MCP speaks over stdout; keep its diagnostic stderr usable in CLI/agent demos.
        "LOGURU_LEVEL": os.getenv("LOGURU_LEVEL", "WARNING"),
        "FASTMCP_LOG_LEVEL": os.getenv("FASTMCP_LOG_LEVEL", "WARNING"),
    }
    for key in (
        "TOOLS_IS_MUTATION_ENABLED",
        "TOOLS_IS_USER_ENABLED",
        "DATA_QUALITY_TOOLS_ENABLED",
        "DATAHUB_MCP_DOCUMENT_TOOLS_DISABLED",
        "SAVE_DOCUMENT_TOOL_ENABLED",
    ):
        value = os.getenv(key)
        if value is not None:
            env[key] = value

    return {"datahub": {
        "transport": "stdio",
        "command": command,
        "args": args,
        "env": env,
    }}


async def load_datahub_mcp_tools() -> list[BaseTool]:
    """The DataHub MCP tools as LangChain tools — hand these to the agent."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(_mcp_connections())
    return await client.get_tools()


def _structured_search_query(keyword: str) -> str:
    """Normalize human metric labels to DataHub's precise `/q a+b` syntax."""
    keyword = keyword.strip()
    if not keyword or keyword == "*" or keyword.startswith("/q"):
        return keyword or "*"
    tokens = [token for token in keyword.replace("_", " ").split() if token]
    return f"/q {'+'.join(tokens)}"


async def _gather_graph_reads(keyword: str) -> tuple[list[dict], dict[str, list[dict]]]:
    """All discovery reads over ONE MCP session.

    `get_tools()` yields session-less tools that respawn the stdio server on
    every `ainvoke` — so N datasets meant N ~10s server starts. Doing the
    search + every get_dataset_queries inside a single `client.session()`
    reuses one connection, turning ~N*10s into one startup.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    client = MultiServerMCPClient(_mcp_connections())
    async with client.session("datahub") as session:
        tools = {t.name: t for t in await load_mcp_tools(session)}
        search = MCPDataHubClient._unwrap(await tools["search"].ainvoke({
            "query": _structured_search_query(keyword),
            "filter": "entity_type = dataset",
            "num_results": 10,
        }))
        results = search.get("searchResults", []) if isinstance(search, dict) else []
        per_dataset: dict[str, list[dict]] = {}
        for r in results:
            urn = (r.get("entity") or {}).get("urn", "")
            if urn.startswith("urn:li:dataset:") and urn not in per_dataset:
                raw = MCPDataHubClient._unwrap(
                    await tools["get_dataset_queries"].ainvoke({"urn": urn})
                )
                per_dataset[urn] = raw.get("queries", []) if isinstance(raw, dict) else []
        return results, per_dataset


async def _gather_investigation_reads(
    keyword: str,
) -> tuple[list[dict], dict[str, list[dict]], list[dict], dict[str, dict]]:
    """Discovery plus entity and downstream-impact context over one MCP session."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    client = MultiServerMCPClient(_mcp_connections())
    async with client.session("datahub") as session:
        tools = {t.name: t for t in await load_mcp_tools(session)}
        search = MCPDataHubClient._unwrap(await tools["search"].ainvoke({
            "query": _structured_search_query(keyword),
            "filter": "entity_type = dataset",
            "num_results": 10,
        }))
        results = search.get("searchResults", []) if isinstance(search, dict) else []
        urns: list[str] = []
        per_dataset: dict[str, list[dict]] = {}
        for result in results:
            urn = (result.get("entity") or {}).get("urn", "")
            if not urn.startswith("urn:li:dataset:") or urn in per_dataset:
                continue
            urns.append(urn)
            raw = MCPDataHubClient._unwrap(
                await tools["get_dataset_queries"].ainvoke({"urn": urn})
            )
            per_dataset[urn] = raw.get("queries", []) if isinstance(raw, dict) else []

        entities: list[dict] = []
        if urns and "get_entities" in tools:
            raw_entities = MCPDataHubClient._unwrap(
                await tools["get_entities"].ainvoke({"urns": urns})
            )
            entities = raw_entities if isinstance(raw_entities, list) else [raw_entities]

        lineage: dict[str, dict[str, dict]] = {}
        if "get_lineage" in tools:
            for urn in urns:
                lineage[urn] = {}
                for direction, upstream in (("upstream", True), ("downstream", False)):
                    raw_lineage = MCPDataHubClient._unwrap(
                        await tools["get_lineage"].ainvoke({
                            "urn": urn,
                            "upstream": upstream,
                            "max_hops": 3,
                            "max_results": 30,
                        })
                    )
                    lineage[urn][direction] = (
                        raw_lineage if isinstance(raw_lineage, dict) else {"raw": raw_lineage}
                    )
        return results, per_dataset, entities, lineage


class MCPDataHubClient(DataHubClient):
    """DataHubClient implemented over the MCP server's tools.

    Sync facade (CLI + deterministic pipeline call this); internally async.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] | None = None

    # ---- plumbing -----------------------------------------------------

    def _ensure_tools(self) -> dict[str, BaseTool]:
        if self._tools is None:
            tools = asyncio.run(load_datahub_mcp_tools())
            self._tools = {t.name: t for t in tools}
        return self._tools

    def _resolve(self, capability: str) -> BaseTool:
        tools = self._ensure_tools()
        for candidate in _CAPABILITIES[capability]:
            if candidate in tools:
                return tools[candidate]
        raise RuntimeError(
            f"DataHub MCP server exposes no tool for '{capability}'. "
            f"Expected one of {_CAPABILITIES[capability]}; server has: {sorted(tools)}. "
            "Update _CAPABILITIES in metricguard/datahub/mcp_client.py."
        )

    @staticmethod
    def _unwrap(result: Any) -> Any:
        """Normalize an MCP tool result to parsed JSON.

        The DataHub MCP server returns content blocks:
        ``[{"type": "text", "text": "<json string>"}]``. Concatenate the text
        blocks and parse them; fall back to the raw value otherwise.
        """
        if (
            isinstance(result, list)
            and result
            and isinstance(result[0], dict)
            and "text" in result[0]
        ):
            result = "".join(b.get("text", "") for b in result if isinstance(b, dict))
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result
        return result

    def _call(self, capability: str, **kwargs: Any) -> Any:
        tool = self._resolve(capability)
        result = self._unwrap(asyncio.run(tool.ainvoke(kwargs)))
        # MCP tools report failures as an "Error calling tool ..." STRING rather
        # than raising. Surface those as exceptions so the approval gate never
        # marks a failed write as executed.
        if isinstance(result, str) and result.lstrip().startswith("Error calling tool"):
            raise RuntimeError(f"DataHub MCP '{capability}' failed: {result}")
        return result

    def describe_tools(self) -> list[dict[str, str]]:
        """What the server actually exposes — for locking down _CAPABILITIES."""
        return [
            {"name": t.name, "description": (t.description or "").split("\n")[0]}
            for t in self._ensure_tools().values()
        ]

    # ---- read side -----------------------------------------------------

    def search_queries(self, keyword: str) -> list[dict[str, Any]]:
        """Search entities; returns the raw searchResults (each carries entity.urn)."""
        result = self._call("search", query=keyword)
        if isinstance(result, dict):
            return result.get("searchResults", [])
        return result if isinstance(result, list) else [result]

    def get_dataset_queries(self, dataset_urn: str) -> list[dict[str, Any]]:
        """Query entities attached to a dataset — each carries the SQL statement."""
        result = self._call("get_dataset_queries", urn=dataset_urn)
        if isinstance(result, dict):
            return result.get("queries", [])
        return result if isinstance(result, list) else [result]

    def bulk_discovery(self, keyword: str) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """search + all get_dataset_queries over one session (fast discovery path).

        Returns (search_results, {dataset_urn: [queries]}).
        """
        return asyncio.run(_gather_graph_reads(keyword))

    def bulk_investigation(
        self, keyword: str,
    ) -> tuple[list[dict], dict[str, list[dict]], list[dict], dict[str, dict]]:
        """One-session graph evidence bundle used by the MetricGuard agent."""
        return asyncio.run(_gather_investigation_reads(keyword))

    def get_entities(self, urns: list[str] | str) -> list[dict[str, Any]] | dict[str, Any]:
        return self._call("get_entities", urns=urns)

    def get_lineage(
        self,
        urn: str,
        *,
        upstream: bool = True,
        max_hops: int = 1,
        max_results: int = 30,
    ) -> dict[str, Any]:
        result = self._call(
            "get_lineage", urn=urn, upstream=upstream,
            max_hops=max_hops, max_results=max_results,
        )
        return result if isinstance(result, dict) else {"raw": result}

    # ---- write side (reached only via the approval-gated base.write()) --

    # write kind -> the verified MCP mutation tool that realizes it.
    # `incident` and glossary-term *creation* have no MCP tool (see _CAPABILITIES);
    # `glossary_term` here means attaching a pre-existing term via add_terms.
    _WRITE_CAPABILITY: dict[str, str] = {
        "tag": "add_tags",
        "glossary_term": "add_terms",
        "structured_property": "add_structured_properties",
        "document": "save_document",
        "description": "update_description",
    }

    def _execute_write(self, action: WriteAction) -> WriteAction:
        capability = self._WRITE_CAPABILITY.get(action.kind)
        if capability is None:
            raise ValueError(
                f"No MCP tool realizes write kind '{action.kind}'. "
                f"Supported: {sorted(self._WRITE_CAPABILITY)}. "
                "(The server has no create-glossary-term or incident tool.)"
            )
        # NOTE: per-tool payload shaping (arg names differ across mutation tools)
        # is handled by the proposal builder in the write-back path (#4); the
        # staged proposal's `payload` is expected to already match the tool's args.
        self._call(capability, **action.payload)
        action.executed = True
        return action


@lru_cache(maxsize=1)
def get_mcp_client() -> MCPDataHubClient:
    return MCPDataHubClient()
