"""Keep every public divergence claim tied to the frozen proof manifest."""

import json
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "warehouse_proofs.json"


def _proofs() -> dict[str, dict[str, object]]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {proof["id"]: proof for proof in payload["proofs"]}


def _fixture_sha256() -> str:
    records = []
    for path in sorted((ROOT / "data" / "fiction_retail").glob("*.csv.gz")):
        file_hash = sha256(path.read_bytes()).hexdigest()
        records.append(f"{file_hash}  {path.relative_to(ROOT)}\n")
    return sha256("".join(records).encode()).hexdigest()


def test_frozen_manifest_identifies_immutable_demo_fixture():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fixture = payload["fixture"]

    assert fixture["path"] == "data/fiction_retail"
    assert fixture["introduced_by_commit"] == "27debb847c0198f17c198046eeef0136173bd379"
    assert fixture["hash_scheme"] == (
        "SHA-256 of lexically sorted '<file SHA-256>  <relative path>' records"
    )
    assert fixture["sha256"] == _fixture_sha256()
    assert payload["frozen_on"] == "2026-07-12"


def test_weekly_revenue_evidence_matches_frozen_proof():
    evidence = json.loads(
        (ROOT / "examples" / "weekly_revenue_evidence.json").read_text(encoding="utf-8")
    )
    proof = _proofs()["weekly_revenue"]
    warehouse_proof = evidence["finance_vs_executive"]["warehouse_proof"]

    for field in (
        "periods_compared",
        "mean_pct_divergence",
        "max_pct_divergence",
        "total_abs_divergence",
        "first_divergence_key",
    ):
        assert warehouse_proof[field] == proof[field]


def test_public_docs_repeat_only_frozen_proof_figures():
    revenue = _proofs()["weekly_revenue"]
    order_volume = _proofs()["weekly_order_volume"]
    refunds = _proofs()["weekly_refund_amount"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pitch = (ROOT / "docs" / "pitch.md").read_text(encoding="utf-8")
    invoke = (ROOT / "docs" / "invoke.md").read_text(encoding="utf-8")
    progress = (ROOT / "progress.md").read_text(encoding="utf-8")

    assert f"{revenue['mean_pct_divergence']:.2f}% / {revenue['max_pct_divergence']:.2f}%" in readme
    assert f"${revenue['total_abs_divergence']:,.2f}" in readme
    assert f"{order_volume['mean_pct_divergence']:.2f}% / {order_volume['max_pct_divergence']:.2f}%" in readme
    assert f"{refunds['mean_pct_divergence']:.2f}% / {refunds['max_pct_divergence']:.2f}%" in readme
    assert "13.07% mean / 16.59% max" in pitch
    assert "mean 13.07% · max 16.59%" in invoke
    assert "$28,917,693.16" in progress
    public_docs = "\n".join((readme, pitch, invoke, progress))
    assert "15.06%" not in public_docs
    assert "19.89%" not in public_docs
