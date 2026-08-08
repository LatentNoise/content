# Release readiness — the state of the thing

What was checked before the repository became public, what a visitor gets on day
one, and what is knowingly unfinished. It records findings; it publishes
nothing.

## Verdict

**Ready to publish.** Nothing found is a blocker in the code or the licensing.
The problems found were fixed (see below), and the three questions that were
left open — the repository's home, the version number and the copyright holder —
are now settled and recorded here.

## What was checked

The repository is published as a fresh history: the first commit is the whole
tree, so the audit is an audit of the working tree, not of past commits.

| Check | Result |
| --- | --- |
| **Secrets in everything about to be committed** — 276 files, 1.6 MB | **Clean.** No key, token, OAuth secret, password, private key, cookie, `Authorization` header, SMTP or cloud credential, credential-in-URL, absolute home path, LAN address or private hostname. |
| Suspicious *paths* | Only `.env.example`, which is intended and contains placeholders and safe defaults only. The real `.env`, the job workspaces, the delivery folder, the analysis cache and the playground media are all ignored. |
| Pattern sweep | `sk-super-secret` is a placeholder in the test that proves keys get redacted; `10.0.0` is a version string in a notification test; `homelab` is the deployment word in prose; `yann@orieult.com` is the deliberate public contact in SECURITY.md and COMMERCIAL.md |
| Generated artefacts | `playground/output/pgverify/sum.md` — a summary produced by a local run — was reachable through the `*.md` allowlist. The ignore rules now scope `playground/input/` and `playground/output/` to their two READMEs. |
| Binary content | Four browser-extension PNG icons. No media, database, archive or build output; no nested `.git`; largest file 85 KB. |
| Licensing files | `LICENSE` (AGPL-3.0-or-later), `NOTICE` naming Typst / ReportLab / DejaVu, `COMMERCIAL.md`, `SECURITY.md`, `GOVERNANCE.md`, `CONTRIBUTING.md`, `CODEOWNERS`, issue templates — all present |
| Licences inside the image | `/app/LICENSE`, `/app/NOTICE`, `/usr/local/share/licenses/typst/LICENSE` |
| AGPL §13 chain | Mechanically correct end to end: `/api/v1/system` reports `license` + `source_url`, every UI renders it (`content_sdk.legal`). The URL 404s until the repository is public — the last thing to re-check on publication day. |
| Documentation links | 46 documents, 125 relative links, 0 broken — and none resolving to a git-ignored target, which is the failure a filesystem-based check does not catch |
| README as a stranger reads it | Answers all five: what it is, who maintains it, how to run it, what it costs, what the licence permits |
| CI on a first push | `ci.yml` needs no secret, pins every action by SHA, and cannot publish: the image job is gated on a `v*` tag and builds with `push: false` |
| `make validate` | Green — 599 backend (5 skipped), 9 CLI, 10 MCP, 32 SDK, 16 layering (4 skipped); ruff format and lint clean across 169 files |
| UI AppTests | Green — 36 passed |
| `make version` | `0.1.0`, consistent across 12 declarations |

The external and release suites (`make validate-all`, `make validate-release`)
need network access and the optional tools, so they are run deliberately rather
than as part of the gate; see [../development/validation.md](../development/validation.md).

## What was found and fixed

**The governance model rests on a platform setting, not on anything versioned.**
`CONTRIBUTING.md`, the README and the issue templates all state that pull
requests cannot be opened. Nothing in the repository makes that true — it holds
only while pull requests are turned off in the repository's settings. Flip that
setting and four documents become wrong at once, on pages whose whole purpose is
to be trusted about the project's terms. It is now row 1 of the settings
checklist, to be verified in the web UI before the repository is announced.

**No record of the platform settings the model depends on.** Now
[github-settings.md](github-settings.md) — twelve settings, why each matters,
and the §13 verification command.

**Documentation linking to files that are not published.** Several links
resolved on the maintainer's disk but pointed at git-ignored paths (`AGENTS.md`,
the `work/` notes), so they would have 404'd for every visitor while the link
checker reported them fine. Removed, and the check now also rejects links whose
target is ignored.

## Decisions taken

1. **The repository's home** is `https://github.com/LatentNoise/content`. It is
   the AGPL §13 source offer, so it must return **200** for an anonymous
   visitor once the repository is public — that is the one licence obligation
   that has to resolve. An operator running a *modified* copy repoints
   `CONTENT_SOURCE_URL` at their own source; the exact list of files is in
   [github-settings.md](github-settings.md#changing-the-repositorys-home).
2. **The version is `0.1.0`**, across all 12 declarations. `docs/contract.md` §9
   already states what will not change without an `/api/v2`, so the stability
   promise is written down independently of the number. `0.1.0` says the
   surface may still move; it is a positioning choice, not a technical one.
3. **The copyright holder is Yann Orieult**, an individual — as `NOTICE` and the
   README footer state. `COMMERCIAL.md`'s dual-licensing option depends on that
   ownership staying undivided, which is why no code contribution is accepted.
   Publishing under an organisation account changes nothing about it.

## Knowingly shipped incomplete

Honest about the edges, so nobody discovers them as surprises:

- **Reserved contract surface.** `execution.mode: sync`, `priority`,
  `retention`, `preferences.language`, `execution_location` and
  `sources[].hints` are refused rather than implemented — declared in
  `content/domain/reserved.py` and in `docs/contract.md` §9.
- **`upload` and `collection` source types** are declared and refused.
- **No retention or purge.** Nothing is deleted automatically; the data
  directory grows until an operator manages it (`docs/storage.md`).
- **No authentication on the API.** Deliberate for single-user self-hosting; the
  README and `docs/architecture/` say so.
- **Optional runners are optional.** Without an LLM, summaries and translations
  report `unavailable`; without the `[stt]` extra, so do audio transcripts. The
  README's "what you get with nothing else installed" table is measured, not
  guessed.
- **The yt-dlp release check depends on a third party staying up.** It now runs
  by default against one stable public video rather than waiting for an env var
  to be set — the component most likely to rot was previously the one nobody
  exercised. The cost is that YouTube being down reads as a failure;
  `CONTENT_RELEASE_YTDLP=off` skips it deliberately, and
  [ytdlp-base-image.md](ytdlp-base-image.md) explains the trade-off.
- **Python packaging is not the delivery path**, though it now works. The
  backend is installed editable or shipped in the image; the wheel and sdist
  were built and inspected once to confirm they carry
  `content/processors/pdf/templates/default.typ`, which is located relative to
  `__file__` and would otherwise be silently absent. Nothing is published to
  PyPI.
