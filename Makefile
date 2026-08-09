# Content — developer entrypoints.
# The Python project lives in apps/backend/; these targets wrap its venv so the
# whole project is driven from the repository root.
#
# One-shot gate before declaring any change done:  make validate

VENV := apps/backend/.venv/bin
SRC  := apps/backend/content apps/backend/tests apps/web-hometube apps/web-studio apps/web-admin apps/web-tests apps/cli apps/mcp packages/python-sdk tests .github/scripts

# Every file that declares the project version. One version, several packages:
# the whole monorepo releases together, so these must always agree —
# `make version` proves it, `make version-update VERSION=x.y.z` maintains it.
# The web apps carry a `__version__` too: each passes it to the notification
# bar, which warns when the UI and the backend run different releases.
VERSION_PYPROJECTS := apps/backend/pyproject.toml apps/cli/pyproject.toml \
                      apps/mcp/pyproject.toml packages/python-sdk/pyproject.toml \
                      apps/web-hometube/pyproject.toml
EXT_DIR            := apps/browser-extension
VERSION_MODULES    := apps/backend/content/__init__.py \
                      packages/python-sdk/content_sdk/__init__.py \
                      apps/web-hometube/app.py apps/web-studio/app.py \
                      apps/web-admin/app.py

.DEFAULT_GOAL := validate
.PHONY: help install format lint test test-all ui-venv test-ui test-ui-live \
        validate validate-all validate-release clean run \
        version version-update version-tag extension-zip docker-up docker-update docker-down \
        docker-logs

help:  ## List the available targets
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*##' $(MAKEFILE_LIST) \
	  | awk -F ':.*## ' '{printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install the engine + SDK + CLI + MCP + shared client
	cd apps/backend && uv venv .venv && uv pip install -e ".[test,dev,pdf]" --python .venv/bin/python
	uv pip install -e packages/python-sdk --python apps/backend/.venv/bin/python
	uv pip install -e apps/cli -e apps/mcp --python apps/backend/.venv/bin/python

format:  ## Rewrite code to the canonical style (ruff format)
	$(VENV)/ruff format $(SRC)

lint:  ## Static checks (ruff: E, F, W, I)
	$(VENV)/ruff check $(SRC)

test:  ## Hermetic test suite (no network, no external tools)
	cd apps/backend && .venv/bin/python -m pytest -q -m "not external and not release"

test-all:  ## Full suite including external tools (yt-dlp / ffmpeg / ollama)
	cd apps/backend && .venv/bin/python -m pytest -q

# The UI AppTests need Streamlit, which the backend venv does not carry, so
# they run in a throwaway venv of their own.
ui-venv:
	uv venv .venv-ui --python 3.13
	uv pip install --python .venv-ui/bin/python -q "streamlit>=1.40" pytest -e packages/python-sdk

# Opt-in like the external suite. Hermetic all the same — a fake client stands
# in for the backend (no network).
test-ui: ui-venv  ## Streamlit UI non-regression AppTests (in a Streamlit venv)
	.venv-ui/bin/python -m pytest -q apps/web-tests -m "not release"

# The same three UIs, but against a REAL engine: the shipping SDK over HTTP to a
# backend subprocess, so a UI that disagrees with the server fails here instead
# of agreeing with its own fake (D-37). Slow, and needs `make install` for
# apps/backend/.venv — it skips cleanly without it.
test-ui-live: ui-venv  ## Drive the three UIs against a live backend (slow)
	.venv-ui/bin/python -m pytest -q apps/web-tests -m release -rs

# The official gate (docs/development/validation.md). Format is checked, not
# rewritten, so a dirty tree fails loudly instead of being silently fixed.
validate:  ## format --check + lint + hermetic tests (backend + cli)
	$(VENV)/ruff format --check $(SRC)
	$(VENV)/ruff check $(SRC)
	cd apps/backend && .venv/bin/python -m pytest -q -m "not external and not release"
	$(VENV)/python -m pytest -q apps/cli/tests
	$(VENV)/python -m pytest -q apps/mcp/tests
	$(VENV)/python -m pytest -q packages/python-sdk/tests
	$(VENV)/python -m pytest -q tests

validate-all: validate test-ui  ## validate + the UI AppTests + the external suite (needs the tools)
	cd apps/backend && .venv/bin/python -m pytest -q -m external

validate-release: validate  ## validate + the end-to-end release checks (real page, yt-dlp, LLM, both PDF renderers)
	@echo "Release validation — each check skips when its prerequisite is absent."
	@echo "  CONTENT_RELEASE_URL    a real page to extract (default: local server)"
	@echo "  CONTENT_RELEASE_YTDLP  media URL for the yt-dlp path (default: a"
	@echo "                         stable public video; 'off' skips the slice)"
	@echo "  CONTENT_OLLAMA_URL     the LLM daemon (default http://localhost:11434)"
	cd apps/backend && .venv/bin/python -m pytest -q -m release -rs -s
	$(MAKE) test-ui-live

clean:  ## Remove caches
	find apps/backend -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf apps/backend/.pytest_cache apps/backend/.ruff_cache .venv-ui

run:  ## Run the engine locally (uvicorn on :8000, no Docker)
	cd apps/backend && .venv/bin/python -m uvicorn content.api.app:app --port 8000

# --- release management ---------------------------------------------------------

