# Validation

The official procedure for checking a change. A modification is only **done**
once `make validate` passes and the acceptance criteria stated for the task are
satisfied.

## A single command

```bash
make validate
```

It chains, in order, and **fails on the first red**:

| Step | Underlying command | Role |
| --- | --- | --- |
| Format | `ruff format --check $(SRC)` (the backend, every app, the SDK and the layering tests — see the Makefile's `SRC`) | The code is in canonical style (checked, **not** rewritten — an unformatted tree fails instead of being silently fixed) |
| Lint | `ruff check $(SRC)` | Real errors, pyflakes, warnings, import sorting (`select = ["E","F","W","I"]`) |
| Tests | `pytest -q -m "not external"` | The **hermetic** suite: no network, no external tools |

## Related targets

| Target | Effect |
| --- | --- |
| `make format` | Rewrites the code in canonical style (run it before committing) |
| `make lint` | Lint only |
| `make test` | Hermetic tests only |
| `make test-all` | The full suite, **including** the tests marked `external` |
| `make test-ui` | The Streamlit AppTests against a fake client (hermetic, own venv) |
| `make test-ui-live` | The same three UIs against a **real** backend (slow, `release`) |
| `make validate-all` | `validate` + the UI AppTests + the `external` tests |
| `make install` | Recreates the venv with the `test` + `dev` extras |

## External tests

The tests marked `@pytest.mark.external` exercise the real yt-dlp / ffmpeg /
Ollama (without the Internet: a local HTTP server, generated files). They
self-skip if the tool is missing. They are **not part** of `make validate` (the
gate must stay hermetic and reproducible everywhere); run them with
`make validate-all` when you touch a provider.

## Continuous integration

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs the same gate
on a machine that is not the maintainer's. It triggers on **pushes and tags**,
never on pull requests: code contributions are not accepted
([GOVERNANCE.md](../../GOVERNANCE.md)), so a PR-driven workflow would never fire.

| Job | What it runs | When |
| --- | --- | --- |
| `make validate` | `make install` first — the documented install path, on a clean machine — then the official gate | every push, every tag |
| `make test-ui` | the Streamlit AppTests, in their own throwaway venv | every push, every tag |
| Images | the backend and the three UIs, each for `linux/amd64` **and** `linux/arm64`, pushed to GHCR | tags `v*` only |

**No stored secret is required**: the gate passes on a fork, on a clone and on a
first push with nothing configured, and the image job authenticates to GHCR with
the repository's own `GITHUB_TOKEN`. A branch push can publish nothing — only a
`v*` tag reaches that job, and pushing a tag is already a deliberate act.
Building both architectures is half the point (the arm64 half is the one that
breaks, since it needs musl wheels); publishing them is the other half.
Actions are pinned to commit SHAs rather than moving tags, because a public
repository's CI is a supply-chain surface.

### Which gates stay local

| Stays local | Why |
| --- | --- |
| `make validate-all` (`-m external`) | needs real yt-dlp / ffmpeg / Ollama; CI must not silently depend on tools and daemons |
| `make validate-release` (`-m release`) | needs a live page, a media URL and an LLM daemon — precisely what breaks *between* releases, so it is run deliberately before one |

The CI runner is bare on purpose: no ffmpeg, no yt-dlp, no Typst, no poppler, no
Ollama. That is what makes "hermetic" mean something. Checks needing one of
those skip themselves rather than fail, so a few assertions that run on a
developer machine do **not** run on CI:

- the Typst half of the PDF renderer matrix (needs the `typst` binary),
- the four PDF checks that read the rendered text back (need `pdftotext`,
  from `poppler-utils`),
- anything behind a font-coverage probe, since coverage differs by machine (D-29).

A green CI therefore means *the hermetic gate holds on a bare machine* — not
that everything was exercised. Run `make validate-all` locally with the tools
installed for the rest; `pytest -rs` lists what skipped and why.

### Hermeticity is enforced, not assumed

"No network" used to be a convention, believed because the suite passed on a
machine that had network. It is now checked: an autouse guard in
`apps/backend/tests/conftest.py` blocks outbound connections and hostname
resolution for every test not marked `external` or `release`, and
`apps/backend/tests/test_hermeticity.py` proves the guard is live rather than
quietly disabled. Loopback stays open — a local HTTP server is not the Internet.

A test that needs a name to resolve stubs the resolver (see `_fixed_resolver` in
`tests/test_text_sources.py`) instead of reaching for DNS.

## Configuration choices

- **A conservative ruff rule set** (`E, F, W, I`): we deliberately exclude the
  modernization rules (`UP`, `RUF`) that would rewrite the public contract
  (`Union[...]`, `datetime`) with no concrete need. A reversible decision.
- **No type checker** for now (a product decision). The day we add one, it will
  enrich `make validate` and this page.

## Definition of Done (a reminder)

A task is done when: `make validate` passes; the acceptance criteria stated for
it are proven; the anticipated edge cases are covered by tests; the directly
affected documentation is up to date; findings that fall outside its scope are
written down rather than lost; no structural decision was left implicit; and the
result is demonstrable reproducibly.

## Manual verification (a smoke test, when relevant)

For a change with a runtime surface, on top of the tests:

```bash
docker compose up --build -d        # port 8010 (see .env)
curl -s localhost:8010/api/v1/health
# Docs: http://localhost:8010/docs  — scripts: playground/examples/*.sh
```

## Release validation

`make validate` proves the engine is internally consistent. It cannot prove that
a real web page is still readable, that yt-dlp still parses YouTube, that the LLM
daemon answers, or that the Typst binary in the image runs — and those are what
break between releases, silently, because the hermetic gate is blind to them.

```bash
make validate-release
```

It runs `validate` first, then the `release`-marked checks end to end through the
public API: web-page extraction, a yt-dlp source, an LLM summary, and a PDF from
**each** renderer (pinned, so a broken backend cannot hide behind `auto`'s
fallback). Each check skips when its prerequisite is absent and the run prints
what it could not exercise — a release check that skips silently reads as a pass.

| Variable | Enables |
| --- | --- |
| `CONTENT_RELEASE_URL` | extraction from a real page (default: a local server) |
| `CONTENT_RELEASE_YTDLP` | the yt-dlp path — **on by default** against a stable public video (needs `yt-dlp` installed); point it elsewhere to use another source, or set `off` to skip it |
| `CONTENT_OLLAMA_URL` | the LLM summary (default `http://localhost:11434`) |

### The UIs against a real backend

`validate-release` finishes with `make test-ui-live`
(`apps/web-tests/test_live_ui.py`): the three Streamlit apps driven by
`AppTest`, but with the **shipping SDK talking over HTTP to a real engine**
started as a subprocess — not `FakeContentClient`.

That distinction is the whole point. Every other UI test answers with whatever
the fake was taught, which is how D-37 survived three waves: Studio kept its own
capability→output map, quietly stopped offering three capabilities, and the fake
agreed with it. These checks compare the UI against what the *server* resolves,
so a renamed field or a new capability shows up as a failure.

It stays offline: the "real URL" each UI is given is served by a local HTTP
server out of a temp directory — a real socket, a real fetch, a real yt-dlp
download, no Internet. The media path needs `ffmpeg` (to generate a clip) and
`yt-dlp`; without them those checks skip and the run says so.

`make validate-all` remains the tool-dependent suite (`-m external`). Both must
be free of known failures before a release; `make validate` alone is the gate for
ordinary changes.
