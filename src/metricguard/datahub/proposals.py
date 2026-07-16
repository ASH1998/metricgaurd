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


class StaleEvidenceError(RuntimeError):
    """The evidence a proposal was staged on no longer matches DataHub.

    Raised at approval time when the canonical definition's current semantic
    signature differs from the snapshot recorded at staging — the world moved
    between staging and approval, so the write must not proceed on stale truth.
    """


class Proposal(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metric: str = ""
    kind: str                                   # glossary_term | tag | incident | document | structured_property
    target: str                                 # URN or entity name
    payload: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""                         # the agent's case for this write
    # Deterministic snapshot of what this proposal was staged on: the canonical
    # query's urn/dataset/dialect + its SemanticSignature dump at staging time.
    # Empty for proposals staged before re-verification existed (or raw agent
    # stages without graph provenance) — approval then skips verification.
    evidence: dict[str, Any] = Field(default_factory=dict)
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
        self._path(proposal.id).write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        path = self._path(proposal_id)
        if not path.exists():
            return None
        return Proposal.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def list(self, status: ProposalStatus | None = None) -> list[Proposal]:
        proposals = [
            Proposal.model_validate(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self.directory.glob("*.json"))
        ]
        if status is not None:
            proposals = [p for p in proposals if p.status == status]
        return sorted(proposals, key=lambda p: p.created_at)

    def verify_evidence(self, proposal: Proposal, client: DataHubClient) -> str:
        """Re-prove the staged evidence against DataHub's CURRENT state.

        Returns "verified" when the canonical definition's current semantic
        signature still equals the staging-time snapshot, or "unverified" when
        the proposal carries no snapshot (legacy / raw stages). Raises
        StaleEvidenceError when the definition changed or can no longer be
        read — the gate re-proves before it writes. Cosmetic SQL edits still
        pass: signature equality is the check, not text equality.
        """
        evidence = proposal.evidence or {}
        query_urn = evidence.get("query_urn", "")
        staged_signature = evidence.get("signature")
        if not query_urn or not staged_signature:
            return "unverified"

        current_sql = _current_query_sql(client, query_urn, evidence.get("dataset_urn", ""))
        if not current_sql.strip():
            raise StaleEvidenceError(
                f"could not re-read the canonical definition ({query_urn}) from "
                "DataHub — it may have been deleted or moved since staging"
            )

        from metricguard.comparison.diff import compare_signatures
        from metricguard.models import SemanticSignature
        from metricguard.signature.extractor import extract_signature

        staged = SemanticSignature.model_validate(staged_signature)
        current = extract_signature(
            current_sql, dialect=evidence.get("dialect") or settings.dialect
        )
        if current.model_dump(mode="json") == staged.model_dump(mode="json"):
            return "verified"

        diff = compare_signatures(staged, current, left_name="staged", right_name="current")
        changed = ", ".join(sorted({d.field for d in diff.diffs})) or "signature"
        raise StaleEvidenceError(
            f"the canonical definition changed since staging — semantic drift in: "
            f"{changed}. Re-investigate before writing."
        )

    def approve(
        self, proposal_id: str, client: DataHubClient, *, verify: bool = True
    ) -> Proposal:
        """Execute a pending proposal through the approval-gated write path.

        With verify=True (default), staged evidence is re-proven against
        DataHub first; a StaleEvidenceError leaves the proposal pending.
        """
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"No proposal '{proposal_id}'")
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(f"Proposal '{proposal_id}' is already {proposal.status.value}")
        if verify:
            self.verify_evidence(proposal, client)  # raises before any write

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


def _current_query_sql(client: DataHubClient, query_urn: str, dataset_urn: str) -> str:
    """Read the CURRENT SQL of a DataHub Query entity.

    Prefers the dataset-queries path (the exact shape graph discovery already
    uses live), falling back to a direct entity read for clients that resolve
    query urns there.
    """
    def statement_of(entity: dict[str, Any]) -> str:
        props = entity.get("properties") or {}
        for holder in (props, entity):
            statement = holder.get("statement")
            if isinstance(statement, dict):
                value = statement.get("value") or ""
                if value.strip():
                    return value
            if isinstance(statement, str) and statement.strip():
                return statement
        return ""

    if dataset_urn:
        try:
            for query in client.get_dataset_queries(dataset_urn):
                if query.get("urn", "") == query_urn:
                    return statement_of(query)
        except Exception:  # noqa: BLE001 — fall through to the direct read
            pass
    try:
        entity = client.get_entities(query_urn)
        if isinstance(entity, list):
            entity = entity[0] if entity else {}
        return statement_of(entity or {})
    except Exception:  # noqa: BLE001 — unreadable counts as "cannot re-verify"
        return ""
