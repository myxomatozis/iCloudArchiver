.PHONY: test lint type smoke

test:
	uv run pytest -v

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

type:
	uv run mypy src/icloud_archiver

smoke:
	@echo "Manual smoke tests live in tests/manual/README.md."
	@echo "Open it and work through the checklist before any real-library run."
