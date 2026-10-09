.PHONY: setup check format smoke build check-dist release-check docs docs-serve api-types

setup:
	uv sync --locked
	pnpm --dir web install --frozen-lockfile
	pnpm --dir docs install --frozen-lockfile

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest
	node --test bridges/codex-acp/cleanup.test.mjs bridges/claude-acp/cleanup.test.mjs
	uv run scripts/generate-api-types.py --check
	pnpm --dir web check
	pnpm --dir web build
	$(MAKE) docs

format:
	uv run ruff format .
	pnpm --dir web format

smoke:
	pnpm --dir web build
	pnpm --dir web test:e2e

build:
	pnpm --dir web build
	uv build --clear --no-sources

check-dist:
	uv run --no-sync python scripts/check_dist.py

release-check: check smoke build check-dist
	uv publish --dry-run --trusted-publishing never dist/*.whl dist/*.tar.gz

docs:
	pnpm --dir docs check

docs-serve:
	pnpm --dir docs dev

api-types:
	uv run scripts/generate-api-types.py
