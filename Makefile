.PHONY: setup check format smoke build check-dist release-check docs docs-serve api-types

setup:
	uv sync --locked
	pnpm --dir web install --frozen-lockfile
	pnpm --dir docs install --frozen-lockfile
	pnpm --dir runtimes/claude-sdk install --frozen-lockfile --ignore-scripts
	node runtimes/claude-sdk/build.mjs --host

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest
	node --test runtimes/claude-sdk/cleanup.test.mjs
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
	pnpm --dir runtimes/claude-sdk install --frozen-lockfile --ignore-scripts
	node runtimes/claude-sdk/build.mjs
	uv run --no-sync python scripts/pack_native.py
	uv build --clear --no-sources --sdist
	for runtime_target in darwin-arm64 darwin-x64 linux-arm64 linux-x64; do FLOWFIELD_BUILD_TARGET=$$runtime_target uv build --wheel --no-sources || exit; done

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
