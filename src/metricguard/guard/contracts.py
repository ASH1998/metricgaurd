"""Guard mode — approved signature contracts + drift detection.

Guard is a second consumer of the Week-1 signature engine, not new machinery:
  contract = approved signature (captured after human approval)
  drift    = signature diff between new/changed SQL and the contract

Catches semantic breaks (window, filters, timezone, dedup, ...) while
ignoring cosmetic changes (aliases, formatting, predicate order).
"""

from __future__ import annotations

import json
from pathlib import Path

from metricguard.comparison.diff import compare_signatures
from metricguard.config import settings
from metricguard.models import Contract, DriftReport, DriftVerdict, SemanticSignature
from metricguard.signature.extractor import extract_signature


class ContractStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or settings.contracts_dir
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, metric: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in metric.lower())
        return self.directory / f"{safe}.json"

    def save(self, contract: Contract) -> Path:
        path = self._path(contract.metric)
        path.write_text(contract.model_dump_json(indent=2))
        return path

    def load(self, metric: str) -> Contract | None:
        path = self._path(metric)
        if not path.exists():
            return None
        return Contract.model_validate(json.loads(path.read_text()))

    def approve(self, metric: str, sql: str, approved_by: str = "",
                dialect: str | None = None) -> Contract:
        """Capture an approved definition's signature as the contract."""
        signature = extract_signature(sql, dialect=dialect or settings.dialect)
        contract = Contract(
            metric=metric, signature=signature,
            approved_by=approved_by, canonical_sql=sql,
        )
        self.save(contract)
        return contract

    def check_drift(self, metric: str, new_sql: str, dialect: str | None = None) -> DriftReport:
        """Compare new/changed SQL against the approved contract.

        Semantic differences -> DRIFT (warn before ship).
        Cosmetic-only changes -> OK (don't cry wolf).
        """
        contract = self.load(metric)
        if contract is None:
            return DriftReport(
                metric=metric, verdict=DriftVerdict.NO_CONTRACT,
                message=f"No approved contract for '{metric}'. "
                        f"Approve one first: metricguard guard approve {metric} <file.sql>",
            )

        new_signature: SemanticSignature = extract_signature(
            new_sql, dialect=dialect or settings.dialect
        )
        report = compare_signatures(
            contract.signature, new_signature,
            left_name=f"contract:{metric}", right_name="proposed change",
        )

        if report.is_conflict:
            fields = ", ".join(d.field for d in report.diffs)
            return DriftReport(
                metric=metric, verdict=DriftVerdict.DRIFT, diffs=report.diffs,
                message=f"SEMANTIC BREAK from approved definition of '{metric}': {fields} changed.",
            )
        return DriftReport(
            metric=metric, verdict=DriftVerdict.OK,
            message="No semantic drift — changes are cosmetic only.",
        )
