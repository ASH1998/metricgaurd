"""Central configuration, loaded from .env / environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class Settings:
    # LLM — LangChain provider-prefixed model string, so any provider works.
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "anthropic:claude-opus-4-8"))

    # Warehouse (empty DSN means execution layer raises NotConfigured)
    postgres_dsn: str = field(default_factory=lambda: os.getenv("POSTGRES_DSN", ""))

    # DataHub
    datahub_gms_url: str = field(default_factory=lambda: os.getenv("DATAHUB_GMS_URL", "http://localhost:8080"))
    datahub_token: str = field(default_factory=lambda: os.getenv("DATAHUB_TOKEN", ""))
    # MCP server connection: "stdio" runs datahub_mcp_command; "http" hits datahub_mcp_url;
    # empty transport = MCP disabled (StubDataHubClient is used).
    datahub_mcp_transport: str = field(default_factory=lambda: os.getenv("DATAHUB_MCP_TRANSPORT", ""))
    datahub_mcp_command: str = field(
        default_factory=lambda: os.getenv("DATAHUB_MCP_COMMAND", "uvx mcp-server-datahub")
    )
    datahub_mcp_url: str = field(default_factory=lambda: os.getenv("DATAHUB_MCP_URL", ""))

    # MetricGuard
    contracts_dir: Path = field(
        default_factory=lambda: Path(os.getenv("METRICGUARD_CONTRACTS_DIR", ".metricguard/contracts"))
    )
    dialect: str = field(default_factory=lambda: os.getenv("METRICGUARD_DIALECT", "postgres"))
    agent_max_iterations: int = field(
        default_factory=lambda: _positive_int(
            os.getenv("METRICGUARD_MAX_AGENT_ITERATIONS"), 40
        )
    )
    require_approval: bool = field(
        default_factory=lambda: _bool(os.getenv("METRICGUARD_REQUIRE_APPROVAL"), default=True)
    )


settings = Settings()
