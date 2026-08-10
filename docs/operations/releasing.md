# Releasing

The whole ceremony in order, written to be followed at 1 a.m. Three actors:
**[shell]** is you at the repo root, **[browser]** is you on GitHub as
YannOrieult, **[tag]** is what pushing the tag does on its own.

A release is: merge everything → bump + notes on one branch → PR → tag on
merged main → publish the draft → one PyPI run → verify. The expensive
property is that every guard here exists because its failure has already
happened once; do the steps in order and none of them can happen again.

## The sequence

1. **[browser] Merge everything the release ships.** Every open branch that
   belongs in the release goes into `main` first. The tag comes later and
   must point at a commit that is *on* `origin/main` — `make version-tag`
   refuses anything else.

2. **[shell] One release branch for the bump and the notes**, cut from the
   updated `main`:

   ```bash
   git checkout main && git pull
   git checkout -b release/v0.4.0
   make version-update        # prompts, suggests patch/minor, edits every declaration
   $EDITOR docs/releases/v0.4.0.md   # the release notes — the draft's body, verbatim
   make validate
   ```

   `make version` proves the declarations agree (pyprojects, `__version__`s,
   the MCP server literal, the Dockerfile label, the extension manifest, the
   `content-sdk==` pins); `version-update` maintains them all, so never edit a
   version by hand. Write the notes now, with the release, in the same
   branch — the tag guard requires the file, and the draft workflow uses it
   as the release body.

3. **[browser] PR and merge the release branch.** Normal review, normal
   merge. Nothing has been released yet.

4. **[shell] Tag the merged commit:**

   ```bash
   git checkout main && git pull
   make version-tag
   ```

   The target enforces, in order: clean tree, versions agree, no existing
   tag for this version, `docs/releases/v<version>.md` present, HEAD is an
   ancestor of `origin/main`. Then it asks, tags `v<version>`, and offers to
   push. Pushing is the trigger — say no and nothing has happened yet.

5. **[tag] The push starts three things by itself**, publishing none of them
   to users:

   - `ci.yml` validates and pushes the **four GHCR images** (`content`,
     `content-hometube`, `content-studio`, `content-console`), amd64 + arm64,
     tagged `:<version>`, `:<minor>`, `:<major>`, `:latest` (exact tag only
     for a pre-release like `0.4.0-rc1`).
   - `release-draft.yml` re-validates the tagged tree, builds the extension
     zip and the three wheels, writes `SHA256SUMS.txt`, and creates a
     **draft** release with `docs/releases/v<version>.md` as its body.
   - Nothing else. PyPI is never triggered by a tag.

6. **[browser] Publish the draft.** Actions finished green → open the draft
   release, read it once as a stranger, publish. This is the moment
   `releases/latest` starts pointing at the new version.

7. **[browser] One PyPI run.** Actions → *Publish to PyPI* → Run workflow
   with `version=<x.y.z>`, `repository=pypi`, `only=all`. The workflow checks
   out the tag (never a branch), verifies the tree declares the requested
   version, rebuilds the wheels, `twine check`s them, and publishes all three
   packages via Trusted Publishing — no token exists to leak. If the
   `pypi` environment has a required reviewer, approve it there.

8. **[shell] Verify.** Each check exercises a different artifact:

   ```bash
   docker pull ghcr.io/latentnoise/content:<x.y.z>   # the images exist
   uv tool install --reinstall content-mcp           # PyPI serves the new version
   curl -s http://<engine>:8010/api/v1/health        # after compose pull: version matches
   ```

## TestPyPI or PyPI?

**Go straight to PyPI in the normal case.** The workflow already rebuilds
from the tag and `twine check --strict`s the metadata before anything is
uploaded, so "the artifact is broken" fails before PyPI sees it. What a
rehearsal would catch beyond that is only what PyPI itself evaluates after
accepting the file — README rendering, metadata quirks, a brand-new package
name. So use `repository=testpypi` when **packaging** changed (pyproject
metadata, entry points, a new package, trove classifiers) and go directly to
`pypi` when only **code** changed.

The reason TestPyPI is not a default step: **a version number is spent
forever on whichever index it touches**. Upload `0.4.0` anywhere and that
index will never accept another `0.4.0`, even after deletion — a rehearsal
that finds a packaging bug still costs the number, and the fix ships as
`0.4.1`. TestPyPI and PyPI keep separate ledgers, so a TestPyPI rehearsal
does not burn the number on PyPI — but it burns it on TestPyPI, and the
habit of rehearsing everything just spends numbers twice for nothing.

## When it goes wrong

The one rule that decides everything below: **git, GHCR and draft releases
can all be walked back; an accepted PyPI upload cannot.** Before the PyPI
run, any mistake is free. After it, the fix is always the next number.

**Tagged the wrong commit, not yet pushed.** `git tag -d v0.4.0`. Nothing
happened; start over.

**Tagged the wrong commit, pushed.** The workflows ran: images exist, a
draft exists. All of it is replaceable — delete the draft release in the
browser, delete the tag (`git push origin :refs/tags/v0.4.0`), re-tag the
right commit, push again. The second run overwrites the GHCR tags and
creates a fresh draft. Fully recoverable — *unless the PyPI run already
happened*, in which case the published wheels are whatever the wrong commit
built: yank them on PyPI (Manage → Options → Yank; hides, never frees) and
release the fix as the next patch.

**Half-merged release branch.** The ancestor guard refuses to tag, which is
the system working (v0.3.0 was tagged on an unmerged commit: the tag's tree
had no release notes and the published notes advertised a 404). Finish the
merge, `git checkout main && git pull`, `make version-tag` again. The
version number is not consumed until PyPI accepts an upload, so re-cutting
the same number locally costs nothing.

**Draft built from a tree without its notes.** The guard now makes this
impossible for new tags; it can still happen when dispatching
`release-draft.yml` on an old tag whose tree predates its notes file. The
tag's tree is frozen, so no re-run will find the notes — paste
`docs/releases/v<version>.md` into the draft body by hand in the browser.
The draft is editable until published; this is why it is a draft.

**PyPI publish fails midway** — say the SDK uploaded and the CLI errored.
The uploaded files are permanent; a plain re-run with `only=all` would trip
over them. Re-run once per *missing* package with `only=cli` / `only=mcp` —
the bootstrap restriction doubles as the recovery tool, uploading exactly
one package per run. If what uploaded is *wrong* rather than incomplete:
yank it and go to the next patch number; there is no other path.

| Artifact | Undo |
| --- | --- |
| Local tag | `git tag -d` — free |
| Pushed tag | delete remote tag + draft, redo — free |
| Draft release | edit or delete in the browser — free |
| GHCR images | overwritten by re-pushing the tag; deletable in package settings |
| PyPI / TestPyPI upload | **never** — yank hides it, the number stays spent |
