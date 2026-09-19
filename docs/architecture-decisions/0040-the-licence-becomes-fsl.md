# ADR 0040 — The licence becomes FSL-1.1-ALv2

Status: accepted (2026-09-18) · Supersedes the AGPL-3.0-or-later choice of the
first release · Related: `COMMERCIAL.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`

## Context

Content was published under AGPL-3.0-or-later. That licence was chosen when the
question was "how do I publish this honestly", and it answered it well: anyone
can read the code, run it, fork it, and a modified network deployment owes its
users the source.

What it does not do is reserve anything. **The AGPL permits selling a hosted
Content**, unmodified or modified-with-published-changes, by anyone, at any
price. The obligation it imposes — publish your modifications — costs a
well-resourced competitor a pull request. That was acceptable while the project
was a self-hosted tool and nothing else was planned.

It stopped being acceptable on 2026-09-16, when the direction was set: **the
hosted engine and the agent door are the products**. The thing that would be
sold is exactly the thing the AGPL lets anyone else sell. A licence that
reserves nothing is a fine choice for a project that intends to sell nothing;
it is the wrong one here.

Two facts made the change possible at all, and neither is permanent:

- **Yann Orieult holds copyright in every line.** Verified on 2026-09-18: every
  commit is his, plus dependabot. The project has never accepted a code
  contribution, deliberately (`CONTRIBUTING.md`), and only the holder of all the
  rights can relicense.
- **Nothing has been sold, and no one has been promised the AGPL going
  forward.** There is no customer whose expectation is being broken.

## Decision

**From the next release, Content is source-available under the Functional
Source License, Version 1.1, ALv2 Future License (`FSL-1.1-ALv2`).** The
licensor is *Yann Orieult, a natural person*; "Latent" and "LatentNoise" are
names, not a legal entity.

### What the licence does

Anyone may read, audit, modify and run Content for any **Permitted Purpose** —
which explicitly includes internal use inside an organisation, non-commercial
education, non-commercial research, and professional services rendered to
another licensee. The single reserved case is a **Competing Use**: offering
Content, or something substantially similar built from it, as a commercial
product or service.

**Each version converts to Apache 2.0 on the second anniversary of its
publication.** That grant is inside the licence, irrevocable, and no later
decision can withdraw it. Content is therefore delayed open source, not
permanently restricted.

### Versions up to 0.8.4 stay AGPL, permanently

Rights already granted cannot be taken back, and the project will never claim
otherwise. **0.8.4 and every version before it remain available under
AGPL-3.0-or-later, forever.** Anyone relying on that can keep relying on it, and
can fork from it. The new licence governs what is published after the change and
nothing else. Every document that states the licence says this explicitly.

### The terms are not edited

`LICENSE` is the official FSL template with `${year}` → `2026` and
`${licensor name}` → `Yann Orieult`, and no other change. A modified
"FSL-like" licence would carry the cost of a bespoke licence — nobody can
recognise it — without the benefit. The file is byte-identical to Tetra's,
which took the same decision the same day.

### Contributions are paused, not refused on principle

Code contributions stay closed, as they already were — but the *reason* is now
sharper and the *state* is explicitly temporary. Undivided copyright is what
made this relicensing possible; a single merged pull request would have made it
impossible without tracking down every contributor. Contributions reopen when a
contributor agreement exists. Issues, bug reports, security reports and design
feedback were always welcome and remain so.

### The source link stays, as a choice rather than a duty

`CONTENT_SOURCE_URL`, `GET /api/v1/system` → `source_url`, and the footer that
`content_sdk.legal` renders in the three surfaces were built to discharge AGPL
§13. That obligation is gone. The mechanism stays: a source-available project
that hides its source is worth less than one that shows it, and an operator
running a fork should still point it at *their* source rather than at upstream.
The docstrings and comments were rewritten to say that, so the code no longer
claims a legal duty it does not have.

## Consequences

- **Content is no longer open source, and must not be described as such.** OSI
  has not approved the FSL and will not: it discriminates against a field of
  endeavour. The accurate words are **source-available** and **delayed open
  source**, and the documentation uses them.
- **It leaves the directories that require an OSI licence** — awesome-selfhosted
  and its kind. Both the `agpl` GitHub topic and the open-source phrasing in the
  repository description go with it.
- **`FSL-1.1-ALv2` is a listed SPDX identifier.** Verified 2026-09-19: the four
  Python packages build with the plain SPDX form (`license = "FSL-1.1-ALv2"`)
  under both setuptools and hatchling, `twine check` passes, and
  `packaging.licenses.canonicalize_license_expression` — the check PyPI itself
  runs at upload — accepts it. No `license = { file = "LICENSE" }` fallback is
  needed anywhere, and no `License ::` classifier had to be removed (PEP 639 had
  already retired them here).
- **Some users will object, and the objection is legitimate.** Standalone
  HomeTube's audience chose a self-hosted tool partly because it was free
  software. The honest answer is the one written down: their versions stay AGPL,
  self-hosting is and remains a Permitted Purpose at no cost, and every future
  version becomes Apache 2.0 after two years. Legacy HomeTube itself stays AGPL,
  decided the same evening.
- **The change is not itself a release.** The branch carries it; publishing it —
  the tag, the images, the PyPI upload — is the maintainer's gesture, and the
  first release under the new licence should say so in its notes rather than let
  anyone discover it from a diff.
