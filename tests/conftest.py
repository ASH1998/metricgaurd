from pathlib import Path

import pytest

SEEDS = Path(__file__).parent.parent / "seeds" / "metric_families" / "weekly_active_users"


@pytest.fixture()
def marketing_sql() -> str:
    return (SEEDS / "marketing_wau.sql").read_text()


@pytest.fixture()
def product_sql() -> str:
    return (SEEDS / "product_wau.sql").read_text()


@pytest.fixture()
def finance_sql() -> str:
    return (SEEDS / "finance_wau.sql").read_text()
