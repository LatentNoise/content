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
# Manifests whose content-sdk==x.y.z dependency pin must move with the
# release: a CLI or MCP wheel pinning the previous SDK is exactly the drift
# the pin exists to prevent.
SDK_PIN_MANIFESTS  := apps/cli/pyproject.toml apps/mcp/pyproject.toml
EXT_DIR            := apps/browser-extension-chromium
VERSION_MODULES    := apps/backend/content/__init__.py \
                      packages/python-sdk/content_sdk/__init__.py \
                      apps/web-hometube/app.py apps/web-studio/app.py \
                      apps/web-admin/app.py

.DEFAULT_GOAL := validate
.PHONY: help install hooks format lint test test-all ui-venv test-ui test-ui-live \
        validate validate-all validate-release clean run \
        version version-update version-tag wheels deploy-compose extension-zip docker-up docker-update docker-down \
        docker-logs

help:  ## List the available targets
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*##' $(MAKEFILE_LIST) \
	  | awk -F ':.*## ' '{printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

install: hooks  ## Create the venv and install the engine + SDK + CLI + MCP + shared client
	cd apps/backend && uv venv .venv && uv pip install -e ".[test,dev,pdf]" --python .venv/bin/python
	uv pip install -e packages/python-sdk --python apps/backend/.venv/bin/python
	uv pip install -e apps/cli -e apps/mcp --python apps/backend/.venv/bin/python

# Git hooks live in .githooks/ (tracked, so a fix reaches every checkout)
# rather than .git/hooks/ (local, invisible, copied once and then stale).
# Pointing core.hooksPath at them is the whole installation, and `make install`
# does it so a fresh clone is guarded before its first commit.
hooks:  ## Arm the tracked git hooks (identity + attribution guards)
	@git config core.hooksPath .githooks
	@echo "git hooks armed from .githooks/ (identity: $$(git config user.email))"

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
	  grep -m1 '"version"' $(EXT_DIR)/manifest.json | sed 's/.*"\([0-9][^"]*\)".*/\1/'; \
	  grep -ho 'content-sdk==[0-9][0-9.]*' $(SDK_PIN_MANIFESTS) | sed 's/.*==//' \
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
	  grep -n 'content-sdk==' $(SDK_PIN_MANIFESTS); \
	  exit 1; \
	fi

version-update:  ## Set the version everywhere (asks when VERSION= is omitted)
	@v="$(VERSION)"; \
	if [ -z "$$v" ]; then \
	  current=$$(grep -m1 '^version = ' apps/backend/pyproject.toml | sed 's/.*"\(.*\)"/\1/'); \
	  major=$${current%%.*}; rest=$${current#*.}; \
	  minor=$${rest%%.*}; patch=$${rest#*.}; \
	  printf 'current version: %s\n' "$$current"; \
	  printf 'suggestions:     %s.%s.%s (fixes only) | %s.%s.0 (features)\n' \
	    "$$major" "$$minor" "$$((patch+1))" "$$major" "$$((minor+1))"; \
	  printf 'new version (x.y.z): '; \
	  read -r v; \
	fi; \
	v=$${v#v}; \
	case "$$v" in \
	  [0-9]*.[0-9]*.[0-9]*) ;; \
	  *) echo "the version must look like x.y.z (got '$$v')"; exit 1;; \
	esac; \
	for f in $(VERSION_PYPROJECTS); do \
	  sed -i.bak "s/^version = \".*\"/version = \"$$v\"/" $$f && rm $$f.bak; \
	done; \
	for f in $(VERSION_MODULES); do \
	  sed -i.bak "s/^__version__ = \".*\"/__version__ = \"$$v\"/" $$f && rm $$f.bak; \
	done; \
	sed -i.bak "s/version=\"[0-9][^\"]*\"/version=\"$$v\"/" \
	  apps/mcp/content_mcp/server.py && rm apps/mcp/content_mcp/server.py.bak; \
	sed -i.bak "s/\"version\": \"[^\"]*\"/\"version\": \"$$v\"/" \
	  $(EXT_DIR)/manifest.json && rm $(EXT_DIR)/manifest.json.bak; \
	sed -i.bak "s/org.opencontainers.image.version=\"[^\"]*\"/org.opencontainers.image.version=\"$$v\"/" \
	  apps/backend/Dockerfile && rm apps/backend/Dockerfile.bak; \
	for f in $(SDK_PIN_MANIFESTS); do \
	  sed -i.bak "s/content-sdk==[0-9][0-9.]*/content-sdk==$$v/" $$f && rm $$f.bak; \
	done; \
	$(MAKE) --no-print-directory version; \
	echo "next: review with 'git diff', commit, then 'make version-tag'"

version-tag:  ## Create the annotated tag v<version> (clean tree required; asks first)
	@test -z "$$(git status --porcelain)" || { \
	  echo "the working tree is not clean — commit or stash first"; exit 1; }
	@$(MAKE) --no-print-directory version >/dev/null
	@v=$$(grep '^version = ' apps/backend/pyproject.toml | sed 's/.*"\(.*\)"/\1/'); \
	if git rev-parse -q --verify "refs/tags/v$$v" >/dev/null; then \
	  echo "v$$v already exists — the tree still declares $$v, so there is"; \
	  echo "nothing new to tag. A tag seals the version the tree declares"; \
	  echo "(the publish workflow verifies they agree). To cut a new release:"; \
	  echo "  1. make version-update      on a branch, then PR + merge"; \
	  echo "  2. make version-tag         back here, on the updated main"; \
	  exit 1; \
	fi; \
	printf 'create annotated tag v%s at %s (%s)? [y/N] ' \
	  "$$v" "$$(git rev-parse --short HEAD)" "$$(git branch --show-current)"; \
	read -r answer; \
	case "$$answer" in \
	  [yY]*) ;; \
	  *) echo "aborted — no tag created"; exit 1;; \
	esac; \
	git tag -a "v$$v" -m "Content v$$v"; \
	echo "tag v$$v created — push it deliberately with: git push origin v$$v"