version:  ## Show every version declaration and fail if they disagree
	@versions=$$( \
	  grep -h '^version = ' $(VERSION_PYPROJECTS) | sed 's/.*"\(.*\)"/\1/'; \
	  grep -h '^__version__ = ' $(VERSION_MODULES) | sed 's/.*"\(.*\)"/\1/'; \
	  grep -o 'version="[0-9][^"]*"' apps/mcp/content_mcp/server.py | sed 's/.*"\(.*\)"/\1/'; \
	  grep -o 'org.opencontainers.image.version="[^"]*"' apps/backend/Dockerfile | sed 's/.*"\(.*\)"/\1/'; \
	  grep -m1 '"version"' $(EXT_DIR)/manifest.json | sed 's/.*"\([0-9][^"]*\)".*/\1/' \
	); \
	distinct=$$(echo "$$versions" | sort -u); \
	count=$$(echo "$$distinct" | wc -l | tr -d ' '); \
	total=$$(echo "$$versions" | wc -l | tr -d ' '); \
	if [ "$$count" = "1" ]; then \
	  echo "version $$distinct — consistent across $$total declarations"; \
	else \
	  echo "VERSIONS DISAGREE:"; \
	  grep -n '^version = ' $(VERSION_PYPROJECTS); \
	  grep -n '^__version__ = ' $(VERSION_MODULES); \
	  grep -n 'version="[0-9][^"]*"' apps/mcp/content_mcp/server.py; \
	  grep -n 'org.opencontainers.image.version=' apps/backend/Dockerfile; \
	  grep -n '"version"' $(EXT_DIR)/manifest.json | head -1; \
	  exit 1; \
	fi

version-update:  ## Set the version everywhere: make version-update VERSION=x.y.z
	@if [ -z "$(VERSION)" ]; then \
	  echo "usage: make version-update VERSION=x.y.z"; exit 1; \
	fi
	@case "$(VERSION)" in \
	  [0-9]*.[0-9]*.[0-9]*) ;; \
	  *) echo "VERSION must look like x.y.z (got '$(VERSION)')"; exit 1;; \
	esac
	@for f in $(VERSION_PYPROJECTS); do \
	  sed -i.bak 's/^version = ".*"/version = "$(VERSION)"/' $$f && rm $$f.bak; \
	done
	@for f in $(VERSION_MODULES); do \
	  sed -i.bak 's/^__version__ = ".*"/__version__ = "$(VERSION)"/' $$f && rm $$f.bak; \
	done
	@sed -i.bak 's/version="[0-9][^"]*"/version="$(VERSION)"/' \
	  apps/mcp/content_mcp/server.py && rm apps/mcp/content_mcp/server.py.bak
	@sed -i.bak '0,/"version"/s/"version": "[^"]*"/"version": "$(VERSION)"/' \
	  $(EXT_DIR)/manifest.json && rm $(EXT_DIR)/manifest.json.bak
	@sed -i.bak 's/org.opencontainers.image.version="[^"]*"/org.opencontainers.image.version="$(VERSION)"/' \
	  apps/backend/Dockerfile && rm apps/backend/Dockerfile.bak
	@$(MAKE) --no-print-directory version
	@echo "next: review with 'git diff', commit, then 'make version-tag'"

version-tag:  ## Create the annotated tag v<version> (clean tree + agreeing versions required)
	@test -z "$$(git status --porcelain)" || { \
	  echo "the working tree is not clean — commit or stash first"; exit 1; }
	@$(MAKE) --no-print-directory version >/dev/null
	@v=$$(grep '^version = ' apps/backend/pyproject.toml | sed 's/.*"\(.*\)"/\1/'); \
	git tag -a "v$$v" -m "Content v$$v"; \
	echo "tag v$$v created — push it deliberately with: git push origin v$$v"

# --- browser extension -----------------------------------------------------------

# The file list comes from `git ls-files`, not from the directory: only tracked
# files are packaged, so no .DS_Store, no editor backup and no local
# experiment can ride along into something people download and load into
# their browser.
extension-zip:  ## Package the Chromium extension for manual install (dist/)
	@version=$$(grep -m1 '"version"' $(EXT_DIR)/manifest.json | sed 's/.*"\([0-9][^"]*\)".*/\1/'); \
	archive="$(CURDIR)/dist/hometube-for-content-$$version.zip"; \
	mkdir -p dist; rm -f "$$archive"; \
	cd $(EXT_DIR) && git ls-files -z | xargs -0 zip -q -X "$$archive"; \
	echo "packaged $$archive"; \
	cd $(CURDIR) && unzip -Z1 "$$archive" | sed 's/^/  /'

# --- docker ----------------------------------------------------------------------

docker-up:  ## Build and start the compose stack (UIs per COMPOSE_PROFILES in .env)
	docker compose up --build -d
	@echo "engine http://localhost:8010 — console http://localhost:8503"
	@echo "UIs per COMPOSE_PROFILES: HomeTube http://localhost:8501 — Studio http://localhost:8502"

# Same engine as docker-up, framed for the everyday loop: after editing code
# or pulling a new version, rebuild from the working tree and refresh the
# running stack. Layer cache keeps untouched services instant; compose only
# recreates containers whose image actually changed; ./data is a bind mount
# and survives; --remove-orphans clears containers whose service was removed.
docker-update:  ## Rebuild images from the working tree and refresh running containers
	docker compose up -d --build --remove-orphans
	@echo "stack refreshed — running now:"
	@docker compose ps --format "  {{.Service}}  {{.Status}}"

docker-down:  ## Stop and remove the compose stack
	docker compose down

docker-logs:  ## Follow the compose logs
	docker compose logs -f
