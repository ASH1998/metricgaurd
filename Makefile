.PHONY: demo demo-down test lint

# Fresh machine -> working demo (warehouse + DataHub + simulated org).
demo:
	bash scripts/setup_demo.sh

# Tear down the demo warehouse (DataHub teardown: `uv run datahub docker nuke`).
demo-down:
	docker compose -f docker-compose.demo.yml down -v

test:
	uv run pytest -q

lint:
	uv run ruff check src tests
