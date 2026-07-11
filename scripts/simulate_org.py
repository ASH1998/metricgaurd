"""Simulate a real org's metric metadata inside DataHub.

MetricGuard's whole premise is that it discovers conflicting metric definitions
*from the graph*. For that to be true, DataHub has to actually contain the org:
several teams, each owning a derived asset (a dbt model or a dashboard tile),
each backed by a SQL query that computes "the same" business metric differently,
all drawing lineage from a handful of shared source tables.

This script emits exactly that, sourced from the seed manifests so there is one
source of truth (`seeds/metric_families/<family>/manifest.json` + the `.sql`).
It emits only stock DataHub Core entities — nothing depends on the metrics PR:

    Domain            one per department        (Finance, Marketing, ...)
    CorpGroup         one per owning team       (finance-data, bi-team, ...)
    Dataset (derived) one per metric definition (the dbt model / dashboard tile)
    Query             one per metric definition (carries the actual SQL) <-- the
                      discovery hook: get_dataset_queries + search surface these
    UpstreamLineage   derived asset  <-  shared source tables
    Ownership+Domain  ties each asset to a team + department

Deliberately NOT emitted: a canonical glossary term linking the definitions.
That linkage is MetricGuard's *write-back* payoff — pre-seeding it would give
away the answer the agent is supposed to discover.

Usage
-----
    # validate everything offline (builds every aspect, no network):
    python scripts/simulate_org.py --dry-run

    # actually push to DataHub (needs a reachable GMS + working token):
    python scripts/simulate_org.py --emit

Config is read from .env: DATAHUB_GMS_URL, DATAHUB_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from datahub.emitter import mce_builder as builder
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    AuditStampClass,
    CorpGroupInfoClass,
    DatasetLineageTypeClass,
    DatasetPropertiesClass,
    DomainPropertiesClass,
    DomainsClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    QueryLanguageClass,
    QueryPropertiesClass,
    QuerySourceClass,
    QueryStatementClass,
    QuerySubjectClass,
    QuerySubjectsClass,
    SubTypesClass,
    UpstreamClass,
    UpstreamLineageClass,
)

REPO = Path(__file__).resolve().parent.parent
SEEDS = REPO / "seeds" / "metric_families"
ENV = REPO / ".env"

# --- source-warehouse identity ------------------------------------------------
# The Postgres connector cataloged the demo warehouse as postgres.metric.*
# (see CLAUDE.md). If the live catalog uses a different dataset-name convention,
# change ONLY this — lineage will re-point. Verify once with:
#   datahub get --urn "<a source urn printed by --dry-run>"
SOURCE_PLATFORM = "postgres"
SOURCE_DB = "postgres"
SOURCE_SCHEMA = "metric"
ENV_FABRIC = "PROD"
ACTOR = builder.make_user_urn("datahub")

# Source tables that are NOT ingested (WAU family is signature-only per CLAUDE.md)
# get a lightweight catalog stub so lineage resolves instead of dangling.
STUB_SOURCE_TABLES = {"events", "billable_events"}

# team -> (display name, department/domain id, domain display)
TEAMS: dict[str, tuple[str, str, str]] = {
    "finance-data": ("Finance Data", "finance", "Finance"),
    "marketing-analytics": ("Marketing Analytics", "marketing", "Marketing"),
    "product-analytics": ("Product Analytics", "product", "Product"),
    "sales-operations": ("Sales Operations", "sales_ops", "Sales Ops"),
    "bi-team": ("BI / Executive", "bi", "Business Intelligence"),
    "fulfillment-analytics": ("Fulfillment Analytics", "operations", "Operations"),
    "customer-success": ("Customer Success", "customer_experience", "Customer Experience"),
    "risk-analytics": ("Risk Analytics", "risk", "Risk"),
}

# Which source tables each definition reads (from the seed SQL). Kept explicit
# rather than parsed so the org wiring is obvious and demo-narratable.
UPSTREAMS: dict[str, list[str]] = {
    "exec_dashboard_weekly_revenue": ["orders"],
    "finance_weekly_revenue": ["orders"],
    "sales_ops_weekly_revenue": ["order_items", "orders"],
    "marketing_wau": ["events"],
    "product_wau": ["events"],
    "finance_wau": ["billable_events"],
    "fulfillment_order_volume": ["orders"],
    "sales_accepted_orders": ["orders"],
    "exec_checkout_count": ["orders"],
    "finance_refund_liability": ["returns"],
    "support_customer_refunds": ["returns"],
    "risk_refund_exposure": ["returns"],
}

# Synthetic "when this definition landed" dates — staggered so the graph reads
# like history accreting over time (and echoes the divergence-since story).
CREATED_ON: dict[str, str] = {
    "exec_dashboard_weekly_revenue": "2022-09-12",
    "finance_weekly_revenue": "2022-11-03",
    "sales_ops_weekly_revenue": "2023-02-20",
    "marketing_wau": "2022-08-01",
    "product_wau": "2022-10-18",
    "finance_wau": "2023-01-09",
    "fulfillment_order_volume": "2023-03-06",
    "sales_accepted_orders": "2023-04-17",
    "exec_checkout_count": "2023-06-12",
    "finance_refund_liability": "2023-05-01",
    "support_customer_refunds": "2023-07-10",
    "risk_refund_exposure": "2023-09-18",
}


def ts(date_str: str) -> int:
    return builder.make_ts_millis(
        datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    )


def audit(date_str: str) -> AuditStampClass:
    return AuditStampClass(time=ts(date_str), actor=ACTOR)


def source_table_urn(table: str) -> str:
    return builder.make_dataset_urn(
        platform=SOURCE_PLATFORM,
        name=f"{SOURCE_DB}.{SOURCE_SCHEMA}.{table}",
        env=ENV_FABRIC,
    )


# ------------------------------------------------------------------------------
# derive the asset shape (platform + name + subtype) from the manifest `source`
# ------------------------------------------------------------------------------
def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in text]
    out = "".join(keep)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


@dataclass
class Asset:
    """A derived metric asset as it should appear in DataHub."""

    name: str  # definition name (e.g. finance_weekly_revenue)
    family: str
    sql: str
    owner: str  # team id (corpGroup)
    source: str  # raw manifest source string
    platform: str  # dbt | superset
    dataset_name: str  # platform-native asset name
    subtype: str

    @property
    def dataset_urn(self) -> str:
        return builder.make_dataset_urn(
            platform=self.platform, name=self.dataset_name, env=ENV_FABRIC
        )

    @property
    def query_urn(self) -> str:
        return f"urn:li:query:mg_{self.family}_{self.name}"

    @property
    def upstream_urns(self) -> list[str]:
        return [source_table_urn(t) for t in UPSTREAMS[self.name]]


def build_asset(family: str, defn: dict) -> Asset:
    source = defn["source"]
    if source.startswith("dbt model:"):
        # "dbt model: marts/finance/weekly_revenue.sql" -> marts.finance.weekly_revenue
        path = source.split(":", 1)[1].strip().removesuffix(".sql")
        dataset_name = path.replace("/", ".")
        platform, subtype = "dbt", "Model"
    elif source.startswith("dashboard query:"):
        # "dashboard query: Executive KPIs / Revenue tile" -> exec_kpis.revenue_tile
        label = source.split(":", 1)[1].strip()
        dataset_name = ".".join(_slug(p) for p in label.split("/"))
        platform, subtype = "superset", "Dashboard Tile"
    else:
        dataset_name = _slug(defn["name"])
        platform, subtype = "postgres", "View"

    sql_path = SEEDS / family / defn["file"]
    return Asset(
        name=defn["name"],
        family=family,
        sql=sql_path.read_text().strip(),
        owner=defn["owner"],
        source=source,
        platform=platform,
        dataset_name=dataset_name,
        subtype=subtype,
    )


def load_assets() -> list[Asset]:
    assets: list[Asset] = []
    for manifest_path in sorted(SEEDS.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        family = manifest["family"]
        for defn in manifest["definitions"]:
            assets.append(build_asset(family, defn))
    return assets


# ------------------------------------------------------------------------------
# aspect construction -> MetadataChangeProposalWrapper list
# ------------------------------------------------------------------------------
def build_mcps(assets: list[Asset]) -> list[MetadataChangeProposalWrapper]:
    mcps: list[MetadataChangeProposalWrapper] = []

    # 1. Domains (departments)
    seen_domains: set[str] = set()
    for _, domain_id, domain_name in TEAMS.values():
        if domain_id in seen_domains:
            continue
        seen_domains.add(domain_id)
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=builder.make_domain_urn(domain_id),
                aspect=DomainPropertiesClass(
                    name=domain_name,
                    description=f"{domain_name} department — simulated org for MetricGuard.",
                ),
            )
        )

    # 2. CorpGroups (owning teams)
    for team_id, (display, _domain_id, _domain_name) in TEAMS.items():
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=builder.make_group_urn(team_id),
                aspect=CorpGroupInfoClass(
                    admins=[],
                    members=[],
                    groups=[],
                    displayName=display,
                    description=f"{display} team.",
                ),
            )
        )

    # 3. Stub source tables that were never ingested (so lineage resolves)
    for table in sorted(STUB_SOURCE_TABLES):
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=source_table_urn(table),
                aspect=DatasetPropertiesClass(
                    name=table,
                    qualifiedName=f"{SOURCE_SCHEMA}.{table}",
                    description=f"Raw event source `{table}` (catalog stub — signature-only, no rows).",
                    customProperties={"metricguard_stub": "true"},
                ),
            )
        )

    # 4. Derived metric assets + query entities
    for a in assets:
        _domain_id = TEAMS[a.owner][1]
        created = CREATED_ON[a.name]

        # 4a. dataset properties
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=a.dataset_urn,
                aspect=DatasetPropertiesClass(
                    name=a.dataset_name.split(".")[-1],
                    qualifiedName=a.dataset_name,
                    description=f"{a.family.replace('_', ' ')} as computed by {a.owner} ({a.source}).",
                    customProperties={
                        "metric_family": a.family,
                        "owner_team": a.owner,
                        "asset_source": a.source,
                    },
                    created=audit(created),
                ),
            )
        )
        # 4b. subtype (dbt Model vs Dashboard Tile)
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=a.dataset_urn,
                aspect=SubTypesClass(typeNames=[a.subtype]),
            )
        )
        # 4c. ownership -> team
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=a.dataset_urn,
                aspect=OwnershipClass(
                    owners=[
                        OwnerClass(
                            owner=builder.make_group_urn(a.owner),
                            type=OwnershipTypeClass.DATAOWNER,
                        )
                    ]
                ),
            )
        )
        # 4d. domain -> department
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=a.dataset_urn,
                aspect=DomainsClass(domains=[builder.make_domain_urn(_domain_id)]),
            )
        )
        # 4e. upstream lineage: derived asset <- shared source tables
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=a.dataset_urn,
                aspect=UpstreamLineageClass(
                    upstreams=[
                        UpstreamClass(
                            dataset=src,
                            type=DatasetLineageTypeClass.TRANSFORMED,
                            auditStamp=audit(created),
                        )
                        for src in a.upstream_urns
                    ]
                ),
            )
        )
        # 4f. the Query entity — carries the actual SQL (THE discovery hook)
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=a.query_urn,
                aspect=QueryPropertiesClass(
                    statement=QueryStatementClass(
                        value=a.sql, language=QueryLanguageClass.SQL
                    ),
                    source=QuerySourceClass.MANUAL,
                    created=audit(created),
                    lastModified=audit(created),
                    name=f"{a.owner}: {a.family}",
                    description=f"{a.family} definition owned by {a.owner} ({a.source}).",
                ),
            )
        )
        # 4g. query subjects: link query to its derived asset + source tables
        mcps.append(
            MetadataChangeProposalWrapper(
                entityUrn=a.query_urn,
                aspect=QuerySubjectsClass(
                    subjects=[
                        QuerySubjectClass(entity=a.dataset_urn),
                        *[QuerySubjectClass(entity=u) for u in a.upstream_urns],
                    ]
                ),
            )
        )

    return mcps


# ------------------------------------------------------------------------------
# output
# ------------------------------------------------------------------------------
def print_plan(assets: list[Asset], mcps: list[MetadataChangeProposalWrapper]) -> None:
    print("\n=== SIMULATED ORG ===")
    by_domain: dict[str, list[Asset]] = {}
    for a in assets:
        by_domain.setdefault(TEAMS[a.owner][2], []).append(a)
    for domain, items in sorted(by_domain.items()):
        print(f"\n  {domain}")
        for a in items:
            ups = ", ".join(t for t in UPSTREAMS[a.name])
            print(f"    - {a.owner:<20} {a.platform}:{a.dataset_name}")
            print(f"        query {a.query_urn}")
            print(f"        reads {ups}")

    print("\n=== CONFLICT FAMILIES (what MetricGuard should rediscover) ===")
    fam: dict[str, list[str]] = {}
    for a in assets:
        fam.setdefault(a.family, []).append(a.owner)
    for family, owners in fam.items():
        print(f"  {family}: {len(owners)} competing defs across {', '.join(owners)}")

    counts: dict[str, int] = {}
    for m in mcps:
        counts[m.aspectName or "?"] = counts.get(m.aspectName or "?", 0) + 1
    print("\n=== ASPECTS TO EMIT ===")
    for name, n in sorted(counts.items()):
        print(f"  {n:>3}  {name}")
    print(f"  ---\n  {len(mcps):>3}  total metadata change proposals")


def read_env(key: str) -> str | None:
    if not ENV.exists():
        return None
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def emit(mcps: list[MetadataChangeProposalWrapper]) -> None:
    from datahub.emitter.rest_emitter import DataHubRestEmitter

    gms = read_env("DATAHUB_GMS_URL")
    token = read_env("DATAHUB_TOKEN")
    if not gms:
        sys.exit("DATAHUB_GMS_URL not set in .env")
    print(f"Connecting to {gms} ...")
    emitter = DataHubRestEmitter(gms_server=gms, token=token)
    emitter.test_connection()  # fails fast on unreachable GMS / bad auth
    print("Connection OK. Emitting ...")
    for i, mcp in enumerate(mcps, 1):
        emitter.emit(mcp)
        if i % 10 == 0 or i == len(mcps):
            print(f"  emitted {i}/{len(mcps)}")
    print("Done. Open the DataHub UI and browse the Finance / Marketing / ... domains.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", help="build+validate aspects, no network (default)")
    grp.add_argument("--emit", action="store_true", help="push to DataHub")
    args = ap.parse_args()

    assets = load_assets()
    mcps = build_mcps(assets)  # constructing these validates every aspect
    print_plan(assets, mcps)

    if args.emit:
        emit(mcps)
    else:
        print("\n[dry-run] all aspects built successfully — nothing sent. Use --emit to push.")


if __name__ == "__main__":
    main()
