# Publishing `content-mcp` to the MCP registry

The official [MCP registry](https://registry.modelcontextprotocol.io) is how a
human finds an MCP server they do not already know about. `content-mcp` is on
PyPI, documented and installable with one command, and until it is listed the
only route to it is knowing this repository exists.

This page is the runbook: what the entry is, what has to be true before it can
be published, and how it stays current.

## What is published

`server.json` at the repository root — tracked (the allowlist `.gitignore`
names it explicitly), validated by `tests/test_mcp_registry_manifest.py`, and
versioned by `make version-update` along with every other declaration.

| Field | Value | Why |
| --- | --- | --- |
| `name` | `io.github.LatentNoise/content` | Registry names are **namespaced**, so the bare word `content` is free inside our own namespace. No `latentcontent`-style workaround is needed — that idea came from assuming a flat global namespace, which the registry does not have. |
| `version` (twice) | the release version | Once for the server, once for the PyPI package. Both are rewritten by `make version-update`; a test fails if they drift. |
| `packages[0]` | `pypi` / `content-mcp` / `stdio` | One package entry. The server speaks stdio; the client spawns it. |
| `environmentVariables` | `CONTENT_API_URL`, `CONTENT_MCP_DOWNLOAD_DIR` | What the entry is *for*: someone who finds it learns how to point the server at their engine without opening the repository. A test asserts every advertised variable is actually read by the server. |

## The two things that must be true first

**1. The published PyPI description must carry the ownership marker.** The
registry proves you own `content-mcp` by fetching its PyPI description and
looking for:

```text
mcp-name: io.github.LatentNoise/content
```

It lives as an HTML comment on the first line of
[`apps/mcp/README.md`](../../apps/mcp/README.md), which `pyproject.toml`
declares as the long description. The check reads what is **already on PyPI**,
not the working tree — so the marker only counts from the first release
published after it was added.

> **0.5.0 does not have it.** It was published before the marker existed, so a
> registry publish naming `0.5.0` is refused. The first version that can be
> announced is **0.6.0**, and this is why registry publication is wired into
> the release workflow rather than run by hand today.

**2. The schema is versioned by date and can move.** `server.json` declares
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`.
Before publishing by hand, re-validate against the live file — the key names in
`packages[]` (`registryType` / `registryBaseUrl` / `identifier`) have moved at
least once in the registry's history:

```bash
curl -sS -o /tmp/server.schema.json \
  https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
apps/backend/.venv/bin/python - <<'PY'
import json, jsonschema
schema = json.load(open("/tmp/server.schema.json"))
doc = json.load(open("server.json"))
errors = sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
print("VALID" if not errors else "\n".join(f"{list(e.path)}: {e.message}" for e in errors))
PY
```

That validation is what caught the first draft's `description`: 158 characters
against a `maxLength` of 100, which would have failed at publish time, after
the login dance.

## How it is published

**Automatically, with the release.** `.github/workflows/publish-pypi.yml` gained
a `registry` job that runs after the PyPI upload of the same release. It checks
`server.json` names the version being published, waits until the `mcp-name`
marker is readable in that version's PyPI description, then authenticates with
**GitHub OIDC** (`mcp-publisher login github-oidc` — no stored secret; the job
carries `id-token: write`) and runs `mcp-publisher publish`.

It is skipped for TestPyPI and for a recovery publish that did not include the
MCP package, because announcing a version whose wheel did not go out would be a
lie the registry then serves.

**By hand**, if that job has to be replayed — `mcp-publisher login github`
opens a browser, so it is the maintainer who completes it. Make sure the
identity it proves is `YannOrieult` (AGENTS.md, *Public identity*):

```bash
brew install mcp-publisher     # or the tarball from the registry's releases
mcp-publisher login github     # opens a browser; proves the io.github.LatentNoise namespace
mcp-publisher publish          # reads ./server.json
```

## Verifying it landed

```bash
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.LatentNoise" | jq
```

The entry should be there with the version that was just published.

## What being listed does and does not buy

**The registry is a catalogue, not an installer.** It holds metadata and
*installation instructions*; the artifact itself stays on PyPI. Our entry says,
in machine-readable form: this server is the PyPI package `content-mcp`, it
speaks stdio, and it reads `CONTENT_API_URL` and `CONTENT_MCP_DOWNLOAD_DIR`.
Everything a client needs to install and launch it without asking the user
anything is therefore already published — whether a given client *does* that is
the client's business, not ours.

**Claude Code does not, as of 2026-08-22.** Checked rather than assumed:
`claude mcp --help` offers `add`, `add-json`, `add-from-claude-desktop`, `get`,
`list`, `login`, `logout`, `remove`, `serve` — no `search`, no `install`, no
registry subcommand of any kind. Articles describing clients that "discover and
install from the registry" are describing an intent, or another client's own
registry. Re-run that one-line check before believing otherwise; it is the kind
of thing that changes without an announcement reaching us.

So registry presence makes Content *findable by a human*, who then runs:

```bash
claude mcp add content --env CONTENT_API_URL=… -- uvx content-mcp
```

`uvx` is what makes that a single step: no install precedes it, and the client
owns the version. Anthropic's curated connector directory is a separate channel
and a later target.

**One field we deliberately do not set.** `packages[].runtimeHint` exists to
tell a client which runtime to use (`uvx` would be ours). The schema asks for
it only when `runtimeArguments` are present, which is not our case, and a sweep
of the registry found no PyPI entry using it — too thin a convention to follow
on speculation. If a client ever documents that it honours the hint, adding it
is one line in `server.json`.

Directories worth submitting to once the registry entry is live, in order of
likely return:

| Where | How |
| --- | --- |
| `modelcontextprotocol/servers` on GitHub | a PR to the community list — the highest visibility of the lot |
| mcp.so | submission form |
| Glama | crawls repositories; check whether it already knows us |
| Smithery | submission |

Each of those is a **write action performed as `YannOrieult`** — check
`gh api user --jq .login` first if any of them is done from the CLI.
