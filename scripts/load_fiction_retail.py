"""One-time loader: fiction-retail CSVs -> Postgres (the demo warehouse).

Reads connection details from .env (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD
/DB_SCHEMA) — the password is never hardcoded here. Idempotent: drops and
recreates each table, then COPYs the CSV in. Reads plain `.csv` or the
committed `.csv.gz` transparently.

    .venv/bin/python scripts/load_fiction_retail.py [--data-dir data/fiction_retail]

After this, run DataHub ingestion against the target schema so the tables are
cataloged (see README / the datahub source recipe).
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

# Typed DDL per table. IDs are UUID strings (kept as text — they're join keys,
# no math); dates are ISO; amounts numeric; quantities integer. Column order
# matches the CSV headers exactly (COPY relies on this).
TABLES: dict[str, list[tuple[str, str]]] = {
    "customers": [
        ("customer_id", "text primary key"), ("name", "text"), ("email", "text"),
        ("phone", "text"), ("signup_date", "date"), ("country", "text"),
        ("state", "text"), ("city", "text"), ("customer_segment", "text"),
    ],
    "products": [
        ("product_id", "text primary key"), ("name", "text"), ("category", "text"),
        ("brand", "text"), ("price", "numeric"), ("weight_kg", "numeric"),
        ("supplier_id", "text"),
    ],
    "suppliers": [
        ("supplier_id", "text primary key"), ("name", "text"), ("country", "text"),
        ("contract_start_date", "date"), ("status", "text"),
    ],
    "warehouses": [
        ("warehouse_id", "text primary key"), ("name", "text"), ("city", "text"),
        ("state", "text"), ("country", "text"), ("capacity_units", "integer"),
        ("opened_date", "date"),
    ],
    "promotions": [
        ("promo_id", "text primary key"), ("promo_code", "text"), ("description", "text"),
        ("discount_pct", "numeric"), ("valid_from", "date"), ("valid_until", "date"),
        ("applies_to_category", "text"), ("max_uses", "integer"), ("status", "text"),
    ],
    "orders": [
        ("order_id", "text primary key"), ("customer_id", "text"), ("order_date", "date"),
        ("order_status", "text"), ("total_amount", "numeric"), ("payment_method", "text"),
        ("shipping_country", "text"), ("promo_id", "text"),
    ],
    "order_items": [
        ("order_item_id", "text primary key"), ("order_id", "text"), ("product_id", "text"),
        ("quantity", "integer"), ("unit_price", "numeric"), ("discount_pct", "numeric"),
    ],
    "shipments": [
        ("shipment_id", "text primary key"), ("order_id", "text"), ("warehouse_id", "text"),
        ("carrier", "text"), ("tracking_number", "text"), ("shipped_date", "date"),
        ("delivered_date", "date"), ("shipment_state", "text"),
    ],
    "inventory": [
        ("inventory_id", "text primary key"), ("product_id", "text"), ("warehouse_id", "text"),
        ("quantity_on_hand", "integer"), ("reserved_quantity", "integer"),
        ("reorder_threshold", "integer"), ("last_restocked_date", "date"),
    ],
    "returns": [
        ("return_id", "text primary key"), ("order_id", "text"), ("product_id", "text"),
        ("return_date", "date"), ("refund_amount", "numeric"),
        ("return_reason_code", "text"), ("processed_by", "text"),
    ],
}

# Load order respects FK-ish dependencies (not enforced, but keeps things tidy)
LOAD_ORDER = [
    "customers", "products", "suppliers", "warehouses", "promotions",
    "orders", "order_items", "shipments", "inventory", "returns",
]


def conn_kwargs(env: dict[str, str | None]) -> dict[str, object]:
    missing = [k for k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD") if not env.get(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    return {
        "host": env["DB_HOST"], "port": int(env.get("DB_PORT") or 5432),
        "dbname": env["DB_NAME"], "user": env["DB_USER"], "password": env["DB_PASSWORD"],
    }


def load(data_dir: Path, env: dict[str, str | None]) -> None:
    schema = (env.get("DB_SCHEMA") or "public").strip()
    with psycopg.connect(**conn_kwargs(env)) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            for name in LOAD_ORDER:
                csv_path = data_dir / f"{name}.csv"
                gz_path = data_dir / f"{name}.csv.gz"
                if not csv_path.exists() and not gz_path.exists():
                    print(f"  ! skip {name}: {csv_path}(.gz) not found")
                    continue
                cols = TABLES[name]
                coldefs = ", ".join(f'"{c}" {t}' for c, t in cols)
                cur.execute(f'DROP TABLE IF EXISTS "{schema}"."{name}" CASCADE')
                cur.execute(f'CREATE TABLE "{schema}"."{name}" ({coldefs})')

                collist = ", ".join(f'"{c}"' for c, _ in cols)
                copy_sql = (
                    f'COPY "{schema}"."{name}" ({collist}) '
                    f"FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
                )
                opener = (
                    (lambda: gzip.open(gz_path, "rb"))
                    if not csv_path.exists()
                    else (lambda: open(csv_path, "rb"))
                )
                with opener() as fh, cur.copy(copy_sql) as copy:
                    while chunk := fh.read(1 << 20):
                        copy.write(chunk)
                cur.execute(f'SELECT count(*) FROM "{schema}"."{name}"')
                print(f"  ✓ {schema}.{name:<12} {cur.fetchone()[0]:>8,} rows")
        conn.commit()
    print(f"\nDone. Tables loaded into schema '{schema}'.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/fiction_retail", type=Path)
    ap.add_argument("--env", default=".env", type=Path)
    args = ap.parse_args()
    env = dotenv_values(args.env)
    print(f"Loading fiction-retail CSVs from {args.data_dir} -> "
          f"{env.get('DB_HOST')}/{env.get('DB_NAME')} schema '{env.get('DB_SCHEMA')}'\n")
    load(args.data_dir, env)


if __name__ == "__main__":
    main()
