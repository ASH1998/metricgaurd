"""Write-back proposal staging — how the agent 'does things' safely.

The agent investigates and STAGES concrete write-back proposals here
(files under .metricguard/proposals/). A human reviews them via
`metricguard proposals list` and executes with `metricguard proposals approve`,
which routes through the approval-gated DataHubClient.write().

This is the human-in-the-loop seam: agent proposes -> human approves ->
mutation executes -> visible in the DataHub UI.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from metricguard.config import settings
from metricguard.datahub.base import DataHubClient, WriteAction


class ProposalStatus(str, Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"


class Proposal(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metric: str = ""
    kind: str                                   # glossary_term | tag | incident | document | structured_property
    target: str                                 # URN or entity name
    payload: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""                         # the agent's case for this write
    status: ProposalStatus = ProposalStatus.PENDING
    resolved_at: datetime | None = None

    def to_action(self) -> WriteAction:
        return WriteAction(kind=self.kind, target=self.target, payload=self.payload)


class ProposalStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or settings.contracts_dir.parent / "proposals"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, proposal_id: str) -> Path:
        return self.directory / f"{proposal_id}.json"

    def stage(self, proposal: Proposal) -> Proposal:
        self._path(proposal.id).write_text(proposal.model_dump_json(indent=2))
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        path = self._path(proposal_id)
        if not path.exists():
            return None
        return Proposal.model_validate(json.loads(path.read_text()))

    def list(self, status: ProposalStatus | None = None) -> list[Proposal]:
        proposals = [
            Proposal.model_validate(json.loads(p.read_text()))
            for p in sorted(self.directory.glob("*.json"))
        ]
        if status is not None:
            proposals = [p for p in proposals if p.status == status]
        return sorted(proposals, key=lambda p: p.created_at)

    def approve(self, proposal_id: str, client: DataHubClient) -> Proposal:
        """Execute a pending proposal through the approval-gated write path."""
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"No proposal '{proposal_id}'")
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(f"Proposal '{proposal_id}' is already {proposal.status.value}")

        client.write(proposal.to_action(), approved=True)  # the human said yes — this IS the approval
        proposal.status = ProposalStatus.EXECUTED
        proposal.resolved_at = datetime.now(timezone.utc)
        self.stage(proposal)
        return proposal

    def reject(self, proposal_id: str) -> Proposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"No proposal '{proposal_id}'")
        proposal.status = ProposalStatus.REJECTED
        proposal.resolved_at = datetime.now(timezone.utc)
        self.stage(proposal)
        return proposal
