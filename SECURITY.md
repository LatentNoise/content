# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately, either through GitHub's private vulnerability reporting on
this repository (Security → Report a vulnerability), if enabled, or by email to
**<yann@orieult.com>**.

Please include:

- what you found and where;
- how to reproduce it;
- what an attacker could actually do with it;
- the Content version and how it was deployed.

**Never include real credentials, cookies, or API tokens** in a report — redact
them. Content is designed so secrets never enter a request (INV-009); please do
not undo that in a bug report.

## What to expect

This is a single-maintainer project with no company behind it. There is **no
guaranteed response time, no bounty, and no support commitment.** Reports are
read and taken seriously, and you will be told when a fix ships — but the
timeline depends on one person's availability.

Please give a reasonable window for a fix before disclosing publicly. If you do
not hear back, send a reminder rather than assuming the report was ignored.

## Supported versions

Content is pre-1.0 and moves fast. Only the **latest release** is supported;
fixes are not backported. If you are running an older version, the first step is
to update.

There is no LTS branch and none is planned.

## Scope

In scope: the engine, the SDK, the CLI, the MCP server, the web UIs, and the
published container images.

Out of scope, because they are separate projects with their own security
processes: **yt-dlp**, **ffmpeg**, **Ollama**, **faster-whisper**, and the cloud
LLM providers. Report those upstream. A Content issue caused by *how it invokes*
one of them is in scope.