# --- python distributions ---------------------------------------------------------

# Wheels for the published packages: the SDK, the `content` CLI and the
# `content-mcp` server. They are attached to each release and, once the names
# are claimed, published to PyPI by .github/workflows/publish-pypi.yml. Until
# then `pip install content-cli` would resolve to nothing (or worse, to
# somebody else's package) — the release assets are the index; see
# apps/cli/README.md and apps/mcp/README.md.
wheels:  ## Build the SDK + CLI + MCP wheels for a release (dist/)
	@rm -f dist/content_sdk-*.whl dist/content_cli-*.whl dist/content_mcp-*.whl \
	       dist/content_sdk-*.tar.gz dist/content_cli-*.tar.gz dist/content_mcp-*.tar.gz
	uv build --out-dir dist packages/python-sdk
	uv build --out-dir dist apps/cli
	uv build --out-dir dist apps/mcp
	@ls -1 dist/content_sdk-* dist/content_cli-* dist/content_mcp-*

# The clone-free install's compose file is generated from the one above, so
# the two can never describe different deployments (tests/test_deploy_compose).
deploy-compose:  ## Regenerate deploy/docker-compose.yml from docker-compose.yml
	$(VENV)/python scripts/gen_deploy_compose.py

# --- browser extension -----------------------------------------------------------

# The file list comes from `git ls-files`, not from the directory: only tracked
# files are packaged, so no .DS_Store, no editor backup and no local
# experiment can ride along into something people download and load into
# their browser. Restricted further to the runtime entries: what the browser
# loads is manifest.json + the five directories it references — never the
# README or the test fixtures. `zip -X` + git's sorted file order keep the
# archive reproducible from the same tree (file mtimes are the one part zip
# records that a fresh clone changes).
EXT_RUNTIME := manifest.json background icons lib options popup
EXT_ZIP_DIR ?= $(CURDIR)/dist

extension-zip:  ## Package the Chromium extension for manual install (dist/)
	@version=$$(grep -m1 '"version"' $(EXT_DIR)/manifest.json | sed 's/.*"\([0-9][^"]*\)".*/\1/'); \
	archive="$(EXT_ZIP_DIR)/content-browser-extension-chromium-v$$version.zip"; \
	mkdir -p "$(EXT_ZIP_DIR)"; rm -f "$$archive"; \
	cd $(EXT_DIR) && git ls-files -z -- $(EXT_RUNTIME) | xargs -0 zip -q -X "$$archive"; \
	echo "packaged $$archive"; \
	shasum -a 256 "$$archive" | sed 's/^/  sha256  /'; \
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
